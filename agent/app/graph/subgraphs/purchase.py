import json
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.graph.state import SharedSlots, get_chat_llm
from app.graph.thought_stream import emit_thought
from app.tools.ticket_tools import (
    PURCHASE_READ_TOOLS,
    PURCHASE_WRITE_TOOLS,
    current_user_ctx,
    quote_seats_from_server,
)

_PURCHASE_READ_MAP = {t.name: t for t in PURCHASE_READ_TOOLS}
_PURCHASE_ALL_TOOLS = PURCHASE_READ_TOOLS + PURCHASE_WRITE_TOOLS
_MAX_PURCHASE_STEPS = 4


class PurchaseSubState(TypedDict, total=False):
    sub_messages: Annotated[List[BaseMessage], add_messages]
    query: str
    history_summary: str
    slots: SharedSlots
    user_id: int
    username: str
    user_role: str
    step_count: int
    answer: str
    tool_steps: List[Dict[str, Any]]
    pending_action: Optional[Dict[str, Any]]


def _get_purchase_llm():
    return get_chat_llm(temperature=0.1).bind_tools(_PURCHASE_ALL_TOOLS)


def _reply_all(
    tool_calls: List[Dict[str, Any]],
    primary_id: Optional[str],
    primary_msg: Optional[ToolMessage],
    skip_note: str = "同轮其余工具调用已跳过。",
) -> List[ToolMessage]:
    out: List[ToolMessage] = []
    for tc in tool_calls:
        tid = tc["id"]
        tname = tc.get("name", "unknown")
        if primary_id is not None and tid == primary_id and primary_msg is not None:
            out.append(primary_msg)
        else:
            out.append(
                ToolMessage(
                    content=json.dumps({"status": "SKIPPED", "message": skip_note}, ensure_ascii=False),
                    name=tname,
                    tool_call_id=tid,
                )
            )
    return out


async def purchase_agent_node(state: PurchaseSubState) -> Dict[str, Any]:
    """
    【子图 ② 节点 1：purchase_agent】
    只拥有选座域工具（search_shows / recommend_seats / propose_create_order）：
    - 若 wants_commit=False（仅咨询/推荐），严禁提前发起 propose_create_order；
    - 若 wants_commit=True 且已有推荐座位槽位（session_id + seat_ids），可直接发起锁座核价。
    """
    slots = dict(state.get("slots") or {})
    wants_commit = bool(slots.get("wants_commit"))
    step_count = int(state.get("step_count") or 0)

    commit_rule = (
        "用户本轮已明确要求锁座/下单！若已知具体的 session_id 与 seat_ids（见下方共享槽位或刚查出的结果），请直接调用 `propose_create_order`；若尚未拿到 seat_ids，先调 `search_shows` / `recommend_seats` 拿到后再调 `propose_create_order`。"
        if wants_commit
        else "用户本轮仅咨询演出或请求推荐座位（尚未确认立即下单），请只调用 `search_shows` 和 `recommend_seats` 给出清晰推荐方案，**严禁在本轮调用 `propose_create_order`**！"
    )

    sys_text = (
        "你是 PulseTicket 的【选座购票专员 (Purchase Agent)】。\n"
        f"【共享上下文槽位】: {json.dumps(slots, ensure_ascii=False)}\n"
        f"【最近 3 轮对话】: {state.get('history_summary') or '无'}\n"
        f"【执行约束】: {commit_rule}\n"
        "严禁在同一条回复里同时混调只读工具和 `propose_create_order`；若后端服务不可达或无余座，必须如实说明。"
    )

    msgs = list(state.get("sub_messages") or [])
    if not msgs:
        msgs = [
            SystemMessage(content=sys_text),
            HumanMessage(content=state.get("query") or ""),
        ]
    else:
        msgs = [SystemMessage(content=sys_text)] + [m for m in msgs if not isinstance(m, SystemMessage)]

    emit_thought(
        key=f"pur-reason-{step_count + 1}",
        phase="purchase",
        agent_label="选座购票子图",
        title="正在规划演出查询与选座动作…" if step_count == 0 else "正在汇总实时余座与比价推荐方案…",
        detail="结合预算与张数约束推理下一步只读工具或核价提案",
        status="running",
    )

    # 达到单轮最大工具步数上限时不再绑定工具，强制收敛总结回复防止死循环
    if step_count >= _MAX_PURCHASE_STEPS:
        resp = await get_chat_llm(temperature=0.1).ainvoke(msgs)
    else:
        resp = await _get_purchase_llm().ainvoke(msgs)

    tcs = getattr(resp, "tool_calls", None) or []
    if tcs:
        planned = ", ".join(tc.get("name", "") for tc in tcs)
        emit_thought(
            key=f"pur-reason-{step_count + 1}",
            phase="purchase",
            agent_label="选座购票子图",
            title=f"决策调用选座工具：{planned}",
            detail="向 Java show-service 获取实时场次与 48 座矩阵库存",
            badges=[tc.get("name", "") for tc in tcs],
            status="done",
        )
    else:
        emit_thought(
            key=f"pur-reason-{step_count + 1}",
            phase="purchase",
            agent_label="选座购票子图",
            title="已完成最佳连座与备选方案整理",
            detail="已将首选推荐席位记忆至跨轮共享槽位 slots，方便后续一键锁座",
            status="done",
        )

    answer = str(resp.content or "").strip() if not tcs else state.get("answer", "")
    return {
        "sub_messages": [resp],
        "step_count": step_count + 1,
        "answer": answer,
    }


async def purchase_read_tools_node(state: PurchaseSubState) -> Dict[str, Any]:
    """【子图 ② 节点 2：purchase_read_tools】执行查演出与连座推荐，并把推荐结果写回共享槽位 `slots`。"""
    last_msg = state["sub_messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    slots = dict(state.get("slots") or {})
    steps = list(state.get("tool_steps") or [])

    token = current_user_ctx.set({
        "user_id": state["user_id"],
        "username": state["username"],
        "role": state.get("user_role", "USER"),
    })
    out_msgs: List[ToolMessage] = []
    try:
        for tc in tool_calls:
            t_name = tc["name"]
            t_args = tc.get("args") or {}
            t_id = tc["id"]

            steps.append({"agent": "purchase_agent", "tool": t_name, "args": t_args})

            if t_name in _PURCHASE_READ_MAP:
                emit_thought(
                    key=f"pur-tool-{t_id}",
                    phase="purchase",
                    agent_label="选座购票子图",
                    title="正在查询在售演出目录…" if t_name == "search_shows" else f"正在计算场次 #{t_args.get('session_id')} 中轴线最佳连座…",
                    detail=json.dumps(t_args, ensure_ascii=False),
                    status="running",
                )
                obs = await _PURCHASE_READ_MAP[t_name].ainvoke(t_args)
                obs_str = str(obs)
                out_msgs.append(ToolMessage(content=obs_str, name=t_name, tool_call_id=t_id))

                if t_name == "search_shows":
                    emit_thought(
                        key=f"pur-tool-{t_id}",
                        phase="purchase",
                        agent_label="选座购票子图",
                        title="已拉取在售演出与排期场次",
                        detail=f"查询关键词：{t_args.get('keyword') or '全部在售'}",
                        badges=["show-service"],
                        status="done",
                    )

                # 自动将最新推荐出来的最佳连座记忆到跨轮共享槽位 slots，方便下一轮“就锁这两张吧”直接引用
                if t_name == "recommend_seats":
                    try:
                        parsed = json.loads(obs_str)
                        rec = parsed.get("recommended")
                        if parsed.get("found") and isinstance(rec, dict):
                            slots["session_id"] = int(parsed.get("sessionId") or t_args.get("session_id"))
                            slots["seat_ids"] = [int(x) for x in (rec.get("seatIds") or [])]
                            slots["seat_labels"] = list(rec.get("seatLabels") or [])
                            emit_thought(
                                key=f"pur-tool-{t_id}",
                                phase="purchase",
                                agent_label="选座购票子图",
                                title=f"场次 #{slots['session_id']} 最优连座：{'、'.join(slots['seat_labels'])}",
                                detail=(
                                    f"区域：{rec.get('zoneName')} · 合计 ¥{rec.get('totalAmount')} "
                                    f"（中轴线偏移分 {rec.get('centerScore')}）"
                                ),
                                badges=[f"#{slots['session_id']}", f"¥{rec.get('totalAmount')}"],
                                status="done",
                            )
                        else:
                            emit_thought(
                                key=f"pur-tool-{t_id}",
                                phase="purchase",
                                agent_label="选座购票子图",
                                title=f"场次 #{t_args.get('session_id')} 未找到满足预算连座",
                                detail=parsed.get("message", "余座不足"),
                                status="warn",
                            )
                    except Exception:
                        pass
            else:
                out_msgs.append(
                    ToolMessage(
                        content=json.dumps({"status": "ERROR", "message": f"非选座只读工具: {t_name}"}, ensure_ascii=False),
                        name=t_name,
                        tool_call_id=t_id,
                    )
                )
    finally:
        current_user_ctx.reset(token)

    return {
        "sub_messages": out_msgs,
        "slots": slots,
        "tool_steps": steps,
    }


async def server_quote_node(state: PurchaseSubState, config: RunnableConfig) -> Dict[str, Any]:
    """
    【子图 ② 节点 3：server_quote 确定性核价节点】：
    向 Java show-service 实时核验余座状态并计算权威总价（完全覆盖/忽略任何模型猜测），
    通过后生成 pending_action 交由主图触发 interrupt() 人工确认。
    """
    last_msg = state["sub_messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    write_tc = next((tc for tc in tool_calls if tc["name"] == "propose_create_order"), None)
    steps = list(state.get("tool_steps") or [])

    if write_tc is None:
        return {
            "sub_messages": _reply_all(tool_calls, None, None, "未找到锁座提案"),
            "pending_action": None,
        }

    t_args = write_tc.get("args") or {}
    t_id = write_tc["id"]
    thread_id = str((config.get("configurable") or {}).get("thread_id") or "")
    idempotency_key = f"{thread_id}:{t_id}" if thread_id else t_id

    steps.append({"agent": "purchase_agent", "tool": "propose_create_order", "args": t_args})
    emit_thought(
        key=f"pur-quote-{t_id}",
        phase="quote",
        agent_label="核价门控",
        title="正在请求 Java 服务端实时核价与余座校验…",
        detail=f"校验场次 #{t_args.get('session_id')} 席位 {t_args.get('seat_ids')} 状态并由 BigDecimal 计算应付总额",
        status="running",
    )

    quote_res = await quote_seats_from_server(t_args.get("session_id"), t_args.get("seat_ids"))
    steps.append({
        "agent": "purchase_agent",
        "tool": "server_quote",
        "args": {
            "ok": bool(quote_res.get("ok")),
            "totalAmount": (quote_res.get("quote") or {}).get("totalAmount"),
        },
    })

    if not quote_res.get("ok"):
        emit_thought(
            key=f"pur-quote-{t_id}",
            phase="quote",
            agent_label="核价门控",
            title="服务端核价未通过 · 已拦截确认卡片",
            detail=quote_res.get("message", "座位已不可用"),
            badges=["409 拦截"],
            status="warn",
        )
        err_msg = ToolMessage(
            content=json.dumps(
                {"status": "QUOTE_FAILED", "message": quote_res.get("message", "服务端核价未通过")},
                ensure_ascii=False,
            ),
            name="propose_create_order",
            tool_call_id=t_id,
        )
        return {
            "sub_messages": _reply_all(tool_calls, t_id, err_msg),
            "tool_steps": steps,
            "pending_action": None,
        }

    quote = quote_res["quote"]
    emit_thought(
        key=f"pur-quote-{t_id}",
        phase="quote",
        agent_label="核价门控",
        title=f"Java 权威核价通过：¥{quote['totalAmount']}",
        detail=(
            f"席位 {'、'.join(quote['seatLabels'])} 均处于 AVAILABLE 状态；"
            f"已签发幂等键并挂起状态图等待人工点击确认"
        ),
        badges=[f"¥{quote['totalAmount']}", "Interrupt 挂起"],
        status="done",
    )
    verified_args = {
        "session_id": quote["sessionId"],
        "seat_ids": quote["seatIds"],
        "seat_labels": quote["seatLabels"],
        "show_title": quote["showTitle"],
        "session_title": quote["sessionTitle"],
        "venue_name": quote["venueName"],
        "total_amount": quote["totalAmount"],
    }
    card_payload = {
        "actionType": "CREATE_ORDER",
        "toolName": "propose_create_order",
        "toolCallId": t_id,
        "idempotencyKey": idempotency_key,
        "title": "确认锁座下单",
        "isMock": bool(quote_res.get("isMock")),
        "priceSource": quote_res.get("source"),
        "args": verified_args,
    }

    slots = dict(state.get("slots") or {})
    slots["session_id"] = quote["sessionId"]
    slots["seat_ids"] = quote["seatIds"]
    slots["seat_labels"] = quote["seatLabels"]

    return {
        "slots": slots,
        "tool_steps": steps,
        "pending_action": {
            "toolName": "propose_create_order",
            "toolCallId": t_id,
            "allToolCalls": tool_calls,
            "idempotencyKey": idempotency_key,
            "execArgs": verified_args,
            "cardPayload": card_payload,
        },
    }


def _route_from_purchase_agent(state: PurchaseSubState) -> str:
    msgs = state.get("sub_messages") or []
    if not msgs:
        return END
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", []) or []
    if not tcs:
        return END
    if any(tc["name"] == "propose_create_order" for tc in tcs):
        return "server_quote"
    return "purchase_read_tools"


def _route_after_quote(state: PurchaseSubState) -> str:
    if state.get("pending_action"):
        return END
    return "purchase_agent"


def build_purchase_subgraph():
    """构建并编译子 Agent ②：选座购票子图"""
    builder = StateGraph(PurchaseSubState)
    builder.add_node("purchase_agent", purchase_agent_node)
    builder.add_node("purchase_read_tools", purchase_read_tools_node)
    builder.add_node("server_quote", server_quote_node)

    builder.add_edge(START, "purchase_agent")
    builder.add_conditional_edges(
        "purchase_agent",
        _route_from_purchase_agent,
        {
            "purchase_read_tools": "purchase_read_tools",
            "server_quote": "server_quote",
            END: END,
        },
    )
    builder.add_edge("purchase_read_tools", "purchase_agent")
    builder.add_conditional_edges(
        "server_quote",
        _route_after_quote,
        {
            "purchase_agent": "purchase_agent",
            END: END,
        },
    )
    return builder.compile()

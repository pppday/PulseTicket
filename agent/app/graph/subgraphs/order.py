import json
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.graph.state import SharedSlots, get_chat_llm
from app.graph.thought_stream import astream_llm_message, emit_thought, emit_tool_card, emit_widget
from app.tools.ticket_tools import (
    ORDER_READ_TOOLS,
    ORDER_WRITE_TOOLS,
    calculate_tiered_refund_quote,
    current_user_ctx,
    inspect_order_for_cancel,
)

_ORDER_READ_MAP = {t.name: t for t in ORDER_READ_TOOLS}
_ORDER_ALL_TOOLS = ORDER_READ_TOOLS + ORDER_WRITE_TOOLS
_MAX_ORDER_STEPS = 4


class OrderSubState(TypedDict, total=False):
    sub_messages: Annotated[List[BaseMessage], add_messages]
    query: str
    history_summary: str
    mode: str
    slots: SharedSlots
    user_id: int
    username: str
    user_role: str
    step_count: int
    answer: str
    citations: List[str]
    tool_steps: List[Dict[str, Any]]
    pending_action: Optional[Dict[str, Any]]


def _get_order_llm():
    return get_chat_llm(temperature=0.1).bind_tools(_ORDER_ALL_TOOLS)


def _get_order_read_llm():
    return get_chat_llm(temperature=0.1).bind_tools(ORDER_READ_TOOLS)


def _reply_all(
    tool_calls: List[Dict[str, Any]],
    primary_id: Optional[str],
    primary_msg: Optional[ToolMessage],
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
                    content=json.dumps({"status": "SKIPPED", "message": "同轮其余工具已跳过"}, ensure_ascii=False),
                    name=tname,
                    tool_call_id=tid,
                )
            )
    return out


async def order_agent_node(state: OrderSubState) -> Dict[str, Any]:
    """
    【子图 ③ 节点 1：order_agent 订单与售后专员】：
    - 查订单调 `list_my_orders`
    - 问某单能退多少钱调 `check_order_refund_detail`（由 Python Decimal 确定性计算手续费，严禁心算！）
    - 取消未支付订单调 `propose_cancel_order`
    - 已支付且距开演 >= 24h 的订单退票调 `propose_refund_order`
    """
    slots = dict(state.get("slots") or {})
    mode = (state.get("mode") or "agent").strip().lower()
    is_chat_mode = mode == "chat"
    step_count = int(state.get("step_count") or 0)

    chat_mode_note = (
        "\n5. 【当前为 Chat 只读咨询模式】：严禁调用 `propose_cancel_order` 与 `propose_refund_order`，"
        "仅查订单与测算退票费；若用户要求取消或退票，请友好提示其切换左下角【Agent 模式】操作。"
        if is_chat_mode
        else ""
    )

    sys_text = (
        "你是 PulseTicket 的【订单与售后专员 (Order Agent)】。\n"
        f"【共享上下文槽位】: {json.dumps(slots, ensure_ascii=False)}\n"
        f"【最近 3 轮对话】: {state.get('history_summary') or '无'}\n"
        "职责规范：\n"
        "1. 若用户未提供订单号，先调用 `list_my_orders` 查询用户名下订单；\n"
        "2. 若用户仅咨询“某单退票扣多少手续费、能退多少钱”而未明确要求退票，调用 `check_order_refund_detail` 获取代码计算的精确金额，并标注 `[POL-REFUND-001]`，严禁自己心算；\n"
        "3. 若用户明确要求取消某笔未支付（UNPAID）订单（如“取消订单/申请取消”），拿到订单号后直接调用 `propose_cancel_order` 弹出确认卡片，勿先用文字二次追问确认；\n"
        "4. 若用户明确要求对已支付（PAID）订单办理退票/退款（如“申请退票/退票退款/我要退款”）：\n"
        "   - 若还未掌握具体订单号，先调用 `list_my_orders`；已有订单号时**直接调用 `propose_refund_order`**（系统会在前置校验节点自动查状态并按 `[POL-REFUND-001]` 核算手续费与实退金额后弹出确认卡片等待用户点击确认；若距开演不足 24h 会自动拦截并交由你向用户说明原因），**不要**用纯文字追问“是否需要发起退票”！"
        + chat_mode_note
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
        key=f"ord-reason-{step_count + 1}",
        phase="order",
        agent_label="订单售后子图",
        title="正在分析订单查询与退改核算需求…",
        detail="由确定性代码执行订单核查与阶梯手续费计算，禁止模型心算金额",
        status="running",
    )

    if step_count >= _MAX_ORDER_STEPS:
        resp = await astream_llm_message(get_chat_llm(temperature=0.1), msgs, allow_stream=True)
    else:
        llm = _get_order_read_llm() if is_chat_mode else _get_order_llm()
        resp = await astream_llm_message(llm, msgs, allow_stream=True)

    tcs = getattr(resp, "tool_calls", None) or []
    emit_thought(
        key=f"ord-reason-{step_count + 1}",
        phase="order",
        agent_label="订单售后子图",
        title=f"调用订单工具：{', '.join(tc.get('name', '') for tc in tcs)}" if tcs else "已完成订单与售后结论汇总",
        detail="查询 order-service 实时状态并同步核对退改条款",
        badges=[tc.get("name", "") for tc in tcs] if tcs else ["Done"],
        status="done",
    )

    answer = str(resp.content or "").strip() if not tcs else state.get("answer", "")
    return {
        "sub_messages": [resp],
        "step_count": step_count + 1,
        "answer": answer,
    }


async def order_read_tools_node(state: OrderSubState) -> Dict[str, Any]:
    """【子图 ③ 节点 2：order_read_tools】执行订单列表查询与确定性退票费率计算。"""
    last_msg = state["sub_messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    steps = list(state.get("tool_steps") or [])
    citations = list(state.get("citations") or [])
    slots = dict(state.get("slots") or {})
    mode = (state.get("mode") or "agent").strip().lower()

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
            steps.append({"agent": "order_agent", "tool": t_name, "args": t_args})

            if t_name in _ORDER_READ_MAP:
                card_id = f"tool-{t_name}-{t_id}"
                card_title = "查询我的实时订单列表" if t_name == "list_my_orders" else "确定性阶梯退票费率核算"
                emit_tool_card(
                    card_id=card_id,
                    tool_name=t_name,
                    title=card_title,
                    status="running",
                    summary="正在调用 Java order-service 与费率引擎…",
                    input_data=t_args,
                )
                obs = await _ORDER_READ_MAP[t_name].ainvoke(t_args)
                obs_str = str(obs)
                out_msgs.append(ToolMessage(content=obs_str, name=t_name, tool_call_id=t_id))

                if t_name == "check_order_refund_detail" and "POL-REFUND-001" not in citations:
                    citations.append("POL-REFUND-001")
                try:
                    parsed = json.loads(obs_str)
                except Exception:
                    parsed = None

                if t_name == "list_my_orders":
                    orders = (parsed or {}).get("orders") or []
                    if orders and not slots.get("order_no"):
                        slots["order_no"] = orders[0].get("orderNo")
                    ok_flag = (parsed or {}).get("ok") is not False
                    emit_tool_card(
                        card_id=card_id,
                        tool_name=t_name,
                        title=card_title,
                        status="done" if ok_flag else "error",
                        summary=f"已拉取 {len(orders)} 笔历史订单" if ok_flag else (parsed or {}).get("error", "查询失败"),
                        input_data=t_args,
                        output_data=parsed if parsed is not None else obs_str,
                    )
                    if mode != "chat":
                        order_options = []
                        unpaid = [o for o in orders if str(o.get("status", "")).upper() == "UNPAID"]
                        paid_list = [o for o in orders if str(o.get("status", "")).upper() == "PAID"]

                        def _fmt_seats(raw_seats: Any) -> str:
                            seat_txt = raw_seats or ""
                            if isinstance(seat_txt, str) and seat_txt.startswith("["):
                                try:
                                    return "、".join(json.loads(seat_txt))
                                except Exception:
                                    return seat_txt
                            return str(seat_txt)

                        for o in unpaid[:2]:
                            ono = str(o.get("orderNo") or "")
                            seat_txt = _fmt_seats(o.get("seatLabelsJson"))
                            order_options.append({
                                "id": f"order-cancel-{ono}",
                                "label": f"{ono} · {o.get('showTitle', '')}",
                                "badge": "待支付 UNPAID",
                                "meta": f"{seat_txt} · 应付 ¥{o.get('totalAmount')}".strip(" ·"),
                                "price": o.get("totalAmount"),
                                "prompt": f"申请取消未支付订单 {ono}",
                            })

                        for o in paid_list:
                            if len(order_options) >= 4:
                                break
                            calc = calculate_tiered_refund_quote(o.get("totalAmount"), o.get("startTime"))
                            if not (calc.get("knownStartTime") and calc.get("refundable")):
                                continue
                            ono = str(o.get("orderNo") or "")
                            seat_txt = _fmt_seats(o.get("seatLabelsJson"))
                            order_options.append({
                                "id": f"order-refund-{ono}",
                                "label": f"{ono} · {o.get('showTitle', '')}",
                                "badge": f"可退 · {calc.get('feeRate')}手续费",
                                "meta": f"{seat_txt} · 预计实退 ¥{calc.get('refundAmount')}".strip(" ·"),
                                "price": calc.get("refundAmount"),
                                "prompt": f"申请退票退款订单 {ono}",
                            })

                        if order_options:
                            emit_widget(
                                widget_id=f"widget-orders-{t_id}",
                                widget_type="ORDER_PICKER",
                                title="可操作订单快捷选择 · 支持取消锁座或阶梯退票",
                                subtitle="点击订单将自动核验状态/退票费率并弹出确认卡片",
                                options=order_options,
                            )
                else:
                    refund_ok = isinstance(parsed, dict) and parsed.get("ok") is not False
                    summary_txt = "阶梯退票费率计算完成"
                    if isinstance(parsed, dict):
                        if parsed.get("refundAmount") is not None:
                            summary_txt = f"费率 {parsed.get('feeRate')} · 预计可退 ¥{parsed.get('refundAmount')}"
                        elif parsed.get("error"):
                            summary_txt = str(parsed.get("error"))
                    emit_tool_card(
                        card_id=card_id,
                        tool_name=t_name,
                        title=card_title,
                        status="done" if refund_ok else "error",
                        summary=summary_txt,
                        input_data=t_args,
                        output_data=parsed if parsed is not None else obs_str,
                    )
            else:
                out_msgs.append(
                    ToolMessage(
                        content=json.dumps({"status": "ERROR", "message": f"非订单只读工具: {t_name}"}, ensure_ascii=False),
                        name=t_name,
                        tool_call_id=t_id,
                    )
                )
    finally:
        current_user_ctx.reset(token)

    return {
        "sub_messages": out_msgs,
        "tool_steps": steps,
        "citations": citations,
        "slots": slots,
    }


async def verify_cancel_node(state: OrderSubState, config: RunnableConfig) -> Dict[str, Any]:
    """
    【子图 ③ 节点 3：verify_cancel 确定性订单状态核验】：
    向 Java order-service 核实订单归属、状态与阶梯退票费率：
    - UNPAID 订单：生成 CANCEL_ORDER 取消确认卡片 pending_action；
    - PAID 且距开演 >= 24h 订单：生成 REFUND_ORDER 阶梯退票确认卡片 pending_action；
    - 不可退或已关闭订单：返回明确核验结果回退给 order_agent 向用户说明。
    """
    last_msg = state["sub_messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    write_tc = next(
        (tc for tc in tool_calls if tc["name"] in ("propose_cancel_order", "propose_refund_order")),
        None,
    )
    steps = list(state.get("tool_steps") or [])
    citations = list(state.get("citations") or [])

    if write_tc is None:
        return {
            "sub_messages": _reply_all(tool_calls, None, None),
            "pending_action": None,
        }

    req_tool_name = write_tc["name"]
    t_args = write_tc.get("args") or {}
    t_id = write_tc["id"]
    order_no = str(t_args.get("order_no") or "").strip()
    default_reason = (
        "用户主动申请已支付订单阶梯退票"
        if req_tool_name == "propose_refund_order"
        else "用户主动申请取消未支付订单"
    )
    refund_reason = str(t_args.get("refund_reason") or default_reason)
    thread_id = str((config.get("configurable") or {}).get("thread_id") or "")
    idempotency_key = f"{thread_id}:{t_id}" if thread_id else t_id

    steps.append({"agent": "order_agent", "tool": req_tool_name, "args": {"order_no": order_no}})

    verify_card_id = f"tool-verify-cancel-{t_id}"
    verify_title = (
        f"核验订单 {order_no} 阶梯退票资格"
        if req_tool_name == "propose_refund_order"
        else f"核验订单 {order_no} 取消/退票状态"
    )
    emit_tool_card(
        card_id=verify_card_id,
        tool_name="verify_order_status",
        title=verify_title,
        status="running",
        summary="正在向 Java order-service 核实订单归属、状态与阶梯退票费率…",
        input_data={"order_no": order_no},
    )

    token = current_user_ctx.set({
        "user_id": state["user_id"],
        "username": state["username"],
        "role": state.get("user_role", "USER"),
    })
    try:
        check_res = await inspect_order_for_cancel(order_no)
    finally:
        current_user_ctx.reset(token)

    steps.append({
        "agent": "order_agent",
        "tool": "verify_order_status",
        "args": {"orderNo": order_no, "ok": bool(check_res.get("ok"))},
    })

    action_type = check_res.get("actionType") or "CANCEL_ORDER"
    resolved_tool_name = check_res.get("toolName") or (
        "propose_refund_order" if action_type == "REFUND_ORDER" else "propose_cancel_order"
    )
    refund_calc = check_res.get("refundCalc") or {}
    if check_res.get("ok"):
        done_summary = (
            f"状态为 PAID · 命中{refund_calc.get('feeRate', '阶梯')}手续费，预计实退 ¥{refund_calc.get('refundAmount')} · 等待确认"
            if action_type == "REFUND_ORDER"
            else "状态为 UNPAID · 允许发起人工取消确认"
        )
    else:
        done_summary = str(check_res.get("message") or "当前状态不支持自助操作")

    emit_tool_card(
        card_id=verify_card_id,
        tool_name="verify_order_status",
        title=verify_title,
        status="done" if check_res.get("ok") else "error",
        summary=done_summary,
        input_data={"order_no": order_no},
        output_data=check_res,
    )

    if not check_res.get("ok"):
        emit_thought(
            key=f"ord-verify-{t_id}",
            phase="order",
            agent_label="订单售后子图",
            title=f"售后前置校验拦截（订单 {order_no}）",
            detail=check_res.get("message", "当前状态不支持自助退票或取消"),
            badges=[check_res.get("orderStatus") or "拦截"],
            status="warn",
        )
        if check_res.get("orderStatus") == "PAID" and "POL-REFUND-001" not in citations:
            citations.append("POL-REFUND-001")
        err_msg = ToolMessage(
            content=json.dumps(
                {"status": "CANCEL_PRECHECK_FAILED", "message": check_res.get("message", "订单状态不支持取消或退票")},
                ensure_ascii=False,
            ),
            name=req_tool_name,
            tool_call_id=t_id,
        )
        return {
            "sub_messages": _reply_all(tool_calls, t_id, err_msg),
            "tool_steps": steps,
            "citations": citations,
            "pending_action": None,
        }

    order_info = check_res["order"]
    verified_args: Dict[str, Any] = {
        "order_no": order_info["orderNo"],
        "show_title": order_info.get("showTitle", ""),
        "session_title": order_info.get("sessionTitle", ""),
        "seat_labels": order_info.get("seatLabels", []),
        "total_amount": order_info.get("totalAmount"),
        "refund_reason": refund_reason,
    }

    if action_type == "REFUND_ORDER":
        if "POL-REFUND-001" not in citations:
            citations.append("POL-REFUND-001")
        verified_args.update({
            "fee_rate": refund_calc.get("feeRate"),
            "fee_amount": refund_calc.get("feeAmount"),
            "refund_amount": refund_calc.get("refundAmount"),
            "tier_name": refund_calc.get("tierName"),
            "hours_before_start": refund_calc.get("hoursBeforeStart"),
        })
        card_title = "确认申请阶梯退票退款"
    else:
        card_title = "确认取消未支付订单"

    card_payload = {
        "actionType": action_type,
        "toolName": resolved_tool_name,
        "toolCallId": t_id,
        "idempotencyKey": idempotency_key,
        "title": card_title,
        "isMock": bool(check_res.get("isMock")),
        "args": verified_args,
    }
    return {
        "tool_steps": steps,
        "citations": citations,
        "pending_action": {
            "toolName": resolved_tool_name,
            "toolCallId": t_id,
            "allToolCalls": tool_calls,
            "idempotencyKey": idempotency_key,
            "execArgs": verified_args,
            "cardPayload": card_payload,
        },
    }


def _route_from_order_agent(state: OrderSubState) -> str:
    msgs = state.get("sub_messages") or []
    if not msgs:
        return END
    tcs = getattr(msgs[-1], "tool_calls", []) or []
    if not tcs:
        return END
    if any(tc["name"] in ("propose_cancel_order", "propose_refund_order") for tc in tcs):
        return "verify_cancel"
    return "order_read_tools"


def _route_after_verify(state: OrderSubState) -> str:
    if state.get("pending_action"):
        return END
    return "order_agent"


def build_order_subgraph():
    """构建并编译子 Agent ③：订单与售后子图"""
    builder = StateGraph(OrderSubState)
    builder.add_node("order_agent", order_agent_node)
    builder.add_node("order_read_tools", order_read_tools_node)
    builder.add_node("verify_cancel", verify_cancel_node)

    builder.add_edge(START, "order_agent")
    builder.add_conditional_edges(
        "order_agent",
        _route_from_order_agent,
        {
            "order_read_tools": "order_read_tools",
            "verify_cancel": "verify_cancel",
            END: END,
        },
    )
    builder.add_edge("order_read_tools", "order_agent")
    builder.add_conditional_edges(
        "verify_cancel",
        _route_after_verify,
        {
            "order_agent": "order_agent",
            END: END,
        },
    )
    return builder.compile()

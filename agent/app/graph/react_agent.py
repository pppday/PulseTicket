import json
import re
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import aiosqlite
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from app.config import settings
from app.tools.ticket_tools import (
    ALL_TOOLS,
    READ_TOOLS_BY_NAME,
    WRITE_TOOL_NAMES,
    current_user_ctx,
    execute_confirmed_write_action,
    inspect_order_for_cancel,
    quote_seats_from_server,
)

SYSTEM_PROMPT = """你是 PulseTicket（智脉票务平台）的官方 AI 购票与客服智能体（基于 LangGraph ReAct + Hybrid RAG 架构）。
你可以在同一个对话框里为用户提供以下服务：
1. 【规则与场馆问答】：当用户询问退票规定、退票手续费、实名制入场、儿童买票身高、演出场馆（上海梅赛德斯/北京天桥/深圳万象城/杭州大麦66/长沙场馆）地铁交通、禁带物品或寄存处时，必须先调用 `search_ticket_policy` 检索知识库，并在回答中明确标注条款编号（如 `[POL-REFUND-001]`），严禁凭空编造规则。
2. 【演出查询与智能选座】：当用户想看近期演出或想选座时，先调用 `search_shows` 获取场次 ID (`sessionId`)，再调用 `recommend_seats` 为用户推荐同排相邻且靠近中轴线的最佳连座。
3. 【锁座下单与取消订单（高风险写操作）】：
   - 当用户要求帮他买票/订票/锁座时，先确保已通过 `recommend_seats` 拿到具体的 `session_id` 和 `seat_ids`，再调用 `propose_create_order`。
   - 当用户要求取消订单时，先调用 `list_my_orders` 查出订单号（若用户未提供），再调用 `propose_cancel_order`（仅支持未支付 UNPAID 订单）。
   - 严禁在同一轮同时调用只读工具和写操作工具，必须先完成只读查询拿到真实数据后，下一步再单独调用写操作工具。
   - 若工具返回服务不可达或核价失败，必须如实告知用户原因，严禁虚构下单成功或单号。
回答请保持简洁、专业、热情，条理清晰。"""


class TicketAgentState(TypedDict, total=False):
    messages: Annotated[List[BaseMessage], add_messages]
    user_id: int
    username: str
    user_role: str
    # 由 prepare_action_node 填充，供 confirm_action_node 无副作用触发 interrupt()
    pending_action: Optional[Dict[str, Any]]


_CACHED_LLM_WITH_TOOLS = None


def _get_llm_with_tools():
    global _CACHED_LLM_WITH_TOOLS
    if _CACHED_LLM_WITH_TOOLS is None:
        if not settings.LLM_API_KEY:
            raise RuntimeError("未配置 LLM_API_KEY（或 DEEPSEEK_API_KEY），请在 .env 中配置后重试")
        llm = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            temperature=0.1,
            timeout=settings.LLM_TIMEOUT_SEC,
        )
        _CACHED_LLM_WITH_TOOLS = llm.bind_tools(ALL_TOOLS)
    return _CACHED_LLM_WITH_TOOLS


def _reply_all_tool_calls(
    tool_calls: List[Dict[str, Any]],
    primary_id: Optional[str],
    primary_msg: Optional[ToolMessage],
    skip_reason: str = "写操作需单独确认，同轮附带的其余工具调用已跳过，如有需要请下一轮单独调用。",
) -> List[ToolMessage]:
    """确保 AIMessage 中的每一个 tool_call_id 都有匹配的 ToolMessage，彻底消灭悬空 tool_call。"""
    out: List[ToolMessage] = []
    for tc in tool_calls:
        tc_id = tc["id"]
        tc_name = tc.get("name", "unknown")
        if primary_id is not None and tc_id == primary_id and primary_msg is not None:
            out.append(primary_msg)
        else:
            out.append(
                ToolMessage(
                    content=json.dumps({"status": "SKIPPED", "message": skip_reason}, ensure_ascii=False),
                    name=tc_name,
                    tool_call_id=tc_id,
                )
            )
    return out


async def agent_node(state: TicketAgentState) -> Dict[str, Any]:
    """【ReAct 推理节点 (Reason)】"""
    msgs = list(state.get("messages") or [])
    if not msgs or not isinstance(msgs[0], SystemMessage):
        msgs = [SystemMessage(content=SYSTEM_PROMPT)] + msgs

    llm_with_tools = _get_llm_with_tools()
    response: AIMessage = await llm_with_tools.ainvoke(msgs)
    return {"messages": [response], "pending_action": None}


async def read_tools_node(state: TicketAgentState) -> Dict[str, Any]:
    """【只读工具执行节点 (Act - ReadOnly)】：严格保证 100% tool_call_id 都有 ToolMessage 回执。"""
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []

    token = current_user_ctx.set({
        "user_id": state["user_id"],
        "username": state["username"],
        "role": state.get("user_role", "USER"),
    })
    out_messages: List[ToolMessage] = []
    try:
        for tc in tool_calls:
            t_name = tc["name"]
            t_args = tc.get("args") or {}
            t_id = tc["id"]

            if t_name in READ_TOOLS_BY_NAME:
                tool_fn = READ_TOOLS_BY_NAME[t_name]
                obs = await tool_fn.ainvoke(t_args)
                out_messages.append(
                    ToolMessage(content=str(obs), name=t_name, tool_call_id=t_id)
                )
            else:
                out_messages.append(
                    ToolMessage(
                        content=json.dumps(
                            {"status": "ERROR", "message": f"未知或不允许在只读节点执行的工具: {t_name}"},
                            ensure_ascii=False,
                        ),
                        name=t_name,
                        tool_call_id=t_id,
                    )
                )
    finally:
        current_user_ctx.reset(token)

    return {"messages": out_messages}


async def prepare_action_node(state: TicketAgentState, config: RunnableConfig) -> Dict[str, Any]:
    """
    【写操作前置服务端核价与状态校验节点】：
    在进入 interrupt() 之前运行且只运行一次（避免 LangGraph resume 重放节点时重复调接口）：
    1. 对锁座下单：向 Java show-service 实时核价与校验余座（绝不信任大模型金额）
    2. 对取消订单：向 Java order-service 校验订单确实处于 UNPAID 状态
    3. 核验通过则组装带 idempotencyKey 的 pending_action 交给 confirm_action_node
    4. 核验失败则直接补齐全部 ToolMessage 并回退 agent_node 说明原因
    """
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    write_tc = next((tc for tc in tool_calls if tc["name"] in WRITE_TOOL_NAMES), None)

    if write_tc is None:
        return {
            "messages": _reply_all_tool_calls(tool_calls, None, None, "未发现有效写操作工具调用"),
            "pending_action": None,
        }

    t_name = write_tc["name"]
    t_args = write_tc.get("args") or {}
    t_id = write_tc["id"]
    thread_id = str((config.get("configurable") or {}).get("thread_id") or "")
    idempotency_key = f"{thread_id}:{t_id}" if thread_id else t_id

    token = current_user_ctx.set({
        "user_id": state["user_id"],
        "username": state["username"],
        "role": state.get("user_role", "USER"),
    })
    try:
        if t_name == "propose_create_order":
            quote_res = await quote_seats_from_server(t_args.get("session_id"), t_args.get("seat_ids"))
            if not quote_res.get("ok"):
                err_msg = ToolMessage(
                    content=json.dumps(
                        {"status": "QUOTE_FAILED", "message": quote_res.get("message", "服务端核价失败")},
                        ensure_ascii=False,
                    ),
                    name=t_name,
                    tool_call_id=t_id,
                )
                return {
                    "messages": _reply_all_tool_calls(tool_calls, t_id, err_msg),
                    "pending_action": None,
                }

            quote = quote_res["quote"]
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
                "toolName": t_name,
                "toolCallId": t_id,
                "idempotencyKey": idempotency_key,
                "title": "确认锁座下单",
                "isMock": bool(quote_res.get("isMock")),
                "priceSource": quote_res.get("source"),
                "args": verified_args,
            }
        else:
            order_no = str(t_args.get("order_no") or "").strip()
            refund_reason = str(t_args.get("refund_reason") or "用户主动申请取消未支付订单")
            check_res = await inspect_order_for_cancel(order_no)
            if not check_res.get("ok"):
                err_msg = ToolMessage(
                    content=json.dumps(
                        {"status": "CANCEL_PRECHECK_FAILED", "message": check_res.get("message", "订单核验失败")},
                        ensure_ascii=False,
                    ),
                    name=t_name,
                    tool_call_id=t_id,
                )
                return {
                    "messages": _reply_all_tool_calls(tool_calls, t_id, err_msg),
                    "pending_action": None,
                }

            order_info = check_res["order"]
            verified_args = {
                "order_no": order_info["orderNo"],
                "show_title": order_info.get("showTitle", ""),
                "session_title": order_info.get("sessionTitle", ""),
                "seat_labels": order_info.get("seatLabels", []),
                "total_amount": order_info.get("totalAmount"),
                "refund_reason": refund_reason,
            }
            card_payload = {
                "actionType": "CANCEL_ORDER",
                "toolName": t_name,
                "toolCallId": t_id,
                "idempotencyKey": idempotency_key,
                "title": "确认取消未支付订单",
                "isMock": bool(check_res.get("isMock")),
                "args": verified_args,
            }
    finally:
        current_user_ctx.reset(token)

    return {
        "pending_action": {
            "toolName": t_name,
            "toolCallId": t_id,
            "idempotencyKey": idempotency_key,
            "execArgs": verified_args,
            "cardPayload": card_payload,
        }
    }


async def confirm_action_node(state: TicketAgentState) -> Dict[str, Any]:
    """
    【Human-in-the-Loop 人工确认中断节点 (无前置副作用，100% 可确定性重放)】：
    读取 prepare_action_node 校验后的 pending_action 并调用 interrupt()；
    恢复后携带 idempotencyKey 调用 Java order-service 并补齐所有 tool_call_id 的 ToolMessage。
    """
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    pending = state.get("pending_action")

    if not pending:
        return {"pending_action": None}

    t_name = pending["toolName"]
    t_id = pending["toolCallId"]
    idempotency_key = pending["idempotencyKey"]
    exec_args = pending["execArgs"]
    card_payload = pending["cardPayload"]

    human_decision = interrupt(card_payload)

    confirmed = bool((human_decision or {}).get("confirmed", False))
    if not confirmed:
        cancel_msg = ToolMessage(
            content=json.dumps(
                {"status": "USER_REJECTED", "message": "用户在确认卡片中点击了【暂不执行/取消操作】。"},
                ensure_ascii=False,
            ),
            name=t_name,
            tool_call_id=t_id,
        )
        return {
            "messages": _reply_all_tool_calls(tool_calls, t_id, cancel_msg),
            "pending_action": None,
        }

    token = current_user_ctx.set({
        "user_id": state["user_id"],
        "username": state["username"],
        "role": state.get("user_role", "USER"),
    })
    try:
        exec_res = await execute_confirmed_write_action(
            t_name,
            exec_args,
            idempotency_key=idempotency_key,
        )
    finally:
        current_user_ctx.reset(token)

    done_msg = ToolMessage(
        content=json.dumps(exec_res, ensure_ascii=False),
        name=t_name,
        tool_call_id=t_id,
    )
    return {
        "messages": _reply_all_tool_calls(tool_calls, t_id, done_msg),
        "pending_action": None,
    }


def route_from_agent(state: TicketAgentState) -> str:
    messages = state.get("messages") or []
    if not messages:
        return END
    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    if not tool_calls:
        return END

    if any(tc["name"] in WRITE_TOOL_NAMES for tc in tool_calls):
        return "prepare_action_node"
    return "read_tools_node"


def route_from_prepare(state: TicketAgentState) -> str:
    if state.get("pending_action"):
        return "confirm_action_node"
    return "agent_node"


def build_ticket_react_graph() -> StateGraph:
    builder = StateGraph(TicketAgentState)

    builder.add_node("agent_node", agent_node)
    builder.add_node("read_tools_node", read_tools_node)
    builder.add_node("prepare_action_node", prepare_action_node)
    builder.add_node("confirm_action_node", confirm_action_node)

    builder.add_edge(START, "agent_node")
    builder.add_conditional_edges(
        "agent_node",
        route_from_agent,
        {
            "read_tools_node": "read_tools_node",
            "prepare_action_node": "prepare_action_node",
            END: END,
        },
    )
    builder.add_edge("read_tools_node", "agent_node")
    builder.add_conditional_edges(
        "prepare_action_node",
        route_from_prepare,
        {
            "confirm_action_node": "confirm_action_node",
            "agent_node": "agent_node",
        },
    )
    builder.add_edge("confirm_action_node", "agent_node")

    return builder


_CANCEL_KEYWORDS = (
    "取消当前操作",
    "放弃当前操作",
    "不买了",
    "先不买",
    "不下单",
    "别下单",
    "暂不执行",
    "算了",
    "不用了",
    "放弃",
    "取消",
)


def _is_explicit_cancel_intent(text: str) -> bool:
    clean = (text or "").strip().lower()
    return any(kw in clean for kw in _CANCEL_KEYWORDS)


def _is_pure_cancel_phrase(text: str) -> bool:
    clean = re.sub(r"[，。！？、,.!?\s]+", "", (text or "").strip())
    return len(clean) <= 10 and _is_explicit_cancel_intent(clean)


def _extract_interrupt_card(snapshot: Any) -> Optional[Dict[str, Any]]:
    if not snapshot or not getattr(snapshot, "next", None):
        return None
    for task in getattr(snapshot, "tasks", None) or []:
        if getattr(task, "interrupts", None):
            return task.interrupts[0].value
    return None


class TicketAgentManager:
    """管理持久化 Checkpointer (PostgreSQL 16 优先，无 PG DSN 时降级 SQLite) 与 LangGraph 会话执行"""

    def __init__(self, db_path: Optional[str] = None, postgres_dsn: Optional[str] = None):
        if db_path is not None:
            self._use_postgres = False
            self.db_path = db_path
            self.postgres_dsn = ""
        else:
            dsn = (postgres_dsn if postgres_dsn is not None else settings.CHECKPOINT_POSTGRES_DSN).strip()
            if dsn:
                self._use_postgres = True
                self.postgres_dsn = dsn
                self.db_path = ""
            else:
                self._use_postgres = False
                self.postgres_dsn = ""
                self.db_path = settings.CHECKPOINT_DB_PATH
        self._pg_pool: Any = None
        self._conn: Optional[aiosqlite.Connection] = None
        self._checkpointer: Any = None
        self.graph = None

    @property
    def checkpoint_backend(self) -> str:
        return "postgresql-async" if self._use_postgres else "sqlite-fallback"

    async def startup(self):
        if self._use_postgres:
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            self._pg_pool = AsyncConnectionPool(
                conninfo=self.postgres_dsn,
                min_size=1,
                max_size=5,
                open=False,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            )
            await self._pg_pool.open()
            self._checkpointer = AsyncPostgresSaver(self._pg_pool)
            await self._checkpointer.setup()
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            self._checkpointer = AsyncSqliteSaver(self._conn)
            await self._checkpointer.setup()
        self.graph = build_ticket_react_graph().compile(checkpointer=self._checkpointer)

    async def shutdown(self):
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._checkpointer = None
        self.graph = None

    @staticmethod
    def _validate_user(user_id: int, username: str, user_role: str = "USER") -> Dict[str, Any]:
        if not isinstance(user_id, int) or user_id <= 0:
            raise PermissionError("缺少有效的用户编号 user_id")
        clean_name = (username or "").strip()
        if not clean_name:
            raise PermissionError("缺少有效的用户名 username")
        return {
            "user_id": user_id,
            "username": clean_name,
            "user_role": (user_role or "USER").strip() or "USER",
        }

    @staticmethod
    def _assert_thread_owner(prev_snap: Any, user_id: int):
        if prev_snap and getattr(prev_snap, "values", None):
            owner_id = prev_snap.values.get("user_id")
            if owner_id is not None and int(owner_id) != int(user_id):
                raise PermissionError("无权操作其他用户的对话线程")

    async def chat(
        self,
        thread_id: str,
        user_message: str,
        user_id: int,
        username: str,
        user_role: str = "USER",
    ) -> Dict[str, Any]:
        user_info = self._validate_user(user_id, username, user_role)
        if self.graph is None:
            await self.startup()

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_info["user_id"])

        # P0-6：若当前会话正处于 interrupt() 挂起等待卡片确认态，严禁直接覆盖状态或文字自动确认付款！
        if prev_snap and prev_snap.next:
            pending_card = _extract_interrupt_card(prev_snap)
            if _is_explicit_cancel_intent(user_message):
                prev_len = len(prev_snap.values.get("messages", [])) if prev_snap.values else 0
                cancel_state = await self.graph.ainvoke(
                    Command(resume={"confirmed": False}),
                    config=config,
                )
                if _is_pure_cancel_phrase(user_message):
                    return await self._format_response(thread_id, config, cancel_state, prev_len)
                # 若用户说了“算了不买了，帮我查一下深圳的电影”，先清理挂起态再继续处理新问题
                prev_snap = await self.graph.aget_state(config)
            else:
                card_title = (pending_card or {}).get("title") or "待确认操作"
                return {
                    "threadId": thread_id,
                    "status": "INTERRUPTED",
                    "reply": (
                        f"您当前有一笔待处理的【{card_title}】卡片尚未确认。"
                        "为保障账户与座位安全，请直接点击确认卡片上的【确认】或【暂不执行】按钮；"
                        "若想放弃当前操作再咨询其他问题，请回复“放弃当前操作”。"
                    ),
                    "toolSteps": [],
                    "confirmationCard": pending_card,
                }

        prev_len = len(prev_snap.values.get("messages", [])) if (prev_snap and prev_snap.values) else 0
        input_state: TicketAgentState = {
            "messages": [HumanMessage(content=user_message)],
            "user_id": user_info["user_id"],
            "username": user_info["username"],
            "user_role": user_info["user_role"],
        }
        result_state = await self.graph.ainvoke(input_state, config=config)
        return await self._format_response(thread_id, config, result_state, prev_len)

    async def confirm(
        self,
        thread_id: str,
        confirmed: bool,
        user_id: int,
        username: str,
        user_role: str = "USER",
    ) -> Dict[str, Any]:
        user_info = self._validate_user(user_id, username, user_role)
        if self.graph is None:
            await self.startup()

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_info["user_id"])

        if not prev_snap or not prev_snap.next:
            raise ValueError(f"会话 {thread_id} 当前没有处于等待人工确认的挂起操作")

        prev_len = len(prev_snap.values.get("messages", []))
        result_state = await self.graph.ainvoke(
            Command(resume={"confirmed": confirmed}),
            config=config,
        )
        return await self._format_response(thread_id, config, result_state, prev_len)

    async def _format_response(
        self,
        thread_id: str,
        config: Dict[str, Any],
        result_state: Dict[str, Any],
        prev_len: int,
    ) -> Dict[str, Any]:
        snapshot = await self.graph.aget_state(config)
        is_interrupted = bool(snapshot.next)

        all_msgs: List[BaseMessage] = result_state.get("messages") or []
        new_msgs = all_msgs[prev_len:]

        tool_steps: List[Dict[str, Any]] = []
        for m in new_msgs:
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    tool_steps.append({
                        "tool": tc["name"],
                        "args": tc.get("args"),
                    })

        confirmation_card = _extract_interrupt_card(snapshot)

        reply_text = ""
        for m in reversed(new_msgs):
            if isinstance(m, AIMessage) and m.content:
                reply_text = str(m.content)
                break

        if is_interrupted and not reply_text and confirmation_card:
            args = confirmation_card.get("args") or {}
            mock_hint = "（DEV_MOCK 离线核价）" if confirmation_card.get("isMock") else ""
            if confirmation_card.get("actionType") == "CREATE_ORDER":
                seats_str = "、".join(args.get("seat_labels") or [])
                reply_text = (
                    f"已为您向服务端实时核验并选定 **《{args.get('show_title')}》** 的座位：**{seats_str}** "
                    f"（核验总价 **¥{args.get('total_amount')}**{mock_hint}）。请在下方的确认卡片中点击【确认锁座下单】完成锁定："
                )
            else:
                reply_text = (
                    f"已核实待支付订单 **{args.get('order_no')}**（《{args.get('show_title')}》，"
                    f"¥{args.get('total_amount')}），请点击下方卡片确认取消并释放座位："
                )

        return {
            "threadId": thread_id,
            "status": "INTERRUPTED" if is_interrupted else "COMPLETED",
            "reply": reply_text,
            "toolSteps": tool_steps,
            "confirmationCard": confirmation_card,
        }


ticket_agent_manager = TicketAgentManager()

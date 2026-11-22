import asyncio
from datetime import datetime
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import aiosqlite
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.config import settings
from app.graph.guard_and_router import input_guard_node, intent_router_node
from app.graph.state import MainGraphState, SubTaskResult, get_chat_llm
from app.graph.thought_stream import ThoughtSession, astream_llm_text, emit_thought, emit_tool_card
from app.graph.subgraphs import (
    build_knowledge_subgraph,
    build_order_subgraph,
    build_purchase_subgraph,
)
from app.observability.tracer import AgentTracer, agent_tracer
from app.storage.conversation_store import ConversationStore, conversation_store, to_iso_utc, utc_now
from app.tools.ticket_tools import current_user_ctx, execute_confirmed_write_action

# 预编译三大业务子图（主图将它们作为黑盒专员节点按序调度，子图间严禁互相网状调用）
_KNOWLEDGE_SUBGRAPH = build_knowledge_subgraph()
_PURCHASE_SUBGRAPH = build_purchase_subgraph()
_ORDER_SUBGRAPH = build_order_subgraph()

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


async def run_knowledge_subgraph(state: MainGraphState, config: RunnableConfig) -> Dict[str, Any]:
    """调度子 Agent ①：知识问答子图 (Agentic RAG)"""
    sub_out = await _KNOWLEDGE_SUBGRAPH.ainvoke(
        {
            "query": state.get("current_query") or "",
            "slots": dict(state.get("slots") or {}),
            "history_summary": state.get("history_summary") or "",
            "retry_count": 0,
            "tool_steps": [],
        },
        config=config,
    )
    new_citations = list(dict.fromkeys((state.get("citations") or []) + (sub_out.get("citations") or [])))
    sub_res: SubTaskResult = {
        "intent": "policy",
        "agent": "knowledge_agent",
        "summary": sub_out.get("answer") or "",
        "citations": sub_out.get("citations") or [],
        "refused": bool(sub_out.get("refused")),
    }
    return {
        "intent_cursor": int(state.get("intent_cursor") or 0) + 1,
        "sub_results": list(state.get("sub_results") or []) + [sub_res],
        "citations": new_citations,
        "tool_steps": list(state.get("tool_steps") or []) + (sub_out.get("tool_steps") or []),
        "executed_subgraphs": list(state.get("executed_subgraphs") or []) + ["knowledge_agent"],
    }


async def run_purchase_subgraph(state: MainGraphState, config: RunnableConfig) -> Dict[str, Any]:
    """调度子 Agent ②：选座购票子图 (只读选座 + 确定性服务端核价)"""
    sub_out = await _PURCHASE_SUBGRAPH.ainvoke(
        {
            "sub_messages": [],
            "query": state.get("current_query") or "",
            "history_summary": state.get("history_summary") or "",
            "mode": state.get("mode") or "agent",
            "slots": dict(state.get("slots") or {}),
            "user_id": state["user_id"],
            "username": state["username"],
            "user_role": state.get("user_role", "USER"),
            "step_count": 0,
            "answer": "",
            "tool_steps": [],
            "pending_action": None,
        },
        config=config,
    )
    pending = sub_out.get("pending_action")
    sub_list = list(state.get("sub_results") or [])
    if not pending and sub_out.get("answer"):
        sub_list.append({
            "intent": "purchase",
            "agent": "purchase_agent",
            "summary": sub_out["answer"],
            "citations": [],
            "refused": False,
        })

    return {
        "intent_cursor": int(state.get("intent_cursor") or 0) + 1,
        "slots": sub_out.get("slots") or state.get("slots") or {},
        "sub_results": sub_list,
        "tool_steps": list(state.get("tool_steps") or []) + (sub_out.get("tool_steps") or []),
        "executed_subgraphs": list(state.get("executed_subgraphs") or []) + ["purchase_agent"],
        "pending_action": pending,
    }


async def run_order_subgraph(state: MainGraphState, config: RunnableConfig) -> Dict[str, Any]:
    """调度子 Agent ③：订单售后子图 (查单 + 确定性阶梯退票算费 + 取消预检)"""
    sub_out = await _ORDER_SUBGRAPH.ainvoke(
        {
            "sub_messages": [],
            "query": state.get("current_query") or "",
            "history_summary": state.get("history_summary") or "",
            "mode": state.get("mode") or "agent",
            "slots": dict(state.get("slots") or {}),
            "user_id": state["user_id"],
            "username": state["username"],
            "user_role": state.get("user_role", "USER"),
            "step_count": 0,
            "answer": "",
            "citations": [],
            "tool_steps": [],
            "pending_action": None,
        },
        config=config,
    )
    pending = sub_out.get("pending_action")
    new_citations = list(dict.fromkeys((state.get("citations") or []) + (sub_out.get("citations") or [])))
    sub_list = list(state.get("sub_results") or [])
    if not pending and sub_out.get("answer"):
        sub_list.append({
            "intent": "order",
            "agent": "order_agent",
            "summary": sub_out["answer"],
            "citations": sub_out.get("citations") or [],
            "refused": False,
        })

    return {
        "intent_cursor": int(state.get("intent_cursor") or 0) + 1,
        "slots": sub_out.get("slots") or state.get("slots") or {},
        "sub_results": sub_list,
        "citations": new_citations,
        "tool_steps": list(state.get("tool_steps") or []) + (sub_out.get("tool_steps") or []),
        "executed_subgraphs": list(state.get("executed_subgraphs") or []) + ["order_agent"],
        "pending_action": pending,
    }


async def chitchat_node(state: MainGraphState) -> Dict[str, Any]:
    """闲聊与能力引导节点（不绑定任何业务工具）"""
    q = state.get("current_query") or ""
    sys_msg = (
        "你是 PulseTicket（智脉票务平台）的官方 AI 购票与客服助手。\n"
        "请礼貌、简洁地回应用户的问候或闲聊，并简要告知你可以协助：\n"
        "1. 查询在售演出与智能推荐中间连座；\n"
        "2. 解答退改手续费、实名入场、场馆地铁与寄存规则；\n"
        "3. 查询个人订单、核算退票费用或取消未支付锁座单。"
    )
    try:
        raw_reply = await astream_llm_text(
            get_chat_llm(temperature=0.3),
            [
                SystemMessage(content=sys_msg),
                HumanMessage(content=q),
            ],
        )
        reply = raw_reply.strip()
    except Exception:
        reply = (
            "您好！我是 PulseTicket 智能购票助手。我可以帮您查演出、推荐最佳中间连座、"
            "解答退改与场馆交通规则，以及查询或取消未支付订单。请问需要帮您看哪场演出？"
        )

    sub_res: SubTaskResult = {
        "intent": "chitchat",
        "agent": "chitchat_node",
        "summary": reply,
        "citations": [],
        "refused": False,
    }
    return {
        "intent_cursor": int(state.get("intent_cursor") or 0) + 1,
        "sub_results": list(state.get("sub_results") or []) + [sub_res],
        "executed_subgraphs": list(state.get("executed_subgraphs") or []) + ["chitchat_node"],
    }


def _format_write_receipt(action_name: str, exec_res: Dict[str, Any], exec_args: Dict[str, Any]) -> str:
    """用确定性模板格式化写操作执行回执（零幻觉、零额外慢请求）"""
    if not exec_res.get("success"):
        return (
            f"⚠️ **操作未完成**\n\n"
            f"- 原因：{exec_res.get('message', '后端服务处理失败')}\n"
            f"- 幂等保护键：`{exec_res.get('idempotencyKey') or 'N/A'}`"
        )

    mock_banner = "\n\n> 🧪 *注：当前运行在 `DEV_MOCK` 离线联调模式，真实锁座请启动 Java 微服务集群。*" if exec_res.get("isMock") else ""

    if action_name == "propose_create_order":
        seats = "、".join(exec_res.get("seatLabels") or exec_args.get("seat_labels") or [])
        return (
            f"✅ **锁座下单成功！**\n\n"
            f"- **订单号**：`{exec_res.get('orderNo')}`\n"
            f"- **演出场次**：《{exec_args.get('show_title')}》 · {exec_args.get('session_title', '')}\n"
            f"- **锁定席位**：{seats}\n"
            f"- **应付总额**：**¥{exec_res.get('totalAmount')}**\n"
            f"- **订单状态**：`{exec_res.get('status', 'UNPAID')}`（请在 **15 分钟** 内于「我的订单」完成支付，超时将由死信队列自动释放座位 `[POL-LOCK-004]`）"
            + mock_banner
        )

    return (
        f"✅ **未支付订单已取消，锁定座位已释放**\n\n"
        f"- **订单号**：`{exec_res.get('orderNo')}`\n"
        f"- **当前状态**：`{exec_res.get('status', 'CLOSED')}`（原因：`{exec_res.get('closeReason') or 'USER_CANCEL'}`）"
        + mock_banner
    )


async def confirm_action_node(state: MainGraphState) -> Dict[str, Any]:
    """
    【主图人工确认断点节点 confirm_action】：
    自身在 interrupt() 之前无任何外部 HTTP 副作用，保证 LangGraph 断点重放 100% 确定性；
    用户点击确认后携带 idempotencyKey 执行真实 Java 订单写操作。
    """
    pending = state.get("pending_action")
    if not pending:
        return {"pending_action": None}

    t_name = pending["toolName"]
    idempotency_key = pending["idempotencyKey"]
    exec_args = pending["execArgs"]
    card_payload = pending["cardPayload"]

    human_decision = interrupt(card_payload)
    confirmed = bool((human_decision or {}).get("confirmed", False))
    steps = list(state.get("tool_steps") or [])
    citations = list(state.get("citations") or [])

    if not confirmed:
        emit_thought(
            key="confirm-exec",
            phase="confirm",
            agent_label="确认门控",
            title="用户已取消确认卡片 · 安全释放挂起态",
            detail="未向 Java 订单服务发起任何锁座或变更请求",
            badges=["已放弃"],
            status="done",
        )
        steps.append({
            "agent": "confirm_gate",
            "tool": "human_confirm_rejected",
            "args": {"toolName": t_name},
        })
        reject_summary = "已按您的选择取消本次操作，未执行任何锁座或订单变更。如需换其他场次或座位，请随时告诉我。"
        return {
            "pending_action": None,
            "tool_steps": steps,
            "sub_results": list(state.get("sub_results") or []) + [{
                "intent": "confirm",
                "agent": "confirm_gate",
                "summary": reject_summary,
                "citations": [],
                "refused": False,
            }],
        }

    exec_card_id = f"tool-exec-{t_name}"
    exec_card_title = (
        "执行分布式锁座下单 (Redisson + Seata AT)"
        if t_name == "propose_create_order"
        else "执行未支付订单取消与座位释放"
    )
    emit_thought(
        key="confirm-exec",
        phase="confirm",
        agent_label="确认门控",
        title="人工确认通过 · 正在携带幂等键请求 Java order-service…",
        detail=f"Idempotency-Key: {idempotency_key}",
        badges=["Seata AT", "Redisson"],
        status="running",
    )
    emit_tool_card(
        card_id=exec_card_id,
        tool_name=t_name,
        title=exec_card_title,
        status="running",
        summary="正在携带幂等键调用 Java order-service 事务下单/关单…",
        input_data={"idempotencyKey": idempotency_key, **exec_args},
    )
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

    emit_thought(
        key="confirm-exec",
        phase="confirm",
        agent_label="确认门控",
        title=(
            f"写操作提交成功 · 单号 {exec_res.get('orderNo')}"
            if exec_res.get("success")
            else "写操作未完成 · 已返回真实错误回执"
        ),
        detail=exec_res.get("message", ""),
        badges=[str(exec_res.get("orderNo") or "FAILED")],
        status="done" if exec_res.get("success") else "warn",
    )
    emit_tool_card(
        card_id=exec_card_id,
        tool_name=t_name,
        title=exec_card_title,
        status="done" if exec_res.get("success") else "error",
        summary=(
            f"已创建锁座订单 {exec_res.get('orderNo')}（总额 ¥{exec_res.get('totalAmount')}）"
            if t_name == "propose_create_order" and exec_res.get("success")
            else (
                f"已取消订单 {exec_res.get('orderNo')} 并释放座位"
                if exec_res.get("success")
                else str(exec_res.get("message") or "后端服务处理失败")
            )
        ),
        input_data={"idempotencyKey": idempotency_key, **exec_args},
        output_data=exec_res,
    )

    steps.append({
        "agent": "confirm_gate",
        "tool": "execute_confirmed_write",
        "args": {
            "toolName": t_name,
            "success": bool(exec_res.get("success")),
            "orderNo": exec_res.get("orderNo"),
            "idempotencyKey": idempotency_key,
        },
    })
    if t_name == "propose_create_order" and exec_res.get("success") and "POL-LOCK-004" not in citations:
        citations.append("POL-LOCK-004")

    receipt_text = _format_write_receipt(t_name, exec_res, exec_args)
    return {
        "pending_action": None,
        "tool_steps": steps,
        "citations": citations,
        "sub_results": list(state.get("sub_results") or []) + [{
            "intent": "confirm",
            "agent": "confirm_gate",
            "summary": receipt_text,
            "citations": ["POL-LOCK-004"] if t_name == "propose_create_order" and exec_res.get("success") else [],
            "refused": False,
        }],
    }


async def response_composer_node(state: MainGraphState) -> Dict[str, Any]:
    """
    【主图终点：response_composer 多子图汇总与输出校验】：
    - 单意图直接输出对应专员结论（零冗余延迟）；
    - 多意图按序分段整合，确保每一项规则引用 `[POL-xxx]` 与选座方案完整呈现。
    """
    results = state.get("sub_results") or []
    if not results:
        reply = state.get("final_reply") or "请问有什么可以帮您？"
    elif len(results) == 1:
        reply = results[0].get("summary") or ""
    else:
        header_map = {
            "policy": "### 📋 票务规则与场馆须知",
            "order": "### 🧾 订单与售后核查",
            "purchase": "### 🎫 演出与智能连座方案",
            "confirm": "### 🛡️ 操作执行结果",
            "chitchat": "### 💬 助手回复",
        }
        sections = []
        for item in results:
            summary = (item.get("summary") or "").strip()
            if not summary:
                continue
            hdr = header_map.get(item.get("intent") or "", "### 📌 处理结果")
            sections.append(f"{hdr}\n{summary}")
        reply = "\n\n---\n\n".join(sections)

    return {
        "final_reply": reply,
        "messages": [AIMessage(content=reply)],
    }


def _route_after_guard(state: MainGraphState) -> str:
    if state.get("guard_blocked"):
        return END
    return "intent_router"


def _route_next_intent(state: MainGraphState) -> str:
    if state.get("pending_action"):
        return "confirm_action"
    intents = state.get("intents") or []
    idx = int(state.get("intent_cursor") or 0)
    if idx >= len(intents):
        return "response_composer"

    nxt = intents[idx]
    if nxt == "policy":
        return "knowledge_agent"
    if nxt == "purchase":
        return "purchase_agent"
    if nxt == "order":
        return "order_agent"
    return "chitchat_node"


def build_main_agent_graph() -> StateGraph:
    """构建主 Graph（安全护栏 + 意图路由 + 3 个业务子图调度 + 确认门控 + 汇总输出）"""
    builder = StateGraph(MainGraphState)

    builder.add_node("input_guard", input_guard_node)
    builder.add_node("intent_router", intent_router_node)
    builder.add_node("knowledge_agent", run_knowledge_subgraph)
    builder.add_node("purchase_agent", run_purchase_subgraph)
    builder.add_node("order_agent", run_order_subgraph)
    builder.add_node("chitchat_node", chitchat_node)
    builder.add_node("confirm_action", confirm_action_node)
    builder.add_node("response_composer", response_composer_node)

    builder.add_edge(START, "input_guard")
    builder.add_conditional_edges(
        "input_guard",
        _route_after_guard,
        {
            "intent_router": "intent_router",
            END: END,
        },
    )

    dispatch_targets = {
        "knowledge_agent": "knowledge_agent",
        "purchase_agent": "purchase_agent",
        "order_agent": "order_agent",
        "chitchat_node": "chitchat_node",
        "confirm_action": "confirm_action",
        "response_composer": "response_composer",
    }
    builder.add_conditional_edges("intent_router", _route_next_intent, dispatch_targets)
    builder.add_conditional_edges("knowledge_agent", _route_next_intent, dispatch_targets)
    builder.add_conditional_edges("purchase_agent", _route_next_intent, dispatch_targets)
    builder.add_conditional_edges("order_agent", _route_next_intent, dispatch_targets)
    builder.add_conditional_edges("chitchat_node", _route_next_intent, dispatch_targets)

    builder.add_edge("confirm_action", "response_composer")
    builder.add_edge("response_composer", END)

    return builder


_NODE_LABELS = {
    "input_guard": "安全护栏校验通过",
    "intent_router": "主图意图路由完成",
    "knowledge_agent": "知识问答子图 · Hybrid RAG 完成",
    "purchase_agent": "选座购票子图 · 场次与连座核验完成",
    "order_agent": "订单售后子图 · 订单与费率核查完成",
    "chitchat_node": "对话引导生成完成",
    "confirm_action": "写操作确认门控完成",
    "response_composer": "多意图响应汇总完成",
}


class MultiAgentTicketManager:
    """
    主 Graph + 3 子图会话协调器：
    - 支持同步 JSON (`chat` / `confirm`) 与实时 SSE 流式 (`stream_chat` / `stream_confirm`)
    - 自动写入 AgentTracer 可观测审计日志
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        postgres_dsn: Optional[str] = None,
        tracer: Optional[AgentTracer] = None,
        conv_store: Optional[ConversationStore] = None,
        enable_cleanup_loop: Optional[bool] = None,
    ):
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
        self.tracer = tracer or agent_tracer
        if conv_store is not None:
            self.conv_store = conv_store
        elif db_path is not None:
            self.conv_store = ConversationStore(
                db_path=str(Path(db_path).with_name("test_conversations.sqlite"))
            )
        else:
            self.conv_store = conversation_store

        if enable_cleanup_loop is not None:
            self._enable_cleanup_loop = enable_cleanup_loop
        else:
            self._enable_cleanup_loop = (db_path is None)

        self._pg_pool: Any = None
        self._conn: Optional[aiosqlite.Connection] = None
        self._checkpointer: Any = None
        self._cleanup_task: Optional[asyncio.Task] = None
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
        await self.tracer.ensure_schema()
        await self.conv_store.ensure_schema()
        self.graph = build_main_agent_graph().compile(checkpointer=self._checkpointer)
        if self._enable_cleanup_loop and self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self):
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._checkpointer = None
        self.graph = None

    async def _delete_checkpoint_thread(self, thread_id: str) -> None:
        if self._checkpointer is not None and hasattr(self._checkpointer, "adelete_thread"):
            try:
                await self._checkpointer.adelete_thread(thread_id)
            except Exception as exc:
                print(f"[PulseTicket Agent] 清理 checkpoint 失败 thread={thread_id}: {exc}")

    async def cleanup_expired_threads(self, now: Optional[datetime] = None) -> int:
        expired_ids = await self.conv_store.purge_expired_threads(now=now)
        for tid in expired_ids:
            await self._delete_checkpoint_thread(tid)
        return len(expired_ids)

    async def _cleanup_loop(self) -> None:
        interval = max(60, int(settings.CLEANUP_INTERVAL_SEC))
        while True:
            try:
                removed = await self.cleanup_expired_threads()
                if removed > 0:
                    print(f"[PulseTicket Agent] 自动清理 {removed} 个超过 {settings.CONVERSATION_TTL_DAYS} 天未活跃会话")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[PulseTicket Agent] 定时清理任务异常: {exc}")
            await asyncio.sleep(interval)

    async def _expire_stale_pending_if_needed(
        self,
        thread_id: str,
        config: Dict[str, Any],
        now: Optional[datetime] = None,
    ) -> bool:
        conv = await self.conv_store.get_conversation(thread_id)
        if not conv or not self.conv_store.is_pending_expired(conv, now=now):
            return False
        if self.graph is not None:
            snap = await self.graph.aget_state(config)
            if snap and snap.next:
                try:
                    await self.graph.ainvoke(Command(resume={"confirmed": False}), config=config)
                except Exception:
                    pass
        await self.conv_store.mark_pending_expired(thread_id)
        return True

    async def list_threads(
        self,
        user_id: int,
        limit: int = 30,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        if self.graph is None:
            await self.startup()
        return await self.conv_store.list_threads(user_id=user_id, limit=limit, now=now)

    async def get_thread_detail(
        self,
        thread_id: str,
        user_id: int,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        if self.graph is None:
            await self.startup()
        conv = await self.conv_store.assert_owner(thread_id, user_id)
        if conv is None:
            return None
        now_dt = now or utc_now()
        if conv["expires_at"] <= to_iso_utc(now_dt):
            return None

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_id)

        expired = await self._expire_stale_pending_if_needed(thread_id, config, now=now_dt)
        if expired:
            conv = await self.conv_store.get_conversation(thread_id)
        messages = await self.conv_store.get_thread_messages(thread_id)
        pending_card = None
        if conv and conv.get("pending_card_json"):
            pending_card = json.loads(conv["pending_card_json"])

        return {
            "threadId": conv["thread_id"],
            "title": conv["title"],
            "status": "INTERRUPTED" if pending_card else "ACTIVE",
            "pendingCard": pending_card,
            "pendingCardExpired": expired,
            "expiresAt": conv["expires_at"],
            "updatedAt": conv["updated_at"],
            "messages": messages,
        }

    async def delete_thread(self, thread_id: str, user_id: int) -> bool:
        if self.graph is None:
            await self.startup()
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_id)
        deleted = await self.conv_store.delete_thread(thread_id, user_id)
        await self._delete_checkpoint_thread(thread_id)
        return deleted

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

    def _build_interrupted_prompt(
        self,
        confirmation_card: Dict[str, Any],
        prior_sub_results: List[SubTaskResult],
    ) -> str:
        prefix_parts = [
            (r.get("summary") or "").strip()
            for r in (prior_sub_results or [])
            if (r.get("summary") or "").strip()
        ]
        args = confirmation_card.get("args") or {}
        mock_hint = "（DEV_MOCK 离线核价）" if confirmation_card.get("isMock") else ""
        if confirmation_card.get("actionType") == "CREATE_ORDER":
            seats_str = "、".join(args.get("seat_labels") or [])
            card_prompt = (
                f"已向服务端实时核验余座并锁定报价：**《{args.get('show_title')}》**（{args.get('session_title', '')}）\n"
                f"- 席位：**{seats_str}**\n"
                f"- 服务端核定总价：**¥{args.get('total_amount')}**{mock_hint}\n\n"
                "请在下方确认卡片点击【确认锁座下单】完成锁定："
            )
        else:
            card_prompt = (
                f"已核实待支付订单 **{args.get('order_no')}**（《{args.get('show_title')}》，"
                f"¥{args.get('total_amount')}），请点击下方确认卡片取消订单并释放座位："
            )

        if prefix_parts:
            return "\n\n---\n\n".join(prefix_parts + [card_prompt])
        return card_prompt

    async def _finalize_payload(
        self,
        *,
        trace_id: str,
        thread_id: str,
        user_info: Dict[str, Any],
        endpoint: str,
        user_message: str,
        config: Dict[str, Any],
        t0: float,
        mode: Optional[str] = None,
        thinking_steps: Optional[List[Dict[str, Any]]] = None,
        tool_cards: Optional[List[Dict[str, Any]]] = None,
        widgets: Optional[List[Dict[str, Any]]] = None,
        resolve_pending_decision: Optional[str] = None,
        record_user_msg: bool = True,
    ) -> Dict[str, Any]:
        snapshot = await self.graph.aget_state(config)
        vals = snapshot.values if (snapshot and snapshot.values) else {}
        is_interrupted = bool(snapshot and snapshot.next)
        confirmation_card = _extract_interrupt_card(snapshot)

        raw_mode = mode or vals.get("mode") or "agent"
        eff_mode = "chat" if str(raw_mode).strip().lower() == "chat" else "agent"

        intents = vals.get("intents") or []
        subgraphs = vals.get("executed_subgraphs") or []
        tool_steps = vals.get("tool_steps") or []
        citations = vals.get("citations") or []
        guard_blocked = bool(vals.get("guard_blocked"))

        if is_interrupted and confirmation_card:
            reply_text = self._build_interrupted_prompt(
                confirmation_card,
                vals.get("sub_results") or [],
            )
        else:
            reply_text = vals.get("final_reply") or ""

        status = "BLOCKED" if guard_blocked else ("INTERRUPTED" if is_interrupted else "COMPLETED")
        write_executed = any(s.get("tool") == "execute_confirmed_write" and (s.get("args") or {}).get("success") for s in tool_steps)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        await self.tracer.record_trace(
            trace_id=trace_id,
            thread_id=thread_id,
            user_id=user_info["user_id"],
            username=user_info["username"],
            endpoint=endpoint,
            user_message=user_message,
            intents=intents,
            subgraphs=subgraphs,
            tool_steps=tool_steps,
            citations=citations,
            guard_blocked=guard_blocked,
            interrupted=is_interrupted,
            write_executed=write_executed,
            latency_ms=latency_ms,
            status=status,
            reply=reply_text,
        )

        evicted = await self.conv_store.record_turn(
            thread_id=thread_id,
            user_id=user_info["user_id"],
            username=user_info["username"],
            user_message=user_message,
            assistant_reply=reply_text,
            status=status,
            mode=eff_mode,
            intents=intents,
            subgraphs=subgraphs,
            citations=citations,
            thinking_steps=list(thinking_steps or []),
            tool_cards=list(tool_cards or []),
            widgets=list(widgets or []),
            confirmation_card=confirmation_card,
            latency_ms=latency_ms,
            record_user_msg=record_user_msg,
            resolve_pending_decision=resolve_pending_decision,
        )
        for evicted_tid in evicted:
            await self._delete_checkpoint_thread(evicted_tid)

        return {
            "traceId": trace_id,
            "threadId": thread_id,
            "status": status,
            "mode": eff_mode,
            "reply": reply_text,
            "intents": intents,
            "subgraphs": subgraphs,
            "citations": citations,
            "toolSteps": tool_steps,
            "thinkingSteps": list(thinking_steps or []),
            "toolCards": list(tool_cards or []),
            "widgets": list(widgets or []),
            "confirmationCard": confirmation_card,
            "latencyMs": latency_ms,
        }

    async def chat(
        self,
        thread_id: str,
        user_message: str,
        user_id: int,
        username: str,
        user_role: str = "USER",
        mode: str = "agent",
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        trace_id = f"ag-{uuid.uuid4().hex[:12]}"
        norm_mode = "chat" if str(mode or "").strip().lower() == "chat" else "agent"
        user_info = self._validate_user(user_id, username, user_role)
        if self.graph is None:
            await self.startup()

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_info["user_id"])
        await self.conv_store.assert_owner(thread_id, user_info["user_id"])

        if await self._expire_stale_pending_if_needed(thread_id, config):
            prev_snap = await self.graph.aget_state(config)

        pending_resolved: Optional[str] = None
        with ThoughtSession(use_queue=False) as ts:
            # P0-6：挂起等待卡片确认时收到新消息
            if prev_snap and prev_snap.next:
                pending_card = _extract_interrupt_card(prev_snap)
                if _is_explicit_cancel_intent(user_message):
                    await self.graph.ainvoke(Command(resume={"confirmed": False}), config=config)
                    pending_resolved = "REJECTED"
                    if _is_pure_cancel_phrase(user_message):
                        return await self._finalize_payload(
                            trace_id=trace_id,
                            thread_id=thread_id,
                            user_info=user_info,
                            endpoint="chat_cancel_pending",
                            user_message=user_message,
                            config=config,
                            t0=t0,
                            mode=norm_mode,
                            thinking_steps=ts.steps,
                            tool_cards=ts.tool_cards,
                            widgets=ts.widgets,
                            resolve_pending_decision="REJECTED",
                        )
                else:
                    card_title = (pending_card or {}).get("title") or "待确认操作"
                    emit_thought(
                        key="pending-guard",
                        phase="confirm",
                        agent_label="确认门控",
                        title=f"检测到未决【{card_title}】卡片",
                        detail="阻止文本绕过卡片自动扣款，保持断点等待明确点击确认或放弃",
                        badges=["Interrupt 保护"],
                        status="warn",
                    )
                    hold_reply = (
                        f"您当前有一笔待处理的【{card_title}】卡片尚未确认。"
                        "为保障座位与资金安全，请直接点击确认卡片上的【确认】或【暂不执行】按钮；"
                        "若想放弃当前操作再咨询其他问题，请回复“放弃当前操作”。"
                    )
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    evicted = await self.conv_store.record_turn(
                        thread_id=thread_id,
                        user_id=user_info["user_id"],
                        username=user_info["username"],
                        user_message=user_message,
                        assistant_reply=hold_reply,
                        status="INTERRUPTED",
                        mode=norm_mode,
                        intents=[],
                        subgraphs=["pending_resolver"],
                        citations=[],
                        thinking_steps=list(ts.steps),
                        tool_cards=[],
                        widgets=[],
                        confirmation_card=pending_card,
                        latency_ms=latency_ms,
                        preserve_existing_pending=True,
                    )
                    for evicted_tid in evicted:
                        await self._delete_checkpoint_thread(evicted_tid)
                    return {
                        "traceId": trace_id,
                        "threadId": thread_id,
                        "status": "INTERRUPTED",
                        "mode": norm_mode,
                        "reply": hold_reply,
                        "intents": [],
                        "subgraphs": ["pending_resolver"],
                        "citations": [],
                        "toolSteps": [],
                        "thinkingSteps": list(ts.steps),
                        "toolCards": [],
                        "widgets": [],
                        "confirmationCard": pending_card,
                        "latencyMs": latency_ms,
                    }

            input_state: MainGraphState = {
                "messages": [HumanMessage(content=user_message)],
                "current_query": user_message,
                "mode": norm_mode,
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "user_role": user_info["user_role"],
            }
            await self.graph.ainvoke(input_state, config=config)
            return await self._finalize_payload(
                trace_id=trace_id,
                thread_id=thread_id,
                user_info=user_info,
                endpoint="chat",
                user_message=user_message,
                config=config,
                t0=t0,
                mode=norm_mode,
                thinking_steps=ts.steps,
                tool_cards=ts.tool_cards,
                widgets=ts.widgets,
                resolve_pending_decision=pending_resolved,
            )

    async def confirm(
        self,
        thread_id: str,
        confirmed: bool,
        user_id: int,
        username: str,
        user_role: str = "USER",
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        trace_id = f"ag-{uuid.uuid4().hex[:12]}"
        user_info = self._validate_user(user_id, username, user_role)
        if self.graph is None:
            await self.startup()

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_info["user_id"])
        await self.conv_store.assert_owner(thread_id, user_info["user_id"])

        if await self._expire_stale_pending_if_needed(thread_id, config):
            raise ValueError("该确认卡片报价已超过 15 分钟有效期，已自动失效，请重新选座核价。")

        if not prev_snap or not prev_snap.next:
            raise ValueError(f"会话 {thread_id} 当前没有处于等待人工确认的挂起操作")

        with ThoughtSession(use_queue=False) as ts:
            await self.graph.ainvoke(Command(resume={"confirmed": confirmed}), config=config)
            return await self._finalize_payload(
                trace_id=trace_id,
                thread_id=thread_id,
                user_info=user_info,
                endpoint="confirm",
                user_message=f"[CARD_CONFIRM={confirmed}]",
                config=config,
                t0=t0,
                thinking_steps=ts.steps,
                resolve_pending_decision="CONFIRMED" if confirmed else "REJECTED",
                record_user_msg=False,
            )

    async def stream_chat(
        self,
        thread_id: str,
        user_message: str,
        user_id: int,
        username: str,
        user_role: str = "USER",
        mode: str = "agent",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """SSE 流式对话生成器：零延迟推送子图 thought 思考步骤、tool_card 工具卡片、widget 选择框、真实 Token 流与确认卡片"""
        t0 = time.perf_counter()
        trace_id = f"ag-{uuid.uuid4().hex[:12]}"
        norm_mode = "chat" if str(mode or "").strip().lower() == "chat" else "agent"
        user_info = self._validate_user(user_id, username, user_role)
        if self.graph is None:
            await self.startup()

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_info["user_id"])
        await self.conv_store.assert_owner(thread_id, user_info["user_id"])

        if await self._expire_stale_pending_if_needed(thread_id, config):
            prev_snap = await self.graph.aget_state(config)

        if prev_snap and prev_snap.next:
            res = await self.chat(thread_id, user_message, user_id, username, user_role, mode=norm_mode)
            for th in res.get("thinkingSteps") or []:
                yield {"event": "thought", "data": th}
            for tc in res.get("toolCards") or []:
                yield {"event": "tool_card", "data": tc}
            for wg in res.get("widgets") or []:
                yield {"event": "widget", "data": wg}
            yield {"event": "token", "data": {"delta": res["reply"]}}
            if res.get("confirmationCard"):
                yield {"event": "interrupt", "data": {"confirmationCard": res["confirmationCard"]}}
            yield {"event": "done", "data": res}
            return

        input_state: MainGraphState = {
            "messages": [HumanMessage(content=user_message)],
            "current_query": user_message,
            "mode": norm_mode,
            "user_id": user_info["user_id"],
            "username": user_info["username"],
            "user_role": user_info["user_role"],
        }

        with ThoughtSession(use_queue=True, stream_tokens=True) as ts:
            assert ts.queue is not None
            sentinel = {"event": "__end__", "data": {}}

            async def _drive():
                try:
                    async for chunk in self.graph.astream(input_state, config=config, stream_mode="updates"):
                        for node_name, node_val in chunk.items():
                            if node_name == "__interrupt__":
                                continue
                            label = _NODE_LABELS.get(node_name, node_name)
                            if node_name == "intent_router" and isinstance(node_val, dict):
                                ts.queue.put_nowait({
                                    "event": "route",
                                    "data": {
                                        "intents": node_val.get("intents") or [],
                                        "slots": node_val.get("slots") or {},
                                        "mode": node_val.get("mode") or norm_mode,
                                    },
                                })
                            ts.queue.put_nowait({
                                "event": "step",
                                "data": {"node": node_name, "label": label},
                            })
                finally:
                    ts.queue.put_nowait(sentinel)

            runner = asyncio.create_task(_drive())
            while True:
                item = await ts.queue.get()
                if item.get("event") == "__end__":
                    break
                yield item
            await runner

            final_payload = await self._finalize_payload(
                trace_id=trace_id,
                thread_id=thread_id,
                user_info=user_info,
                endpoint="stream_chat",
                user_message=user_message,
                config=config,
                t0=t0,
                mode=norm_mode,
                thinking_steps=ts.steps,
                tool_cards=ts.tool_cards,
                widgets=ts.widgets,
            )

            # 若本次走了确定性文案/拒答/多意图汇总/中断卡片，未在子图内推流 Token，则按块补推文本
            if not ts.has_streamed_tokens:
                text = final_payload.get("reply") or ""
                step_size = 16
                for idx in range(0, len(text), step_size):
                    yield {"event": "token", "data": {"delta": text[idx : idx + step_size]}}
                    await asyncio.sleep(0.01)

        if final_payload.get("confirmationCard"):
            yield {
                "event": "interrupt",
                "data": {"confirmationCard": final_payload["confirmationCard"]},
            }

        yield {"event": "done", "data": final_payload}

    async def stream_confirm(
        self,
        thread_id: str,
        confirmed: bool,
        user_id: int,
        username: str,
        user_role: str = "USER",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        t0 = time.perf_counter()
        trace_id = f"ag-{uuid.uuid4().hex[:12]}"
        user_info = self._validate_user(user_id, username, user_role)
        if self.graph is None:
            await self.startup()

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        prev_snap = await self.graph.aget_state(config)
        self._assert_thread_owner(prev_snap, user_info["user_id"])
        await self.conv_store.assert_owner(thread_id, user_info["user_id"])

        if await self._expire_stale_pending_if_needed(thread_id, config):
            raise ValueError("该确认卡片报价已超过 15 分钟有效期，已自动失效，请重新选座核价。")

        if not prev_snap or not prev_snap.next:
            raise ValueError(f"会话 {thread_id} 当前没有处于等待人工确认的挂起操作")

        with ThoughtSession(use_queue=True, stream_tokens=False) as ts:
            assert ts.queue is not None
            sentinel = {"event": "__end__", "data": {}}

            async def _drive_confirm():
                try:
                    await self.graph.ainvoke(Command(resume={"confirmed": confirmed}), config=config)
                finally:
                    ts.queue.put_nowait(sentinel)

            runner = asyncio.create_task(_drive_confirm())
            while True:
                item = await ts.queue.get()
                if item.get("event") == "__end__":
                    break
                yield item
            await runner

            res = await self._finalize_payload(
                trace_id=trace_id,
                thread_id=thread_id,
                user_info=user_info,
                endpoint="stream_confirm",
                user_message=f"[CARD_CONFIRM={confirmed}]",
                config=config,
                t0=t0,
                mode="agent",
                thinking_steps=ts.steps,
                tool_cards=ts.tool_cards,
                widgets=ts.widgets,
                resolve_pending_decision="CONFIRMED" if confirmed else "REJECTED",
                record_user_msg=False,
            )

        text = res.get("reply") or ""
        step_size = 16
        for idx in range(0, len(text), step_size):
            yield {"event": "token", "data": {"delta": text[idx : idx + step_size]}}
            await asyncio.sleep(0.01)
        yield {"event": "done", "data": res}


multi_agent_manager = MultiAgentTicketManager()

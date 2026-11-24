"""
SSE 实时思考流与富交互事件通道（基于 Python `contextvars` 实现异步请求级隔离）。

为什么这样设计（面试/阅读要点）：
- LangGraph 的主图节点、业务子图节点嵌套很深，如果把 SSE 队列作为参数一层层透传会严重污染函数签名；
- 借助 `contextvars.ContextVar`，在单次 HTTP 请求外层套一个 `with ThoughtSession(...)`，
  内部任意深度的节点直接调用 `emit_thought / emit_tool_card / emit_widget / emit_token`，
  既能实时推入当前请求专属的 `asyncio.Queue` 供 FastAPI SSE 秒级下发，
  又能同步收集到列表里，等对话结束一起存入数据库供历史回显，且多并发请求之间 100% 互不串台。
"""

import asyncio
import contextvars
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage

# 以下 7 个 ContextVar 仅在当前异步协程上下文内有效，负责把“当前请求”的队列、收集器和计时起点绑定起来
_thought_queue_ctx: contextvars.ContextVar[Optional[asyncio.Queue]] = contextvars.ContextVar(
    "thought_queue_ctx", default=None
)
_thought_list_ctx: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = contextvars.ContextVar(
    "thought_list_ctx", default=None
)
_tool_cards_ctx: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = contextvars.ContextVar(
    "tool_cards_ctx", default=None
)
_widgets_ctx: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = contextvars.ContextVar(
    "widgets_ctx", default=None
)
_streamed_tokens_ctx: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar(
    "streamed_tokens_ctx", default=None
)
_stream_tokens_enabled_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "stream_tokens_enabled_ctx", default=False
)
_turn_t0_ctx: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "turn_t0_ctx", default=None
)


class ThoughtSession:
    """管理单次请求的实时事件流（思考步骤、工具卡片、选择组件、真实 Token 流）与完整轨迹收集。"""

    def __init__(self, use_queue: bool = False, stream_tokens: bool = False):
        # 同步 /chat 接口传 use_queue=False（只收集不推流）；SSE /chat/stream 传 use_queue=True
        self.queue: Optional[asyncio.Queue] = asyncio.Queue() if use_queue else None
        self.steps: List[Dict[str, Any]] = []
        self.tool_cards: List[Dict[str, Any]] = []
        self.widgets: List[Dict[str, Any]] = []
        self.streamed_tokens: List[str] = []
        self.stream_tokens_enabled: bool = bool(use_queue and stream_tokens)
        self.t0: float = time.perf_counter()
        self._tokens = []

    @property
    def has_streamed_tokens(self) -> bool:
        """判断本轮是否已经在子图内实时推送过真实 LLM Token（用于决定末尾是否需要补推文本）。"""
        return bool("".join(self.streamed_tokens))

    def __enter__(self):
        # 进入上下文时把当前会话实例绑定到所有 ContextVar，并保存 token 以便退出时精确还原
        self._tokens = [
            _thought_queue_ctx.set(self.queue),
            _thought_list_ctx.set(self.steps),
            _tool_cards_ctx.set(self.tool_cards),
            _widgets_ctx.set(self.widgets),
            _streamed_tokens_ctx.set(self.streamed_tokens),
            _stream_tokens_enabled_ctx.set(self.stream_tokens_enabled),
            _turn_t0_ctx.set(self.t0),
        ]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 退出时逐个 reset，彻底防止异常或协程复用造成上下文泄漏
        for var, tok in zip(
            (
                _thought_queue_ctx,
                _thought_list_ctx,
                _tool_cards_ctx,
                _widgets_ctx,
                _streamed_tokens_ctx,
                _stream_tokens_enabled_ctx,
                _turn_t0_ctx,
            ),
            self._tokens,
        ):
            var.reset(tok)


def set_token_streaming_enabled(enabled: bool) -> None:
    """动态开关当前请求的 Token 直推能力（例如多意图查询时先关闭，避免子图裸文直接打到界面破坏分节排版）。"""
    _stream_tokens_enabled_ctx.set(bool(enabled))


def is_token_streaming_enabled() -> bool:
    """检查当前请求是否开启了实时 Token 流推送。"""
    return bool(_stream_tokens_enabled_ctx.get())


def _elapsed_ms() -> int:
    """计算距离本轮请求开始已经过去了多少毫秒，用于前端时间轴展示耗时。"""
    t0 = _turn_t0_ctx.get()
    return int((time.perf_counter() - t0) * 1000) if t0 is not None else 0


def _sanitize_tool_data(obj: Any, max_str: int = 2500, depth: int = 0) -> Any:
    """递归清洗并截断工具卡片的输入/输出载荷，限制嵌套深度与单字段长度，防止超大 JSON 撑爆 SSE 或数据库。"""
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "…(truncated)"
    if depth >= 6:
        s = str(obj)
        return s if len(s) <= max_str else s[:max_str] + "…(truncated)"
    if isinstance(obj, dict):
        return {str(k): _sanitize_tool_data(v, max_str=max_str, depth=depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_tool_data(item, max_str=max_str, depth=depth + 1) for item in obj]
    s = str(obj)
    return s if len(s) <= max_str else s[:max_str] + "…(truncated)"


def emit_thought(
    *,
    key: str,
    phase: str,
    agent_label: str,
    title: str,
    detail: str = "",
    badges: Optional[List[str]] = None,
    status: str = "done",
) -> None:
    """
    从任意主图节点或子图内部实时发射一条结构化思考步骤：
    - 若同一 key 先发了 status='running'，后续发 status='done'/'warn' 时在同一步骤上原地升级状态与详情；
    - 同时向 SSE 队列实时投递 `thought` 事件，前端时间轴毫秒级同步更新。
    """
    steps = _thought_list_ctx.get()
    q = _thought_queue_ctx.get()

    payload: Dict[str, Any] = {
        "key": key,
        "phase": phase,
        "agentLabel": agent_label,
        "title": title,
        "detail": detail,
        "badges": badges or [],
        "status": status,
        "atMs": _elapsed_ms(),
    }

    if steps is not None:
        # 相同 key 原地更新（把“执行中…”无缝替换成“已完成”），避免时间轴出现重复条目
        replaced = False
        for idx, existing in enumerate(steps):
            if existing.get("key") == key:
                steps[idx] = payload
                replaced = True
                break
        if not replaced:
            steps.append(payload)

    if q is not None:
        q.put_nowait({"event": "thought", "data": payload})


def emit_token(delta: str) -> None:
    """向 SSE 队列实时推送真实流式文本分片。"""
    if not delta or not is_token_streaming_enabled():
        return
    q = _thought_queue_ctx.get()
    buf = _streamed_tokens_ctx.get()
    if buf is not None:
        buf.append(delta)
    if q is not None:
        q.put_nowait({"event": "token", "data": {"delta": delta}})


def emit_tool_card(
    *,
    card_id: str,
    tool_name: str,
    title: str,
    status: str = "running",
    summary: str = "",
    input_data: Any = None,
    output_data: Any = None,
) -> None:
    """实时推送（或原地更新）内联工具执行卡片，供前端以 </> 查看输入输出明细。"""
    cards = _tool_cards_ctx.get()
    q = _thought_queue_ctx.get()

    payload: Dict[str, Any] = {
        "id": card_id,
        "toolName": tool_name,
        "title": title,
        "status": status,
        "summary": summary,
        "input": _sanitize_tool_data(input_data),
        "output": _sanitize_tool_data(output_data),
        "atMs": _elapsed_ms(),
    }

    if cards is not None:
        replaced = False
        for idx, existing in enumerate(cards):
            if existing.get("id") == card_id:
                cards[idx] = payload
                replaced = True
                break
        if not replaced:
            cards.append(payload)

    if q is not None:
        q.put_nowait({"event": "tool_card", "data": payload})


def emit_widget(
    *,
    widget_id: str,
    widget_type: str,
    title: str,
    subtitle: str = "",
    options: List[Dict[str, Any]],
    replace_types: Optional[List[str]] = None,
) -> None:
    """实时推送消息内嵌交互选择卡片（选演出/选连座/选待取消订单）。"""
    widgets = _widgets_ctx.get()
    q = _thought_queue_ctx.get()

    payload: Dict[str, Any] = {
        "id": widget_id,
        "widgetType": widget_type,
        "title": title,
        "subtitle": subtitle,
        "options": options,
    }

    if widgets is not None:
        # 例如已经算出具体连座卡片 SEAT_PICKER 时，把同轮之前推过的泛化 SHOW_PICKER 顶掉，界面更清爽
        if replace_types:
            widgets[:] = [w for w in widgets if w.get("widgetType") not in replace_types]
        replaced = False
        for idx, existing in enumerate(widgets):
            if existing.get("id") == widget_id:
                widgets[idx] = payload
                replaced = True
                break
        if not replaced:
            widgets.append(payload)

    if q is not None:
        q.put_nowait({"event": "widget", "data": payload})


def _extract_chunk_text(chunk: Any) -> str:
    """兼容不同 OpenAI/LangChain 厂商返回的 chunk.content 结构（纯字符串或分片列表），统一提取纯文本。"""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def _can_astream(llm: Any) -> bool:
    """判断大模型对象是否支持真实异步流式（同时排除单测里的 unittest.mock.Mock 对象，防止假流报错）。"""
    return callable(getattr(llm, "astream", None)) and not hasattr(llm, "assert_called")


async def astream_llm_text(llm: Any, messages: List[Any], *, allow_stream: bool = True) -> str:
    """以真实 SSE Token 流式调用纯文本 LLM，不可流式时自动回退 ainvoke。"""
    if allow_stream and is_token_streaming_enabled() and _thought_queue_ctx.get() is not None and _can_astream(llm):
        parts: List[str] = []
        async for chunk in llm.astream(messages):
            delta = _extract_chunk_text(chunk)
            if delta:
                parts.append(delta)
                emit_token(delta)
        return "".join(parts)

    resp = await llm.ainvoke(messages)
    return str(getattr(resp, "content", "") or "")


async def astream_llm_message(llm: Any, messages: List[Any], *, allow_stream: bool = True) -> Any:
    """以真实 SSE Token 流式调用绑定工具的 LLM，聚合并返回完整 AIMessage。"""
    if allow_stream and is_token_streaming_enabled() and _thought_queue_ctx.get() is not None and _can_astream(llm):
        agg: Any = None
        saw_tool_call = False
        async for chunk in llm.astream(messages):
            # LangChain chunk 支持 `+` 运算符，会把流式碎片自动拼回完整的 AIMessageChunk（含 tool_calls）
            agg = chunk if agg is None else agg + chunk
            # 一旦模型开始输出工具调用参数，就立刻停止往聊天正文推 Token，避免把内部调工具的过程泄露给用户
            if getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None):
                saw_tool_call = True
            if not saw_tool_call:
                delta = _extract_chunk_text(chunk)
                if delta:
                    emit_token(delta)
        return agg if agg is not None else AIMessage(content="")

    return await llm.ainvoke(messages)

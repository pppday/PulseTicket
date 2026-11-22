import asyncio
import contextvars
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage

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
        return bool("".join(self.streamed_tokens))

    def __enter__(self):
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
    _stream_tokens_enabled_ctx.set(bool(enabled))


def is_token_streaming_enabled() -> bool:
    return bool(_stream_tokens_enabled_ctx.get())


def _elapsed_ms() -> int:
    t0 = _turn_t0_ctx.get()
    return int((time.perf_counter() - t0) * 1000) if t0 is not None else 0


def _sanitize_tool_data(obj: Any, max_str: int = 2500, depth: int = 0) -> Any:
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
            agg = chunk if agg is None else agg + chunk
            if getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None):
                saw_tool_call = True
            if not saw_tool_call:
                delta = _extract_chunk_text(chunk)
                if delta:
                    emit_token(delta)
        return agg if agg is not None else AIMessage(content="")

    return await llm.ainvoke(messages)

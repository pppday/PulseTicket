import asyncio
import contextvars
import time
from typing import Any, Dict, List, Optional

_thought_queue_ctx: contextvars.ContextVar[Optional[asyncio.Queue]] = contextvars.ContextVar(
    "thought_queue_ctx", default=None
)
_thought_list_ctx: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = contextvars.ContextVar(
    "thought_list_ctx", default=None
)
_turn_t0_ctx: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "turn_t0_ctx", default=None
)


class ThoughtSession:
    """管理单次请求的实时思考流队列（SSE 零延迟推送）与完整思考轨迹收集。"""

    def __init__(self, use_queue: bool = False):
        self.queue: Optional[asyncio.Queue] = asyncio.Queue() if use_queue else None
        self.steps: List[Dict[str, Any]] = []
        self.t0: float = time.perf_counter()
        self._tokens = []

    def __enter__(self):
        self._tokens = [
            _thought_queue_ctx.set(self.queue),
            _thought_list_ctx.set(self.steps),
            _turn_t0_ctx.set(self.t0),
        ]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        t_q, t_l, t_0 = self._tokens
        _thought_queue_ctx.reset(t_q)
        _thought_list_ctx.reset(t_l)
        _turn_t0_ctx.reset(t_0)


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
    t0 = _turn_t0_ctx.get()
    at_ms = int((time.perf_counter() - t0) * 1000) if t0 is not None else 0

    payload: Dict[str, Any] = {
        "key": key,
        "phase": phase,
        "agentLabel": agent_label,
        "title": title,
        "detail": detail,
        "badges": badges or [],
        "status": status,
        "atMs": at_ms,
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

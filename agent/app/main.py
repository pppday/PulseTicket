"""
PulseTicket AI 智能体服务的 FastAPI 入口层（默认监听 8204 端口）。

在整体架构中的位置与职责：
- 位于 Spring Cloud Gateway 之后，负责承接前端发来的同步对话、SSE 实时流式对话、
  人工确认卡片恢复（Human-in-the-Loop）、历史会话 CRUD 以及管理员审计查询；
- 严格校验网关验签后透传的身份请求头（`X-User-Id` / `X-Username` / `X-User-Role`），
  彻底杜绝绕过登录或默认 1 号用户越权下单的风险；
- 所有核心多智能体编排均委托给 v2 版本的 `multi_agent_manager`（在本文件中以别名 `ticket_agent_manager` 引入）。
"""

from contextlib import asynccontextmanager
import json
from typing import Literal, Optional, Tuple
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
# 引入 v2 多智能体主图管理器（主图路由 + 3 子图 + Interrupt 确认门控）
from app.graph.main_graph import multi_agent_manager as ticket_agent_manager
from app.observability.tracer import agent_tracer
from app.rag.hybrid_rag import hybrid_rag_engine
from app.storage.conversation_store import conversation_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 应用生命周期钩子：
    - 启动阶段：先加载 `knowledge/*.md` 构建 Hybrid RAG 索引，再初始化 LangGraph Checkpointer 连接池、
      审计日志表、会话展示表及后台 7 天过期会话清理协程；
    - 关闭阶段：优雅释放数据库连接池与后台定时任务，避免端口或连接泄漏。
    """
    chunk_count = hybrid_rag_engine.load_and_index()
    await ticket_agent_manager.startup()
    print(
        f"[PulseTicket Agent v2] 主 Graph + 3 子图已就绪 | "
        f"知识分片={chunk_count} | DEV_MOCK={settings.DEV_MOCK} | "
        f"checkpointer={ticket_agent_manager.checkpoint_backend} | tracer={agent_tracer.backend_name} | "
        f"conversations={getattr(ticket_agent_manager, 'conv_store', conversation_store).backend_name}"
    )
    yield
    await ticket_agent_manager.shutdown()


app = FastAPI(
    title="PulseTicket Multi-Agent Service",
    description="基于 LangGraph (主图意图路由 + 知识问答/选座购票/订单售后 3 子图 + Interrupt 确认门控 + Hybrid RAG) 的票务智能体服务",
    version="2.0.0",
    lifespan=lifespan,
)

# 允许前端开发服务或网关跨域访问 Agent 接口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RagSearchRequest(BaseModel):
    """独立调试 Hybrid RAG 混合检索接口的请求体。"""

    query: str = Field(..., description="用户的规则提问，例如：上海那场演出临时去不了能退票吗？")
    top_k: int = Field(3, ge=1, le=6)


class ChatRequest(BaseModel):
    """用户发送一轮对话消息的请求体（支持 agent 执行模式与 chat 只读咨询模式）。"""

    threadId: str = Field(..., min_length=1, max_length=128, description="多轮会话线程 ID（用于 PostgreSQL Checkpointer 持久化）")
    message: str = Field(..., min_length=1, max_length=2500, description="用户在对话框输入的自然语言消息")
    mode: Literal["agent", "chat"] = Field("agent", description="对话模式：agent=任务执行与工具调用，chat=轻量只读咨询")


class ConfirmRequest(BaseModel):
    """用户在前端点击确认卡片按钮后，用于恢复 LangGraph interrupt() 断点的请求体。"""

    threadId: str = Field(..., min_length=1, max_length=128, description="需要恢复的挂起会话线程 ID")
    confirmed: bool = Field(..., description="true=点击【确认锁座/确认取消】，false=点击【暂不执行】")


def _require_auth_headers(
    x_user_id: Optional[str],
    x_username: Optional[str],
    x_user_role: Optional[str],
) -> Tuple[int, str, str]:
    """严格校验网关透传的登录身份头，彻底移除默认 1 号用户兜底。"""
    # 网关校验完 JWT 后会把用户 ID、用户名、角色写入自定义请求头；若缺失或非法直接返回 401
    raw_id = (x_user_id or "").strip()
    if not raw_id.isdigit() or int(raw_id) <= 0:
        raise HTTPException(status_code=401, detail="缺少有效身份头 X-User-Id，请先登录并通过网关访问")
    clean_name = (x_username or "").strip()
    if not clean_name:
        raise HTTPException(status_code=401, detail="缺少有效身份头 X-Username，请先登录并通过网关访问")
    clean_role = (x_user_role or "USER").strip().upper() or "USER"
    return int(raw_id), clean_name, clean_role


def _format_sse(event: str, data: dict) -> str:
    """将后端事件打包成标准 SSE (Server-Sent Events) 文本帧，两条换行符标志一帧结束。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/agent/health")
async def health():
    """健康检查与运行态自检接口：返回模型配置、存储后端（PG/SQLite）及知识库索引状态，便于部署探活与排查。"""
    return {
        "code": 200,
        "service": "ticket-agent",
        "version": "2.0.0",
        "architecture": "MainGraph(Router+Guard) + 3 Subgraphs(Knowledge/Purchase/Order)",
        "port": settings.AGENT_PORT,
        "llmModel": settings.LLM_MODEL,
        "devMock": settings.DEV_MOCK,
        "checkpointBackend": ticket_agent_manager.checkpoint_backend,
        "traceBackend": agent_tracer.backend_name,
        "conversationBackend": getattr(ticket_agent_manager, "conv_store", conversation_store).backend_name,
        "conversationTtlDays": settings.CONVERSATION_TTL_DAYS,
        "contextMaxTurns": settings.LLM_CONTEXT_MAX_TURNS,
        "indexedKnowledgeChunks": len(hybrid_rag_engine._chunks),
        "ragBackend": hybrid_rag_engine.backend_info,
    }


@app.post("/api/agent/rag/search")
async def test_hybrid_rag(req: RagSearchRequest):
    """单独测试 Hybrid RAG (jieba BM25 + bge-m3/pgvector + RRF + Cross-Encoder Rerank) 的检索明细与打分"""
    hits = await hybrid_rag_engine.search(req.query, top_k=req.top_k)
    return {
        "code": 200,
        "query": req.query,
        "backend": hybrid_rag_engine.backend_info,
        "hits": hits,
    }


@app.post("/api/agent/chat")
async def chat_endpoint(
    req: ChatRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """同步 JSON 对话入口：等待主图全部节点执行完毕后一次性返回最终结果（便于自动化评测与脚本联调）。"""
    uid, uname, role = _require_auth_headers(x_user_id, x_username, x_user_role)
    try:
        data = await ticket_agent_manager.chat(
            thread_id=req.threadId,
            user_message=req.message,
            user_id=uid,
            username=uname,
            user_role=role,
            mode=req.mode,
        )
        return {"code": 200, "data": data}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agent/chat/stream")
async def chat_stream_endpoint(
    req: ChatRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """SSE 实时流式对话入口（推送 route / step / token / interrupt / done 事件）"""
    uid, uname, role = _require_auth_headers(x_user_id, x_username, x_user_role)

    async def event_gen():
        # 逐条消费 manager 产出的实时事件字典，异常时转换为 error 事件推给前端避免连接静默断开
        try:
            async for item in ticket_agent_manager.stream_chat(
                thread_id=req.threadId,
                user_message=req.message,
                user_id=uid,
                username=uname,
                user_role=role,
                mode=req.mode,
            ):
                yield _format_sse(item["event"], item["data"])
        except PermissionError as e:
            yield _format_sse("error", {"code": 403, "message": str(e)})
        except Exception as e:
            yield _format_sse("error", {"code": 500, "message": str(e)})

    # X-Accel-Buffering: no 告诉 Nginx 不要攒满缓冲区再转发，确保打字机 Token 和思考步骤毫秒级直达浏览器
    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/agent/confirm")
async def confirm_endpoint(
    req: ConfirmRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """同步版人工确认恢复入口：恢复挂起的 LangGraph interrupt() 断点，执行或放弃锁座/退票/取消写操作。"""
    uid, uname, role = _require_auth_headers(x_user_id, x_username, x_user_role)
    try:
        data = await ticket_agent_manager.confirm(
            thread_id=req.threadId,
            confirmed=req.confirmed,
            user_id=uid,
            username=uname,
            user_role=role,
        )
        return {"code": 200, "data": data}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/agent/confirm/stream")
async def confirm_stream_endpoint(
    req: ConfirmRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """SSE 流式版人工确认恢复入口：实时回传确认执行过程中的思考步骤、工具卡片与最终订单回执。"""
    uid, uname, role = _require_auth_headers(x_user_id, x_username, x_user_role)

    async def event_gen():
        try:
            async for item in ticket_agent_manager.stream_confirm(
                thread_id=req.threadId,
                confirmed=req.confirmed,
                user_id=uid,
                username=uname,
                user_role=role,
            ):
                yield _format_sse(item["event"], item["data"])
        except PermissionError as e:
            yield _format_sse("error", {"code": 403, "message": str(e)})
        except ValueError as e:
            yield _format_sse("error", {"code": 400, "message": str(e)})
        except Exception as e:
            yield _format_sse("error", {"code": 500, "message": str(e)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/agent/traces")
async def get_agent_traces(
    limit: int = Query(default=25, ge=1, le=100),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """ADMIN 专属：读取 Agent 主图路由、子图轨迹、引用条款与安全拦截审计日志"""
    _, _, role = _require_auth_headers(x_user_id, x_username, x_user_role)
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="仅限架构运维专员 (ADMIN) 查看 Agent 编排审计轨迹")
    data = await agent_tracer.list_recent_traces(limit=limit)
    return {"code": 200, "data": data}


@app.get("/api/agent/threads")
async def list_agent_threads(
    limit: int = Query(default=30, ge=1, le=30),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """查询当前登录用户的 7 天内历史会话列表（按最近活跃倒序，最多 30 条）"""
    uid, _, _ = _require_auth_headers(x_user_id, x_username, x_user_role)
    threads = await ticket_agent_manager.list_threads(user_id=uid, limit=limit)
    return {
        "code": 200,
        "data": {
            "threads": threads,
            "ttlDays": settings.CONVERSATION_TTL_DAYS,
        },
    }


@app.get("/api/agent/threads/{thread_id}")
async def get_agent_thread_detail(
    thread_id: str,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """获取单条历史会话的展示消息、思考链轨迹与 15 分钟内有效的待确认卡片"""
    uid, _, _ = _require_auth_headers(x_user_id, x_username, x_user_role)
    try:
        detail = await ticket_agent_manager.get_thread_detail(thread_id=thread_id, user_id=uid)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if detail is None:
        raise HTTPException(status_code=404, detail="对话不存在或已超过 7 天保留期")
    return {"code": 200, "data": detail}


@app.delete("/api/agent/threads/{thread_id}")
async def delete_agent_thread(
    thread_id: str,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_username: Optional[str] = Header(default=None, alias="X-Username"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
):
    """删除个人历史会话，同时联动清理展示历史与 LangGraph Checkpoint 状态"""
    uid, _, _ = _require_auth_headers(x_user_id, x_username, x_user_role)
    try:
        deleted = await ticket_agent_manager.delete_thread(thread_id=thread_id, user_id=uid)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"code": 200, "data": {"threadId": thread_id, "deleted": deleted}}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.AGENT_PORT, reload=True)

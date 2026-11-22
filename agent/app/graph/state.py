from typing import Annotated, Any, Dict, List, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages

from app.config import settings


class SharedSlots(TypedDict, total=False):
    """
    【跨子图/跨轮次共享的结构化业务槽位】
    原因：每次进入子图时会重置子图临时消息列表（防历史冗余干扰推理），
    因此把演出名、场次 ID、张数、预算上限、上一轮推荐单价与价格偏好存在主图 SharedSlots 中，
    再由 LangGraph Checkpointer 持久化到 PostgreSQL，实现“白雪公主 -> 有便宜一点的吗 -> 就锁这个”多轮衔接。
    """
    show_keyword: Optional[str]
    category: Optional[str]
    session_id: Optional[int]
    seat_count: Optional[int]
    max_unit_price: Optional[float]
    last_unit_price: Optional[float]      # 上一次给用户推荐的单张票价，供“便宜一点/再低一档”向下筛选
    price_preference: Optional[str]       # "best_view" | "lowest_price" | "highest_price"
    seat_ids: Optional[List[int]]
    seat_labels: Optional[List[str]]
    order_no: Optional[str]
    wants_commit: bool


class SubTaskResult(TypedDict, total=False):
    """单个子 Agent 执行完成后的标准汇总结果"""
    intent: str
    agent: str
    summary: str
    citations: List[str]
    refused: bool


class MainGraphState(TypedDict, total=False):
    """
    【主 Graph 全局共享状态】：
    - messages + history_summary: 对话消息流与超长历史滚动摘要
    - user_id / username / user_role: 网关透传可信身份（严禁默认值）
    - intents / intent_cursor: 本轮拆解出的有序意图队列与当前执行游标
    - slots: 跨子图共享槽位（记住上一轮推荐的场次/座位，支持“就锁这两张”指代）
    - sub_results / citations / tool_steps: 子图产出、引用条款编号与可观测轨迹
    - pending_action: 待 confirm_action_node 触发 interrupt() 的核验后写操作载荷
    """
    messages: Annotated[List[BaseMessage], add_messages]
    history_summary: str
    user_id: int
    username: str
    user_role: str

    current_query: str
    mode: str
    guard_blocked: bool
    guard_reason: str

    intents: List[str]
    intent_cursor: int
    slots: SharedSlots

    sub_results: List[SubTaskResult]
    citations: List[str]
    tool_steps: List[Dict[str, Any]]
    executed_subgraphs: List[str]

    pending_action: Optional[Dict[str, Any]]
    final_reply: str


_LLM_POOL: Dict[float, ChatOpenAI] = {}


def get_chat_llm(temperature: float = 0.1) -> ChatOpenAI:
    """单例化获取大模型客户端，避免每次进入节点重复创建连接池。"""
    key = round(float(temperature), 2)
    if key not in _LLM_POOL:
        if not settings.LLM_API_KEY:
            raise RuntimeError("未配置 LLM_API_KEY（或 DEEPSEEK_API_KEY），请在 .env 中配置后重试")
        _LLM_POOL[key] = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            temperature=key,
            timeout=settings.LLM_TIMEOUT_SEC,
        )
    return _LLM_POOL[key]

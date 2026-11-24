"""
v2 多智能体主图全局状态定义与大模型客户端池。

职责说明：
- 定义 `SharedSlots`（跨轮次、跨子图传递的结构化业务槽位，避免“记住上一轮选的演出和座位”全靠长文本对话）；
- 定义 `SubTaskResult`（每个子图跑完后的统一标准汇报格式）与 `MainGraphState`（LangGraph 主图共享黑板）；
- 提供按 `temperature` 缓存复用的 `get_chat_llm()`，防止每次进入节点都重新初始化 HTTP 连接池。
"""

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
    show_keyword: Optional[str]           # 演出名称/城市/场馆关键词（如“星际回响”）
    category: Optional[str]               # 演出分类（CONCERT / MUSICAL / IMAX_MOVIE / LIVEHOUSE）
    session_id: Optional[int]             # 已锁定的场次 ID（如 101）
    seat_count: Optional[int]             # 购票张数（默认 1~6）
    max_unit_price: Optional[float]       # 单张最高预算上限（元）
    last_unit_price: Optional[float]      # 上一次给用户推荐的单张票价，供“便宜一点/再低一档”向下筛选
    price_preference: Optional[str]       # "best_view" | "lowest_price" | "highest_price"
    position_preference: Optional[str]    # 座位横向位置偏好："center"（中轴，默认）| "side"（靠边/过道）
    preferred_row: Optional[int]          # 本轮用户明确指定的排号（1~6），单轮坐标约束，避免误当成数据库 seat_id
    preferred_col: Optional[int]          # 本轮用户明确指定的列/座号（1~8），与 preferred_row 组合精确查座
    seat_ids: Optional[List[int]]         # 上一轮推荐或用户明确选定的真实数据库座位主键列表
    seat_labels: Optional[List[str]]      # 对应的可读座位标签列表（如 ["3排04座", "3排05座"]）
    order_no: Optional[str]               # 最近涉及的订单号（如 TK202611160001）
    wants_commit: bool                    # 本轮用户是否明确要求“现在就锁座/下单”（False 表示只看推荐不弹窗）


class SubTaskResult(TypedDict, total=False):
    """单个子 Agent 执行完成后的标准汇总结果"""
    intent: str                           # 对应的意图类型：policy / purchase / order / chitchat / confirm
    agent: str                            # 执行节点标识：knowledge_agent / purchase_agent / order_agent 等
    summary: str                          # 子图最终产出的回答正文
    citations: List[str]                  # 命中的官方条款编号列表（如 ["POL-REFUND-001"]）
    refused: bool                         # 知识问答子图是否因证据不足触发了诚实拒答


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
    # 使用 LangGraph 内置 add_messages reducer：每次节点返回 messages 时自动追加而非覆盖
    messages: Annotated[List[BaseMessage], add_messages]
    history_summary: str                  # 最近 3 轮裁剪后的简短历史文本，供各子图理解多轮指代
    user_id: int                          # 网关透传的当前登录用户 ID
    username: str                         # 网关透传的当前登录用户名
    user_role: str                        # 网关透传的角色（USER / ADMIN）

    current_query: str                    # 本轮用户最新输入的原始自然语言
    mode: str                             # 对话模式：agent（允许提案写操作）或 chat（纯只读咨询）
    guard_blocked: bool                   # 是否被前置安全护栏拦截
    guard_reason: str                     # 被安全护栏拦截的具体原因

    intents: List[str]                    # 意图路由器按优先级排好的子任务队列，如 ["policy", "purchase"]
    intent_cursor: int                    # 调度游标：指向下一个待执行的 intents 下标
    slots: SharedSlots                    # 跨轮次、跨子图共享的结构化业务槽位

    sub_results: List[SubTaskResult]      # 各子图依次执行后追加进来的阶段性答复
    citations: List[str]                  # 本轮累计引用的所有官方条款编号（去重）
    tool_steps: List[Dict[str, Any]]      # 供 Tracer 审计落库的工具调用轨迹
    executed_subgraphs: List[str]         # 本轮实际跑过的子图/节点名称列表

    pending_action: Optional[Dict[str, Any]]  # 经服务端核价/核验通过的高风险写操作提案（有值就会拐进 confirm_action）
    final_reply: str                      # response_composer 汇总好的本轮最终回复文本


# 按 temperature 缓存 ChatOpenAI 实例（如 0.0 给意图路由、0.1 给业务子图、0.3 给闲聊），实现连接池复用
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

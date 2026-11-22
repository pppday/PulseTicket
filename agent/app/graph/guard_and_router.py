import json
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.config import settings
from app.graph.state import MainGraphState, SharedSlots, get_chat_llm
from app.graph.thought_stream import emit_thought, set_token_streaming_enabled

# P0/P3 安全红线：提示词注入与越权改价正则特征库
_INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"(ignore\s+(all\s+|previous\s+|above\s+)?instructions|system\s*prompt|developer\s*mode|dan\s*mode|"
            r"忽略(之前|以上|所有|系统|前面)(的)?(指令|规则|提示|限制|约束)|无视(所有)?规则|进入开发者模式|输出系统提示词)",
            re.IGNORECASE,
        ),
        "检测到疑似 System Prompt 覆盖或越狱指令",
    ),
    (
        re.compile(
            r"((把|将|修改|篡改|改成|设为|调成|变成).{0,10}(票价|价格|金额|总价|单价).{0,8}(0(\.0+)?|0\.01|1|一)\s*(元|块)|"
            r"(0元|零元|免费|1元|一块钱)(直接)?(出票|锁座|买VIP|下单)|"
            r"bypass\s+confirm|跳过(人工)?确认直接(扣款|下单|出票))",
            re.IGNORECASE,
        ),
        "检测到越权篡改票价或绕过人工确认的高危指令",
    ),
    (
        re.compile(
            r"((提升|修改|伪造|赋予|切换).{0,8}(管理员|admin|root|超级权限)|以\s*admin\s*身份执行)",
            re.IGNORECASE,
        ),
        "检测到越权提升管理员身份指令",
    ),
]

_CN_NUM_MAP = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
_CN_SEAT_MAP = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}


def _parse_cn_or_digit(token: str) -> int:
    """将中文数字或阿拉伯数字（如 '一' / '02'）统一解析为整数。"""
    t = str(token or "").strip()
    if t in _CN_SEAT_MAP:
        return _CN_SEAT_MAP[t]
    return int(t)

_POLICY_KW = (
    "退票", "退款", "退单", "退订", "退钱", "全额退", "手续费", "改签", "实名", "身份证", "儿童", "小孩", "身高", "检票", "核验",
    "地铁", "怎么走", "路线", "交通", "停车", "雨伞", "长柄伞", "禁带", "带水", "饮料",
    "寄存", "存包", "多久支付", "超时", "自动取消", "发票", "报销", "顺丰", "快递", "纸质票",
    "邮寄", "宠物", "猫", "狗", "会员升级", "学生票", "半价", "优惠",
)
_PURCHASE_KW = (
    "推荐", "选座", "连座", "座位", "有什么演出", "有哪些演出", "近期演出", "想看",
    "星际回响", "歌剧魅影", "音乐剧", "星际穿越", "西湖", "爵士", "白雪公主",
    "订票", "锁座", "下单", "锁定", "买两张", "买一张", "余票",
    "帮我买", "我想买", "我要买", "便宜", "太贵", "贵了", "低价", "性价比",
    "换个座位", "换座位", "其他价位", "别的价位", "看台",
    "观看体验", "观演体验", "最佳", "靠边", "过道",
)
_CHEAPER_KW = (
    "便宜一点", "便宜点", "更便宜", "便宜些", "便宜的", "最便宜",
    "太贵", "贵了", "低价", "性价比", "经济", "省钱", "看台", "便宜",
)
_RELATIVE_CHEAPER_KW = (
    "便宜一点", "便宜点", "更便宜", "便宜些", "太贵", "贵了", "再便宜", "换个低价",
)
_HIGHER_KW = (
    "贵一点", "更贵", "最好的", "最好", "最佳", "观看体验", "观演体验",
    "视角最好", "视听体验", "最前排", "vip", "内场至尊", "前排", "视野最好",
)
_SIDE_KW = ("靠边", "靠过道", "过道", "边上", "边缘", "最边")
_CENTER_KW = ("中间", "中轴", "正中", "正中间", "居中")
_ORDER_KW = (
    "我的订单", "历史订单", "查订单", "订单状态", "取消订单", "退掉这单", "退订订单",
    "取消刚才", "刚才那单", "不付了", "申请退票", "申请退款", "退票退款",
    "帮我退票", "帮我退款", "我要退票", "我要退款",
    "怎么退款", "如何退款", "怎么退票", "如何退票", "退票流程", "退款流程",
)
_COMMIT_KW = (
    "锁座", "锁定", "下单", "就买这", "就订这", "就选这", "帮我买这", "帮我锁", "确认买", "立即购买",
)

ROUTER_SYSTEM_PROMPT = """你是 PulseTicket 票务主控图的【意图路由器 (Intent Router)】。
请分析用户的最新输入（结合已有槽位上下文），只输出严格的 JSON 对象，不要输出任何 Markdown 代码块或多余解释。

可选意图枚举（按需选 1~3 个，若同一句话既问规则又让选座/查演出，必须同时输出多个意图！）：
- "policy": 询问退改规则、手续费、实名制、儿童身高、场馆地铁交通、禁带物品、寄存、发票/快递/宠物等平台政策与须知
- "order": 查询用户名下已有订单、核实某笔已有订单能退多少钱、取消未支付订单或为已支付且符合窗口期的订单办理阶梯退票
- "purchase": 查询在售演出场次、按预算/位置偏好（靠边或中间/便宜或最佳体验/指定第几排几座）推荐选座、或要求对选定座位发起锁座下单
- "chitchat": 纯打招呼、感谢、闲聊（不涉及任何规则、演出、选座或订单）

输出 JSON 格式示例：
{
  "intents": ["policy", "purchase"],
  "slots": {
    "show_keyword": "星际回响",
    "category": null,
    "session_id": null,
    "seat_count": 2,
    "max_unit_price": 700,
    "price_preference": "lowest_price",
    "position_preference": "side",
    "seat_ids": null,
    "order_no": null,
    "wants_commit": false
  }
}
注意：
- `seat_count`: 仅在用户本轮明确提出新张数时填写，未提及时返回 null 以继承上下文已有张数。
- `price_preference`: 用户要低价/便宜/性价比/看台传 `"lowest_price"`，要 VIP/前排/最佳观看体验传 `"highest_price"`，默认 `"best_view"`（未提及可传 null）。
- `position_preference`: 用户说靠边/过道传 `"side"`，说中间/中轴传 `"center"`，未提及传 null。
- `seat_ids`: 仅在用户明确给出数据库 seat_ids 列表时填写；绝不可把“第一排2号座位”中的“2号”误填成 `seat_ids=[2]`！
- `wants_commit` 仅在用户明确要求“现在帮我锁座/下单/买下选定座位/我要第X排Y号座位”时为 true；如果只是“帮我推荐/有便宜一点的吗/看看座位”则为 false！"""


def detect_prompt_injection(text: str) -> Optional[str]:
    clean = (text or "").strip()
    if len(clean) > 2000:
        return "单次输入长度超出安全上限（2000 字符）"
    for pattern, reason in _INJECTION_PATTERNS:
        if pattern.search(clean):
            return reason
    return None


def summarize_and_trim_history(
    messages: List[BaseMessage],
    existing_summary: str = "",
    keep_recent: Optional[int] = None,
) -> Tuple[str, List[BaseMessage]]:
    """
    【对话上下文 3 轮滑动窗口裁剪】：
    只取最近 LLM_CONTEXT_MAX_TURNS 轮（默认 3 轮 = 6 条历史消息，不含当前最新提问），
    废弃 600 字滚动摘要累加防止 Context Rot；单条消息（尤其是助手长回复）截断到上限字符数，
    跨轮指代主要交给 SharedSlots 结构化槽位透传。
    """
    chat_msgs = [
        m
        for m in (messages or [])
        if isinstance(m, (HumanMessage, AIMessage)) and str(m.content or "").strip()
    ]
    prior_msgs = chat_msgs[:-1] if (chat_msgs and isinstance(chat_msgs[-1], HumanMessage)) else chat_msgs
    window_size = keep_recent if keep_recent is not None else max(2, settings.LLM_CONTEXT_MAX_TURNS * 2)
    recent_prior = prior_msgs[-window_size:] if window_size > 0 else []

    max_chars = max(50, settings.LLM_CONTEXT_ASSISTANT_MAX_CHARS)
    lines: List[str] = []
    for m in recent_prior:
        role = "用户" if isinstance(m, HumanMessage) else "助手"
        txt = re.sub(r"\s+", " ", str(m.content)).strip()
        if len(txt) > max_chars:
            txt = txt[:max_chars] + "…"
        lines.append(f"{role}: {txt}")

    return "\n".join(lines), recent_prior


def _reset_stale_show_slots(slots: SharedSlots, keep_budget: bool = False) -> None:
    """当用户切换到另一部新演出时，清理上一部演出残留的场次、座位、排座坐标、位置与推荐单价槽位。"""
    for k in ("session_id", "seat_ids", "seat_labels", "last_unit_price", "preferred_row", "preferred_col"):
        slots.pop(k, None)
    if not keep_budget:
        slots.pop("max_unit_price", None)
        slots.pop("price_preference", None)
        slots.pop("position_preference", None)


def extract_deterministic_signals(query: str, prev_slots: Optional[SharedSlots] = None) -> Tuple[List[str], SharedSlots]:
    """
    【确定性正则槽位抽取与意图兜底】：
    设计思路：把订单号、场次 ID、精确排座坐标（第X排Y号）、靠边/中轴偏好、预算数字与价格偏好用确定性代码提前抽准，
    既给 LLM Router 做先验校准（防止大模型把“第一排2号座位”误当成数据库主键 seat_id=2），也在离线单测或模型波动时兜底。
    """
    q = (query or "").strip()
    q_lower = q.lower()
    slots: SharedSlots = dict(prev_slots or {})
    # 排号/座号是单轮显式坐标约束，默认每轮重置，避免上一轮查过“第一排2号座”后锁死后续推荐
    slots.pop("preferred_row", None)
    slots.pop("preferred_col", None)

    # 1. 抽取订单号 (如 TK202611160001)
    m_order = re.search(r"\b(TK\d{8,20}[A-Z0-9]*)\b", q, re.IGNORECASE)
    if m_order:
        slots["order_no"] = m_order.group(1).upper()

    # 2. 抽取场次 ID (如 sessionId 101 / 101场次)
    m_sess = re.search(r"(?:session[_\s]*id\s*[=:：]?\s*|场次\s*#?)(\d{3})\b", q, re.IGNORECASE)
    if m_sess:
        slots["session_id"] = int(m_sess.group(1))

    # 3. 抽取张数 (如 两张 / 3张 / "买张")
    m_cnt_digit = re.search(r"([1-6])\s*张", q)
    m_cnt_cn = re.search(r"([一两二三四五六])\s*张", q)
    if m_cnt_digit:
        slots["seat_count"] = int(m_cnt_digit.group(1))
    elif m_cnt_cn:
        slots["seat_count"] = _CN_NUM_MAP[m_cnt_cn.group(1)]
    elif re.search(r"(?:买|要|来|订|抢|选)\s*张", q):
        slots["seat_count"] = 1

    # 4. 抽取单价预算 (如 700元以内 / 500块以下)
    m_price = re.search(r"(\d{2,4})\s*(?:元|块)(?:以内|以下|左右|内)?", q)
    if m_price:
        slots["max_unit_price"] = float(m_price.group(1))

    # 5. 优先识别“第X排Y号座位 / 第X排”物理坐标，再识别显式数据库 seat_ids
    # 关键修复：若先跑 `(\d+)号座位`，会把“第一排2号座位”误解析成数据库主键 seat_ids=[2]（属于完全不同的演出场次）！
    m_row_col = re.search(
        r"第?\s*([1-6一二三四五六])\s*排\s*第?\s*0?([1-8一二三四五六七八])\s*(?:号座位|号座|座位|号|座)",
        q,
    )
    m_row_only = None if m_row_col else re.search(r"第?\s*([1-6一二三四五六])\s*排", q)
    m_seat_list = re.search(r"seat_ids\s*=\s*\[([\d,\s]+)\]", q, re.IGNORECASE)
    m_seat_ids = None

    if m_seat_list:
        parsed_ids = sorted({int(x.strip()) for x in m_seat_list.group(1).split(",") if x.strip().isdigit()})
        if parsed_ids:
            slots["seat_ids"] = parsed_ids
    elif m_row_col:
        slots["preferred_row"] = _parse_cn_or_digit(m_row_col.group(1))
        slots["preferred_col"] = _parse_cn_or_digit(m_row_col.group(2))
        if not (m_cnt_digit or m_cnt_cn):
            slots["seat_count"] = 1
        # 用户明确点名某排某座时，清空上一轮推荐席位与残留偏好/价格上限，确保直接按坐标精确查库
        for stale_k in ("seat_ids", "seat_labels", "position_preference", "price_preference"):
            slots.pop(stale_k, None)
        if not m_price:
            slots.pop("max_unit_price", None)
    else:
        if m_row_only:
            slots["preferred_row"] = _parse_cn_or_digit(m_row_only.group(1))
            slots.pop("seat_ids", None)
            slots.pop("seat_labels", None)
            if not m_price:
                slots.pop("max_unit_price", None)
        m_seat_ids = re.search(r"(\d{1,3})\s*(?:和|、|,|与)\s*(\d{1,3})\s*号?座位", q)
        if m_seat_ids:
            slots["seat_ids"] = sorted({int(m_seat_ids.group(1)), int(m_seat_ids.group(2))})
        elif not m_row_only:
            m_single_seat = re.search(r"(?<!\d)(\d{1,3})\s*号座位", q)
            if m_single_seat:
                slots["seat_ids"] = [int(m_single_seat.group(1))]

    # 6. 抽取演出关键词；若切换到新演出则自动清理旧场次与旧座位槽位
    for kw in ("星际回响", "PulseLive", "歌剧魅影", "Phantom", "星际穿越", "Interstellar", "西湖仲夏夜", "白雪公主", "梅赛德斯", "天桥", "万象城", "大麦"):
        if kw.lower() in q_lower:
            prev_kw = str(slots.get("show_keyword") or "").strip().lower()
            if prev_kw and prev_kw != kw.lower() and not m_sess and not m_seat_list:
                _reset_stale_show_slots(slots, keep_budget=bool(m_price))
            slots["show_keyword"] = kw
            break

    # 7. 价格与横向位置偏好感知：
    #    - “靠边/过道” -> position_preference="side"（优先 01/08 座）
    #    - “便宜一点/性价比” -> price_preference="lowest_price"
    #    - “最佳观看体验/前排 VIP” -> price_preference="highest_price"，同时清掉上一轮残留的 max_unit_price 与靠边偏好
    explicit_db_seats = bool(m_seat_list or m_seat_ids)
    has_side_kw = any(k in q for k in _SIDE_KW)
    has_center_kw = any(k in q for k in _CENTER_KW)
    if has_side_kw and not m_row_col:
        slots["position_preference"] = "side"
        if not explicit_db_seats:
            slots.pop("seat_ids", None)
            slots.pop("seat_labels", None)
    elif has_center_kw and not m_row_col:
        slots["position_preference"] = "center"
        if not explicit_db_seats:
            slots.pop("seat_ids", None)
            slots.pop("seat_labels", None)

    if any(k in q for k in _CHEAPER_KW) and not m_row_col:
        slots["price_preference"] = "lowest_price"
        if not explicit_db_seats:
            slots.pop("seat_ids", None)
            slots.pop("seat_labels", None)
        if not m_price and any(k in q for k in _RELATIVE_CHEAPER_KW) and slots.get("last_unit_price"):
            # 基于上轮推荐价（如 ¥880）自动设置严格更低的单价上限（¥879.0）
            slots["max_unit_price"] = round(max(1.0, float(slots["last_unit_price"]) - 1.0), 2)
    elif any(k in q_lower for k in _HIGHER_KW) and not m_row_col:
        slots["price_preference"] = "highest_price"
        if not explicit_db_seats:
            slots.pop("seat_ids", None)
            slots.pop("seat_labels", None)
        if not m_price:
            slots.pop("max_unit_price", None)
        if not has_side_kw:
            # 从“靠边便宜”切换到“最佳观看体验”时显式重置为中轴视野，防止大模型受上轮历史影响继续传 position="side"
            slots["position_preference"] = "center"

    # 8. 判断本轮是否明确发起锁座下单
    wants_commit = any(k in q for k in _COMMIT_KW)
    if (
        m_row_col
        and re.search(r"(我要|我想买|帮我买|买|订|锁|选|来|要)", q)
        and not any(k in q for k in ("吗", "怎么样", "有没有", "贵不贵", "多少钱", "空着", "还有", "推荐"))
    ):
        # 用户点名精确坐标并表态购买（如“我要第一排2号座位”），查出真实 seatId 后直接弹核价确认卡片
        wants_commit = True
    if "如果" in q and ("推荐" in q or "选两张" in q or "选座" in q) and "下单" not in q:
        wants_commit = False
    slots["wants_commit"] = wants_commit

    # 9. 规则意图识别
    intents: List[str] = []
    if any(k in q for k in _POLICY_KW):
        intents.append("policy")
    if m_order or any(k in q for k in _ORDER_KW):
        intents.append("order")
    if (
        wants_commit
        or m_sess
        or m_seat_ids
        or m_row_col
        or m_row_only
        or has_side_kw
        or has_center_kw
        or any(k in q for k in _PURCHASE_KW)
    ):
        # 避免“退票/取消订单”误判成买票
        if not ("取消订单" in q and not any(pk in q for pk in ("推荐", "买", "选座", "锁座"))):
            intents.append("purchase")

    if not intents:
        intents.append("chitchat")

    return _normalize_intents(intents), slots


def _normalize_intents(raw_intents: List[str]) -> List[str]:
    """统一固定多意图执行顺序：先答规则(policy) -> 再查改订单(order) -> 最后选座下单(purchase)。"""
    priority = {"policy": 1, "order": 2, "purchase": 3, "chitchat": 4}
    valid = [i for i in raw_intents if i in priority]
    dedup = sorted(set(valid), key=lambda x: priority[x])
    if len(dedup) > 1 and "chitchat" in dedup:
        dedup.remove("chitchat")
    return dedup[:3] if dedup else ["chitchat"]


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return None
    return None


async def input_guard_node(state: MainGraphState) -> Dict[str, Any]:
    """【主图第 1 站：安全护栏 input_guard】校验身份、长度限制与提示词注入/改价攻击。"""
    uid = state.get("user_id")
    uname = (state.get("username") or "").strip()
    if not isinstance(uid, int) or uid <= 0 or not uname:
        raise PermissionError("主图安全护栏拦截：缺少可信网关身份 (user_id / username)")

    msgs = state.get("messages") or []
    latest_query = state.get("current_query") or ""
    if not latest_query and msgs:
        latest_query = str(msgs[-1].content or "")

    summary, _ = summarize_and_trim_history(msgs, state.get("history_summary") or "")

    block_reason = detect_prompt_injection(latest_query)
    if block_reason:
        emit_thought(
            key="guard",
            phase="guard",
            agent_label="安全护栏",
            title="拦截高危提示词注入 / 越权指令",
            detail=block_reason,
            badges=["BLOCKED"],
            status="warn",
        )
        safe_reply = (
            f"🛡️ **安全护栏已拦截该请求**（原因：{block_reason}）。\n\n"
            "PulseTicket 的所有票价核算、库存锁定与角色权限均由后端 Java 微服务确定性校验，"
            "不支持通过对话指令修改票价、绕过确认卡片或变更系统规则。如需正常查询演出、场馆交通、退改规则或选座，请重新提问。"
        )
        return {
            "current_query": latest_query,
            "history_summary": summary,
            "guard_blocked": True,
            "guard_reason": block_reason,
            "intents": [],
            "intent_cursor": 0,
            "sub_results": [],
            "citations": [],
            "executed_subgraphs": ["input_guard"],
            "tool_steps": [{"agent": "main_guard", "tool": "input_guard_blocked", "args": {"reason": block_reason}}],
            "pending_action": None,
            "final_reply": safe_reply,
            "messages": [AIMessage(content=safe_reply)],
        }

    emit_thought(
        key="guard",
        phase="guard",
        agent_label="安全护栏",
        title="可信身份与输入安全审查通过",
        detail=f"网关透传身份 {uname} (#{uid}) 校验有效，未检出越权改价或注入特征",
        badges=[f"UID {uid}"],
        status="done",
    )
    return {
        "current_query": latest_query,
        "history_summary": summary,
        "guard_blocked": False,
        "guard_reason": "",
        "sub_results": [],
        "citations": [],
        "executed_subgraphs": [],
        "tool_steps": [],
        "pending_action": None,
        "final_reply": "",
    }


async def intent_router_node(state: MainGraphState) -> Dict[str, Any]:
    """
    【主图第 2 站：结构化意图路由与槽位抽取节点 intent_router】：
    自己不绑定任何业务工具，专注将用户单句拆解为有序意图队列 `intents` 并合并跨轮共享槽位 `slots`。
    """
    query = state.get("current_query") or ""
    prev_slots: SharedSlots = dict(state.get("slots") or {})
    emit_thought(
        key="router",
        phase="router",
        agent_label="主图路由",
        title="正在拆解用户意图与跨轮槽位…",
        detail="识别是否包含复合多意图，并提取场次、张数、预算与锁座确认倾向",
        status="running",
    )
    rule_intents, merged_slots = extract_deterministic_signals(query, prev_slots)

    final_intents = rule_intents
    try:
        llm = get_chat_llm(temperature=0.0)
        slot_ctx = json.dumps(merged_slots, ensure_ascii=False)
        recent_ctx = (state.get("history_summary") or "").strip() or "无"
        resp = await llm.ainvoke([
            SystemMessage(content=ROUTER_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"【最近 {settings.LLM_CONTEXT_MAX_TURNS} 轮对话】:\n{recent_ctx}\n"
                    f"【已有上下文槽位】: {slot_ctx}\n"
                    f"【用户最新输入】: {query}"
                )
            ),
        ])
        parsed = _extract_json_obj(str(resp.content or ""))
        if parsed and isinstance(parsed.get("intents"), list):
            llm_intents = [str(x).strip() for x in parsed["intents"] if x]
            # 合并强信号（如用户明确问了政策词或带订单号时绝不漏掉对应子图）
            combined = list(llm_intents)
            for must_have in ("policy", "order"):
                if must_have in rule_intents and must_have not in combined:
                    combined.append(must_have)
            if merged_slots.get("wants_commit") and "purchase" in rule_intents and "purchase" not in combined:
                combined.append("purchase")
            final_intents = _normalize_intents(combined)

            llm_slots = parsed.get("slots") or {}
            if isinstance(llm_slots, dict):
                prev_kw = str(prev_slots.get("show_keyword") or "").strip().lower()
                llm_kw = str(llm_slots.get("show_keyword") or "").strip()
                if llm_kw and prev_kw and llm_kw.lower() != prev_kw and not merged_slots.get("seat_ids"):
                    _reset_stale_show_slots(merged_slots)

                has_exact_row_col = (
                    merged_slots.get("preferred_row") is not None
                    and merged_slots.get("preferred_col") is not None
                )
                rule_pref_hit = any(k in query for k in _CHEAPER_KW) or any(k in query.lower() for k in _HIGHER_KW)
                rule_pos_hit = any(k in query for k in _SIDE_KW) or any(k in query for k in _CENTER_KW)

                for k in ("show_keyword", "category", "session_id", "seat_count", "max_unit_price", "order_no"):
                    val = llm_slots.get(k)
                    if val is None or val == "":
                        continue
                    if k == "seat_count":
                        # 精确点名某排某座且未说“几张”时锁定为 1 张，防止把“2号座位”的 2 误当作 2 张
                        if has_exact_row_col and not re.search(r"[1-6一两二三四五六]\s*张", query):
                            continue
                        if prev_slots.get("seat_count") is not None and not re.search(r"[1-6一两二三四五六]|张|个人|位", query):
                            continue
                    if k == "max_unit_price":
                        # 防止模型从历史对话中把上一轮已清除的低价预算（如 280）带回到“最佳观看体验/第一排2号座”
                        if (has_exact_row_col or any(hk in query.lower() for hk in _HIGHER_KW)) and not re.search(r"\d{2,4}\s*(?:元|块)", query):
                            continue
                        if not re.search(r"\d", query):
                            continue
                    merged_slots[k] = val

                llm_pref = llm_slots.get("price_preference")
                if not rule_pref_hit and not has_exact_row_col and llm_pref in ("best_view", "lowest_price", "highest_price"):
                    merged_slots["price_preference"] = llm_pref

                llm_pos = llm_slots.get("position_preference")
                if not rule_pos_hit and not has_exact_row_col and not any(hk in query.lower() for hk in _HIGHER_KW):
                    if llm_pos in ("center", "side"):
                        merged_slots["position_preference"] = llm_pos

                # 仅在用户确实指定了数据库 seat_ids（而非“第一排2号座”物理坐标或换价位重选）时采纳 LLM 返回的 seat_ids
                if (
                    isinstance(llm_slots.get("seat_ids"), list)
                    and llm_slots["seat_ids"]
                    and not merged_slots.get("preferred_row")
                    and not rule_pref_hit
                    and not rule_pos_hit
                    and re.search(r"seat_ids\s*=|\d+\s*(?:和|、|,|与)\s*\d+\s*号?座位", query)
                ):
                    merged_slots["seat_ids"] = [int(x) for x in llm_slots["seat_ids"]]
                if isinstance(llm_slots.get("wants_commit"), bool):
                    merged_slots["wants_commit"] = bool(merged_slots.get("wants_commit"))
    except Exception:
        # 离线无 Key 评测或模型波动时平滑回退到确定性路由结果
        final_intents = rule_intents

    mode = (state.get("mode") or "agent").strip().lower()
    if mode not in ("agent", "chat"):
        mode = "agent"
    if mode == "chat":
        # Chat 模式严格只读咨询，禁止触发任何写操作提交
        merged_slots["wants_commit"] = False

    if len(final_intents) > 1:
        # 多意图汇总需要带小标题分区排版，关闭子图内裸 Token 直推改由汇总后统一推送
        set_token_streaming_enabled(False)

    intent_cn = {
        "policy": "规则与场馆问答",
        "purchase": "选座与购票专员",
        "order": "订单与售后专员",
        "chitchat": "常规对话引导",
    }
    mode_label = "Chat 只读咨询" if mode == "chat" else "Agent 任务执行"
    route_chain = " → ".join(intent_cn.get(i, i) for i in final_intents)
    slot_notes = []
    if merged_slots.get("show_keyword"):
        slot_notes.append(f"演出「{merged_slots['show_keyword']}」")
    if merged_slots.get("session_id"):
        slot_notes.append(f"场次 #{merged_slots['session_id']}")
    if merged_slots.get("seat_count"):
        slot_notes.append(f"{merged_slots['seat_count']} 张")
    if merged_slots.get("max_unit_price"):
        slot_notes.append(f"单价 ≤ ¥{int(merged_slots['max_unit_price'])}")
    if merged_slots.get("price_preference") == "lowest_price":
        slot_notes.append("偏好低价优先")
    elif merged_slots.get("price_preference") == "highest_price":
        slot_notes.append("偏好 VIP/最佳体验")
    if merged_slots.get("position_preference") == "side":
        slot_notes.append("偏好靠边/过道")
    elif merged_slots.get("position_preference") == "center":
        slot_notes.append("偏好中轴视野")
    if merged_slots.get("preferred_row") is not None and merged_slots.get("preferred_col") is not None:
        slot_notes.append(f"指定 {merged_slots['preferred_row']}排{int(merged_slots['preferred_col']):02d}座")
    elif merged_slots.get("preferred_row") is not None:
        slot_notes.append(f"指定第 {merged_slots['preferred_row']} 排")
    if merged_slots.get("seat_ids"):
        slot_notes.append(f"席位 ID {merged_slots['seat_ids']}")
    if merged_slots.get("order_no"):
        slot_notes.append(f"订单 {merged_slots['order_no']}")
    slot_summary = f"；继承/提取槽位：{', '.join(slot_notes)}" if slot_notes else ""

    emit_thought(
        key="router",
        phase="router",
        agent_label="主图路由",
        title=f"[{mode_label}] 编排调度：{route_chain}",
        detail=f"按确定性优先级拆解为 {len(final_intents)} 个子任务顺序执行{slot_summary}",
        badges=[mode_label] + [intent_cn.get(i, i) for i in final_intents],
        status="done",
    )

    step_record = {
        "agent": "intent_router",
        "tool": "route_intents",
        "args": {
            "mode": mode,
            "intents": final_intents,
            "wants_commit": bool(merged_slots.get("wants_commit")),
        },
    }
    return {
        "mode": mode,
        "intents": final_intents,
        "intent_cursor": 0,
        "slots": merged_slots,
        "tool_steps": list(state.get("tool_steps") or []) + [step_record],
    }

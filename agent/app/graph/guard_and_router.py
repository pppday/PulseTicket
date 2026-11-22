import json
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.config import settings
from app.graph.state import MainGraphState, SharedSlots, get_chat_llm
from app.graph.thought_stream import emit_thought

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

_POLICY_KW = (
    "退票", "手续费", "改签", "实名", "身份证", "儿童", "小孩", "身高", "检票", "核验",
    "地铁", "怎么走", "路线", "交通", "停车", "雨伞", "长柄伞", "禁带", "带水", "饮料",
    "寄存", "存包", "多久支付", "超时", "自动取消", "发票", "报销", "顺丰", "快递", "纸质票",
    "邮寄", "宠物", "猫", "狗", "会员升级", "学生票", "半价", "优惠",
)
_PURCHASE_KW = (
    "推荐", "选座", "连座", "座位", "有什么演出", "有哪些演出", "近期演出", "想看",
    "星际回响", "歌剧魅影", "音乐剧", "星际穿越", "西湖", "爵士",
    "订票", "锁座", "下单", "锁定", "买两张", "买一张", "余票",
    "帮我买", "我想买", "我要买",
)
_ORDER_KW = (
    "我的订单", "历史订单", "查订单", "订单状态", "取消订单", "退掉这单", "退订订单",
    "取消刚才", "刚才那单", "不付了",
)
_COMMIT_KW = (
    "锁座", "锁定", "下单", "就买这", "就订这", "就选这", "帮我买这", "帮我锁", "确认买", "立即购买",
)

ROUTER_SYSTEM_PROMPT = """你是 PulseTicket 票务主控图的【意图路由器 (Intent Router)】。
请分析用户的最新输入（结合已有槽位上下文），只输出严格的 JSON 对象，不要输出任何 Markdown 代码块或多余解释。

可选意图枚举（按需选 1~3 个，若同一句话既问规则又让选座/查演出，必须同时输出多个意图！）：
- "policy": 询问退改规则、手续费、实名制、儿童身高、场馆地铁交通、禁带物品、寄存、发票/快递/宠物等平台政策与须知
- "order": 查询用户名下已有订单、核实某笔已有订单能退多少钱、或申请取消未支付订单
- "purchase": 查询在售演出场次、按预算推荐连座、或要求对选定座位发起锁座下单
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
    "seat_ids": null,
    "order_no": null,
    "wants_commit": false
  }
}
注意：
- `wants_commit` 仅在用户明确要求“现在帮我锁座/下单/买下选定座位”时为 true；如果只是“帮我推荐/看看座位”则为 false！"""


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


def extract_deterministic_signals(query: str, prev_slots: Optional[SharedSlots] = None) -> Tuple[List[str], SharedSlots]:
    """确定性正则槽位抽取与意图兜底（既给 LLM Router 做先验校准，也在离线评测或 JSON 异常时兜底）。"""
    q = (query or "").strip()
    q_lower = q.lower()
    slots: SharedSlots = dict(prev_slots or {})

    # 1. 抽取订单号 (如 TK202611160001)
    m_order = re.search(r"\b(TK\d{8,20}[A-Z0-9]*)\b", q, re.IGNORECASE)
    if m_order:
        slots["order_no"] = m_order.group(1).upper()

    # 2. 抽取场次 ID (如 sessionId 101 / 101场次)
    m_sess = re.search(r"(?:session[_\s]*id\s*[=:：]?\s*|场次\s*#?)(\d{3})\b", q, re.IGNORECASE)
    if m_sess:
        slots["session_id"] = int(m_sess.group(1))

    # 3. 抽取张数 (如 两张 / 3张)
    m_cnt_digit = re.search(r"([1-6])\s*张", q)
    m_cnt_cn = re.search(r"([一两二三四五六])\s*张", q)
    if m_cnt_digit:
        slots["seat_count"] = int(m_cnt_digit.group(1))
    elif m_cnt_cn:
        slots["seat_count"] = _CN_NUM_MAP[m_cnt_cn.group(1)]

    # 4. 抽取单价预算 (如 700元以内 / 500块以下)
    m_price = re.search(r"(\d{2,4})\s*(?:元|块)(?:以内|以下|左右|内)?", q)
    if m_price:
        slots["max_unit_price"] = float(m_price.group(1))

    # 5. 抽取明确指定的座位 ID 列表 (如 20和21号座位)
    m_seat_ids = re.search(r"(\d{1,3})\s*(?:和|、|,|与)\s*(\d{1,3})\s*号?座位", q)
    if m_seat_ids:
        slots["seat_ids"] = sorted({int(m_seat_ids.group(1)), int(m_seat_ids.group(2))})

    # 6. 抽取演出关键词
    for kw in ("星际回响", "PulseLive", "歌剧魅影", "Phantom", "星际穿越", "Interstellar", "西湖仲夏夜", "梅赛德斯", "天桥", "万象城", "大麦"):
        if kw.lower() in q_lower:
            slots["show_keyword"] = kw
            break

    # 7. 判断本轮是否明确发起锁座下单
    wants_commit = any(k in q for k in _COMMIT_KW)
    if "如果" in q and ("推荐" in q or "选两张" in q or "选座" in q) and "下单" not in q:
        wants_commit = False
    slots["wants_commit"] = wants_commit

    # 8. 规则意图识别
    intents: List[str] = []
    if any(k in q for k in _POLICY_KW):
        intents.append("policy")
    if m_order or any(k in q for k in _ORDER_KW):
        intents.append("order")
    if wants_commit or m_sess or m_seat_ids or any(k in q for k in _PURCHASE_KW):
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
                for k in ("show_keyword", "category", "session_id", "seat_count", "max_unit_price", "order_no"):
                    val = llm_slots.get(k)
                    if val is not None and val != "":
                        merged_slots[k] = val
                if isinstance(llm_slots.get("seat_ids"), list) and llm_slots["seat_ids"]:
                    merged_slots["seat_ids"] = [int(x) for x in llm_slots["seat_ids"]]
                if isinstance(llm_slots.get("wants_commit"), bool):
                    merged_slots["wants_commit"] = merged_slots["wants_commit"] or bool(merged_slots.get("wants_commit"))
    except Exception:
        # 离线无 Key 评测或模型波动时平滑回退到确定性路由结果
        final_intents = rule_intents

    intent_cn = {
        "policy": "规则与场馆问答",
        "purchase": "选座与购票专员",
        "order": "订单与售后专员",
        "chitchat": "常规对话引导",
    }
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
    if merged_slots.get("seat_ids"):
        slot_notes.append(f"席位 ID {merged_slots['seat_ids']}")
    if merged_slots.get("order_no"):
        slot_notes.append(f"订单 {merged_slots['order_no']}")
    slot_summary = f"；继承/提取槽位：{', '.join(slot_notes)}" if slot_notes else ""

    emit_thought(
        key="router",
        phase="router",
        agent_label="主图路由",
        title=f"编排调度链路：{route_chain}",
        detail=f"按确定性优先级拆解为 {len(final_intents)} 个子任务顺序执行{slot_summary}",
        badges=[intent_cn.get(i, i) for i in final_intents],
        status="done",
    )

    step_record = {
        "agent": "intent_router",
        "tool": "route_intents",
        "args": {
            "intents": final_intents,
            "wants_commit": bool(merged_slots.get("wants_commit")),
        },
    }
    return {
        "intents": final_intents,
        "intent_cursor": 0,
        "slots": merged_slots,
        "tool_steps": list(state.get("tool_steps") or []) + [step_record],
    }

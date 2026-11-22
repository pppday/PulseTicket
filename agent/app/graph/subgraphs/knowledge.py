import re
from typing import Any, Dict, List, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.graph.state import SharedSlots, get_chat_llm
from app.graph.thought_stream import astream_llm_text, emit_thought, emit_token, emit_tool_card
from app.rag.hybrid_rag import hybrid_rag_engine, tokenize_zh

# 知识库明确未覆盖的高频越界业务词（用于精确识别“知识库盲区”，杜绝模型自由发挥编造政策）
_UNCOVERED_TOPICS = {
    "发票": "发票开具与报销凭证",
    "报销": "发票开具与报销凭证",
    "税号": "发票开具与报销凭证",
    "顺丰": "纸质票快递邮寄",
    "邮寄": "纸质票快递邮寄",
    "快递": "纸质票快递邮寄",
    "纸质票": "纸质票快递邮寄",
    "宠物": "携带宠物入场规定",
    "小狗": "携带宠物入场规定",
    "猫咪": "携带宠物入场规定",
    "导盲犬": "特殊宠物入场审批",
    "黑钻": "VIP/黑钻会员升级门槛",
    "会员升级": "VIP/黑钻会员升级门槛",
    "学生票": "学生证/特殊人群折扣",
    "半价票": "学生证/特殊人群折扣",
    "军人": "军人/特殊证件优待",
    "老年票": "老年人购票折扣",
    "充电宝": "场馆内充电宝租借",
    "酒店": "周边酒旅住宿合作",
}

# 已收录在 5 篇官方规则文档中的核心主题锚点词
_COVERED_ANCHORS = (
    "退票", "退款", "退单", "退订", "退钱", "全额退", "手续费", "改签", "免费退", "取消",
    "实名", "身份证", "核验", "检票", "二维码",
    "儿童", "小孩", "身高", "1.2", "迟到", "幕间", "地铁", "怎么走", "路线", "交通", "停车",
    "梅赛德斯", "天桥", "万象城", "大麦", "梅溪湖", "贺龙", "琴岛", "音乐厅",
    "禁带", "雨伞", "长柄伞", "水", "饮料", "打火机", "灯牌", "寄存", "存包",
    "选座", "连座", "锁座", "15分钟", "超时", "自动关单", "最多买几张", "限购",
)


class KnowledgeSubState(TypedDict, total=False):
    query: str
    slots: SharedSlots
    history_summary: str
    rewritten_query: str
    retry_count: int
    hits: List[Dict[str, Any]]
    uncovered_items: List[str]
    evidence_ok: bool
    answer: str
    citations: List[str]
    refused: bool
    tool_steps: List[Dict[str, Any]]


async def query_rewrite_node(state: KnowledgeSubState) -> Dict[str, Any]:
    """【子图节点 1：query_rewrite】口语化问题改写与多轮指代补全；重试时自动扩展检索关键词。"""
    raw_q = (state.get("query") or "").strip()
    slots = state.get("slots") or {}
    retry = int(state.get("retry_count") or 0)

    # 剪掉与规则无关的后半句选座指令（例如“能退票吗？可以的话帮我推荐两张700以内的连座”）
    policy_focus = re.split(r"[，,。？?！!]\s*(?:可以的话|要是能退|如果可以|那帮我|顺便帮我)(?:推荐|选|买|锁)", raw_q)[0]
    if not policy_focus.strip():
        policy_focus = raw_q

    show_kw = slots.get("show_keyword") or ""
    if retry == 0:
        rewritten = f"{show_kw} {policy_focus}".strip() if show_kw and show_kw not in policy_focus else policy_focus
        if any(k in policy_focus for k in ("退款", "退单", "退订", "退钱", "全额退")) and "退票" not in policy_focus:
            rewritten = f"{rewritten} 退票 阶梯手续费".strip()
    else:
        # 第 2 次重试：追加规范票务术语扩大召回面
        rewritten = f"{policy_focus} 票务规则 条款 退改 退票 入场 场馆交通 须知"

    steps = list(state.get("tool_steps") or [])
    steps.append({
        "agent": "knowledge_agent",
        "tool": "query_rewrite",
        "args": {"rewritten": rewritten, "attempt": retry + 1},
    })
    emit_thought(
        key=f"kg-rewrite-{retry + 1}",
        phase="knowledge",
        agent_label="知识问答子图",
        title="口语消歧与检索词改写" if retry == 0 else "证据不足 · 扩展同义词二次检索",
        detail=f"提炼规则检索焦点：「{rewritten[:72]}」",
        badges=[f"Attempt #{retry + 1}"],
        status="done",
    )
    return {"rewritten_query": rewritten, "tool_steps": steps}


async def hybrid_retrieve_node(state: KnowledgeSubState) -> Dict[str, Any]:
    """【子图节点 2：hybrid_retrieve】执行 jieba BM25 + bge-m3/pgvector + RRF + Cross-Encoder Rerank 混合检索（无 Key 时降级 BM25-only）。"""
    rq = state.get("rewritten_query") or state.get("query") or ""
    retry = int(state.get("retry_count") or 0)
    binfo = getattr(hybrid_rag_engine, "backend_info", None) or {}
    if binfo.get("degraded"):
        pipeline_desc = "jieba BM25 关键词降级召回（未配置 Embedding Key）"
    else:
        emb_name = binfo.get("embedding", "BAAI/bge-m3")
        vec_store = binfo.get("vectorStore", "pgvector-hnsw")
        rerank_suffix = " + Cross-Encoder Rerank" if binfo.get("rerank") else ""
        pipeline_desc = f"jieba BM25 + {emb_name} 向量（{vec_store}）+ RRF{rerank_suffix}"
    emit_thought(
        key=f"kg-retrieve-{retry + 1}",
        phase="knowledge",
        agent_label="知识问答子图",
        title="正在执行 Hybrid RAG 双路召回…" if not binfo.get("degraded") else "正在执行 BM25 降级召回…",
        detail=f"执行 {pipeline_desc}",
        status="running",
    )
    rag_card_id = f"tool-rag-{retry + 1}"
    emit_tool_card(
        card_id=rag_card_id,
        tool_name="search_ticket_policy",
        title="检索官方票务知识库 (Hybrid RAG)",
        status="running",
        summary=f"正在执行 {pipeline_desc}…",
        input_data={"query": rq, "top_k": 3, "attempt": retry + 1},
    )
    hits = await hybrid_rag_engine.search(rq, top_k=3)

    top_code = hits[0]["docCode"] if hits else "NONE"
    top_title = hits[0]["docTitle"] if hits else "无命中"
    top_score = hits[0]["finalScore"] if hits else 0.0
    emit_thought(
        key=f"kg-retrieve-{retry + 1}",
        phase="knowledge",
        agent_label="知识问答子图",
        title=f"Hybrid RAG 命中《{top_title}》",
        detail=f"Top-1 条款 [{top_code}]（RRF 融合得分 {top_score}），共召回 {len(hits)} 个候选语义块",
        badges=[top_code, f"Score {top_score}"],
        status="done",
    )
    emit_tool_card(
        card_id=rag_card_id,
        tool_name="search_ticket_policy",
        title="检索官方票务知识库 (Hybrid RAG)",
        status="done",
        summary=(
            f"命中《{top_title}》[{top_code}]（融合得分 {top_score}，共 {len(hits)} 条）"
            if hits
            else "未检索到高置信度官方条款"
        ),
        input_data={"query": rq, "top_k": 3, "attempt": retry + 1},
        output_data={
            "backend": binfo,
            "hits": [
                {
                    "docCode": h.get("docCode"),
                    "docTitle": h.get("docTitle"),
                    "score": h.get("finalScore"),
                    "excerpt": str(h.get("content") or "")[:240],
                }
                for h in hits
            ],
        },
    )

    steps = list(state.get("tool_steps") or [])
    steps.append({
        "agent": "knowledge_agent",
        "tool": "search_ticket_policy",
        "args": {
            "query": rq,
            "attempt": retry + 1,
            "topDoc": hits[0]["docCode"] if hits else None,
            "topScore": hits[0]["finalScore"] if hits else 0.0,
        },
    })
    return {"hits": hits, "tool_steps": steps}


async def grade_evidence_node(state: KnowledgeSubState) -> Dict[str, Any]:
    """
    【子图节点 3：grade_evidence 证据充分性判定】：
    检查命中文档与用户问题的真实语义匹配度及未覆盖盲区；
    若证据不足则先触发 1 次改写重试，仍不足则强制路由至 refuse_answer 诚实拒答。
    """
    raw_q = state.get("query") or ""
    hits = state.get("hits") or []
    retry = int(state.get("retry_count") or 0)

    uncovered = sorted({desc for kw, desc in _UNCOVERED_TOPICS.items() if kw in raw_q})
    has_covered_anchor = any(a in raw_q for a in _COVERED_ANCHORS)

    top_score = float(hits[0]["finalScore"]) if hits else 0.0
    q_tokens = set(tokenize_zh(raw_q))
    chunk_tokens = set()
    for h in hits[:2]:
        chunk_tokens.update(tokenize_zh(h.get("content", "")))
    overlap_count = len(q_tokens & chunk_tokens)

    # 判断是否存在真实可支撑证据：必须有基本词项重合且得分达标，且不能只是纯盲区提问
    if uncovered and not has_covered_anchor:
        is_sufficient = False
    else:
        is_sufficient = bool(hits) and (
            (has_covered_anchor and top_score >= 0.25)
            or (top_score >= 0.48 and overlap_count >= 2)
        )

    if not is_sufficient and retry < 1:
        emit_thought(
            key="kg-grade-retry",
            phase="knowledge",
            agent_label="知识问答子图",
            title="初次检索未覆盖核心问题锚点，触发自反思重试",
            detail=f"检测到未覆盖细则（{'、'.join(uncovered) or '置信度偏低'}），返回改写节点扩充同义术语再检一次",
            badges=["Self-Reflect"],
            status="warn",
        )
        return {
            "evidence_ok": False,
            "uncovered_items": uncovered,
            "retry_count": retry + 1,
        }

    if is_sufficient:
        emit_thought(
            key="kg-grade-pass",
            phase="knowledge",
            agent_label="知识问答子图",
            title="证据充分性审查通过",
            detail="召回条款可准确支撑回答，正在撰写受控回复并注入强制条款引用标签…",
            badges=["Grounded"],
            status="running",
        )
    return {
        "evidence_ok": is_sufficient,
        "uncovered_items": uncovered,
    }


def _route_after_grade(state: KnowledgeSubState) -> str:
    if state.get("evidence_ok"):
        return "generate_answer"
    # 若刚刚在 grade 中把 retry_count 递增到了 1 且还没有跑第 2 次检索，看 tool_steps 里检索次数
    retrieve_runs = sum(1 for s in (state.get("tool_steps") or []) if s.get("tool") == "search_ticket_policy")
    if int(state.get("retry_count") or 0) >= 1 and retrieve_runs < 2:
        return "query_rewrite"
    return "refuse_answer"


async def generate_answer_node(state: KnowledgeSubState) -> Dict[str, Any]:
    """【子图节点 4A：generate_answer】严格基于证据生成回答，并强制校验 `[POL-xxx]` 条款编号引用。"""
    raw_q = state.get("query") or ""
    hits = state.get("hits") or []
    uncovered = state.get("uncovered_items") or []

    evidence_blocks = []
    available_codes: List[str] = []
    for h in hits:
        code = h["docCode"]
        if code not in available_codes:
            available_codes.append(code)
        evidence_blocks.append(f"[{code} 《{h['docTitle']}》]\n{h['content']}")
    evidence_text = "\n\n---\n\n".join(evidence_blocks)

    uncovered_note = ""
    if uncovered:
        uncovered_note = (
            f"\n注意：用户问题中同时提到的「{'、'.join(uncovered)}」未在官方知识库中收录，"
            "你必须对这部分明确说明“知识库暂未覆盖该细则，建议联系人工客服确认”，严禁自行编造！"
        )

    sys_prompt = (
        "你是 PulseTicket 知识问答专员（Knowledge Agent）。\n"
        "请严格且仅依据下方提供的【检索证据】回答用户的规则/交通/入场相关疑问：\n"
        "1. 必须在关键结论后明确标注条款编号（格式如 `[POL-REFUND-001]`）；\n"
        "2. 只回答规则/场馆/入场部分，选座与订单操作会由其他专员子图接着处理，无需重复寒暄；\n"
        "3. 严禁编造证据中不存在的数字、时间或政策。"
        + uncovered_note
    )

    recent_ctx = (state.get("history_summary") or "").strip()
    history_block = f"【最近 3 轮对话上下文】:\n{recent_ctx}\n\n" if recent_ctx else ""

    answer_text = ""
    try:
        llm = get_chat_llm(temperature=0.1)
        raw_ans = await astream_llm_text(
            llm,
            [
                SystemMessage(content=sys_prompt),
                HumanMessage(content=f"{history_block}【检索证据】:\n{evidence_text}\n\n【用户问题】:\n{raw_q}"),
            ],
        )
        answer_text = raw_ans.strip()
    except Exception:
        # 离线无 LLM Key 时使用证据摘要确定性输出
        top = hits[0]
        answer_text = f"根据官方规则 `[{top['docCode']}]`（《{top['docTitle']}》）：\n{top['content']}"
        if uncovered:
            answer_text += f"\n\n另外，关于「{'、'.join(uncovered)}」当前知识库暂未覆盖相关细则，请联系人工客服核实。"

    # 强校验：提取模型实际标出的 [POL-xxx]，若模型漏标则自动补上最高分证据编号
    found_codes = re.findall(r"POL-[A-Z]+-\d+", answer_text)
    if not found_codes and available_codes:
        primary_code = available_codes[0]
        suffix = f" `[{primary_code}]`"
        answer_text = f"{answer_text}{suffix}"
        emit_token(suffix)
        found_codes = [primary_code]

    dedup_codes = list(dict.fromkeys(found_codes))
    emit_thought(
        key="kg-grade-pass",
        phase="knowledge",
        agent_label="知识问答子图",
        title="规则解答生成与条款引用核验完成",
        detail=f"已基于官方条款生成答复，引用凭据：{', '.join(dedup_codes)}",
        badges=dedup_codes,
        status="done",
    )
    return {
        "answer": answer_text,
        "citations": dedup_codes,
        "refused": False,
    }


async def refuse_answer_node(state: KnowledgeSubState) -> Dict[str, Any]:
    """【子图节点 4B：refuse_answer】检索重试后证据仍不足时诚实拒答，零幻觉兜底。"""
    uncovered = state.get("uncovered_items") or []
    topic_str = f"（涉及：{'、'.join(uncovered)}）" if uncovered else ""
    emit_thought(
        key="kg-refuse",
        phase="knowledge",
        agent_label="知识问答子图",
        title="知识库未收录该细则 · 执行零幻觉诚实拒答",
        detail=f"经 2 轮检索仍无官方条款覆盖{'「' + '、'.join(uncovered) + '」' if uncovered else '该问题'}，已阻断模型猜测并引导人工客服",
        badges=["诚实拒答"],
        status="warn",
    )
    refusal = (
        f"抱歉，关于您咨询的该项细则{topic_str}，当前 PulseTicket 官方票务知识库暂未收录明确条款。"
        "为避免给您提供不准确的信息，建议您通过平台人工客服或场馆现场服务台进一步核实。"
    )
    steps = list(state.get("tool_steps") or [])
    steps.append({
        "agent": "knowledge_agent",
        "tool": "refuse_insufficient_evidence",
        "args": {"uncovered": uncovered},
    })
    return {
        "answer": refusal,
        "citations": [],
        "refused": True,
        "tool_steps": steps,
    }


def build_knowledge_subgraph():
    """构建并编译子 Agent ①：知识问答 Agentic RAG 子图"""
    builder = StateGraph(KnowledgeSubState)
    builder.add_node("query_rewrite", query_rewrite_node)
    builder.add_node("hybrid_retrieve", hybrid_retrieve_node)
    builder.add_node("grade_evidence", grade_evidence_node)
    builder.add_node("generate_answer", generate_answer_node)
    builder.add_node("refuse_answer", refuse_answer_node)

    builder.add_edge(START, "query_rewrite")
    builder.add_edge("query_rewrite", "hybrid_retrieve")
    builder.add_edge("hybrid_retrieve", "grade_evidence")
    builder.add_conditional_edges(
        "grade_evidence",
        _route_after_grade,
        {
            "query_rewrite": "query_rewrite",
            "generate_answer": "generate_answer",
            "refuse_answer": "refuse_answer",
        },
    )
    builder.add_edge("generate_answer", END)
    builder.add_edge("refuse_answer", END)
    return builder.compile()

import json
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.graph.state import SharedSlots, get_chat_llm
from app.graph.thought_stream import astream_llm_message, emit_thought, emit_tool_card, emit_widget
from app.tools.ticket_tools import (
    PURCHASE_READ_TOOLS,
    PURCHASE_WRITE_TOOLS,
    _clean_seat_label,
    current_user_ctx,
    quote_seats_from_server,
)

_PURCHASE_READ_MAP = {t.name: t for t in PURCHASE_READ_TOOLS}
_PURCHASE_ALL_TOOLS = PURCHASE_READ_TOOLS + PURCHASE_WRITE_TOOLS
_MAX_PURCHASE_STEPS = 4


class PurchaseSubState(TypedDict, total=False):
    sub_messages: Annotated[List[BaseMessage], add_messages]
    query: str
    history_summary: str
    mode: str
    slots: SharedSlots
    user_id: int
    username: str
    user_role: str
    step_count: int
    answer: str
    tool_steps: List[Dict[str, Any]]
    pending_action: Optional[Dict[str, Any]]


def _get_purchase_llm():
    return get_chat_llm(temperature=0.1).bind_tools(_PURCHASE_ALL_TOOLS)


def _get_purchase_read_llm():
    return get_chat_llm(temperature=0.1).bind_tools(PURCHASE_READ_TOOLS)


def _reply_all(
    tool_calls: List[Dict[str, Any]],
    primary_id: Optional[str],
    primary_msg: Optional[ToolMessage],
    skip_note: str = "同轮其余工具调用已跳过。",
) -> List[ToolMessage]:
    out: List[ToolMessage] = []
    for tc in tool_calls:
        tid = tc["id"]
        tname = tc.get("name", "unknown")
        if primary_id is not None and tid == primary_id and primary_msg is not None:
            out.append(primary_msg)
        else:
            out.append(
                ToolMessage(
                    content=json.dumps({"status": "SKIPPED", "message": skip_note}, ensure_ascii=False),
                    name=tname,
                    tool_call_id=tid,
                )
            )
    return out


async def purchase_agent_node(state: PurchaseSubState) -> Dict[str, Any]:
    """
    【子图 ② 节点 1：purchase_agent】
    只拥有选座域工具（search_shows / recommend_seats / propose_create_order）：
    - 若 wants_commit=False（仅咨询/推荐），严禁提前发起 propose_create_order；
    - 若 wants_commit=True 且已有推荐座位槽位（session_id + seat_ids），可直接发起锁座核价。
    """
    slots = dict(state.get("slots") or {})
    mode = (state.get("mode") or "agent").strip().lower()
    is_chat_mode = mode == "chat"
    wants_commit = False if is_chat_mode else bool(slots.get("wants_commit"))
    step_count = int(state.get("step_count") or 0)

    if is_chat_mode:
        commit_rule = (
            "当前处于 Chat 只读咨询模式：仅允许调用 `search_shows` 与 `recommend_seats` 查询并回答咨询；"
            "严禁发起锁座下单，若用户要求锁座下单请友好提示其切换左下角【Agent 模式】执行。"
        )
    elif wants_commit:
        commit_rule = (
            "用户本轮已明确要求锁座/下单！\n"
            "- 若共享槽位中已有精确对应的 `session_id` 与 `seat_ids`（注意：若本轮指定了 `preferred_row`/`preferred_col` 且 `seat_ids` 尚未查出，则先调 `recommend_seats(session_id, row_no, col_no, count=1)` 获取真实数据库 `seatIds`），拿到 `seatIds` 后立即在下一步调用 `propose_create_order`；\n"
            "- 若 `recommend_seats` 返回该指定座位已售/已锁 (`found=False`)，请如实告知用户当前状态并推荐同排相邻余座，切勿强行下单。"
        )
    else:
        commit_rule = "用户本轮仅咨询演出或请求推荐座位（尚未确认立即下单），请只调用 `search_shows` 和 `recommend_seats` 给出清晰推荐方案，**严禁在本轮调用 `propose_create_order`**！"

    sys_text = (
        "你是 PulseTicket 的【选座购票专员 (Purchase Agent)】。\n"
        f"【共享上下文槽位】: {json.dumps(slots, ensure_ascii=False)}\n"
        f"【历史对话参考（仅用于理解指代，回答必须严格紧扣下方当前用户的最新提问，严禁错答上一轮诉求）】:\n{state.get('history_summary') or '无'}\n"
        f"【执行约束】: {commit_rule}\n"
        "选座工具传参与回复风格规范：\n"
        "1. 若共享槽位中已确定 `session_id` 且用户是在调整预算/切换体验/指定排座，**无需重复调用 `search_shows`**，直接调用 `recommend_seats`；\n"
        "2. 调用 `recommend_seats` 时必须严格对齐共享槽位：\n"
        "   - 若有 `preferred_row` / `preferred_col`（如“第一排2号座位”），务必传入 `row_no` 与 `col_no`（精确查库拿真实 `seatId`）；\n"
        "   - 将 `sort_by` 设为 `slots.price_preference`（便宜/性价比传 `'lowest_price'`，最佳观看体验/VIP 传 `'highest_price'`，未指定传 `'best_view'`）；\n"
        "   - 将 `position` 设为 `slots.position_preference`（靠边/过道传 `'side'`，中间/中轴传 `'center'`）；\n"
        "   - 将 `max_unit_price` 设为 `slots.max_unit_price`（若已清空则不要传旧上限）；\n"
        "3. 回复风格：直接、自然地回应用户本轮具体诉求（如靠边低价、切换最佳视野或确认具体排座），如果前面轮次已经介绍过该演出的场馆和票价区间，**不要每轮重复背诵一整段演出基础模板**；简明给出本轮新推荐的座位、价格与其余在售票档即可；\n"
        "4. 严禁在同一条回复里同时混调只读工具和 `propose_create_order`；若后端服务不可达或目标座位已售/无余座，必须如实说明。"
    )

    # 【关键 Bug 修复：防止多轮 ReAct 出现“滞后一轮回复”】
    # 根因回顾：此前 Step 1 返回 {"sub_messages": [resp]} 只保存了 AIMessage(tool_calls)，未保存 HumanMessage(query)；
    # 导致 Step 2 进入 else 分支重新拼装 msgs 时，列表变成 [SystemMessage, AIMessage, ToolMessage]，
    # 本轮用户的 HumanMessage 彻底丢失！大模型在写最终回复时只能看到 SystemMessage 里的“历史对话”，
    # 从而每次都在回答上一轮的问题（慢一拍）。
    # 修复方案：无论 Step 1 还是 Step 2+，始终把当前最新一轮的 HumanMessage(current_query) 固定锚定在
    # SystemMessage 之后、工具轨迹 (AIMessage / ToolMessage) 之前，严格保持标准
    # [System -> User(本轮最新输入) -> Assistant(tool_calls) -> Tool -> ...] 对话拓扑。
    current_query = state.get("query") or ""
    tool_history = [
        m for m in (state.get("sub_messages") or [])
        if not isinstance(m, (SystemMessage, HumanMessage))
    ]
    msgs = [
        SystemMessage(content=sys_text),
        HumanMessage(content=current_query),
    ] + tool_history

    emit_thought(
        key=f"pur-reason-{step_count + 1}",
        phase="purchase",
        agent_label="选座购票子图",
        title="正在规划演出查询与选座动作…" if step_count == 0 else "正在汇总实时余座与比价推荐方案…",
        detail="结合预算与张数约束推理下一步只读工具或核价提案",
        status="running",
    )

    # 达到单轮最大工具步数上限时不再绑定工具，强制收敛总结回复防止死循环
    if step_count >= _MAX_PURCHASE_STEPS:
        resp = await astream_llm_message(get_chat_llm(temperature=0.1), msgs, allow_stream=True)
    else:
        llm = _get_purchase_read_llm() if is_chat_mode else _get_purchase_llm()
        resp = await astream_llm_message(llm, msgs, allow_stream=(not wants_commit))

    tcs = getattr(resp, "tool_calls", None) or []
    if tcs:
        planned = ", ".join(tc.get("name", "") for tc in tcs)
        emit_thought(
            key=f"pur-reason-{step_count + 1}",
            phase="purchase",
            agent_label="选座购票子图",
            title=f"决策调用选座工具：{planned}",
            detail="向 Java show-service 获取实时场次与 48 座矩阵库存",
            badges=[tc.get("name", "") for tc in tcs],
            status="done",
        )
    else:
        emit_thought(
            key=f"pur-reason-{step_count + 1}",
            phase="purchase",
            agent_label="选座购票子图",
            title="已完成最佳连座与备选方案整理",
            detail="已将首选推荐席位记忆至跨轮共享槽位 slots，方便后续一键锁座",
            status="done",
        )

    answer = str(resp.content or "").strip() if not tcs else state.get("answer", "")
    return {
        "sub_messages": [resp],
        "step_count": step_count + 1,
        "answer": answer,
    }


async def purchase_read_tools_node(state: PurchaseSubState) -> Dict[str, Any]:
    """【子图 ② 节点 2：purchase_read_tools】执行查演出与连座推荐，并把推荐结果写回共享槽位 `slots`。"""
    last_msg = state["sub_messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    slots = dict(state.get("slots") or {})
    mode = (state.get("mode") or "agent").strip().lower()
    steps = list(state.get("tool_steps") or [])

    token = current_user_ctx.set({
        "user_id": state["user_id"],
        "username": state["username"],
        "role": state.get("user_role", "USER"),
    })
    out_msgs: List[ToolMessage] = []
    try:
        for tc in tool_calls:
            t_name = tc["name"]
            t_args = tc.get("args") or {}
            t_id = tc["id"]

            effective_args = dict(t_args)
            if t_name == "recommend_seats":
                # 【确定性槽位回填】：防止 LLM 漏传靠边偏好、价格偏好、张数或精确排座坐标
                if effective_args.get("row_no") is None and slots.get("preferred_row") is not None:
                    effective_args["row_no"] = int(slots["preferred_row"])
                if effective_args.get("col_no") is None and slots.get("preferred_col") is not None:
                    effective_args["col_no"] = int(slots["preferred_col"])

                is_exact_lookup = (
                    effective_args.get("row_no") is not None
                    and effective_args.get("col_no") is not None
                )
                if is_exact_lookup:
                    # 精确查指定排座（如“第一排2号座”）时，强制按 1 座查询并忽略历史残留的低价上限
                    effective_args["count"] = 1
                    effective_args.pop("max_unit_price", None)
                else:
                    if effective_args.get("count") is None and slots.get("seat_count"):
                        effective_args["count"] = int(slots["seat_count"])
                    if effective_args.get("max_unit_price") is None and slots.get("max_unit_price") is not None:
                        effective_args["max_unit_price"] = float(slots["max_unit_price"])
                    # 以本轮路由槽位的价格与位置偏好为准，防止 LLM 受上一轮历史文案惯性继续传错 sort_by/position
                    if slots.get("price_preference"):
                        effective_args["sort_by"] = str(slots["price_preference"])
                    if slots.get("position_preference"):
                        effective_args["position"] = str(slots["position_preference"])

            steps.append({"agent": "purchase_agent", "tool": t_name, "args": effective_args})

            if t_name in _PURCHASE_READ_MAP:
                card_id = f"tool-{t_name}-{t_id}"
                if t_name == "search_shows":
                    card_title = "查询在售演出与场次库存"
                    thought_running = "正在查询在售演出目录…"
                elif effective_args.get("row_no") is not None and effective_args.get("col_no") is not None:
                    r_v = int(effective_args["row_no"])
                    c_v = int(effective_args["col_no"])
                    card_title = f"查询场次 #{effective_args.get('session_id')} {r_v}排{c_v:02d}座实时状态"
                    thought_running = f"正在核验场次 #{effective_args.get('session_id')} {r_v}排{c_v:02d}座余座与票价…"
                elif effective_args.get("position") == "side":
                    card_title = f"智能推荐场次 #{effective_args.get('session_id')} 靠边/过道席位"
                    thought_running = f"正在计算场次 #{effective_args.get('session_id')} 靠边最优席位…"
                else:
                    card_title = f"智能推荐场次 #{effective_args.get('session_id')} 最佳连座"
                    thought_running = f"正在计算场次 #{effective_args.get('session_id')} 最佳连座方案…"
                emit_thought(
                    key=f"pur-tool-{t_id}",
                    phase="purchase",
                    agent_label="选座购票子图",
                    title=thought_running,
                    detail=json.dumps(effective_args, ensure_ascii=False),
                    status="running",
                )
                emit_tool_card(
                    card_id=card_id,
                    tool_name=t_name,
                    title=card_title,
                    status="running",
                    summary="正在向 Java show-service 获取实时数据…",
                    input_data=effective_args,
                )
                obs = await _PURCHASE_READ_MAP[t_name].ainvoke(effective_args)
                obs_str = str(obs)
                out_msgs.append(ToolMessage(content=obs_str, name=t_name, tool_call_id=t_id))

                if t_name == "search_shows":
                    parsed_shows = None
                    try:
                        json_part = obs_str.split("\n", 1)[1] if obs_str.startswith("[数据源:") and "\n" in obs_str else obs_str
                        parsed_shows = json.loads(json_part)
                    except Exception:
                        parsed_shows = None

                    show_ok = isinstance(parsed_shows, list)
                    show_cnt = len(parsed_shows) if show_ok else 0
                    card_status = "done" if show_ok else ("error" if isinstance(parsed_shows, dict) and parsed_shows.get("ok") is False else "done")
                    card_summary = (
                        f"已查询到 {show_cnt} 部在售演出及场次库存"
                        if show_ok
                        else (parsed_shows.get("error", "演出查询异常") if isinstance(parsed_shows, dict) else "演出目录拉取完成")
                    )
                    emit_thought(
                        key=f"pur-tool-{t_id}",
                        phase="purchase",
                        agent_label="选座购票子图",
                        title="已拉取在售演出与排期场次",
                        detail=f"查询关键词：{t_args.get('keyword') or '全部在售'}",
                        badges=["show-service"],
                        status="done" if card_status == "done" else "warn",
                    )
                    emit_tool_card(
                        card_id=card_id,
                        tool_name=t_name,
                        title=card_title,
                        status=card_status,
                        summary=card_summary,
                        input_data=t_args,
                        output_data=parsed_shows if parsed_shows is not None else obs_str,
                    )
                    if mode != "chat" and not slots.get("wants_commit") and isinstance(parsed_shows, list) and parsed_shows:
                        seat_cnt = int(slots.get("seat_count") or 2)
                        show_options = []
                        for idx, sh in enumerate(parsed_shows[:4]):
                            s_list = sh.get("sessions") or []
                            first_sess = s_list[0] if s_list else {}
                            sid = first_sess.get("sessionId")
                            s_title = first_sess.get("sessionTitle") or "在线排期"
                            prompt_str = (
                                f"帮我推荐《{sh.get('title')}》场次 session_id={sid} 的 {seat_cnt} 张最佳中间连座"
                                if sid
                                else f"查询《{sh.get('title')}》场次与可用座位"
                            )
                            show_options.append({
                                "id": f"show-{sh.get('showId') or idx}",
                                "label": str(sh.get("title") or "在售演出"),
                                "badge": str(sh.get("city") or "在售"),
                                "meta": f"¥{sh.get('minPrice')}~{sh.get('maxPrice')} · {s_title}",
                                "price": sh.get("minPrice"),
                                "prompt": prompt_str,
                            })
                        emit_widget(
                            widget_id=f"widget-shows-{t_id}",
                            widget_type="SHOW_PICKER",
                            title="请选择想查看连座的演出",
                            subtitle="点击选项将自动为您检索最佳中间连座方案",
                            options=show_options,
                        )

                # 自动将最新推荐出来的最佳连座与推荐单价记忆到跨轮共享槽位 slots，方便下一轮“有便宜一点的吗 / 就锁这两张”无缝衔接
                if t_name == "recommend_seats":
                    try:
                        parsed = json.loads(obs_str)
                        rec = parsed.get("recommended")
                        if parsed.get("found") and isinstance(rec, dict):
                            sid_val = int(parsed.get("sessionId") or t_args.get("session_id"))
                            slots["session_id"] = sid_val
                            slots["seat_ids"] = [int(x) for x in (rec.get("seatIds") or [])]
                            if slots["seat_ids"]:
                                slots["seat_count"] = len(slots["seat_ids"])
                            clean_rec_labels = [
                                _clean_seat_label(rec.get("zoneName"), lb)
                                for lb in (rec.get("seatLabels") or [])
                            ]
                            slots["seat_labels"] = clean_rec_labels
                            if rec.get("unitPrice") is not None:
                                slots["last_unit_price"] = float(rec["unitPrice"])
                            labels_str = "、".join(clean_rec_labels)
                            is_exact_hit = bool(parsed.get("exactMatch"))
                            pos_used = parsed.get("position") or effective_args.get("position") or "center"
                            if is_exact_hit:
                                score_note = f"精确匹配真实席位 ID {slots['seat_ids']}"
                                done_title = f"场次 #{slots['session_id']} 已核验指定席位：{labels_str}"
                                done_summary = f"已核验 {rec.get('zoneName')} {labels_str} 可售（单价 ¥{rec.get('unitPrice')}）"
                            elif pos_used == "side":
                                score_note = f"靠边偏移分 {rec.get('sideScore')}"
                                done_title = f"场次 #{slots['session_id']} 靠边优选：{labels_str}"
                                done_summary = f"已推荐靠边席位 {rec.get('zoneName')} {labels_str}（单价 ¥{rec.get('unitPrice')} · 合计 ¥{rec.get('totalAmount')}）"
                            else:
                                score_note = f"中轴线偏移分 {rec.get('centerScore')}"
                                done_title = f"场次 #{slots['session_id']} 最优方案：{labels_str}"
                                done_summary = f"已推荐 {rec.get('zoneName')} {labels_str}（单价 ¥{rec.get('unitPrice')} · 合计 ¥{rec.get('totalAmount')}）"
                            emit_thought(
                                key=f"pur-tool-{t_id}",
                                phase="purchase",
                                agent_label="选座购票子图",
                                title=done_title,
                                detail=(
                                    f"区域：{rec.get('zoneName')} · 单价 ¥{rec.get('unitPrice')} · 合计 ¥{rec.get('totalAmount')} "
                                    f"（{score_note}）"
                                ),
                                badges=[f"#{slots['session_id']}", f"¥{rec.get('totalAmount')}"],
                                status="done",
                            )
                            emit_tool_card(
                                card_id=card_id,
                                tool_name=t_name,
                                title=card_title,
                                status="done",
                                summary=done_summary,
                                input_data=effective_args,
                                output_data=parsed,
                            )
                            if mode != "chat" and not slots.get("wants_commit"):
                                candidates = [rec] + list(parsed.get("alternatives") or [])
                                sort_pref = parsed.get("sortBy") or effective_args.get("sort_by") or "best_view"
                                pos_pref = parsed.get("position") or effective_args.get("position") or "center"
                                is_exact_hit = bool(parsed.get("exactMatch"))
                                rec_unit = rec.get("unitPrice")
                                seat_options = []
                                for idx, opt in enumerate(candidates[:3]):
                                    opt_ids = [int(x) for x in (opt.get("seatIds") or [])]
                                    z_name = str(opt.get("zoneName") or "").strip()
                                    opt_labels = [
                                        _clean_seat_label(z_name, lb)
                                        for lb in (opt.get("seatLabels") or [])
                                    ]
                                    labels_joined = "、".join(opt_labels)
                                    opt_unit = opt.get("unitPrice")
                                    if idx == 0:
                                        if is_exact_hit:
                                            badge_txt = "指定席位"
                                        elif pos_pref == "side":
                                            badge_txt = "靠边低价" if sort_pref == "lowest_price" else "靠边优选"
                                        elif sort_pref == "lowest_price":
                                            badge_txt = "低价优选"
                                        else:
                                            badge_txt = "最佳中轴"
                                    elif opt_unit is not None and opt_unit != rec_unit:
                                        badge_txt = f"¥{int(float(opt_unit))} 档"
                                    else:
                                        badge_txt = f"备选 #{idx}"
                                    seat_options.append({
                                        "id": f"seat-{sid_val}-{'-'.join(str(x) for x in opt_ids)}",
                                        "label": f"{z_name} · {labels_joined}".strip(" ·"),
                                        "badge": badge_txt,
                                        "meta": f"单价 ¥{opt_unit} · 合计 ¥{opt.get('totalAmount')}",
                                        "price": opt.get("totalAmount"),
                                        "prompt": f"请帮我锁定场次 session_id={sid_val} seat_ids={opt_ids}（{z_name} {labels_joined}）并下单",
                                    })
                                if seat_options:
                                    emit_widget(
                                        widget_id=f"widget-seats-{t_id}",
                                        widget_type="SEAT_PICKER",
                                        title=f"选择推荐席位快速锁座（{parsed.get('showTitle') or ''}）",
                                        subtitle=f"{parsed.get('sessionTitle') or ''} · 点击即进入 Java 服务端实时核价与人工确认",
                                        options=seat_options,
                                        replace_types=["SHOW_PICKER"],
                                    )
                        else:
                            msg_fail = parsed.get("message", "余座不足")
                            emit_thought(
                                key=f"pur-tool-{t_id}",
                                phase="purchase",
                                agent_label="选座购票子图",
                                title=f"场次 #{effective_args.get('session_id')} 目标席位不可用或预算内余座不足",
                                detail=msg_fail,
                                status="warn",
                            )
                            emit_tool_card(
                                card_id=card_id,
                                tool_name=t_name,
                                title=card_title,
                                status="error" if parsed.get("ok") is False else "done",
                                summary=msg_fail,
                                input_data=effective_args,
                                output_data=parsed,
                            )
                            # 若用户指定了精确坐标但该座已锁/已售，且有同排相邻备选席位，顺手推一张快捷替代选座卡
                            fallback_alts = list(parsed.get("alternatives") or [])
                            if mode != "chat" and fallback_alts:
                                sid_val = int(parsed.get("sessionId") or effective_args.get("session_id") or 0)
                                alt_options = []
                                for idx, opt in enumerate(fallback_alts[:3]):
                                    opt_ids = [int(x) for x in (opt.get("seatIds") or [])]
                                    z_name = str(opt.get("zoneName") or "").strip()
                                    opt_labels = [
                                        _clean_seat_label(z_name, lb)
                                        for lb in (opt.get("seatLabels") or [])
                                    ]
                                    labels_joined = "、".join(opt_labels)
                                    alt_options.append({
                                        "id": f"seat-{sid_val}-{'-'.join(str(x) for x in opt_ids)}",
                                        "label": f"{z_name} · {labels_joined}".strip(" ·"),
                                        "badge": f"相邻备选 #{idx + 1}",
                                        "meta": f"单价 ¥{opt.get('unitPrice')} · 合计 ¥{opt.get('totalAmount')}",
                                        "price": opt.get("totalAmount"),
                                        "prompt": f"请帮我锁定场次 session_id={sid_val} seat_ids={opt_ids}（{z_name} {labels_joined}）并下单",
                                    })
                                if alt_options and sid_val:
                                    emit_widget(
                                        widget_id=f"widget-seats-alt-{t_id}",
                                        widget_type="SEAT_PICKER",
                                        title=f"目标座位不可用 · 同排相邻余座推荐（{parsed.get('showTitle') or ''}）",
                                        subtitle=f"{parsed.get('sessionTitle') or ''} · 点击可直接锁定备选席位",
                                        options=alt_options,
                                        replace_types=["SHOW_PICKER"],
                                    )
                    except Exception:
                        emit_tool_card(
                            card_id=card_id,
                            tool_name=t_name,
                            title=card_title,
                            status="done",
                            summary="连座计算完成",
                            input_data=t_args,
                            output_data=obs_str,
                        )
            else:
                out_msgs.append(
                    ToolMessage(
                        content=json.dumps({"status": "ERROR", "message": f"非选座只读工具: {t_name}"}, ensure_ascii=False),
                        name=t_name,
                        tool_call_id=t_id,
                    )
                )
    finally:
        current_user_ctx.reset(token)

    return {
        "sub_messages": out_msgs,
        "slots": slots,
        "tool_steps": steps,
    }


async def server_quote_node(state: PurchaseSubState, config: RunnableConfig) -> Dict[str, Any]:
    """
    【子图 ② 节点 3：server_quote 确定性核价节点】：
    向 Java show-service 实时核验余座状态并计算权威总价（完全覆盖/忽略任何模型猜测），
    通过后生成 pending_action 交由主图触发 interrupt() 人工确认。
    """
    last_msg = state["sub_messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []
    write_tc = next((tc for tc in tool_calls if tc["name"] == "propose_create_order"), None)
    steps = list(state.get("tool_steps") or [])

    if write_tc is None:
        return {
            "sub_messages": _reply_all(tool_calls, None, None, "未找到锁座提案"),
            "pending_action": None,
        }

    t_args = write_tc.get("args") or {}
    t_id = write_tc["id"]
    thread_id = str((config.get("configurable") or {}).get("thread_id") or "")
    idempotency_key = f"{thread_id}:{t_id}" if thread_id else t_id

    steps.append({"agent": "purchase_agent", "tool": "propose_create_order", "args": t_args})
    quote_card_id = f"tool-quote-{t_id}"
    emit_thought(
        key=f"pur-quote-{t_id}",
        phase="quote",
        agent_label="核价门控",
        title="正在请求 Java 服务端实时核价与余座校验…",
        detail=f"校验场次 #{t_args.get('session_id')} 席位 {t_args.get('seat_ids')} 状态并由 BigDecimal 计算应付总额",
        status="running",
    )
    emit_tool_card(
        card_id=quote_card_id,
        tool_name="server_quote",
        title="Java 服务端权威核价与余座校验",
        status="running",
        summary=f"正在实时核验场次 #{t_args.get('session_id')} 席位状态与票价…",
        input_data={"session_id": t_args.get("session_id"), "seat_ids": t_args.get("seat_ids")},
    )

    quote_res = await quote_seats_from_server(t_args.get("session_id"), t_args.get("seat_ids"))
    steps.append({
        "agent": "purchase_agent",
        "tool": "server_quote",
        "args": {
            "ok": bool(quote_res.get("ok")),
            "totalAmount": (quote_res.get("quote") or {}).get("totalAmount"),
        },
    })

    if not quote_res.get("ok"):
        emit_thought(
            key=f"pur-quote-{t_id}",
            phase="quote",
            agent_label="核价门控",
            title="服务端核价未通过 · 已拦截确认卡片",
            detail=quote_res.get("message", "座位已不可用"),
            badges=["409 拦截"],
            status="warn",
        )
        emit_tool_card(
            card_id=quote_card_id,
            tool_name="server_quote",
            title="Java 服务端权威核价与余座校验",
            status="error",
            summary=quote_res.get("message", "服务端核价未通过"),
            input_data={"session_id": t_args.get("session_id"), "seat_ids": t_args.get("seat_ids")},
            output_data=quote_res,
        )
        err_msg = ToolMessage(
            content=json.dumps(
                {"status": "QUOTE_FAILED", "message": quote_res.get("message", "服务端核价未通过")},
                ensure_ascii=False,
            ),
            name="propose_create_order",
            tool_call_id=t_id,
        )
        return {
            "sub_messages": _reply_all(tool_calls, t_id, err_msg),
            "tool_steps": steps,
            "pending_action": None,
        }

    quote = quote_res["quote"]
    emit_thought(
        key=f"pur-quote-{t_id}",
        phase="quote",
        agent_label="核价门控",
        title=f"Java 权威核价通过：¥{quote['totalAmount']}",
        detail=(
            f"席位 {'、'.join(quote['seatLabels'])} 均处于 AVAILABLE 状态；"
            f"已签发幂等键并挂起状态图等待人工点击确认"
        ),
        badges=[f"¥{quote['totalAmount']}", "Interrupt 挂起"],
        status="done",
    )
    emit_tool_card(
        card_id=quote_card_id,
        tool_name="server_quote",
        title="Java 服务端权威核价与余座校验",
        status="done",
        summary=f"核价通过：{'、'.join(quote['seatLabels'])}，应付总额 ¥{quote['totalAmount']}",
        input_data={"session_id": t_args.get("session_id"), "seat_ids": t_args.get("seat_ids")},
        output_data=quote_res,
    )
    verified_args = {
        "session_id": quote["sessionId"],
        "seat_ids": quote["seatIds"],
        "seat_labels": quote["seatLabels"],
        "show_title": quote["showTitle"],
        "session_title": quote["sessionTitle"],
        "venue_name": quote["venueName"],
        "total_amount": quote["totalAmount"],
    }
    card_payload = {
        "actionType": "CREATE_ORDER",
        "toolName": "propose_create_order",
        "toolCallId": t_id,
        "idempotencyKey": idempotency_key,
        "title": "确认锁座下单",
        "isMock": bool(quote_res.get("isMock")),
        "priceSource": quote_res.get("source"),
        "args": verified_args,
    }

    slots = dict(state.get("slots") or {})
    slots["session_id"] = quote["sessionId"]
    slots["seat_ids"] = quote["seatIds"]
    slots["seat_labels"] = quote["seatLabels"]

    return {
        "slots": slots,
        "tool_steps": steps,
        "pending_action": {
            "toolName": "propose_create_order",
            "toolCallId": t_id,
            "allToolCalls": tool_calls,
            "idempotencyKey": idempotency_key,
            "execArgs": verified_args,
            "cardPayload": card_payload,
        },
    }


def _route_from_purchase_agent(state: PurchaseSubState) -> str:
    msgs = state.get("sub_messages") or []
    if not msgs:
        return END
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", []) or []
    if not tcs:
        return END
    if any(tc["name"] == "propose_create_order" for tc in tcs):
        return "server_quote"
    return "purchase_read_tools"


def _route_after_quote(state: PurchaseSubState) -> str:
    if state.get("pending_action"):
        return END
    return "purchase_agent"


def build_purchase_subgraph():
    """
    构建并编译子 Agent ②：选座购票子图（只读推荐与服务端确定性核价分离，严禁模型直接写库）。

    内部有向图流转：
      START -> purchase_agent(LLM 推理：Chat模式仅绑只读工具，Agent模式加绑提案工具)
            ├── 调 search_shows / recommend_seats -> purchase_read_tools(调 Java 查余座 + 写回 SharedSlots + 推 PICKER) -> 回到 purchase_agent
            ├── 调 propose_create_order -> server_quote(绕过模型直接调 Java 权威核价与余座检验)
            │      ├── 余座被抢/过期 -> 报错 ToolMessage 退回 purchase_agent 说明
            │      └── 核价通过 -> 打包 pending_action -> END (交由主图 confirm_action 触发 interrupt 挂起等确认)
            └── 无工具调用或达步数上限 -> END
    """
    builder = StateGraph(PurchaseSubState)
    builder.add_node("purchase_agent", purchase_agent_node)
    builder.add_node("purchase_read_tools", purchase_read_tools_node)
    builder.add_node("server_quote", server_quote_node)

    builder.add_edge(START, "purchase_agent")
    builder.add_conditional_edges(
        "purchase_agent",
        _route_from_purchase_agent,
        {
            "purchase_read_tools": "purchase_read_tools",
            "server_quote": "server_quote",
            END: END,
        },
    )
    builder.add_edge("purchase_read_tools", "purchase_agent")
    builder.add_conditional_edges(
        "server_quote",
        _route_after_quote,
        {
            "purchase_agent": "purchase_agent",
            END: END,
        },
    )
    return builder.compile()

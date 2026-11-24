"""
【PulseTicket 智能体业务工具箱（Tools & 确定性校验函数）】

通俗理解：这是 Python Agent 连接 Java 微服务的“手脚”——智能体不直连业务 MySQL，所有查演出、找连座、核价、查单、算退费、锁座下单、取消与退票，全通过本文件经 HTTP 调用 `show-service` 和 `order-service`。

重要安全分工：
- 只读工具（`@tool`）：`search_ticket_policy`、`search_shows`、`recommend_seats`、`list_my_orders`、`check_order_refund_detail`；
- 写操作提案工具（`@tool`）：`propose_create_order`、`propose_cancel_order`、`propose_refund_order`（仅负责向图状态提交草案，绝不直接改库！）；
- 确定性校验与真写函数（普通 Python 函数，不暴露给 LLM 直接调）：`quote_seats_from_server`（服务端权威核价）、`inspect_order_for_cancel`（售后资格与退票费精算）、`execute_confirmed_write_action`（仅在用户点击确认卡片后，携带幂等键真正调 Java 写接口）。
"""

import contextvars
from datetime import datetime
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
import httpx
from langchain_core.tools import tool

from app.config import settings
from app.rag.hybrid_rag import hybrid_rag_engine

# ==============================================================================
# 【前后端与 Agent 职责边界说明】
# Python Agent 服务本身不直连业务 MySQL 库：
#   - 演出目录、48 座矩阵库存、权威核价：统一经 HTTP 调用 Java show-service (:8202)
#   - 查询个人订单、锁座创单、取消未支付单、阶梯退票：统一经 HTTP 调用 Java order-service (:8203)
# Python 侧只做：意图路由、RAG 条款检索、连座筛选与写前确定性预校验，彻底杜绝大模型篡改票价或越权写库。
# ==============================================================================

# 请求级上下文变量：禁止设置任何默认用户（缺失时严格拒绝服务，杜绝越权替 1 号用户下单）
current_user_ctx: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "current_user_ctx",
    default=None,
)


def _clean_seat_label(zone_name: Any, raw_label: Any) -> str:
    """去掉座位名称里重复的区域前缀（如把 '内场全息至尊区 1排04座' 规范为 '1排04座'）。"""
    z = str(zone_name or "").strip()
    lbl = str(raw_label or "").strip()
    if z and lbl.startswith(z):
        stripped = lbl[len(z):].strip(" ·-")
        if stripped:
            return stripped
    return lbl


def _format_full_seat_label(zone_name: Any, raw_label: Any) -> str:
    """统一拼接 '区域名 排座号'，避免出现两次重复区域名。"""
    z = str(zone_name or "").strip()
    short_lbl = _clean_seat_label(z, raw_label)
    return f"{z} {short_lbl}".strip() if z else short_lbl


def get_required_user() -> Dict[str, Any]:
    """读取当前请求绑定用户身份；缺失或非法时直接抛出 PermissionError。"""
    user = current_user_ctx.get()
    if not isinstance(user, dict):
        raise PermissionError("缺少用户身份上下文，请登录后通过网关访问")
    user_id = user.get("user_id")
    username = (user.get("username") or "").strip()
    if not isinstance(user_id, int) or user_id <= 0 or not username:
        raise PermissionError("用户身份上下文无效 (user_id / username)")
    return {
        "user_id": user_id,
        "username": username,
        "role": (user.get("role") or "USER").strip() or "USER",
    }


def _build_user_headers() -> Dict[str, str]:
    user = get_required_user()
    return {
        "X-User-Id": str(user["user_id"]),
        "X-Username": str(user["username"]),
        "X-User-Role": str(user["role"]),
    }


# 仅在显式开启 DEV_MOCK=true 时使用的离线联调数据（字段与 deploy/sql/init_databases.sql 对齐）
_MOCK_SHOWS = [
    {
        "showId": 1,
        "title": "「星际回响 · PulseLive」2026 全息交响电音巡演",
        "category": "CONCERT",
        "city": "上海",
        "venueName": "梅赛德斯-奔驰文化中心 · 主场馆",
        "minPrice": 380.0,
        "maxPrice": 1280.0,
        "sessions": [
            {
                "sessionId": 101,
                "sessionTitle": "11月15日 周六 19:30 首演黄金场",
                "hallName": "中央全息穹顶大厅",
                "availableSeats": 44,
            },
            {
                "sessionId": 102,
                "sessionTitle": "11月16日 周日 19:30 返场加演夜",
                "hallName": "中央全息穹顶大厅",
                "availableSeats": 48,
            },
        ],
    },
    {
        "showId": 2,
        "title": "百老汇原版音乐剧《歌剧魅影 Phantom》20周年纪念版",
        "category": "MUSICAL",
        "city": "北京",
        "venueName": "天桥艺术中心 · 大剧场",
        "minPrice": 280.0,
        "maxPrice": 980.0,
        "sessions": [
            {
                "sessionId": 201,
                "sessionTitle": "11月20日 周五 19:30 首演礼宾场",
                "hallName": "歌剧大剧场一层的厅",
                "availableSeats": 46,
            }
        ],
    },
    {
        "showId": 3,
        "title": "《星际穿越 Interstellar》十周年 IMAX GT 激光重映礼",
        "category": "IMAX_MOVIE",
        "city": "深圳",
        "venueName": "万象城 IMAX GT 巨幕影厅",
        "minPrice": 89.0,
        "maxPrice": 199.0,
        "sessions": [
            {
                "sessionId": 301,
                "sessionTitle": "11月08日 周六 20:00 IMAX GT 零点纪念场",
                "hallName": "1号 IMAX GT 激光巨幕厅",
                "availableSeats": 46,
            }
        ],
    },
    {
        "showId": 4,
        "title": "西湖仲夏夜 · 蓝调爵士与后摇不插电音乐节",
        "category": "LIVEHOUSE",
        "city": "杭州",
        "venueName": "大麦 66 Livehouse · 旗舰厅",
        "minPrice": 180.0,
        "maxPrice": 480.0,
        "sessions": [
            {
                "sessionId": 401,
                "sessionTitle": "11月12日 周四 20:30 星空不插电专场",
                "hallName": "Livehouse 全景主厅",
                "availableSeats": 48,
            }
        ],
    },
]

_MOCK_PRICE_TIERS = {
    1: (1280.0, 680.0, 380.0),
    101: (1280.0, 680.0, 380.0),
    102: (1280.0, 680.0, 380.0),
    201: (980.0, 580.0, 280.0),
    301: (199.0, 139.0, 89.0),
    401: (480.0, 280.0, 180.0),
}

_MOCK_SOLD_COORDS = {
    101: {(1, 4), (1, 5), (2, 4), (2, 5)},
    201: {(1, 4), (1, 5)},
    301: {(3, 4), (3, 5)},
}


def _find_mock_session_meta(session_id: int) -> Dict[str, Any]:
    for show in _MOCK_SHOWS:
        for sess in show["sessions"]:
            if int(sess["sessionId"]) == int(session_id):
                return {"show": show, "session": sess}
    return {
        "show": _MOCK_SHOWS[0],
        "session": {"sessionId": session_id, "sessionTitle": f"场次 #{session_id}"},
    }


def _build_mock_seat_map(session_id: int) -> Dict[str, Any]:
    meta = _find_mock_session_meta(session_id)
    show = meta["show"]
    session = meta["session"]
    p_vip, p_mid, p_std = _MOCK_PRICE_TIERS.get(int(session_id), (1280.0, 680.0, 380.0))
    sold_coords = _MOCK_SOLD_COORDS.get(int(session_id), set())

    seats: List[Dict[str, Any]] = []
    # 对齐 init_databases.sql 的自增 ID 规律（每场次 48 座）
    session_offset_map = {101: 1, 102: 49, 201: 97, 301: 145, 401: 193}
    seat_id_seq = session_offset_map.get(int(session_id), int(session_id) * 100 + 1)

    for r in range(1, 7):
        if r <= 2:
            zone_name, price = "内场至尊VIP区", p_vip
        elif r <= 4:
            zone_name, price = "中轴声场优选区", p_mid
        else:
            zone_name, price = "全景看台标准区", p_std
        for c in range(1, 9):
            seats.append({
                "id": seat_id_seq,
                "sessionId": session_id,
                "rowNo": r,
                "colNo": c,
                "seatLabel": f"{r}排{c:02d}座",
                "zoneName": zone_name,
                "price": price,
                "status": "SOLD" if (r, c) in sold_coords else "AVAILABLE",
            })
            seat_id_seq += 1

    return {
        "show": show,
        "session": session,
        "seats": seats,
    }


def _compute_quote_from_seat_map(
    session_id: int,
    seat_ids: List[int],
    show_obj: Dict[str, Any],
    sess_obj: Dict[str, Any],
    seats: List[Dict[str, Any]],
    source: str,
    is_mock: bool,
) -> Dict[str, Any]:
    if str(show_obj.get("status", "ON_SALE")).upper() == "OFF_SHELF":
        return {"ok": False, "retryable": False, "message": "该演出已下架归档，暂不支持购买"}

    seat_by_id = {int(s["id"]): s for s in seats if s.get("id") is not None}
    sorted_ids = sorted({int(sid) for sid in seat_ids})

    missing = [sid for sid in sorted_ids if sid not in seat_by_id]
    if missing:
        return {"ok": False, "retryable": False, "message": f"座位编号 {missing} 不属于场次 #{session_id}"}

    unavailable = [
        f"{seat_by_id[sid].get('seatLabel', sid)}({seat_by_id[sid].get('status')})"
        for sid in sorted_ids
        if seat_by_id[sid].get("status") != "AVAILABLE"
    ]
    if unavailable:
        return {
            "ok": False,
            "retryable": False,
            "message": f"所选座位已被锁定或售出，请重新选座: {'、'.join(unavailable)}",
        }

    total = sum(Decimal(str(seat_by_id[sid]["price"])) for sid in sorted_ids)
    labels = [
        _format_full_seat_label(seat_by_id[sid].get("zoneName"), seat_by_id[sid].get("seatLabel"))
        for sid in sorted_ids
    ]
    return {
        "ok": True,
        "isMock": is_mock,
        "source": source,
        "quote": {
            "showId": show_obj.get("id") or show_obj.get("showId"),
            "showTitle": show_obj.get("title", ""),
            "venueName": show_obj.get("venueName", ""),
            "sessionId": int(session_id),
            "sessionTitle": sess_obj.get("sessionTitle", f"场次 #{session_id}"),
            "startTime": sess_obj.get("startTime"),
            "seatIds": sorted_ids,
            "seatLabels": labels,
            "seatCount": len(sorted_ids),
            "totalAmount": float(total.quantize(Decimal("0.01"))),
        },
    }


async def quote_seats_from_server(session_id: Any, seat_ids: Any) -> Dict[str, Any]:
    """
    【P0-2 服务端权威核价与余座实时检查】：
    进入 interrupt 确认卡片前调用 Java show-service 实时计算应付总额并校验座位仍处于 AVAILABLE，
    彻底禁止信任大模型生成的 total_amount 或 seat_labels。
    """
    try:
        sid_int = int(session_id)
        parsed_ids = sorted({int(x) for x in (seat_ids or [])})
    except (TypeError, ValueError):
        return {"ok": False, "retryable": False, "message": "场次或座位参数格式无效"}

    if sid_int <= 0 or not parsed_ids:
        return {"ok": False, "retryable": False, "message": "缺少有效的 session_id 或 seat_ids"}
    if len(parsed_ids) > 6:
        return {"ok": False, "retryable": False, "message": "单笔订单最多支持选座 6 张"}

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(
                f"{settings.SHOW_SERVICE_URL}/api/show/public/sessions/{sid_int}/quote",
                json={"seatIds": parsed_ids},
            )
            if resp.status_code != 404:
                body = resp.json()
                if resp.status_code == 200 and body.get("code") == 200 and body.get("data"):
                    return {
                        "ok": True,
                        "isMock": False,
                        "source": "Java show-service(:8202) quote",
                        "quote": body["data"],
                    }
                return {
                    "ok": False,
                    "retryable": resp.status_code >= 500,
                    "message": body.get("message", f"服务端核价失败 (HTTP {resp.status_code})"),
                }

            # 兼容尚未重启加载 /quote 的 show-service：回退拉取实时座位图并在本地按权威数据核价
            seats_resp = await client.get(
                f"{settings.SHOW_SERVICE_URL}/api/show/public/sessions/{sid_int}/seats"
            )
            seats_body = seats_resp.json()
            if seats_resp.status_code != 200 or seats_body.get("code") != 200 or not seats_body.get("data"):
                return {
                    "ok": False,
                    "retryable": seats_resp.status_code >= 500,
                    "message": seats_body.get("message", f"获取场次座位失败 (HTTP {seats_resp.status_code})"),
                }
            data = seats_body["data"]
            return _compute_quote_from_seat_map(
                sid_int,
                parsed_ids,
                data.get("show") or {},
                data.get("session") or {},
                data.get("seats") or [],
                "Java show-service(:8202) seats",
                is_mock=False,
            )
    except Exception as exc:
        if settings.DEV_MOCK:
            mock_map = _build_mock_seat_map(sid_int)
            return _compute_quote_from_seat_map(
                sid_int,
                parsed_ids,
                mock_map["show"],
                mock_map["session"],
                mock_map["seats"],
                "本地 DEV_MOCK 离线核价",
                is_mock=True,
            )
        return {
            "ok": False,
            "retryable": True,
            "message": f"演出座位服务暂不可达，无法核验实时座位与票价（{type(exc).__name__}），已拦截发起下单确认。",
        }


def calculate_tiered_refund_quote(
    total_amount: Any,
    start_time_str: Optional[str],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    【确定性阶梯退票算费引擎 — 严格对齐 [POL-REFUND-001]】：
    禁止让大模型心算百分比！由确定性代码根据演出开演时间与实付金额精确计算手续费与应退金额。
    """
    amt = Decimal(str(total_amount or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ref_now = now or datetime.now()

    if not start_time_str:
        return {
            "policyCode": "POL-REFUND-001",
            "knownStartTime": False,
            "totalAmount": float(amt),
            "summary": "未获取到场次精确开演时间，需人工核实适用退票档位 [POL-REFUND-001]。",
        }

    clean_ts = str(start_time_str).strip().replace("T", " ")
    if "." in clean_ts:
        clean_ts = clean_ts.split(".")[0]
    try:
        start_dt = datetime.strptime(clean_ts[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {
            "policyCode": "POL-REFUND-001",
            "knownStartTime": False,
            "totalAmount": float(amt),
            "summary": f"开演时间格式无法解析 ({start_time_str})，需人工核实退票费率 [POL-REFUND-001]。",
        }

    hours_left = (start_dt - ref_now).total_seconds() / 3600.0
    hours_round = round(hours_left, 2)

    if hours_left >= 168:
        rate = Decimal("0.00")
        refundable = True
        tier = "无条件免费退票期（距开演 ≥ 168 小时 / 7 天）"
    elif hours_left >= 48:
        rate = Decimal("0.10")
        refundable = True
        tier = "标准退票期（距开演 48h ~ 168h，收取 10% 手续费）"
    elif hours_left >= 24:
        rate = Decimal("0.20")
        refundable = True
        tier = "临期退票期（距开演 24h ~ 48h，收取 20% 手续费）"
    else:
        rate = Decimal("1.00")
        refundable = False
        tier = "停止退票期（距开演不足 24 小时或已开演，原则上不支持个人原因退票）"

    if refundable:
        fee = (amt * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        refund = (amt - fee).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rate_str = f"{int(rate * 100)}%"
    else:
        fee = amt
        refund = Decimal("0.00")
        rate_str = "不可退"

    return {
        "policyCode": "POL-REFUND-001",
        "knownStartTime": True,
        "startTime": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "hoursBeforeStart": hours_round,
        "refundable": refundable,
        "tierName": tier,
        "feeRate": rate_str,
        "totalAmount": float(amt),
        "feeAmount": float(fee),
        "refundAmount": float(refund),
    }


async def inspect_order_for_cancel(order_no: str) -> Dict[str, Any]:
    """
    进入取消/退票确认卡片前向 Java order-service 校验订单归属与当前真实状态：
    - UNPAID 订单：允许进入 CANCEL_ORDER 取消确认卡片
    - PAID 订单：运行确定性阶梯退票算费，>= 24h 允许进入 REFUND_ORDER 确认卡片；< 24h 拦截并附明细说明
    - CLOSED 订单：直接告知无需重复操作
    """
    clean_no = (order_no or "").strip()
    if not clean_no:
        return {"ok": False, "retryable": False, "message": "订单号不能为空"}

    headers = _build_user_headers()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.ORDER_SERVICE_URL}/api/order/detail/{clean_no}",
                headers=headers,
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("code") != 200 or not body.get("data"):
                return {
                    "ok": False,
                    "retryable": resp.status_code >= 500,
                    "message": body.get("message", f"查询订单失败 (HTTP {resp.status_code})"),
                }
            order = body["data"]
            status = order.get("status")
            try:
                seat_labels = json.loads(order.get("seatLabelsJson") or "[]")
            except Exception:
                seat_labels = []

            if status == "PAID":
                calc = calculate_tiered_refund_quote(order.get("totalAmount"), order.get("startTime"))
                if calc.get("knownStartTime") and calc.get("refundable"):
                    return {
                        "ok": True,
                        "isMock": False,
                        "actionType": "REFUND_ORDER",
                        "toolName": "propose_refund_order",
                        "orderStatus": "PAID",
                        "refundCalc": calc,
                        "order": {
                            "orderNo": order.get("orderNo", clean_no),
                            "showTitle": order.get("showTitle", ""),
                            "sessionTitle": order.get("sessionTitle", ""),
                            "seatLabels": seat_labels,
                            "totalAmount": order.get("totalAmount"),
                            "startTime": order.get("startTime"),
                            "ticketCode": order.get("ticketCode"),
                            "status": status,
                        },
                    }

                calc_desc = ""
                if calc.get("knownStartTime"):
                    calc_desc = (
                        f"经系统按 [{calc['policyCode']}] 确定性核算：当前距开演 {calc['hoursBeforeStart']} 小时，"
                        f"处于「{calc['tierName']}」，暂不支持自助退票。"
                    )
                else:
                    calc_desc = str(calc.get("summary") or "场次开演时间未知，暂不支持自助退票。")
                return {
                    "ok": False,
                    "retryable": False,
                    "orderStatus": "PAID",
                    "refundCalc": calc,
                    "message": f"订单 {clean_no} 已出票（核验码 {order.get('ticketCode') or '已签发'}）。{calc_desc}",
                }
            if status != "UNPAID":
                return {
                    "ok": False,
                    "retryable": False,
                    "message": f"订单 {clean_no} 当前状态为 {status}（{order.get('closeReason') or '已关闭'}），无需重复操作。",
                }

            return {
                "ok": True,
                "isMock": False,
                "actionType": "CANCEL_ORDER",
                "toolName": "propose_cancel_order",
                "order": {
                    "orderNo": order.get("orderNo", clean_no),
                    "showTitle": order.get("showTitle", ""),
                    "sessionTitle": order.get("sessionTitle", ""),
                    "seatLabels": seat_labels,
                    "totalAmount": order.get("totalAmount"),
                    "expireAt": order.get("expireAt"),
                    "status": status,
                },
            }
    except Exception as exc:
        if settings.DEV_MOCK:
            return {
                "ok": True,
                "isMock": True,
                "actionType": "CANCEL_ORDER",
                "toolName": "propose_cancel_order",
                "order": {
                    "orderNo": clean_no,
                    "showTitle": "「星际回响 · PulseLive」2026 全息交响电音巡演",
                    "sessionTitle": "首演黄金场",
                    "seatLabels": ["中轴声场优选区 3排04座", "中轴声场优选区 3排05座"],
                    "totalAmount": 1360.0,
                    "status": "UNPAID",
                },
            }
        return {
            "ok": False,
            "retryable": True,
            "message": f"订单服务暂不可达，无法核验订单 {clean_no} 状态（{type(exc).__name__}）。",
        }


@tool
async def search_ticket_policy(query: str) -> str:
    """
    【Hybrid RAG 知识库检索工具】
    当用户询问退票规定、退票手续费、改签说明、实名制入场、儿童免票身高、
    演出场馆地铁交通路线、禁带物品（如雨伞/水/灯牌）、寄存处或锁座超时时间等规则时调用。
    """
    hits = await hybrid_rag_engine.search(query, top_k=3)
    if not hits:
        return "未检索到相关票务规则条款。"

    blocks = []
    for h in hits:
        blocks.append(
            f"【引用条款 {h['docCode']} · 《{h['docTitle']}》 (融合得分={h['finalScore']})】\n{h['content']}"
        )
    return "\n\n---\n\n".join(blocks)


@tool
async def search_shows(keyword: Optional[str] = None, category: Optional[str] = None) -> str:
    """
    【Java show-service 只读工具】
    查询当前在售的演出列表、票价区间、场馆及场次编号（sessionId）。
    参数:
    - keyword: 演出名称、城市或场馆关键词（如 "星际回响"、"上海"、"梅赛德斯"、"歌剧魅影"），为空则查全部在售演出。
    - category: 演出分类（CONCERT / MUSICAL / IMAX_MOVIE / LIVEHOUSE），可为空。
    """
    shows_data: List[Dict[str, Any]] = []
    from_java = False
    error_detail = ""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            params = {"category": category} if category else {}
            resp = await client.get(f"{settings.SHOW_SERVICE_URL}/api/show/public/list", params=params)
            if resp.status_code == 200:
                body = resp.json()
                raw_list = body.get("data") or []
                for item in raw_list:
                    sh = item.get("show") or {}
                    sessions = item.get("sessions") or []
                    shows_data.append({
                        "showId": sh.get("id"),
                        "title": sh.get("title"),
                        "category": sh.get("category"),
                        "city": sh.get("city"),
                        "venueName": sh.get("venueName"),
                        "minPrice": sh.get("minPrice"),
                        "maxPrice": sh.get("maxPrice"),
                        "sessions": [
                            {
                                "sessionId": s.get("id"),
                                "sessionTitle": s.get("sessionTitle"),
                                "hallName": s.get("hallName"),
                                "availableSeats": s.get("availableSeats"),
                            }
                            for s in sessions
                        ],
                    })
                from_java = True
            else:
                error_detail = f"HTTP {resp.status_code}"
    except Exception as exc:
        error_detail = type(exc).__name__

    if not from_java:
        if not settings.DEV_MOCK:
            return json.dumps({
                "ok": False,
                "error": f"演出服务 show-service 暂不可达 ({error_detail})，无法查询真实在售演出，请确认后端微服务已启动。",
            }, ensure_ascii=False)
        shows_data = list(_MOCK_SHOWS)

    if keyword:
        kw = keyword.strip().lower()
        filtered = [
            s for s in shows_data
            if kw in str(s.get("title", "")).lower()
            or kw in str(s.get("venueName", "")).lower()
            or kw in str(s.get("city", "")).lower()
        ]
        if filtered:
            shows_data = filtered

    source_tag = "Java show-service(:8202)" if from_java else "本地 DEV_MOCK 数据（仅离线调试）"
    return f"[数据源: {source_tag}]\n" + json.dumps(shows_data, ensure_ascii=False, indent=2)


@tool
async def recommend_seats(
    session_id: int,
    count: int = 2,
    max_unit_price: Optional[float] = None,
    sort_by: str = "best_view",
    position: str = "center",
    row_no: Optional[int] = None,
    col_no: Optional[int] = None,
) -> str:
    """
    【Java show-service 智能连座、靠边选座、精确排座定位与多票档推荐工具】
    根据场次 ID (session_id)、张数 (count)、单张最高预算 (max_unit_price)、排序偏好 (sort_by)、
    横向位置偏好 (position) 及可选精确排号/座号 (row_no / col_no)，实时拉取 Java 48 座矩阵库存并计算方案：
    - `sort_by="best_view"`: 优先视野最佳前排（默认）
    - `sort_by="lowest_price"`: 优先最低价格档位（“便宜一点 / 低价 / 性价比 / 看台”）
    - `sort_by="highest_price"`: 优先最高 VIP 票档（“最佳观看体验 / 最前排 / VIP”）
    - `position="center"`: 优先中轴线（第 4/5 列，默认）
    - `position="side"`: 优先靠边/过道（第 1 列或最末列，如 01座/08座；当用户说“靠边/过道”时必须传此值）
    - `row_no` / `col_no`: 当用户明确指定排号或精确座位（如“第一排2号座位” -> `row_no=1, col_no=2`）时，
      直接定位真实数据库 `seatId` 并校验是否可售（绝不把“2号座位”误当成 MySQL 主键 `id=2`）。
    """
    count = max(1, min(6, int(count)))
    norm_sort = str(sort_by or "best_view").strip().lower()
    if norm_sort in ("lowest_price", "cheapest", "lowest", "price_asc", "low_price"):
        norm_sort = "lowest_price"
    elif norm_sort in ("highest_price", "vip", "highest", "price_desc", "high_price"):
        norm_sort = "highest_price"
    else:
        norm_sort = "best_view"

    norm_pos = str(position or "center").strip().lower()
    if norm_pos in ("side", "aisle", "edge", "靠边", "过道", "边上"):
        norm_pos = "side"
    else:
        norm_pos = "center"

    target_row = int(row_no) if row_no is not None else None
    target_col = int(col_no) if col_no is not None else None

    seats: List[Dict[str, Any]] = []
    show_title = ""
    session_title = f"场次 #{session_id}"
    from_java = False
    error_detail = ""

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.SHOW_SERVICE_URL}/api/show/public/sessions/{session_id}/seats"
            )
            if resp.status_code == 200:
                body = resp.json() or {}
                data = body.get("data") or {}
                if body.get("code") == 200 and data:
                    show_obj = data.get("show") or {}
                    sess_obj = data.get("session") or {}
                    show_title = show_obj.get("title") or ""
                    session_title = sess_obj.get("sessionTitle") or session_title
                    seats = data.get("seats") or []
                    from_java = True
                else:
                    error_detail = body.get("message") or f"code={body.get('code')}"
            else:
                error_detail = f"HTTP {resp.status_code}"
    except Exception as exc:
        error_detail = type(exc).__name__

    if not from_java:
        if not settings.DEV_MOCK:
            return json.dumps({
                "found": False,
                "ok": False,
                "message": f"演出座位服务不可达或场次查询失败（{error_detail}），无法推荐实时余座。",
            }, ensure_ascii=False)
        mock_map = _build_mock_seat_map(session_id)
        show_title = mock_map["show"]["title"]
        session_title = mock_map["session"]["sessionTitle"]
        seats = mock_map["seats"]

    all_available = [s for s in seats if s.get("status") == "AVAILABLE"]
    # 动态推算本场次每排最大列号与中轴线坐标（标准 6×8 矩阵中 max_col=8、center_axis=4.5）
    max_col = max((int(s.get("colNo") or 8) for s in seats), default=8)
    center_axis = (1.0 + max_col) / 2.0

    def _build_contiguous_candidates(seat_pool: List[Dict[str, Any]], win_size: int) -> List[Dict[str, Any]]:
        rows_map: Dict[int, List[Dict[str, Any]]] = {}
        for s in seat_pool:
            rows_map.setdefault(int(s["rowNo"]), []).append(s)

        out: List[Dict[str, Any]] = []
        for r_no, r_seats in rows_map.items():
            r_seats.sort(key=lambda x: int(x["colNo"]))
            for i in range(len(r_seats) - win_size + 1):
                window = r_seats[i : i + win_size]
                if int(window[-1]["colNo"]) - int(window[0]["colNo"]) == win_size - 1:
                    # centerScore 越小越靠中轴（如 4/5 座为 0.5）；sideScore 越小越靠两侧过道（如 1/8 座为 0.0）
                    center_dist = sum(abs(float(x["colNo"]) - center_axis) for x in window) / win_size
                    side_dist = sum(
                        min(abs(int(x["colNo"]) - 1), abs(max_col - int(x["colNo"])))
                        for x in window
                    ) / win_size
                    total_price = sum(float(x["price"]) for x in window)
                    z_name = str(window[0].get("zoneName") or "").strip()
                    out.append({
                        "rowNo": r_no,
                        "colNos": [int(x["colNo"]) for x in window],
                        "zoneName": z_name,
                        "seatIds": [int(x["id"]) for x in window],
                        "seatLabels": [_clean_seat_label(z_name, x.get("seatLabel")) for x in window],
                        "unitPrice": round(float(window[0]["price"]), 2),
                        "totalAmount": round(total_price, 2),
                        "centerScore": round(center_dist, 2),
                        "sideScore": round(side_dist, 2),
                    })
        return out

    pos_metric = "sideScore" if norm_pos == "side" else "centerScore"

    # 始终统计该场次全部可用票档汇总，避免大模型“盲人摸象”不知道还有哪些档位
    tier_buckets: Dict[tuple, List[Dict[str, Any]]] = {}
    for s in all_available:
        key = (round(float(s.get("price", 0)), 2), str(s.get("zoneName") or "").strip())
        tier_buckets.setdefault(key, []).append(s)

    price_tiers: List[Dict[str, Any]] = []
    for (tier_price, tier_zone), tier_seats in sorted(tier_buckets.items(), key=lambda kv: kv[0][0]):
        tier_cands = _build_contiguous_candidates(tier_seats, count)
        tier_cands.sort(key=lambda x: (x[pos_metric], x["rowNo"]))
        price_tiers.append({
            "unitPrice": tier_price,
            "zoneName": tier_zone,
            "availableSeats": len(tier_seats),
            "bestOption": tier_cands[0] if tier_cands else None,
        })

    # ==================== 分支 A：用户明确指定了精确座位（row_no + col_no） ====================
    # 例如“我要第一排2号座位”：直接按 (rowNo, colNo) 在真实座位矩阵中查出唯一数据库 seatId，
    # 不受上一轮残留预算上限 max_unit_price 拦截，若该座已被锁/已售则如实播报并提供同排相邻备选。
    if target_row is not None and target_col is not None:
        exact_seat = next(
            (s for s in seats if int(s.get("rowNo", -1)) == target_row and int(s.get("colNo", -1)) == target_col),
            None,
        )
        same_row_avail = [s for s in all_available if int(s.get("rowNo", -1)) == target_row]
        nearby_cands = _build_contiguous_candidates(same_row_avail, 1)
        nearby_cands.sort(key=lambda x: (abs(x["colNos"][0] - target_col), x["centerScore"]))

        if exact_seat is None:
            return json.dumps({
                "found": False,
                "exactMatch": False,
                "isMock": not from_java,
                "source": "Java show-service(:8202)" if from_java else "本地 DEV_MOCK 座位矩阵",
                "showTitle": show_title,
                "sessionId": session_id,
                "sessionTitle": session_title,
                "requestedSeat": f"{target_row}排{target_col:02d}座",
                "priceTiers": price_tiers,
                "alternatives": nearby_cands[:2],
                "message": f"场次 #{session_id} 不存在 {target_row}排{target_col:02d}座（本场次共 1~6 排、每排 1~{max_col} 座）。",
            }, ensure_ascii=False, indent=2)

        z_name = str(exact_seat.get("zoneName") or "").strip()
        clean_lb = _clean_seat_label(z_name, exact_seat.get("seatLabel"))
        exact_status = str(exact_seat.get("status") or "UNKNOWN").upper()
        if exact_status != "AVAILABLE":
            status_cn = "已被他人锁定（待支付）" if exact_status == "LOCKED" else "已售出"
            return json.dumps({
                "found": False,
                "exactMatch": False,
                "isMock": not from_java,
                "source": "Java show-service(:8202)" if from_java else "本地 DEV_MOCK 座位矩阵",
                "showTitle": show_title,
                "sessionId": session_id,
                "sessionTitle": session_title,
                "requestedSeat": clean_lb,
                "requestedSeatId": int(exact_seat["id"]),
                "requestedSeatStatus": exact_status,
                "priceTiers": price_tiers,
                "alternatives": nearby_cands[:2],
                "message": f"场次 #{session_id} 的 {z_name} {clean_lb} 当前{status_cn}（status={exact_status}），暂无法锁定。",
            }, ensure_ascii=False, indent=2)

        exact_cand = {
            "rowNo": target_row,
            "colNos": [target_col],
            "zoneName": z_name,
            "seatIds": [int(exact_seat["id"])],
            "seatLabels": [clean_lb],
            "unitPrice": round(float(exact_seat["price"]), 2),
            "totalAmount": round(float(exact_seat["price"]), 2),
            "centerScore": round(abs(float(target_col) - center_axis), 2),
            "sideScore": round(float(min(abs(target_col - 1), abs(max_col - target_col))), 2),
        }
        alts = [c for c in nearby_cands if c["seatIds"] != exact_cand["seatIds"]][:2]
        return json.dumps({
            "found": True,
            "exactMatch": True,
            "isMock": not from_java,
            "source": "Java show-service(:8202)" if from_java else "本地 DEV_MOCK 座位矩阵",
            "showTitle": show_title,
            "sessionId": session_id,
            "sessionTitle": session_title,
            "sortBy": norm_sort,
            "position": norm_pos,
            "priceTiers": price_tiers,
            "recommended": exact_cand,
            "alternatives": alts,
        }, ensure_ascii=False, indent=2)

    # ==================== 分支 B：按指定排号 / 预算 / 靠边或中轴偏好智能推荐 ====================
    available = [
        s for s in all_available
        if (max_unit_price is None or float(s.get("price", 0)) <= float(max_unit_price))
        and (target_row is None or int(s.get("rowNo", -1)) == target_row)
    ]

    if len(available) < count:
        lowest_hint = (
            f"当前全场最低可售档位为「{price_tiers[0]['zoneName']}」¥{price_tiers[0]['unitPrice']}/张（余 {price_tiers[0]['availableSeats']} 座）。"
            if price_tiers
            else "当前场次暂无可售余座。"
        )
        cond_desc = (
            f"第 {target_row} 排"
            if target_row is not None
            else f"预算（<= ¥{max_unit_price}）"
        )
        return json.dumps({
            "found": False,
            "showTitle": show_title,
            "sessionId": session_id,
            "sessionTitle": session_title,
            "sortBy": norm_sort,
            "position": norm_pos,
            "priceTiers": price_tiers,
            "message": f"当前场次满足{cond_desc}的空余座位不足 {count} 个。{lowest_hint}",
        }, ensure_ascii=False, indent=2)

    candidates = _build_contiguous_candidates(available, count)
    if norm_sort == "lowest_price":
        candidates.sort(key=lambda x: (x["unitPrice"], x[pos_metric], x["rowNo"]))
    elif norm_sort == "highest_price":
        candidates.sort(key=lambda x: (-x["unitPrice"], x[pos_metric], x["rowNo"]))
    else:
        candidates.sort(key=lambda x: (x[pos_metric], x["rowNo"]))

    best = candidates[0] if candidates else None
    alternatives: List[Dict[str, Any]] = []
    if best is not None:
        used_keys = {tuple(best["seatIds"])}
        seen_prices = {best["unitPrice"]}
        # 跨票档优先挑选不同价位档位的代表席位（如 ¥880 / ¥580 / ¥280 并列展示），方便用户直观比价
        tier_order = (
            candidates[1:]
            if norm_sort != "best_view"
            else sorted(candidates[1:], key=lambda x: (-x["unitPrice"], x[pos_metric], x["rowNo"]))
        )
        for cand in tier_order:
            k = tuple(cand["seatIds"])
            if cand["unitPrice"] not in seen_prices and k not in used_keys:
                alternatives.append(cand)
                used_keys.add(k)
                seen_prices.add(cand["unitPrice"])
                if len(alternatives) >= 2:
                    break
        for cand in candidates[1:]:
            if len(alternatives) >= 2:
                break
            k = tuple(cand["seatIds"])
            if k not in used_keys:
                alternatives.append(cand)
                used_keys.add(k)

    return json.dumps({
        "found": best is not None,
        "exactMatch": False,
        "isMock": not from_java,
        "source": "Java show-service(:8202)" if from_java else "本地 DEV_MOCK 座位矩阵",
        "showTitle": show_title,
        "sessionId": session_id,
        "sessionTitle": session_title,
        "sortBy": norm_sort,
        "position": norm_pos,
        "priceTiers": price_tiers,
        "recommended": best,
        "alternatives": alternatives,
    }, ensure_ascii=False, indent=2)


@tool
async def list_my_orders() -> str:
    """
    【Java order-service 只读工具】
    查询当前登录用户的历史订单列表（含订单号 orderNo、演出名称、座位号、实付金额与状态）。
    """
    headers = _build_user_headers()
    error_detail = ""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{settings.ORDER_SERVICE_URL}/api/order/my", headers=headers)
            body = resp.json()
            if resp.status_code == 200 and body.get("code") == 200:
                orders = body.get("data") or []
                simplified = [
                    {
                        "orderNo": o.get("orderNo"),
                        "showTitle": o.get("showTitle"),
                        "sessionTitle": o.get("sessionTitle"),
                        "startTime": o.get("startTime"),
                        "seatLabelsJson": o.get("seatLabelsJson"),
                        "totalAmount": o.get("totalAmount"),
                        "status": o.get("status"),
                        "closeReason": o.get("closeReason"),
                        "ticketCode": o.get("ticketCode"),
                    }
                    for o in orders[:5]
                ]
                return json.dumps({
                    "ok": True,
                    "isMock": False,
                    "source": "Java order-service(:8203)",
                    "orders": simplified,
                }, ensure_ascii=False, indent=2)
            error_detail = body.get("message") or f"HTTP {resp.status_code}"
    except Exception as exc:
        error_detail = type(exc).__name__

    if not settings.DEV_MOCK:
        return json.dumps({
            "ok": False,
            "error": f"订单服务 order-service 查询失败或不可达（{error_detail}），请稍后重试。",
        }, ensure_ascii=False)

    return json.dumps({
        "ok": True,
        "isMock": True,
        "source": "本地 DEV_MOCK 示例订单",
        "orders": [
            {
                "orderNo": "TK202611160001",
                "showTitle": "「星际回响 · PulseLive」2026 全息交响电音巡演",
                "sessionTitle": "11月15日 周六 19:30 首演黄金场",
                "startTime": "2026-11-25 19:30:00",
                "seatLabelsJson": '["中轴声场优选区 3排04座", "中轴声场优选区 3排05座"]',
                "totalAmount": 1360.0,
                "status": "UNPAID",
            }
        ],
    }, ensure_ascii=False, indent=2)


@tool
async def check_order_refund_detail(order_no: str) -> str:
    """
    【订单售后确定性核算工具】
    查询指定订单号（order_no）的实时状态，并按 [POL-REFUND-001] 阶梯退票规则自动计算距离开演小时数、
    退票手续费率、手续费金额与预计可退金额（禁止大模型自行心算金额）。
    """
    clean_no = (order_no or "").strip()
    if not clean_no:
        return json.dumps({"ok": False, "message": "请提供订单号 order_no"}, ensure_ascii=False)

    headers = _build_user_headers()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.ORDER_SERVICE_URL}/api/order/detail/{clean_no}",
                headers=headers,
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("code") != 200 or not body.get("data"):
                return json.dumps({
                    "ok": False,
                    "message": body.get("message", f"未查到订单 {clean_no}"),
                }, ensure_ascii=False)
            order = body["data"]
            calc = calculate_tiered_refund_quote(order.get("totalAmount"), order.get("startTime"))
            return json.dumps({
                "ok": True,
                "isMock": False,
                "orderNo": order.get("orderNo"),
                "showTitle": order.get("showTitle"),
                "sessionTitle": order.get("sessionTitle"),
                "status": order.get("status"),
                "totalAmount": order.get("totalAmount"),
                "ticketCode": order.get("ticketCode"),
                "refundQuote": calc,
            }, ensure_ascii=False, indent=2)
    except Exception as exc:
        if settings.DEV_MOCK:
            calc = calculate_tiered_refund_quote(1360.0, "2026-11-25 19:30:00")
            return json.dumps({
                "ok": True,
                "isMock": True,
                "orderNo": clean_no,
                "showTitle": "「星际回响 · PulseLive」2026 全息交响电音巡演",
                "sessionTitle": "11月15日 周六 19:30 首演黄金场",
                "status": "UNPAID",
                "totalAmount": 1360.0,
                "refundQuote": calc,
            }, ensure_ascii=False, indent=2)
        return json.dumps({
            "ok": False,
            "message": f"订单服务不可达（{type(exc).__name__}），无法查询订单详情。",
        }, ensure_ascii=False)


@tool
async def propose_create_order(
    session_id: int,
    seat_ids: List[int],
) -> str:
    """
    【高风险写操作工具 — 发起锁座下单确认】
    当用户明确表达“帮我买票/订票/锁座/下单”且已通过 recommend_seats 选定具体 session_id 与 seat_ids 后调用此工具。
    安全约束：仅需传入 session_id 和 seat_ids，系统会在服务端向 Java 实时核验余座并重新计算票价，
    随后触发 LangGraph interrupt() 在前端弹出【确认锁座下单】卡片等待用户人工确认。
    """
    return "PENDING_HUMAN_CONFIRMATION"


@tool
async def propose_cancel_order(
    order_no: str,
    refund_reason: str = "用户主动申请取消未支付订单",
) -> str:
    """
    【高风险写操作工具 — 取消未支付订单】
    当用户明确要求取消某笔未支付（UNPAID）订单时调用此工具。
    系统会先向 Java 校验订单状态，仅未支付订单会触发取消确认卡片。
    """
    return "PENDING_HUMAN_CONFIRMATION"


@tool
async def propose_refund_order(
    order_no: str,
    refund_reason: str = "用户主动申请已支付订单阶梯退票",
) -> str:
    """
    【高风险写操作工具 — 已支付订单自助阶梯退票】
    当用户明确要求退票/退款某笔已支付（PAID）且距离开演 >= 24 小时的订单时调用此工具。
    系统会先向 Java 校验订单归属并按 [POL-REFUND-001] 核算准确手续费与实退金额，随后弹出确认卡片。
    """
    return "PENDING_HUMAN_CONFIRMATION"


async def execute_confirmed_write_action(
    action_name: str,
    args: Dict[str, Any],
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    当用户在对话框点击【确认执行】恢复 LangGraph 状态图后，真正向 Java order-service(:8203) 发起写操作请求。
    严禁在 DEV_MOCK=False 时吞掉异常伪造成功单号！
    """
    headers = _build_user_headers()

    if action_name == "propose_create_order":
        payload: Dict[str, Any] = {
            "sessionId": int(args["session_id"]),
            "seatIds": [int(x) for x in args["seat_ids"]],
            "fastCloseDemo": False,
            "simulateSeataRollback": False,
        }
        req_headers = dict(headers)
        if idempotency_key:
            payload["idempotencyKey"] = idempotency_key
            req_headers["Idempotency-Key"] = idempotency_key

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"{settings.ORDER_SERVICE_URL}/api/order/create",
                    headers=req_headers,
                    json=payload,
                )
                body = resp.json()
                if resp.status_code == 200 and body.get("code") == 200 and body.get("data"):
                    order_data = body["data"]
                    return {
                        "success": True,
                        "isMock": False,
                        "source": "Java order-service(:8203) [Redisson + Seata AT]",
                        "orderNo": order_data.get("orderNo"),
                        "status": order_data.get("status", "UNPAID"),
                        "totalAmount": order_data.get("totalAmount", args.get("total_amount")),
                        "seatLabels": args.get("seat_labels"),
                        "idempotencyKey": idempotency_key,
                        "message": body.get("message", "锁座下单成功，请在15分钟内完成支付"),
                    }
                return {
                    "success": False,
                    "retryable": resp.status_code >= 500,
                    "message": body.get("message", f"下单失败 (HTTP {resp.status_code})"),
                }
        except Exception as exc:
            if settings.DEV_MOCK:
                return {
                    "success": True,
                    "isMock": True,
                    "source": "本地 DEV_MOCK 模拟锁座（未连接 Java order-service:8203）",
                    "orderNo": "TK20261116MOCK01",
                    "status": "UNPAID",
                    "totalAmount": args.get("total_amount"),
                    "seatLabels": args.get("seat_labels"),
                    "idempotencyKey": idempotency_key,
                    "message": "【DEV_MOCK 离线模拟】模拟锁座成功，真实下单请启动 Java 微服务。",
                }
            return {
                "success": False,
                "retryable": True,
                "idempotencyKey": idempotency_key,
                "message": (
                    f"调用 Java 订单服务超时或不可达（{type(exc).__name__}），本次未确认下单是否完成，请勿重复抢座，"
                    "请先查询我的订单确认结果，或稍后使用同一幂等键重试。"
                ),
            }

    if action_name == "propose_cancel_order":
        order_no = str(args["order_no"]).strip()
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"{settings.ORDER_SERVICE_URL}/api/order/{order_no}/cancel",
                    headers=headers,
                )
                body = resp.json()
                order_data = body.get("data") or {}
                if resp.status_code == 200 and body.get("code") == 200 and order_data.get("status") == "CLOSED":
                    return {
                        "success": True,
                        "isMock": False,
                        "source": "Java order-service(:8203)",
                        "orderNo": order_no,
                        "status": order_data.get("status"),
                        "closeReason": order_data.get("closeReason"),
                        "message": body.get("message", "订单已取消，锁定座位已释放"),
                    }
                return {
                    "success": False,
                    "retryable": resp.status_code >= 500,
                    "message": body.get("message", f"取消订单失败 (HTTP {resp.status_code})"),
                }
        except Exception as exc:
            if settings.DEV_MOCK:
                return {
                    "success": True,
                    "isMock": True,
                    "source": "本地 DEV_MOCK 模拟取消（未连接 Java order-service:8203）",
                    "orderNo": order_no,
                    "status": "CLOSED",
                    "closeReason": "USER_CANCEL",
                    "message": f"【DEV_MOCK 离线模拟】订单 {order_no} 已模拟取消。",
                }
            return {
                "success": False,
                "retryable": True,
                "message": f"调用 Java 订单服务取消接口超时或不可达（{type(exc).__name__}），请先查询订单状态确认。",
            }

    if action_name == "propose_refund_order":
        order_no = str(args["order_no"]).strip()
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"{settings.ORDER_SERVICE_URL}/api/order/{order_no}/refund",
                    headers=headers,
                )
                body = resp.json()
                receipt = body.get("data") or {}
                if resp.status_code == 200 and body.get("code") == 200 and receipt.get("status") == "CLOSED":
                    return {
                        "success": True,
                        "isMock": False,
                        "source": "Java order-service(:8203) [CAS + Seat Release]",
                        "orderNo": receipt.get("orderNo", order_no),
                        "status": receipt.get("status"),
                        "closeReason": receipt.get("closeReason", "USER_REFUND"),
                        "policyCode": receipt.get("policyCode", "POL-REFUND-001"),
                        "tierName": receipt.get("tierName"),
                        "feeRate": receipt.get("feeRate"),
                        "totalAmount": receipt.get("totalAmount"),
                        "feeAmount": receipt.get("feeAmount"),
                        "refundAmount": receipt.get("refundAmount"),
                        "pointsDeducted": receipt.get("pointsDeducted"),
                        "hoursBeforeStart": receipt.get("hoursBeforeStart"),
                        "message": body.get("message", "退票申请已办结，座位已释放"),
                    }
                return {
                    "success": False,
                    "retryable": resp.status_code >= 500,
                    "message": body.get("message", f"退票申请失败 (HTTP {resp.status_code})"),
                }
        except Exception as exc:
            if settings.DEV_MOCK:
                return {
                    "success": True,
                    "isMock": True,
                    "source": "本地 DEV_MOCK 模拟退票（未连接 Java order-service:8203）",
                    "orderNo": order_no,
                    "status": "CLOSED",
                    "closeReason": "USER_REFUND",
                    "policyCode": "POL-REFUND-001",
                    "tierName": args.get("tier_name", "标准退票期"),
                    "feeRate": args.get("fee_rate", "0%"),
                    "totalAmount": args.get("total_amount"),
                    "feeAmount": args.get("fee_amount", 0.0),
                    "refundAmount": args.get("refund_amount", args.get("total_amount")),
                    "message": f"【DEV_MOCK 离线模拟】订单 {order_no} 已模拟退票。",
                }
            return {
                "success": False,
                "retryable": True,
                "message": f"调用 Java 订单服务退票接口超时或不可达（{type(exc).__name__}），请先查询订单状态确认。",
            }

    return {"success": False, "retryable": False, "message": f"未知写操作工具: {action_name}"}


# 按子 Agent 职责域划分工具集（最小权限原则）
KNOWLEDGE_TOOLS = [search_ticket_policy]
PURCHASE_READ_TOOLS = [search_shows, recommend_seats]
PURCHASE_WRITE_TOOLS = [propose_create_order]
ORDER_READ_TOOLS = [list_my_orders, check_order_refund_detail]
ORDER_WRITE_TOOLS = [propose_cancel_order, propose_refund_order]

# 区分只读工具与高风险写操作工具（读写分离路由）
READ_TOOLS = [
    search_ticket_policy,
    search_shows,
    recommend_seats,
    list_my_orders,
    check_order_refund_detail,
]

WRITE_TOOLS = [
    propose_create_order,
    propose_cancel_order,
    propose_refund_order,
]

ALL_TOOLS = READ_TOOLS + WRITE_TOOLS
READ_TOOLS_BY_NAME = {t.name: t for t in READ_TOOLS}
WRITE_TOOL_NAMES = {t.name for t in WRITE_TOOLS}

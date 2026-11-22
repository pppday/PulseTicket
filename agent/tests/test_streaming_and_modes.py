from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, AIMessageChunk

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

os.environ["DEV_MOCK"] = "false"

from app.graph import guard_and_router as gr_mod  # noqa: E402
from app.graph.guard_and_router import extract_deterministic_signals  # noqa: E402
from app.graph import main_graph as mg_mod  # noqa: E402
from app.graph.main_graph import MultiAgentTicketManager  # noqa: E402
from app.graph.subgraphs import knowledge as kg_mod  # noqa: E402
from app.graph.subgraphs import order as ord_mod  # noqa: E402
from app.graph.subgraphs import purchase as pur_mod  # noqa: E402
from app.observability.tracer import AgentTracer  # noqa: E402
from app.rag.hybrid_rag import hybrid_rag_engine  # noqa: E402
from app.tools import ticket_tools as tools_mod  # noqa: E402


class FakeStreamingLLM:
    """支持 .astream() 与 .ainvoke() 的测试替身，验证真 SSE 逐片推流且结尾不重复。"""

    def __init__(self, chunks_per_call):
        self._calls = list(chunks_per_call)

    async def ainvoke(self, msgs):
        item = self._calls.pop(0) if self._calls else AIMessage(content="完成。")
        if isinstance(item, list):
            return AIMessage(content="".join(str(x) for x in item))
        return item

    async def astream(self, msgs):
        item = self._calls.pop(0) if self._calls else ["完成。"]
        if isinstance(item, AIMessage):
            yield AIMessageChunk(content=str(item.content or ""), tool_calls=getattr(item, "tool_calls", []))
            return
        for piece in item:
            yield AIMessageChunk(content=str(piece))


class FakeStaticLLM:
    def __init__(self, text: str):
        self.text = text

    async def ainvoke(self, msgs):
        return AIMessage(content=self.text)


class FakeHttpResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class TestStreamingAndModes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        hybrid_rag_engine.load_and_index()
        self._tmp = tempfile.TemporaryDirectory()
        self.ckpt_path = str(Path(self._tmp.name) / "stream_ckpt.sqlite")
        self.trace_path = str(Path(self._tmp.name) / "stream_traces.sqlite")
        self.tracer = AgentTracer(db_path=self.trace_path)
        self.manager = MultiAgentTicketManager(db_path=self.ckpt_path, tracer=self.tracer)
        await self.manager.startup()

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self._tmp.cleanup()

    def test_1_extract_deterministic_signals_parses_bracketed_seat_ids(self):
        intents1, slots1 = extract_deterministic_signals(
            "请帮我锁定场次 session_id=101 seat_ids=[20]（3排04座）并下单"
        )
        self.assertIn("purchase", intents1)
        self.assertTrue(slots1["wants_commit"])
        self.assertEqual(slots1["session_id"], 101)
        self.assertEqual(slots1["seat_ids"], [20])

        intents2, slots2 = extract_deterministic_signals(
            "请帮我锁定场次 session_id=101 seat_ids=[21, 20]（3排04座、3排05座）并下单"
        )
        self.assertIn("purchase", intents2)
        self.assertEqual(slots2["seat_ids"], [20, 21])

    async def test_2_chat_mode_enforces_read_only_no_interrupt(self):
        router_llm = FakeStaticLLM(
            '{"intents": ["purchase"], "slots": {"show_keyword": "星际回响", "session_id": 101, "seat_ids": [20], "wants_commit": true}}'
        )
        pur_read_llm = FakeStaticLLM(
            "当前处于 Chat 只读咨询模式，为您查询到该场次余座充足，若需锁座下单请切换左下角【Agent 模式】执行。"
        )
        with patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
             patch.object(pur_mod, "_get_purchase_read_llm", return_value=pur_read_llm) as mock_read_llm, \
             patch.object(pur_mod, "_get_purchase_llm", side_effect=AssertionError("Chat mode must not bind write tools")):
            res = await self.manager.chat(
                thread_id="th-chat-readonly",
                user_message="直接帮我锁座下单 session_id=101 seat_ids=[20]",
                user_id=1001,
                username="zhaolan",
                mode="chat",
            )
        mock_read_llm.assert_called_once()
        self.assertEqual(res["mode"], "chat")
        self.assertEqual(res["status"], "COMPLETED")
        self.assertIsNone(res["confirmationCard"])

    async def test_3_stream_chat_true_tokens_not_duplicated(self):
        router_llm = FakeStaticLLM('{"intents": ["policy"], "slots": {}}')
        kg_stream_llm = FakeStreamingLLM([["开演前", "7天以上", "免手续费", " [POL-REFUND-001]。"]])

        events = []
        with patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
             patch.object(kg_mod, "get_chat_llm", return_value=kg_stream_llm):
            async for ev in self.manager.stream_chat(
                thread_id="th-stream-rag",
                user_message="退票怎么收手续费？",
                user_id=1001,
                username="zhaolan",
                mode="chat",
            ):
                events.append(ev)

        token_deltas = [e["data"]["delta"] for e in events if e["event"] == "token"]
        tool_cards = [e["data"] for e in events if e["event"] == "tool_card"]
        done_payload = next(e["data"] for e in events if e["event"] == "done")

        self.assertGreaterEqual(len(token_deltas), 4)
        self.assertEqual("".join(token_deltas), done_payload["reply"])
        self.assertEqual(done_payload["mode"], "chat")
        self.assertTrue(any(c["toolName"] == "search_ticket_policy" and c["status"] == "done" for c in tool_cards))

    async def test_4_purchase_emits_tool_cards_and_seat_picker_and_persists(self):
        router_llm = FakeStaticLLM(
            '{"intents": ["purchase"], "slots": {"show_keyword": "星际回响", "session_id": 101, "seat_count": 2, "max_unit_price": 700, "wants_commit": false}}'
        )
        pur_stream_llm = FakeStreamingLLM([
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "tc_rec_1",
                    "name": "recommend_seats",
                    "args": {"session_id": 101, "count": 2, "max_unit_price": 700},
                    "type": "tool_call",
                }],
            ),
            ["已为您", "找到最佳连座：3排04座、3排05座（共¥1360）。"],
        ])

        seats = []
        sid = 1
        for r in range(1, 7):
            zone = "内场至尊VIP区" if r <= 2 else ("中轴声场优选区" if r <= 4 else "全景看台标准区")
            price = 1280.0 if r <= 2 else (680.0 if r <= 4 else 380.0)
            for c in range(1, 9):
                seats.append({
                    "id": sid,
                    "sessionId": 101,
                    "rowNo": r,
                    "colNo": c,
                    "seatLabel": f"{r}排{c:02d}座",
                    "zoneName": zone,
                    "price": price,
                    "status": "AVAILABLE",
                })
                sid += 1

        seat_api_payload = {
            "code": 200,
            "data": {
                "show": {"id": 1, "title": "「星际回响 · PulseLive」2026 全息交响电音巡演", "status": "ON_SALE"},
                "session": {"id": 101, "sessionTitle": "11月15日 周六 19:30 首演黄金场"},
                "seats": seats,
            },
        }

        events = []
        with patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
             patch.object(pur_mod, "_get_purchase_llm", return_value=pur_stream_llm), \
             patch.object(tools_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=FakeHttpResp(200, seat_api_payload))):
            async for ev in self.manager.stream_chat(
                thread_id="th-widget-1",
                user_message="推荐场次101两张700以内的连座",
                user_id=1001,
                username="zhaolan",
                mode="agent",
            ):
                events.append(ev)

        widget_events = [e["data"] for e in events if e["event"] == "widget"]
        self.assertTrue(any(w["widgetType"] == "SEAT_PICKER" and len(w["options"]) >= 2 for w in widget_events))
        first_opt = widget_events[-1]["options"][0]
        self.assertIn("seat_ids=", first_opt["prompt"])

        detail = await self.manager.get_thread_detail(thread_id="th-widget-1", user_id=1001)
        self.assertIsNotNone(detail)
        assistant_msg = next(m for m in detail["messages"] if m["role"] == "assistant")
        self.assertEqual(assistant_msg["mode"], "agent")
        self.assertTrue(any(c["toolName"] == "recommend_seats" for c in assistant_msg["toolCards"]))
        self.assertTrue(any(w["widgetType"] == "SEAT_PICKER" for w in assistant_msg["widgets"]))

    async def test_5_chitchat_node_receives_three_turn_history_context(self):
        captured_calls = []

        class CaptureChitchatLLM:
            async def ainvoke(self, msgs):
                captured_calls.append(list(msgs))
                return AIMessage(content="您叫 ppday。" if len(captured_calls) > 1 else "您好，ppday！")

        router_llm = FakeStaticLLM('{"intents": ["chitchat"], "slots": {}}')
        chitchat_llm = CaptureChitchatLLM()

        with patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
             patch.object(mg_mod, "get_chat_llm", return_value=chitchat_llm):
            r1 = await self.manager.chat(
                thread_id="th-mem-chat",
                user_message="我叫ppday",
                user_id=1001,
                username="zhaolan",
                mode="chat",
            )
            r2 = await self.manager.chat(
                thread_id="th-mem-chat",
                user_message="我叫什么名字",
                user_id=1001,
                username="zhaolan",
                mode="chat",
            )

        self.assertIn("ppday", r1["reply"])
        self.assertIn("ppday", r2["reply"])
        self.assertEqual(len(captured_calls), 2)
        turn2_contents = [str(getattr(m, "content", "")) for m in captured_calls[1]]
        self.assertTrue(
            any("我叫ppday" in c for c in turn2_contents),
            f"Turn 2 chitchat prompt missing Turn 1 history: {turn2_contents}",
        )

    async def test_6_how_to_refund_hits_pol_refund_001_without_refusal(self):
        router_llm = FakeStaticLLM('{"intents": ["policy", "order"], "slots": {}}')
        kg_llm = FakeStreamingLLM([["距离开演大于等于 7 天免费退票 [POL-REFUND-001]。"]])
        ord_llm = FakeStreamingLLM([["如需对具体订单办理退票，可先查询订单列表后一键发起。"]])

        with patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
             patch.object(kg_mod, "get_chat_llm", return_value=kg_llm), \
             patch.object(ord_mod, "_get_order_llm", return_value=ord_llm):
            res = await self.manager.chat(
                thread_id="th-how-refund",
                user_message="怎么退款",
                user_id=1001,
                username="zhaolan",
                mode="agent",
            )

        self.assertEqual(res["status"], "COMPLETED")
        self.assertNotIn("暂未收录", res["reply"])
        self.assertIn("POL-REFUND-001", res["citations"])

    async def test_7_paid_order_refund_interrupt_and_execute_flow(self):
        future_start = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        detail_resp = FakeHttpResp(200, {
            "code": 200,
            "data": {
                "orderNo": "TK202611220099",
                "showTitle": "「歌剧魅影」驻场版",
                "sessionTitle": "首演礼宾场",
                "seatLabelsJson": '["1排01座"]',
                "totalAmount": 1000.0,
                "startTime": future_start,
                "ticketCode": "PT-8899-0099",
                "status": "PAID",
            },
        })
        refund_resp = FakeHttpResp(200, {
            "code": 200,
            "message": "退票申请已办结，座位已释放",
            "data": {
                "orderNo": "TK202611220099",
                "status": "CLOSED",
                "closeReason": "USER_REFUND",
                "policyCode": "POL-REFUND-001",
                "tierName": "标准退票期（距开演 48h ~ 168h，收取 10% 手续费）",
                "feeRate": "10%",
                "totalAmount": 1000.0,
                "feeAmount": 100.0,
                "refundAmount": 900.0,
            },
        })

        router_llm = FakeStaticLLM('{"intents": ["order"], "slots": {"order_no": "TK202611220099"}}')
        ord_llm = FakeStreamingLLM([
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "tc_ref_1",
                    "name": "propose_refund_order",
                    "args": {"order_no": "TK202611220099"},
                    "type": "tool_call",
                }],
            )
        ])

        with patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
             patch.object(ord_mod, "_get_order_llm", return_value=ord_llm), \
             patch.object(tools_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=detail_resp)), \
             patch.object(tools_mod.httpx.AsyncClient, "post", new=AsyncMock(return_value=refund_resp)):
            r1 = await self.manager.chat(
                thread_id="th-refund-flow",
                user_message="我要给订单 TK202611220099 申请退票",
                user_id=1001,
                username="zhaolan",
                mode="agent",
            )
            self.assertEqual(r1["status"], "INTERRUPTED")
            card = r1["confirmationCard"]
            self.assertEqual(card["actionType"], "REFUND_ORDER")
            self.assertEqual(card["args"]["fee_rate"], "10%")
            self.assertEqual(card["args"]["fee_amount"], 100.0)
            self.assertEqual(card["args"]["refund_amount"], 900.0)

            r2 = await self.manager.confirm(
                thread_id="th-refund-flow",
                confirmed=True,
                user_id=1001,
                username="zhaolan",
            )
            self.assertEqual(r2["status"], "COMPLETED")
            self.assertIn("900.0", r2["reply"])
            self.assertIn("USER_REFUND", r2["reply"])

    async def test_8_recommend_seats_multitier_cheaper_and_label_dedup(self):
        # 模拟与线上 show 6 (白雪公主 #403) 一致的 3 个票档且 seatLabel 含冗余区域前缀的脏数据
        seats_403 = []
        sid = 289
        for r in range(1, 7):
            zone = "内场全息至尊区" if r <= 2 else ("中轴声场优选区" if r <= 4 else "全景看台标准区")
            price = 880.0 if r <= 2 else (580.0 if r <= 4 else 280.0)
            for c in range(1, 9):
                seats_403.append({
                    "id": sid,
                    "sessionId": 403,
                    "rowNo": r,
                    "colNo": c,
                    "seatLabel": f"{zone} {r}排{c:02d}座",
                    "zoneName": zone,
                    "price": price,
                    "status": "AVAILABLE",
                })
                sid += 1

        seat_payload_403 = {
            "code": 200,
            "data": {
                "show": {"id": 6, "title": "白雪公主", "status": "ON_SALE"},
                "session": {"id": 403, "sessionTitle": "12月24日 周四 19:30 平安夜黄金场"},
                "seats": seats_403,
            },
        }

        # 1) 验证 recommend_seats 直接输出全票档汇总 priceTiers、跨档 alternatives 与去重 seatLabels
        tok = tools_mod.current_user_ctx.set({"user_id": 1001, "username": "zhaolan", "role": "USER"})
        try:
            with patch.object(tools_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=FakeHttpResp(200, seat_payload_403))):
                raw_best = await tools_mod.recommend_seats.ainvoke({"session_id": 403, "count": 1, "sort_by": "best_view"})
                parsed_best = json.loads(raw_best)
                self.assertTrue(parsed_best["found"])
                self.assertEqual(parsed_best["recommended"]["unitPrice"], 880.0)
                self.assertEqual(parsed_best["recommended"]["seatLabels"], ["1排04座"])
                self.assertEqual([t["unitPrice"] for t in parsed_best["priceTiers"]], [280.0, 580.0, 880.0])
                alt_prices = {a["unitPrice"] for a in parsed_best["alternatives"]}
                self.assertIn(580.0, alt_prices)
                self.assertIn(280.0, alt_prices)
        finally:
            tools_mod.current_user_ctx.reset(tok)

        # 2) 验证多轮对话："帮我买张白雪公主的票" -> "有便宜一点的吗"
        router_calls = []

        class DynamicRouterLLM:
            async def ainvoke(self, msgs):
                idx = len(router_calls)
                router_calls.append(idx)
                if idx == 0:
                    return AIMessage(content='{"intents": ["purchase"], "slots": {"show_keyword": "白雪公主", "seat_count": 1, "wants_commit": false}}')
                return AIMessage(content='{"intents": ["purchase"], "slots": {"price_preference": "lowest_price", "wants_commit": false}}')

        shows_payload_403 = {
            "code": 200,
            "data": [{
                "show": {"id": 6, "title": "白雪公主", "category": "CONCERT", "city": "上海", "venueName": "梅赛德斯-奔驰文化中心 · 主场馆", "minPrice": 280.0, "maxPrice": 880.0},
                "sessions": [{"id": 403, "sessionTitle": "12月24日 周四 19:30 平安夜黄金场", "hallName": "中央全息穹顶大厅", "availableSeats": 48}],
            }],
        }

        async def _fake_get(url, *args, **kwargs):
            if "/public/list" in str(url):
                return FakeHttpResp(200, shows_payload_403)
            return FakeHttpResp(200, seat_payload_403)

        pur_llm = FakeStreamingLLM([
            AIMessage(content="", tool_calls=[{"id": "tc_s1", "name": "recommend_seats", "args": {"session_id": 403, "count": 1}, "type": "tool_call"}]),
            ["为您推荐《白雪公主》最佳中轴座 ¥880，另有 ¥580 优选区与 ¥280 看台可选。"],
            AIMessage(content="", tool_calls=[{"id": "tc_s2", "name": "recommend_seats", "args": {"session_id": 403, "count": 1, "max_unit_price": 879.0, "sort_by": "lowest_price"}, "type": "tool_call"}]),
            ["已为您切换低价优先档：推荐全场最高性价比看台 ¥280 与中档 ¥580。"],
        ])

        events_turn1 = []
        events_turn2 = []
        with patch.object(gr_mod, "get_chat_llm", return_value=DynamicRouterLLM()), \
             patch.object(pur_mod, "_get_purchase_llm", return_value=pur_llm), \
             patch.object(tools_mod.httpx.AsyncClient, "get", side_effect=_fake_get):
            async for ev in self.manager.stream_chat(
                thread_id="th-snowwhite-cheaper",
                user_message="帮我买张白雪公主的票",
                user_id=1001,
                username="zhaolan",
                mode="agent",
            ):
                events_turn1.append(ev)

            async for ev in self.manager.stream_chat(
                thread_id="th-snowwhite-cheaper",
                user_message="有便宜一点的吗",
                user_id=1001,
                username="zhaolan",
                mode="agent",
            ):
                events_turn2.append(ev)

        w1 = next(e["data"] for e in events_turn1 if e["event"] == "widget")
        self.assertEqual(w1["widgetType"], "SEAT_PICKER")
        self.assertNotIn("内场全息至尊区 · 内场全息至尊区", w1["options"][0]["label"])
        self.assertGreaterEqual(len({o["price"] for o in w1["options"]}), 2)

        w2 = next(e["data"] for e in events_turn2 if e["event"] == "widget")
        self.assertEqual(w2["widgetType"], "SEAT_PICKER")
        self.assertTrue(all(float(o["price"]) < 880.0 for o in w2["options"]), f"Turn 2 still had >=880 option: {w2['options']}")
        self.assertEqual(w2["options"][0]["badge"], "低价优选")


if __name__ == "__main__":
    unittest.main()

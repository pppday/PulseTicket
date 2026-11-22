import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

import sys

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

# 确保默认测试不受外部环境 DEV_MOCK 污染
os.environ["DEV_MOCK"] = "false"

from app.config import settings  # noqa: E402
from app.graph import react_agent as react_mod  # noqa: E402
from app.graph.react_agent import TicketAgentManager  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.hybrid_rag import hybrid_rag_engine  # noqa: E402
from app.tools import ticket_tools as tools_mod  # noqa: E402
from app.tools.ticket_tools import current_user_ctx, execute_confirmed_write_action  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.recorded_messages = []

    async def ainvoke(self, msgs):
        self.recorded_messages.append(list(msgs))
        if not self._responses:
            return AIMessage(content="已完成。")
        return self._responses.pop(0)


class TestTicketAgentP0Fixes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        settings.DEV_MOCK = False
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test_ckpt.sqlite")
        self.manager = TicketAgentManager(db_path=self.db_path)
        await self.manager.startup()

    async def asyncTearDown(self):
        await self.manager.shutdown()
        self._tmpdir.cleanup()

    async def test_1_no_fake_success_when_dev_mock_false(self):
        tok = current_user_ctx.set({"user_id": 1001, "username": "zhaolan", "role": "USER"})
        try:
            with patch.object(httpx.AsyncClient, "post", side_effect=httpx.ConnectTimeout("timeout")):
                res = await execute_confirmed_write_action(
                    "propose_create_order",
                    {"session_id": 101, "seat_ids": [20, 21]},
                    idempotency_key="th-1:tc-1",
                )
            self.assertFalse(res["success"])
            self.assertTrue(res["retryable"])
            self.assertNotIn("PT202611108899", json.dumps(res, ensure_ascii=False))
        finally:
            current_user_ctx.reset(tok)

    async def test_2_server_quote_and_idempotency_key_end_to_end(self):
        captured_order_calls = []

        async def mock_post(self_client, url, headers=None, json=None, **kwargs):
            if url.endswith("/api/show/public/sessions/101/quote"):
                return FakeResponse(200, {
                    "code": 200,
                    "data": {
                        "showId": 1,
                        "showTitle": "「星际回响 · PulseLive」2026 全息交响电音巡演",
                        "venueName": "梅赛德斯-奔驰文化中心 · 主场馆",
                        "sessionId": 101,
                        "sessionTitle": "11月15日 周六 19:30 首演黄金场",
                        "seatIds": [20, 21],
                        "seatLabels": ["中轴声场优选区 3排04座", "中轴声场优选区 3排05座"],
                        "totalAmount": 1360.0,
                    },
                })
            if url.endswith("/api/order/create"):
                captured_order_calls.append({"headers": dict(headers or {}), "json": dict(json or {})})
                return FakeResponse(200, {
                    "code": 200,
                    "message": "锁座下单成功",
                    "data": {
                        "orderNo": "TK202611160001",
                        "status": "UNPAID",
                        "totalAmount": 1360.0,
                    },
                })
            raise AssertionError(f"Unexpected POST url: {url}")

        fake_llm = FakeLLM([
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_buy_01",
                    "name": "propose_create_order",
                    "args": {"session_id": 101, "seat_ids": [21, 20]},
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="下单成功！订单号 TK202611160001，应付 ¥1360。"),
        ])

        with patch.object(httpx.AsyncClient, "post", new=mock_post), \
             patch.object(react_mod, "_get_llm_with_tools", return_value=fake_llm):
            res1 = await self.manager.chat(
                thread_id="th-quote-1",
                user_message="帮我锁 101 场次的 20 和 21 号座位",
                user_id=1001,
                username="zhaolan",
            )
            self.assertEqual(res1["status"], "INTERRUPTED")
            card = res1["confirmationCard"]
            self.assertIsNotNone(card)
            self.assertEqual(card["idempotencyKey"], "th-quote-1:call_buy_01")
            self.assertEqual(card["args"]["total_amount"], 1360.0)
            self.assertEqual(card["args"]["seat_ids"], [20, 21])
            self.assertEqual(len(captured_order_calls), 0)

            res2 = await self.manager.confirm(
                thread_id="th-quote-1",
                confirmed=True,
                user_id=1001,
                username="zhaolan",
            )
            self.assertEqual(res2["status"], "COMPLETED")
            self.assertEqual(len(captured_order_calls), 1)
            sent = captured_order_calls[0]
            self.assertEqual(sent["headers"].get("Idempotency-Key"), "th-quote-1:call_buy_01")
            self.assertEqual(sent["headers"].get("X-User-Id"), "1001")
            self.assertEqual(sent["json"].get("idempotencyKey"), "th-quote-1:call_buy_01")

    async def test_3_no_dangling_tool_calls_when_mixed_read_and_write(self):
        fake_llm = FakeLLM([
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc_read_policy", "name": "search_ticket_policy", "args": {"query": "退票"}, "type": "tool_call"},
                    {"id": "tc_write_order", "name": "propose_create_order", "args": {"session_id": 101, "seat_ids": [20]}, "type": "tool_call"},
                ],
            ),
            AIMessage(content="已为您跳过并取消操作。"),
        ])

        mock_quote = AsyncMock(return_value={
            "ok": True,
            "isMock": False,
            "source": "Java",
            "quote": {
                "showId": 1,
                "showTitle": "「星际回响 · PulseLive」2026 全息交响电音巡演",
                "venueName": "梅赛德斯-奔驰文化中心 · 主场馆",
                "sessionId": 101,
                "sessionTitle": "黄金场",
                "seatIds": [20],
                "seatLabels": ["中轴声场优选区 3排04座"],
                "totalAmount": 680.0,
            },
        })

        with patch.object(react_mod, "quote_seats_from_server", new=mock_quote), \
             patch.object(react_mod, "_get_llm_with_tools", return_value=fake_llm):
            res1 = await self.manager.chat("th-mix-1", "顺便买票", user_id=1001, username="zhaolan")
            self.assertEqual(res1["status"], "INTERRUPTED")

            res2 = await self.manager.confirm("th-mix-1", confirmed=False, user_id=1001, username="zhaolan")
            self.assertEqual(res2["status"], "COMPLETED")

            # 检查第二次调用 LLM 时传入的历史消息：两个 tool_call_id 都必须有 ToolMessage
            second_call_msgs = fake_llm.recorded_messages[1]
            tool_msg_ids = {m.tool_call_id for m in second_call_msgs if isinstance(m, ToolMessage)}
            self.assertEqual(tool_msg_ids, {"tc_read_policy", "tc_write_order"})

    async def test_4_interrupted_state_blocks_ambiguous_chat_and_allows_explicit_cancel(self):
        fake_llm = FakeLLM([
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "tc_buy_p06",
                    "name": "propose_create_order",
                    "args": {"session_id": 101, "seat_ids": [20]},
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="已为您放弃本次锁座操作。"),
        ])
        mock_quote = AsyncMock(return_value={
            "ok": True,
            "isMock": False,
            "source": "Java",
            "quote": {
                "showId": 1,
                "showTitle": "「星际回响 · PulseLive」2026 全息交响电音巡演",
                "venueName": "梅赛德斯-奔驰文化中心 · 主场馆",
                "sessionId": 101,
                "sessionTitle": "黄金场",
                "seatIds": [20],
                "seatLabels": ["3排04座"],
                "totalAmount": 680.0,
            },
        })

        with patch.object(react_mod, "quote_seats_from_server", new=mock_quote), \
             patch.object(react_mod, "_get_llm_with_tools", return_value=fake_llm):
            r1 = await self.manager.chat("th-p06", "锁座", user_id=1001, username="zhaolan")
            self.assertEqual(r1["status"], "INTERRUPTED")

            # 非取消意图（甚至说“确认”文本）绝不能绕过卡片自动下单
            r2 = await self.manager.chat("th-p06", "确认", user_id=1001, username="zhaolan")
            self.assertEqual(r2["status"], "INTERRUPTED")
            self.assertIsNotNone(r2["confirmationCard"])

            # 明确取消文本会唤醒并干净退出中断状态
            r3 = await self.manager.chat("th-p06", "算了不买了", user_id=1001, username="zhaolan")
            self.assertEqual(r3["status"], "COMPLETED")

    async def test_5_hybrid_rag_matches_real_db_venues_and_refund_rules(self):
        chunks = hybrid_rag_engine.load_and_index()
        self.assertGreaterEqual(chunks, 10)

        venue_hits = await hybrid_rag_engine.search("上海梅赛德斯奔驰文化中心坐几号线地铁", top_k=2)
        self.assertEqual(venue_hits[0]["docCode"], "POL-VENUE-005")

        refund_hits = await hybrid_rag_engine.search("开演前三天临时去不了能退票吗扣多少", top_k=2)
        self.assertEqual(refund_hits[0]["docCode"], "POL-REFUND-001")


class TestFastApiAuthEndpoints(unittest.TestCase):
    def test_missing_headers_401_and_cross_user_403(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_manager = react_mod.ticket_agent_manager
            import app.main as main_mod

            temp_mgr = TicketAgentManager(db_path=str(Path(tmpdir) / "api.sqlite"))
            main_mod.ticket_agent_manager = temp_mgr

            fake_llm = FakeLLM([
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "tc_api_1",
                        "name": "propose_create_order",
                        "args": {"session_id": 101, "seat_ids": [20]},
                        "type": "tool_call",
                    }],
                ),
            ])
            mock_quote = AsyncMock(return_value={
                "ok": True,
                "isMock": False,
                "source": "Java",
                "quote": {
                    "showId": 1,
                    "showTitle": "「星际回响 · PulseLive」2026 全息交响电音巡演",
                    "venueName": "梅赛德斯",
                    "sessionId": 101,
                    "sessionTitle": "黄金场",
                    "seatIds": [20],
                    "seatLabels": ["3排04座"],
                    "totalAmount": 680.0,
                },
            })

            try:
                with patch.object(react_mod, "quote_seats_from_server", new=mock_quote), \
                     patch.object(react_mod, "_get_llm_with_tools", return_value=fake_llm), \
                     TestClient(app) as client:
                    # 1. 缺失身份头直接 401
                    r_no_auth = client.post("/api/agent/chat", json={"threadId": "t1", "message": "你好"})
                    self.assertEqual(r_no_auth.status_code, 401)

                    r_confirm_no_auth = client.post("/api/agent/confirm", json={"threadId": "t1", "confirmed": True})
                    self.assertEqual(r_confirm_no_auth.status_code, 401)

                    # 2. 用户 1001 发起挂起，用户 1002 尝试确认直接 403 拦截
                    r_1001 = client.post(
                        "/api/agent/chat",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                        json={"threadId": "t-cross", "message": "锁座"},
                    )
                    self.assertEqual(r_1001.status_code, 200)
                    self.assertEqual(r_1001.json()["data"]["status"], "INTERRUPTED")

                    r_1002 = client.post(
                        "/api/agent/confirm",
                        headers={"X-User-Id": "1002", "X-Username": "linfeng"},
                        json={"threadId": "t-cross", "confirmed": True},
                    )
                    self.assertEqual(r_1002.status_code, 403)
            finally:
                main_mod.ticket_agent_manager = orig_manager


if __name__ == "__main__":
    unittest.main()

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

os.environ["DEV_MOCK"] = "false"

from app.graph import guard_and_router as gr_mod  # noqa: E402
from app.graph.guard_and_router import summarize_and_trim_history  # noqa: E402
from app.graph.main_graph import MultiAgentTicketManager  # noqa: E402
from app.graph.subgraphs import knowledge as kg_mod  # noqa: E402
from app.graph.subgraphs import purchase as pur_mod  # noqa: E402
from app.main import app  # noqa: E402
from app.observability.tracer import AgentTracer  # noqa: E402
from app.storage.conversation_store import ConversationStore, to_iso_utc  # noqa: E402


class FakeSimpleLLM:
    def __init__(self, reply_text: str):
        self.reply_text = reply_text

    async def ainvoke(self, msgs):
        return AIMessage(content=self.reply_text)


class FakeSeqLLM:
    def __init__(self, items):
        self.items = list(items)

    async def ainvoke(self, msgs):
        if not self.items:
            return AIMessage(content="处理完成。")
        return self.items.pop(0)


class TestConversationHistoryArchitecture(unittest.IsolatedAsyncioTestCase):
    def test_1_three_turn_context_trimming_and_assistant_truncation(self):
        long_reply_turn4 = "详" * 500
        msgs = [
            HumanMessage(content="第1轮问题"),
            AIMessage(content="第1轮回答"),
            HumanMessage(content="第2轮问题"),
            AIMessage(content="第2轮回答"),
            HumanMessage(content="第3轮问题"),
            AIMessage(content="第3轮回答"),
            HumanMessage(content="第4轮问题"),
            AIMessage(content=long_reply_turn4),
            HumanMessage(content="第5轮问题"),
            AIMessage(content="第5轮回答"),
            HumanMessage(content="第6轮当前问题"),
        ]

        ctx_str, kept = summarize_and_trim_history(msgs, existing_summary="不应该保留的旧滚动摘要")

        self.assertEqual(len(kept), 6)
        self.assertNotIn("第1轮", ctx_str)
        self.assertNotIn("第2轮", ctx_str)
        self.assertNotIn("不应该保留的旧滚动摘要", ctx_str)
        self.assertNotIn("第6轮当前问题", ctx_str)
        self.assertIn("用户: 第3轮问题", ctx_str)
        self.assertIn("用户: 第5轮问题", ctx_str)
        self.assertIn("助手: 第5轮回答", ctx_str)

        # 第4轮超长助手回复应截断到 <= 301 字符（300 + 省略号）
        assistant_lines = [line for line in ctx_str.splitlines() if line.startswith("助手: 详")]
        self.assertEqual(len(assistant_lines), 1)
        body = assistant_lines[0].split("助手: ", 1)[1]
        self.assertEqual(len(body), 301)
        self.assertTrue(body.endswith("…"))

    async def test_2_sliding_7day_ttl_and_expired_cleanup_deletes_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conv_store = ConversationStore(db_path=str(Path(tmpdir) / "conv.sqlite"))
            tracer = AgentTracer(db_path=str(Path(tmpdir) / "trace.sqlite"))
            mgr = MultiAgentTicketManager(
                db_path=str(Path(tmpdir) / "ckpt.sqlite"),
                tracer=tracer,
                conv_store=conv_store,
                enable_cleanup_loop=False,
            )
            await mgr.startup()
            try:
                day0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
                await conv_store.record_turn(
                    thread_id="th-expire",
                    user_id=1001,
                    username="zhaolan",
                    user_message="周六上海演出什么时候开场？",
                    assistant_reply="19:30 开场。",
                    status="COMPLETED",
                    intents=["policy"],
                    subgraphs=["knowledge_agent"],
                    citations=[],
                    thinking_steps=[],
                    confirmation_card=None,
                    latency_ms=100,
                    now=day0,
                )
                await conv_store.record_turn(
                    thread_id="th-keep",
                    user_id=1001,
                    username="zhaolan",
                    user_message="想买两张星际回响",
                    assistant_reply="为您推荐 101 场次。",
                    status="COMPLETED",
                    intents=["purchase"],
                    subgraphs=["purchase_agent"],
                    citations=[],
                    thinking_steps=[],
                    confirmation_card=None,
                    latency_ms=120,
                    now=day0,
                )

                # 第 5 天继续活跃 th-keep，滑动顺延 7 天至第 12 天
                day5 = day0 + timedelta(days=5)
                await conv_store.record_turn(
                    thread_id="th-keep",
                    user_id=1001,
                    username="zhaolan",
                    user_message="700以内可以吗",
                    assistant_reply="可以，有两张680元连座。",
                    status="COMPLETED",
                    intents=["purchase"],
                    subgraphs=["purchase_agent"],
                    citations=[],
                    thinking_steps=[],
                    confirmation_card=None,
                    latency_ms=130,
                    now=day5,
                )

                day8 = day0 + timedelta(days=8)
                with patch.object(mgr._checkpointer, "adelete_thread", new=AsyncMock()) as del_ckpt:
                    removed = await mgr.cleanup_expired_threads(now=day8)

                self.assertEqual(removed, 1)
                del_ckpt.assert_awaited_once_with("th-expire")

                remaining = await conv_store.list_threads(user_id=1001, now=day8)
                self.assertEqual([t["threadId"] for t in remaining], ["th-keep"])
                self.assertEqual(await conv_store.get_thread_messages("th-expire"), [])
                self.assertEqual(len(await conv_store.get_thread_messages("th-keep")), 4)
            finally:
                await mgr.shutdown()

    def test_3_cross_user_thread_access_and_delete_return_403(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.main as main_mod

            orig_mgr = main_mod.ticket_agent_manager
            orig_tracer = main_mod.agent_tracer
            t_tracer = AgentTracer(db_path=str(Path(tmpdir) / "traces.sqlite"))
            t_conv = ConversationStore(db_path=str(Path(tmpdir) / "conv.sqlite"))
            t_mgr = MultiAgentTicketManager(
                db_path=str(Path(tmpdir) / "ckpt.sqlite"),
                tracer=t_tracer,
                conv_store=t_conv,
                enable_cleanup_loop=False,
            )
            main_mod.ticket_agent_manager = t_mgr
            main_mod.agent_tracer = t_tracer

            try:
                router_llm = FakeSimpleLLM('{"intents": ["chitchat"], "slots": {}}')
                chat_llm = FakeSimpleLLM("您好！可以帮您查演出、推荐连座和看退改政策。")
                with TestClient(app) as client, \
                     patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
                     patch("app.graph.main_graph.get_chat_llm", return_value=chat_llm):
                    r_chat = client.post(
                        "/api/agent/chat",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                        json={"threadId": "th-own-1", "message": "你好"},
                    )
                    self.assertEqual(r_chat.status_code, 200)

                    # 跨用户读取与删除必须 返回 403
                    r_get_other = client.get(
                        "/api/agent/threads/th-own-1",
                        headers={"X-User-Id": "2002", "X-Username": "other"},
                    )
                    self.assertEqual(r_get_other.status_code, 403)

                    r_del_other = client.delete(
                        "/api/agent/threads/th-own-1",
                        headers={"X-User-Id": "2002", "X-Username": "other"},
                    )
                    self.assertEqual(r_del_other.status_code, 403)

                    # 本人读取正常拿到 2 条展示消息
                    r_get_self = client.get(
                        "/api/agent/threads/th-own-1",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                    )
                    self.assertEqual(r_get_self.status_code, 200)
                    self.assertEqual(len(r_get_self.json()["data"]["messages"]), 2)
            finally:
                main_mod.ticket_agent_manager = orig_mgr
                main_mod.agent_tracer = orig_tracer

    def test_4_pending_card_expires_after_15_minutes_and_unblocks_chat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.main as main_mod

            orig_mgr = main_mod.ticket_agent_manager
            orig_tracer = main_mod.agent_tracer
            conv_db_path = str(Path(tmpdir) / "conv.sqlite")
            t_tracer = AgentTracer(db_path=str(Path(tmpdir) / "traces.sqlite"))
            t_conv = ConversationStore(db_path=conv_db_path)
            t_mgr = MultiAgentTicketManager(
                db_path=str(Path(tmpdir) / "ckpt.sqlite"),
                tracer=t_tracer,
                conv_store=t_conv,
                enable_cleanup_loop=False,
            )
            main_mod.ticket_agent_manager = t_mgr
            main_mod.agent_tracer = t_tracer

            mock_quote = AsyncMock(return_value={
                "ok": True,
                "isMock": False,
                "source": "Java show-service(:8202) quote",
                "quote": {
                    "showId": 1,
                    "showTitle": "「星际回响 · PulseLive」",
                    "venueName": "梅赛德斯",
                    "sessionId": 101,
                    "sessionTitle": "黄金场",
                    "seatIds": [20, 21],
                    "seatLabels": ["3排04座", "3排05座"],
                    "totalAmount": 1360.0,
                },
            })

            def _make_interrupt_llm():
                return FakeSeqLLM([
                    AIMessage(
                        content="",
                        tool_calls=[{
                            "id": "tc_exp_lock",
                            "name": "propose_create_order",
                            "args": {"session_id": 101, "seat_ids": [20, 21]},
                            "type": "tool_call",
                        }],
                    ),
                ])

            async def _backdate_pending(tid: str):
                stale_iso = to_iso_utc(datetime.now(timezone.utc) - timedelta(minutes=16))
                async with aiosqlite.connect(conv_db_path) as db:
                    await db.execute(
                        "UPDATE agent_conversation SET pending_created_at = ? WHERE thread_id = ?",
                        (stale_iso, tid),
                    )
                    await db.commit()

            try:
                with TestClient(app) as client, \
                     patch.object(pur_mod, "quote_seats_from_server", new=mock_quote):
                    # Subcase A: 卡片超 15 分钟后直接点 confirm 返回 400 且标记 EXPIRED
                    with patch.object(gr_mod, "get_chat_llm", return_value=FakeSimpleLLM('{"intents": ["purchase"], "slots": {"session_id": 101, "seat_ids": [20, 21], "wants_commit": true}}')), \
                         patch.object(pur_mod, "_get_purchase_llm", return_value=_make_interrupt_llm()):
                        r1 = client.post(
                            "/api/agent/chat",
                            headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                            json={"threadId": "th-exp-confirm", "message": "帮我锁定101场次20和21号座位下单"},
                        )
                        self.assertEqual(r1.status_code, 200)
                        self.assertEqual(r1.json()["data"]["status"], "INTERRUPTED")

                    import asyncio
                    asyncio.run(_backdate_pending("th-exp-confirm"))

                    r_conf = client.post(
                        "/api/agent/confirm",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                        json={"threadId": "th-exp-confirm", "confirmed": True},
                    )
                    self.assertEqual(r_conf.status_code, 400)
                    self.assertIn("15 分钟", r_conf.json()["detail"])

                    r_det_a = client.get(
                        "/api/agent/threads/th-exp-confirm",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                    )
                    self.assertEqual(r_det_a.status_code, 200)
                    msgs_a = r_det_a.json()["data"]["messages"]
                    self.assertEqual(msgs_a[-1]["cardDecision"], "EXPIRED")

                    # Subcase B: 卡片超 15 分钟后打开历史线程自动清理挂起态，后续提问不再被阻塞
                    with patch.object(gr_mod, "get_chat_llm", return_value=FakeSimpleLLM('{"intents": ["purchase"], "slots": {"session_id": 101, "seat_ids": [20, 21], "wants_commit": true}}')), \
                         patch.object(pur_mod, "_get_purchase_llm", return_value=_make_interrupt_llm()):
                        r2 = client.post(
                            "/api/agent/chat",
                            headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                            json={"threadId": "th-exp-unblock", "message": "锁定101场次20和21号座位下单"},
                        )
                        self.assertEqual(r2.json()["data"]["status"], "INTERRUPTED")

                    asyncio.run(_backdate_pending("th-exp-unblock"))

                    r_det_b = client.get(
                        "/api/agent/threads/th-exp-unblock",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                    )
                    self.assertEqual(r_det_b.status_code, 200)
                    det_b = r_det_b.json()["data"]
                    self.assertIsNone(det_b["pendingCard"])
                    self.assertTrue(det_b["pendingCardExpired"])
                    self.assertEqual(det_b["messages"][-1]["cardDecision"], "EXPIRED")

                    with patch.object(gr_mod, "get_chat_llm", return_value=FakeSimpleLLM('{"intents": ["chitchat"], "slots": {}}')), \
                         patch("app.graph.main_graph.get_chat_llm", return_value=FakeSimpleLLM("重新为您推荐上海近期演出。")):
                        r_next = client.post(
                            "/api/agent/chat",
                            headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                            json={"threadId": "th-exp-unblock", "message": "重新看看上海有哪些演出"},
                        )
                        self.assertEqual(r_next.status_code, 200)
                        self.assertEqual(r_next.json()["data"]["status"], "COMPLETED")
                        self.assertIn("重新为您推荐", r_next.json()["data"]["reply"])
            finally:
                main_mod.ticket_agent_manager = orig_mgr
                main_mod.agent_tracer = orig_tracer

    def test_5_rest_endpoints_list_detail_delete_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.main as main_mod

            orig_mgr = main_mod.ticket_agent_manager
            orig_tracer = main_mod.agent_tracer
            t_tracer = AgentTracer(db_path=str(Path(tmpdir) / "traces.sqlite"))
            t_conv = ConversationStore(db_path=str(Path(tmpdir) / "conv.sqlite"))
            t_mgr = MultiAgentTicketManager(
                db_path=str(Path(tmpdir) / "ckpt.sqlite"),
                tracer=t_tracer,
                conv_store=t_conv,
                enable_cleanup_loop=False,
            )
            main_mod.ticket_agent_manager = t_mgr
            main_mod.agent_tracer = t_tracer

            try:
                router_llm = FakeSimpleLLM('{"intents": ["policy"], "slots": {}}')
                kg_llm = FakeSimpleLLM("距离开演前 168 小时以上免费退票 [POL-REFUND-001]。")
                with TestClient(app) as client, \
                     patch.object(gr_mod, "get_chat_llm", return_value=router_llm), \
                     patch.object(kg_mod, "get_chat_llm", return_value=kg_llm):
                    r_health = client.get("/api/agent/health")
                    self.assertEqual(r_health.status_code, 200)
                    h = r_health.json()
                    self.assertEqual(h["conversationBackend"], "sqlite-fallback")
                    self.assertEqual(h["conversationTtlDays"], 7)
                    self.assertEqual(h["contextMaxTurns"], 3)

                    r_chat = client.post(
                        "/api/agent/chat",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                        json={"threadId": "th-flow-1", "message": "演出前三天能退票吗？"},
                    )
                    self.assertEqual(r_chat.status_code, 200)

                    r_list = client.get(
                        "/api/agent/threads",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                    )
                    self.assertEqual(r_list.status_code, 200)
                    threads = r_list.json()["data"]["threads"]
                    self.assertEqual(len(threads), 1)
                    self.assertEqual(threads[0]["threadId"], "th-flow-1")
                    self.assertEqual(threads[0]["messageCount"], 2)
                    self.assertTrue(threads[0]["expiresAt"])

                    r_det = client.get(
                        "/api/agent/threads/th-flow-1",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                    )
                    self.assertEqual(r_det.status_code, 200)
                    self.assertEqual(len(r_det.json()["data"]["messages"]), 2)

                    r_del = client.delete(
                        "/api/agent/threads/th-flow-1",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                    )
                    self.assertEqual(r_del.status_code, 200)
                    self.assertTrue(r_del.json()["data"]["deleted"])

                    r_gone = client.get(
                        "/api/agent/threads/th-flow-1",
                        headers={"X-User-Id": "1001", "X-Username": "zhaolan"},
                    )
                    self.assertEqual(r_gone.status_code, 404)
            finally:
                main_mod.ticket_agent_manager = orig_mgr
                main_mod.agent_tracer = orig_tracer


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

os.environ["DEV_MOCK"] = "false"

from app.config import settings  # noqa: E402
from app.rag import hybrid_rag as hr_mod  # noqa: E402
from app.rag.hybrid_rag import (  # noqa: E402
    TicketHybridRagEngine,
    _PgVectorDenseStore,
    _split_long_section,
)


class TestPgVectorHybridRag(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_key = settings.EMBEDDING_API_KEY
        self._orig_dsn = settings.PGVECTOR_DSN
        self._orig_rerank = settings.RERANK_ENABLED
        self._orig_table = settings.PGVECTOR_TABLE
        hr_mod._QUERY_VEC_CACHE.clear()

    def tearDown(self):
        settings.EMBEDDING_API_KEY = self._orig_key
        settings.PGVECTOR_DSN = self._orig_dsn
        settings.RERANK_ENABLED = self._orig_rerank
        settings.PGVECTOR_TABLE = self._orig_table
        hr_mod._QUERY_VEC_CACHE.clear()

    def test_1_long_section_secondary_chunking_with_overlap(self):
        sentences = [f"第{i}条规则说明：观众需提前到场凭身份证与动态核验码入场，严禁携带违禁品。" for i in range(1, 26)]
        section = "## 一、入场规范\n" + "\n".join(sentences)
        doc_prefix = "[POL-TEST-001 《测试规则》]"

        chunks = _split_long_section(section, doc_prefix=doc_prefix, max_chars=260, overlap=40)

        self.assertGreater(len(chunks), 2)
        for c in chunks:
            self.assertTrue(c.startswith(doc_prefix))
            self.assertIn("## 一、入场规范", c)
            self.assertLessEqual(len(c), 260)

        # 相邻块正文末尾与下一块正文开头应存在重叠片段
        body0 = chunks[0].split("## 一、入场规范\n", 1)[1]
        body1 = chunks[1].split("## 一、入场规范\n", 1)[1]
        overlap_tail = body0[-20:]
        self.assertIn(overlap_tail, body1)

    async def test_2_bm25_only_degraded_mode_without_api_key(self):
        settings.EMBEDDING_API_KEY = ""
        settings.PGVECTOR_DSN = ""
        engine = TicketHybridRagEngine()
        count = engine.load_and_index()

        self.assertGreaterEqual(count, 10)
        self.assertTrue(engine.backend_info["degraded"])
        self.assertEqual(engine.backend_info["dim"], 0)
        self.assertEqual(engine.backend_info["vectorStore"], "bm25-only-degraded")

        hits = await engine.search("开演前三天临时去不了能退票吗扣多少", top_k=3)
        self.assertEqual(hits[0]["docCode"], "POL-REFUND-001")
        self.assertEqual(hits[0]["vectorScore"], 0.0)
        self.assertTrue(hits[0]["degraded"])
        self.assertEqual(hits[0]["retrievalBackend"], "bm25-only-degraded")

    async def test_3_hybrid_rrf_and_cross_encoder_rerank_preserve_final_score(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            kb = Path(tmpdir)
            (kb / "01_a.md").write_text("# [POL-A-001] 文档A\n## 退票\n普通退票手续费十元。\n", encoding="utf-8")
            (kb / "02_b.md").write_text("# [POL-B-002] 文档B\n## 交通\n地铁八号线直达场馆。\n", encoding="utf-8")
            (kb / "03_c.md").write_text("# [POL-C-003] 文档C\n## 寄存\n现场设有免费寄存柜。\n", encoding="utf-8")

            settings.EMBEDDING_API_KEY = "sk-test-fake"
            settings.PGVECTOR_DSN = ""
            settings.RERANK_ENABLED = True

            engine = TicketHybridRagEngine(knowledge_dir=kb)
            chunk_vecs = np.array([
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
            ], dtype=np.float32)
            query_vec = np.array([[0.9, 0.1, 0.0, 0.0]], dtype=np.float32)

            async def fake_rerank(query, candidates):
                # 故意把最后一个候选排第一，验证 Cross-Encoder 重排顺序生效但 finalScore 不被覆盖
                reordered = []
                for idx, item in enumerate(reversed(candidates)):
                    copy_item = dict(item)
                    copy_item["rerankScore"] = round(0.95 - idx * 0.2, 4)
                    reordered.append(copy_item)
                return reordered, True

            with patch.object(hr_mod, "_remote_embed_sync", return_value=chunk_vecs), \
                 patch.object(hr_mod, "_remote_embed_async", new=AsyncMock(return_value=query_vec)), \
                 patch.object(hr_mod, "_rerank_async", side_effect=fake_rerank):
                cnt = engine.load_and_index()
                self.assertEqual(cnt, 3)
                self.assertFalse(engine.backend_info["degraded"])
                self.assertEqual(engine.backend_info["dim"], 4)

                hits = await engine.search("退票手续费", top_k=3)
                self.assertEqual(hits[0]["docCode"], "POL-C-003")
                self.assertEqual(hits[0]["rerankScore"], 0.95)
                self.assertIn("cross-encoder-rerank", hits[0]["retrievalBackend"])
                # finalScore 保留融合尺度，未被 rerankScore 覆盖
                self.assertNotEqual(hits[0]["finalScore"], hits[0]["rerankScore"])

    def test_4_pgvector_store_creates_extension_hnsw_index_and_queries_cosine(self):
        settings.PGVECTOR_DSN = "postgresql://u:p@127.0.0.1:5432/db"
        settings.PGVECTOR_TABLE = "ticket_kb_chunks"

        executed_sql = []

        class FakeCursor:
            def __init__(self, rows=None):
                self._rows = rows or []

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return self._rows[0] if self._rows else None

        fake_conn = MagicMock()
        fake_conn.closed = False

        def fake_execute(sql, params=None):
            executed_sql.append((sql.strip(), params))
            if "FROM ticket_kb_meta" in sql:
                return FakeCursor([])
            if "ORDER BY embedding <=>" in sql:
                return FakeCursor([(0, 0.9321), (1, 0.4123)])
            return FakeCursor([])

        fake_conn.execute.side_effect = fake_execute

        fake_psycopg = MagicMock()
        fake_psycopg.connect.return_value = fake_conn

        store = _PgVectorDenseStore()
        with patch.dict(sys.modules, {"psycopg": fake_psycopg}):
            ok = store.connect()
            self.assertTrue(ok)
            self.assertTrue(store.available)

            store.ensure_index(
                dim=4,
                model_name="BAAI/bge-m3",
                fingerprint="fp-1",
                rows=[
                    {
                        "idx": 0,
                        "docCode": "POL-A",
                        "docTitle": "A",
                        "sourceFile": "a.md",
                        "content": "hello",
                        "vector": np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
                    }
                ],
            )
            scores = store.query(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), limit=3)

        self.assertAlmostEqual(scores[0], 0.9321, places=4)
        self.assertAlmostEqual(scores[1], 0.4123, places=4)

        all_sql = "\n".join(s for s, _ in executed_sql)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", all_sql)
        self.assertIn("USING hnsw (embedding vector_cosine_ops)", all_sql)
        self.assertIn("embedding <=> %s::vector", all_sql)

    async def test_5_main_graph_manager_uses_async_postgres_saver_when_dsn_configured(self):
        from psycopg.rows import dict_row
        from app.graph import main_graph as mg_mod
        from app.observability.tracer import AgentTracer
        from app.storage.conversation_store import ConversationStore

        mock_tracer = MagicMock(spec=AgentTracer)
        mock_tracer.ensure_schema = AsyncMock()
        mock_conv_store = MagicMock(spec=ConversationStore)
        mock_conv_store.ensure_schema = AsyncMock()

        mgr = mg_mod.MultiAgentTicketManager(
            postgres_dsn="postgresql://user:pass@127.0.0.1:5432/db",
            tracer=mock_tracer,
            conv_store=mock_conv_store,
            enable_cleanup_loop=False,
        )
        self.assertEqual(mgr.checkpoint_backend, "postgresql-async")

        fake_pool = MagicMock()
        fake_pool.open = AsyncMock()
        fake_pool.close = AsyncMock()

        fake_saver = MagicMock()
        fake_saver.setup = AsyncMock()

        fake_builder = MagicMock()
        fake_builder.compile.return_value = "COMPILED_PG_GRAPH"

        with patch("psycopg_pool.AsyncConnectionPool", return_value=fake_pool) as pool_cls, \
             patch("langgraph.checkpoint.postgres.aio.AsyncPostgresSaver", return_value=fake_saver) as saver_cls, \
             patch.object(mg_mod, "build_main_agent_graph", return_value=fake_builder):
            await mgr.startup()

            pool_cls.assert_called_once_with(
                conninfo="postgresql://user:pass@127.0.0.1:5432/db",
                min_size=1,
                max_size=5,
                open=False,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            )
            fake_pool.open.assert_awaited_once()
            saver_cls.assert_called_once_with(fake_pool)
            fake_saver.setup.assert_awaited_once()
            mock_tracer.ensure_schema.assert_awaited_once()
            mock_conv_store.ensure_schema.assert_awaited_once()
            fake_builder.compile.assert_called_once_with(checkpointer=fake_saver)
            self.assertEqual(mgr.graph, "COMPILED_PG_GRAPH")

            await mgr.shutdown()
            fake_pool.close.assert_awaited_once()
            self.assertIsNone(mgr.graph)

    async def test_6_agent_tracer_uses_postgresql_and_maps_snake_case_summary(self):
        from psycopg.rows import dict_row
        from app.observability.tracer import AgentTracer

        tracer = AgentTracer(postgres_dsn="postgresql://user:pass@127.0.0.1:5432/db")
        self.assertEqual(tracer.backend_name, "postgresql")

        executed = []

        class FakeAsyncCursor:
            def __init__(self, rows=None):
                self._rows = rows or []

            async def fetchall(self):
                return self._rows

            async def fetchone(self):
                return self._rows[0] if self._rows else None

        class FakeAsyncConn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, sql, params=None):
                executed.append((sql.strip(), params))
                if "ORDER BY id DESC LIMIT %s" in sql:
                    return FakeAsyncCursor([
                        {
                            "id": 1,
                            "trace_id": "ag-test-pg",
                            "thread_id": "th-pg-1",
                            "user_id": 1,
                            "username": "zhaolan",
                            "endpoint": "chat",
                            "user_message": "退票手续费是多少？",
                            "intents_json": '["policy"]',
                            "subgraphs_json": '["knowledge_agent"]',
                            "tool_steps_json": "[]",
                            "citations_json": '["POL-REFUND-001"]',
                            "guard_blocked": 0,
                            "interrupted": 0,
                            "write_executed": 0,
                            "latency_ms": 1420,
                            "status": "COMPLETED",
                            "reply_preview": "免收手续费",
                            "created_at": "2026-11-21 18:00:00",
                        }
                    ])
                if "total_runs" in sql:
                    return FakeAsyncCursor([
                        {
                            "total_runs": 1,
                            "guard_blocked_count": 0,
                            "interrupted_count": 0,
                            "write_executed_count": 0,
                            "avg_latency_ms": 1420,
                        }
                    ])
                return FakeAsyncCursor([])

        with patch("psycopg.AsyncConnection.connect", new=AsyncMock(return_value=FakeAsyncConn())) as conn_fn:
            tid = await tracer.record_trace(
                trace_id="ag-test-pg",
                thread_id="th-pg-1",
                user_id=1,
                username="zhaolan",
                endpoint="chat",
                user_message="退票手续费是多少？",
                intents=["policy"],
                subgraphs=["knowledge_agent"],
                tool_steps=[],
                citations=["POL-REFUND-001"],
                latency_ms=1420,
                reply="免收手续费",
            )
            self.assertEqual(tid, "ag-test-pg")

            res = await tracer.list_recent_traces(limit=5)
            conn_fn.assert_awaited_with(
                "postgresql://user:pass@127.0.0.1:5432/db",
                autocommit=True,
                row_factory=dict_row,
            )

        self.assertEqual(res["summary"]["totalRuns"], 1)
        self.assertEqual(res["summary"]["avgLatencyMs"], 1420)
        self.assertEqual(res["traces"][0]["traceId"], "ag-test-pg")
        all_sql = "\n".join(s for s, _ in executed)
        self.assertIn("CREATE TABLE IF NOT EXISTS agent_trace_log", all_sql)
        self.assertIn("BIGSERIAL PRIMARY KEY", all_sql)
        self.assertIn("VALUES (%s,", all_sql)


if __name__ == "__main__":
    unittest.main()

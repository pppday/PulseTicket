from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, AsyncIterator, Dict, List, Optional

import aiosqlite

from app.config import settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(raw: Optional[str]) -> Optional[datetime]:
    val = (raw or "").strip()
    if not val:
        return None
    try:
        if val.endswith("Z"):
            return datetime.strptime(val, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(val)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def make_thread_title(first_message: str, max_len: int = 20) -> str:
    clean = re.sub(r"\s+", " ", (first_message or "")).strip()
    if not clean:
        return "新对话"
    if len(clean) <= max_len:
        return clean
    return clean[:max_len] + "…"


def make_preview(text: str, max_len: int = 80) -> str:
    clean = re.sub(r"[#*_`>]+", "", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) <= max_len:
        return clean
    return clean[:max_len] + "…"


class ConversationStore:
    """
    展示层会话历史存储（PostgreSQL 16 优先，无 DSN 或测试显式传入 db_path 时降级 SQLite）：
    - 与 LangGraph Checkpointer / 模型上下文窗口解耦，专责前端 7 天滑动过期列表与消息回显。
    """

    def __init__(self, db_path: Optional[str] = None, postgres_dsn: Optional[str] = None):
        if db_path is not None:
            self._use_postgres = False
            self.db_path = db_path
            self.postgres_dsn = ""
        else:
            dsn = (postgres_dsn if postgres_dsn is not None else settings.CHECKPOINT_POSTGRES_DSN).strip()
            if dsn:
                self._use_postgres = True
                self.postgres_dsn = dsn
                self.db_path = ""
            else:
                self._use_postgres = False
                self.postgres_dsn = ""
                default_dir = Path(settings.CHECKPOINT_DB_PATH).resolve().parent
                self.db_path = str(default_dir / "ticket_agent_conversations.sqlite")
        self._initialized = False

    @property
    def backend_name(self) -> str:
        return "postgresql" if self._use_postgres else "sqlite-fallback"

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[Any]:
        if self._use_postgres:
            import psycopg
            from psycopg.rows import dict_row

            async with await psycopg.AsyncConnection.connect(
                self.postgres_dsn,
                autocommit=True,
                row_factory=dict_row,
            ) as conn:
                yield conn
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                yield conn

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self._use_postgres else sql

    async def _execute(self, conn: Any, sql: str, params: tuple = ()):
        return await conn.execute(self._sql(sql), params)

    async def _fetchone(self, conn: Any, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        cur = await self._execute(conn, sql, params)
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def _fetchall(self, conn: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cur = await self._execute(conn, sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _commit_if_sqlite(self, conn: Any) -> None:
        if not self._use_postgres:
            await conn.commit()

    async def ensure_schema(self) -> None:
        if self._initialized:
            return
        id_col = "id BIGSERIAL PRIMARY KEY" if self._use_postgres else "id INTEGER PRIMARY KEY AUTOINCREMENT"
        user_id_type = "BIGINT" if self._use_postgres else "INTEGER"
        flag_type = "INTEGER"

        async with self._connect() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS agent_conversation (
                    thread_id TEXT PRIMARY KEY,
                    user_id {user_id_type} NOT NULL,
                    username TEXT NOT NULL,
                    title TEXT NOT NULL,
                    last_preview TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    pending_card_json TEXT,
                    pending_created_at TEXT,
                    message_count {flag_type} NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_user_updated ON agent_conversation(user_id, updated_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_expires ON agent_conversation(expires_at)"
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS agent_message (
                    {id_col},
                    thread_id TEXT NOT NULL,
                    user_id {user_id_type} NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'COMPLETED',
                    mode TEXT NOT NULL DEFAULT 'agent',
                    intents_json TEXT NOT NULL DEFAULT '[]',
                    subgraphs_json TEXT NOT NULL DEFAULT '[]',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    thinking_steps_json TEXT NOT NULL DEFAULT '[]',
                    tool_cards_json TEXT NOT NULL DEFAULT '[]',
                    widgets_json TEXT NOT NULL DEFAULT '[]',
                    confirmation_card_json TEXT,
                    card_decision TEXT,
                    latency_ms {flag_type} NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_thread_id ON agent_message(thread_id, id ASC)"
            )
            if self._use_postgres:
                await conn.execute(
                    "ALTER TABLE agent_message ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'agent'"
                )
                await conn.execute(
                    "ALTER TABLE agent_message ADD COLUMN IF NOT EXISTS tool_cards_json TEXT NOT NULL DEFAULT '[]'"
                )
                await conn.execute(
                    "ALTER TABLE agent_message ADD COLUMN IF NOT EXISTS widgets_json TEXT NOT NULL DEFAULT '[]'"
                )
            else:
                cols = {r["name"] for r in await self._fetchall(conn, "PRAGMA table_info(agent_message)")}
                if "mode" not in cols:
                    await conn.execute(
                        "ALTER TABLE agent_message ADD COLUMN mode TEXT NOT NULL DEFAULT 'agent'"
                    )
                if "tool_cards_json" not in cols:
                    await conn.execute(
                        "ALTER TABLE agent_message ADD COLUMN tool_cards_json TEXT NOT NULL DEFAULT '[]'"
                    )
                if "widgets_json" not in cols:
                    await conn.execute(
                        "ALTER TABLE agent_message ADD COLUMN widgets_json TEXT NOT NULL DEFAULT '[]'"
                    )
            await self._commit_if_sqlite(conn)
        self._initialized = True

    async def get_conversation(self, thread_id: str) -> Optional[Dict[str, Any]]:
        await self.ensure_schema()
        async with self._connect() as conn:
            return await self._fetchone(
                conn,
                "SELECT * FROM agent_conversation WHERE thread_id = ?",
                (thread_id,),
            )

    async def assert_owner(self, thread_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        conv = await self.get_conversation(thread_id)
        if conv is not None and int(conv["user_id"]) != int(user_id):
            raise PermissionError("无权操作其他用户的对话线程")
        return conv

    @staticmethod
    def is_pending_expired(conv: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
        if not conv or not conv.get("pending_card_json"):
            return False
        pending_at = parse_iso_utc(conv.get("pending_created_at"))
        if pending_at is None:
            return False
        now_dt = now or utc_now()
        return (now_dt - pending_at) > timedelta(minutes=settings.CONFIRM_CARD_TTL_MINUTES)

    async def mark_pending_expired(self, thread_id: str) -> None:
        await self.ensure_schema()
        async with self._connect() as conn:
            await self._execute(
                conn,
                """
                UPDATE agent_conversation
                   SET pending_card_json = NULL,
                       pending_created_at = NULL,
                       status = 'ACTIVE'
                 WHERE thread_id = ?
                """,
                (thread_id,),
            )
            await self._execute(
                conn,
                """
                UPDATE agent_message
                   SET card_decision = 'EXPIRED'
                 WHERE thread_id = ? AND card_decision = 'PENDING'
                """,
                (thread_id,),
            )
            await self._commit_if_sqlite(conn)

    async def record_turn(
        self,
        *,
        thread_id: str,
        user_id: int,
        username: str,
        user_message: str,
        assistant_reply: str,
        status: str,
        mode: str = "agent",
        intents: List[str],
        subgraphs: List[str],
        citations: List[str],
        thinking_steps: List[Dict[str, Any]],
        tool_cards: Optional[List[Dict[str, Any]]] = None,
        widgets: Optional[List[Dict[str, Any]]] = None,
        confirmation_card: Optional[Dict[str, Any]],
        latency_ms: int,
        record_user_msg: bool = True,
        resolve_pending_decision: Optional[str] = None,
        preserve_existing_pending: bool = False,
        now: Optional[datetime] = None,
    ) -> List[str]:
        """写入本轮展示消息，刷新 7 天滑动过期时间，并裁剪超限历史消息/会话。返回被淘汰的旧 thread_id 列表。"""
        await self.ensure_schema()
        now_dt = now or utc_now()
        now_iso = to_iso_utc(now_dt)
        expires_iso = to_iso_utc(now_dt + timedelta(days=settings.CONVERSATION_TTL_DAYS))

        evicted_threads: List[str] = []
        async with self._connect() as conn:
            existing = await self._fetchone(
                conn,
                "SELECT * FROM agent_conversation WHERE thread_id = ?",
                (thread_id,),
            )
            if existing is not None and int(existing["user_id"]) != int(user_id):
                raise PermissionError("无权操作其他用户的对话线程")

            if resolve_pending_decision:
                await self._execute(
                    conn,
                    """
                    UPDATE agent_message
                       SET card_decision = ?
                     WHERE thread_id = ? AND card_decision = 'PENDING'
                    """,
                    (resolve_pending_decision, thread_id),
                )

            insert_msg_sql = """
                INSERT INTO agent_message (
                    thread_id, user_id, role, content, status, mode,
                    intents_json, subgraphs_json, citations_json, thinking_steps_json,
                    tool_cards_json, widgets_json,
                    confirmation_card_json, card_decision, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            eff_mode = "chat" if str(mode or "").strip().lower() == "chat" else "agent"

            if record_user_msg and (user_message or "").strip():
                await self._execute(
                    conn,
                    insert_msg_sql,
                    (
                        thread_id,
                        int(user_id),
                        "user",
                        (user_message or "").strip(),
                        "COMPLETED",
                        eff_mode,
                        "[]",
                        "[]",
                        "[]",
                        "[]",
                        "[]",
                        "[]",
                        None,
                        None,
                        0,
                        now_iso,
                    ),
                )

            msg_card = None if preserve_existing_pending else confirmation_card
            msg_card_json = json.dumps(msg_card, ensure_ascii=False) if msg_card else None
            msg_card_decision = "PENDING" if msg_card else None

            await self._execute(
                conn,
                insert_msg_sql,
                (
                    thread_id,
                    int(user_id),
                    "assistant",
                    assistant_reply or "",
                    status or "COMPLETED",
                    eff_mode,
                    json.dumps(intents or [], ensure_ascii=False),
                    json.dumps(subgraphs or [], ensure_ascii=False),
                    json.dumps(citations or [], ensure_ascii=False),
                    json.dumps(thinking_steps or [], ensure_ascii=False),
                    json.dumps(tool_cards or [], ensure_ascii=False),
                    json.dumps(widgets or [], ensure_ascii=False),
                    msg_card_json,
                    msg_card_decision,
                    max(0, int(latency_ms)),
                    now_iso,
                ),
            )

            # 限制单会话最多保留 CONVERSATION_MAX_MESSAGES_PER_THREAD 条展示消息
            max_msgs = max(10, settings.CONVERSATION_MAX_MESSAGES_PER_THREAD)
            msg_id_rows = await self._fetchall(
                conn,
                "SELECT id FROM agent_message WHERE thread_id = ? ORDER BY id DESC",
                (thread_id,),
            )
            if len(msg_id_rows) > max_msgs:
                drop_ids = [r["id"] for r in msg_id_rows[max_msgs:]]
                placeholders = ", ".join(["?"] * len(drop_ids))
                await self._execute(
                    conn,
                    f"DELETE FROM agent_message WHERE id IN ({placeholders})",
                    tuple(drop_ids),
                )
                msg_count = max_msgs
            else:
                msg_count = len(msg_id_rows)

            if existing is not None:
                title = existing["title"]
                created_at = existing["created_at"]
            else:
                title = make_thread_title(user_message or assistant_reply)
                created_at = now_iso

            if preserve_existing_pending and existing and existing.get("pending_card_json"):
                conv_pending_json = existing["pending_card_json"]
                conv_pending_at = existing.get("pending_created_at") or now_iso
                conv_status = "INTERRUPTED"
            elif confirmation_card:
                conv_pending_json = json.dumps(confirmation_card, ensure_ascii=False)
                if existing and existing.get("pending_card_json") and existing.get("pending_created_at"):
                    conv_pending_at = existing["pending_created_at"]
                else:
                    conv_pending_at = now_iso
                conv_status = "INTERRUPTED"
            else:
                conv_pending_json = None
                conv_pending_at = None
                conv_status = "ACTIVE"

            preview = make_preview(assistant_reply or user_message)
            await self._execute(
                conn,
                """
                INSERT INTO agent_conversation (
                    thread_id, user_id, username, title, last_preview, status,
                    pending_card_json, pending_created_at, message_count,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    username = excluded.username,
                    last_preview = excluded.last_preview,
                    status = excluded.status,
                    pending_card_json = excluded.pending_card_json,
                    pending_created_at = excluded.pending_created_at,
                    message_count = excluded.message_count,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    thread_id,
                    int(user_id),
                    username,
                    title,
                    preview,
                    conv_status,
                    conv_pending_json,
                    conv_pending_at,
                    msg_count,
                    created_at,
                    now_iso,
                    expires_iso,
                ),
            )

            # 限制单用户最多保留 CONVERSATION_MAX_THREADS_PER_USER 个会话
            max_threads = max(1, settings.CONVERSATION_MAX_THREADS_PER_USER)
            user_threads = await self._fetchall(
                conn,
                "SELECT thread_id FROM agent_conversation WHERE user_id = ? ORDER BY updated_at DESC, thread_id DESC",
                (int(user_id),),
            )
            if len(user_threads) > max_threads:
                evicted_threads = [r["thread_id"] for r in user_threads[max_threads:]]
                placeholders = ", ".join(["?"] * len(evicted_threads))
                await self._execute(
                    conn,
                    f"DELETE FROM agent_message WHERE thread_id IN ({placeholders})",
                    tuple(evicted_threads),
                )
                await self._execute(
                    conn,
                    f"DELETE FROM agent_conversation WHERE thread_id IN ({placeholders})",
                    tuple(evicted_threads),
                )

            await self._commit_if_sqlite(conn)

        return evicted_threads

    async def list_threads(
        self,
        user_id: int,
        limit: int = 30,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        await self.ensure_schema()
        now_dt = now or utc_now()
        now_iso = to_iso_utc(now_dt)
        cap = max(1, min(settings.CONVERSATION_MAX_THREADS_PER_USER, int(limit)))

        async with self._connect() as conn:
            rows = await self._fetchall(
                conn,
                """
                SELECT thread_id, user_id, title, last_preview, status,
                       pending_card_json, pending_created_at, message_count,
                       created_at, updated_at, expires_at
                  FROM agent_conversation
                 WHERE user_id = ? AND expires_at > ?
                 ORDER BY updated_at DESC, thread_id DESC
                 LIMIT ?
                """,
                (int(user_id), now_iso, cap),
            )

        out: List[Dict[str, Any]] = []
        for r in rows:
            has_pending = bool(r.get("pending_card_json")) and not self.is_pending_expired(r, now=now_dt)
            out.append({
                "threadId": r["thread_id"],
                "title": r["title"],
                "lastPreview": r["last_preview"],
                "status": "INTERRUPTED" if has_pending else "ACTIVE",
                "hasPendingCard": has_pending,
                "messageCount": int(r["message_count"] or 0),
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
                "expiresAt": r["expires_at"],
            })
        return out

    async def get_thread_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        await self.ensure_schema()
        async with self._connect() as conn:
            rows = await self._fetchall(
                conn,
                "SELECT * FROM agent_message WHERE thread_id = ? ORDER BY id ASC",
                (thread_id,),
            )

        messages: List[Dict[str, Any]] = []
        for r in rows:
            card = json.loads(r["confirmation_card_json"]) if r.get("confirmation_card_json") else None
            messages.append({
                "id": f"db-{r['id']}",
                "role": r["role"],
                "content": r["content"],
                "status": r.get("status") or None,
                "mode": r.get("mode") or "agent",
                "intents": json.loads(r.get("intents_json") or "[]"),
                "subgraphs": json.loads(r.get("subgraphs_json") or "[]"),
                "citations": json.loads(r.get("citations_json") or "[]"),
                "thinkingSteps": json.loads(r.get("thinking_steps_json") or "[]"),
                "toolCards": json.loads(r.get("tool_cards_json") or "[]"),
                "widgets": json.loads(r.get("widgets_json") or "[]"),
                "thinkingExpanded": False,
                "userToggledThinking": False,
                "confirmationCard": card,
                "cardDecision": r.get("card_decision") or None,
                "latencyMs": int(r["latency_ms"]) if r.get("latency_ms") else None,
                "createdAt": r["created_at"],
            })
        return messages

    async def delete_thread(self, thread_id: str, user_id: int) -> bool:
        await self.ensure_schema()
        conv = await self.assert_owner(thread_id, user_id)
        if conv is None:
            return False
        async with self._connect() as conn:
            await self._execute(conn, "DELETE FROM agent_message WHERE thread_id = ?", (thread_id,))
            await self._execute(conn, "DELETE FROM agent_conversation WHERE thread_id = ?", (thread_id,))
            await self._commit_if_sqlite(conn)
        return True

    async def purge_expired_threads(self, now: Optional[datetime] = None) -> List[str]:
        await self.ensure_schema()
        now_iso = to_iso_utc(now or utc_now())
        async with self._connect() as conn:
            rows = await self._fetchall(
                conn,
                "SELECT thread_id FROM agent_conversation WHERE expires_at <= ?",
                (now_iso,),
            )
            expired_ids = [r["thread_id"] for r in rows]
            if expired_ids:
                placeholders = ", ".join(["?"] * len(expired_ids))
                await self._execute(
                    conn,
                    f"DELETE FROM agent_message WHERE thread_id IN ({placeholders})",
                    tuple(expired_ids),
                )
                await self._execute(
                    conn,
                    f"DELETE FROM agent_conversation WHERE thread_id IN ({placeholders})",
                    tuple(expired_ids),
                )
                await self._commit_if_sqlite(conn)
        return expired_ids


conversation_store = ConversationStore()

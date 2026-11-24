"""
【票务知识库双路混合检索引擎（Hybrid RAG）】

通俗理解：这是智能体的“政策档案馆管理员”，负责把 `knowledge/*.md` 里的退票条款、实名制、场馆路线切块建索引，并用双路召回 + 重排把最准的条款翻出来。

完整检索链路（面试高频考点）：
1. 语义分片：按 Markdown 二级标题（`##`）拆块，超长章节再用 500 字滑动窗口 + 50 字重叠切分，避免把一条规则截成两半；
2. 稀疏检索（Sparse）：`jieba` 中文分词 + 领域同义词桥接 + `BM25Okapi`，擅长精确命中数字（如 1.2 米、48 小时）、站名和条款编号；
3. 稠密检索（Dense）：远程 Embedding 模型（`BAAI/bge-m3`）生成向量存入 PostgreSQL `pgvector`（HNSW 余弦索引），理解口语化语义；
4. 融合与精排：先做 RRF（倒数秩融合）合并双路候选，再调用 Cross-Encoder（`BAAI/bge-reranker-v2-m3`）做细粒度打分重排；
5. 优雅降级：本地没配 Embedding Key 或无 PG 时不造假向量，诚实降级为内存矩阵或 BM25-only 并标记 `degraded=True`。
"""

import atexit
from collections import OrderedDict
import hashlib
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx
import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from app.config import settings

logger = logging.getLogger(__name__)

STOPWORDS = {
    "的", "了", "呢", "吗", "啊", "吧", "呀", "我", "你", "他", "是", "在", "有", "和", "与",
    "就", "都", "而", "及", "着", "或", "一个", "请问", "你好", "您好", "这个", "那个", "怎么", "可以",
    "想问", "一下", "帮我", "看看", "能不能"
}

# 票务领域同义词语义桥接词典（增强 BM25 稀疏检索对口语化表达的召回能力）
TICKET_SYNONYM_BRIDGES: List[Tuple[List[str], str]] = [
    (["退票", "退款", "退单", "退订", "全额退", "退钱", "不去", "去不了", "临时有事", "手续费", "扣多少", "改签", "换时间", "取消订单"],
     "阶梯退票 手续费率 开演前48小时 开演前7天 免费退票 停止退票 UNPAID POL-REFUND-001"),
    (["实名", "身份证", "小孩", "儿童", "身高", "检票", "核验", "二维码", "迟到", "入场"],
     "强实名制 一人一证一票 动态电子票核验码 1.2米儿童全价票 幕间休息 POL-VERIFY-002"),
    (["地铁", "怎么走", "路线", "停车", "梅赛德斯", "天桥", "万象城", "大麦", "上海", "北京", "深圳", "杭州", "梅溪湖", "长沙", "雨伞", "长柄伞", "喝水", "寄存", "存包"],
     "场馆交通 地铁出站 梅赛德斯-奔驰文化中心 天桥艺术中心 万象城IMAX 大麦66 禁带物品 长柄雨伞 免费自助存包柜 寄存处 POL-VENUE-003 POL-VENUE-005"),
    (["连座", "挨着", "坐一起", "选座", "锁座", "多久支付", "15分钟", "超时", "自动取消"],
     "智能选座 同排连续相邻 中轴线 Redisson分布式锁 15分钟倒计时 RabbitMQ死信关单 POL-LOCK-004"),
]

_EMBED_BATCH_SIZE = 16
_QUERY_CACHE_MAX = 512
_QUERY_VEC_CACHE: "OrderedDict[Tuple[str, str], np.ndarray]" = OrderedDict()
_VALID_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


def _vec_to_pg_literal(vec: List[float] | np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


def _cache_get(model_name: str, text: str) -> Optional[np.ndarray]:
    key = (model_name, text)
    vec = _QUERY_VEC_CACHE.get(key)
    if vec is not None:
        _QUERY_VEC_CACHE.move_to_end(key)
    return vec


def _cache_put(model_name: str, text: str, vec: np.ndarray) -> None:
    key = (model_name, text)
    _QUERY_VEC_CACHE[key] = vec
    _QUERY_VEC_CACHE.move_to_end(key)
    while len(_QUERY_VEC_CACHE) > _QUERY_CACHE_MAX:
        _QUERY_VEC_CACHE.popitem(last=False)


def expand_ticket_concepts(text: str) -> str:
    lower = (text or "").lower()
    extra = []
    for triggers, expansion in TICKET_SYNONYM_BRIDGES:
        if any(t in lower for t in triggers):
            extra.append(expansion)
    return (text + " " + " ".join(extra)).strip() if extra else text


def tokenize_zh(text: str) -> List[str]:
    raw_tokens = jieba.lcut((text or "").lower())
    out: List[str] = []
    for tok in raw_tokens:
        t = tok.strip()
        if not t or t in STOPWORDS or re.match(r"^[\W_]+$", t):
            continue
        out.append(t)
    for code in re.findall(r"pol-[a-z]+-\d+", (text or "").lower()):
        if code not in out:
            out.append(code)
    return out


def _split_long_section(
    section_text: str,
    doc_prefix: str,
    max_chars: int = 500,
    overlap: int = 50,
) -> List[str]:
    """
    二级标题章节二次滑动窗口切分：
    - 长度 <= max_chars 的章节保持完整单块；
    - 超过 max_chars 时保留文档编号头与章节标题，按段落/句末切分并在相邻块间保留 overlap 字重叠。
    """
    sec = (section_text or "").strip()
    if not sec:
        return []

    prefix = (doc_prefix or "").strip()
    full_block = sec if (not prefix or prefix.split()[0].lstrip("[") in sec) else f"{prefix}\n{sec}"
    if len(full_block) <= max_chars:
        return [full_block]

    lines = sec.splitlines()
    if lines[0].lstrip().startswith("## "):
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
    else:
        heading = ""
        body = sec

    if prefix and prefix.split()[0].lstrip("[") not in heading:
        header = f"{prefix}\n{heading}".strip() if heading else prefix
    else:
        header = heading or prefix

    if not body:
        return [header[:max_chars]]

    body_budget = max(80, max_chars - (len(header) + 1 if header else 0))
    eff_overlap = max(0, min(overlap, body_budget // 3))

    # 先按换行与中文/英文句末标点切为语义原子句
    raw_segments = re.split(r"(?:\n+|(?<=[。；！？!?]))", body)
    segments: List[str] = []
    for seg in raw_segments:
        s = seg.strip()
        if not s:
            continue
        if len(s) <= body_budget:
            segments.append(s)
        else:
            step = max(1, body_budget - eff_overlap)
            pos = 0
            while pos < len(s):
                segments.append(s[pos:pos + body_budget])
                if pos + body_budget >= len(s):
                    break
                pos += step

    if not segments:
        return [full_block[:max_chars]]

    sub_bodies: List[str] = []
    curr = ""
    for seg in segments:
        candidate = f"{curr}\n{seg}".strip() if curr else seg
        if len(candidate) <= body_budget:
            curr = candidate
        else:
            if curr:
                sub_bodies.append(curr)
                tail = curr[-eff_overlap:].strip() if eff_overlap > 0 else ""
                if tail and len(tail) + 1 + len(seg) <= body_budget:
                    curr = f"{tail}\n{seg}"
                else:
                    curr = seg
            else:
                sub_bodies.append(seg[:body_budget])
                curr = ""
    if curr:
        sub_bodies.append(curr)

    return [f"{header}\n{b}".strip() if header else b for b in sub_bodies]


def _parse_embedding_response(data_items: List[Dict[str, Any]], expected_len: int) -> np.ndarray:
    if len(data_items) != expected_len:
        raise ValueError(f"Embedding response length mismatch: expected {expected_len}, got {len(data_items)}")
    ordered = (
        sorted(data_items, key=lambda x: x.get("index", 0))
        if all("index" in x for x in data_items)
        else data_items
    )
    vecs = np.array([item["embedding"] for item in ordered], dtype=np.float32)
    if vecs.ndim != 2 or vecs.shape[0] != expected_len or vecs.shape[1] == 0:
        raise ValueError(f"Invalid embedding tensor shape: {vecs.shape}")
    return _l2_normalize(vecs)


def _remote_embed_sync(texts: List[str]) -> np.ndarray:
    if not settings.EMBEDDING_API_KEY:
        raise RuntimeError("EMBEDDING_API_KEY is not configured")
    if not texts:
        return np.empty((0, 0), dtype=np.float32)
    batches: List[np.ndarray] = []
    headers = {"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"}
    url = f"{settings.EMBEDDING_BASE_URL}/embeddings"
    with httpx.Client(timeout=15.0) as client:
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i:i + _EMBED_BATCH_SIZE]
            resp = client.post(
                url,
                headers=headers,
                json={"model": settings.EMBEDDING_MODEL, "input": batch},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or []
            batches.append(_parse_embedding_response(data, len(batch)))
    return np.vstack(batches)


async def _remote_embed_async(texts: List[str]) -> np.ndarray:
    if not settings.EMBEDDING_API_KEY:
        raise RuntimeError("EMBEDDING_API_KEY is not configured")
    if not texts:
        return np.empty((0, 0), dtype=np.float32)
    batches: List[np.ndarray] = []
    headers = {"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"}
    url = f"{settings.EMBEDDING_BASE_URL}/embeddings"
    async with httpx.AsyncClient(timeout=15.0) as client:
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i:i + _EMBED_BATCH_SIZE]
            resp = await client.post(
                url,
                headers=headers,
                json={"model": settings.EMBEDDING_MODEL, "input": batch},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or []
            batches.append(_parse_embedding_response(data, len(batch)))
    return np.vstack(batches)


async def _rerank_async(query: str, candidates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    if not (settings.RERANK_ENABLED and settings.EMBEDDING_API_KEY and candidates):
        return candidates, False
    url = f"{settings.EMBEDDING_BASE_URL}/rerank"
    headers = {"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"}
    payload = {
        "model": settings.RERANK_MODEL,
        "query": query,
        "documents": [c["content"] for c in candidates],
        "top_n": len(candidates),
        "return_documents": False,
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            rows = resp.json().get("results") or []
        if not rows:
            return candidates, False

        sorted_rows = sorted(rows, key=lambda r: float(r.get("relevance_score", 0.0)), reverse=True)
        seen_idx = set()
        reordered: List[Dict[str, Any]] = []
        for r in sorted_rows:
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen_idx:
                seen_idx.add(idx)
                item = dict(candidates[idx])
                item["rerankScore"] = round(float(r.get("relevance_score", 0.0)), 4)
                reordered.append(item)
        for idx, cand in enumerate(candidates):
            if idx not in seen_idx:
                reordered.append(dict(cand))
        return reordered, True
    except Exception as exc:
        logger.warning("Cross-Encoder Rerank failed, keeping RRF fusion order: %s", exc)
        return candidates, False


class _PgVectorDenseStore:
    """
    PostgreSQL + pgvector (HNSW 余弦索引) 向量存储：
    - 仅在配置 PGVECTOR_DSN 且成功连接 PostgreSQL 时启用；
    - 建表：ticket_kb_chunks + HNSW vector_cosine_ops；
    - 失败或未配置时降级为内存矩阵（有 Embedding Key）或 BM25-only（无 Embedding Key）。
    """

    def __init__(self) -> None:
        self._conn: Any = None
        self._active_dsn: str = ""
        atexit.register(self.close)

    @property
    def available(self) -> bool:
        return self._conn is not None and not getattr(self._conn, "closed", False)

    @staticmethod
    def _validated_table_name() -> str:
        name = (settings.PGVECTOR_TABLE or "ticket_kb_chunks").strip()
        if not _VALID_IDENT_RE.fullmatch(name):
            raise ValueError(f"Invalid PGVECTOR_TABLE identifier: {name!r}")
        return name

    def connect(self) -> bool:
        dsn = (settings.PGVECTOR_DSN or "").strip()
        if not dsn:
            self.close()
            return False
        if self.available and self._active_dsn == dsn:
            return True

        self.close()
        try:
            import psycopg
        except Exception as exc:
            logger.warning("psycopg import failed, pgvector store unavailable: %s", exc)
            return False

        try:
            conn = psycopg.connect(dsn, autocommit=True, connect_timeout=3)
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            try:
                from pgvector.psycopg import register_vector
                register_vector(conn)
            except Exception:
                pass
            self._conn = conn
            self._active_dsn = dsn
            return True
        except Exception as exc:
            logger.warning("PostgreSQL pgvector connect failed, falling back: %s", exc)
            self._conn = None
            self._active_dsn = ""
            return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._active_dsn = ""

    def _read_meta(self, table: str) -> Dict[str, str]:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS ticket_kb_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL);"
        )
        prefix = f"{table}:"
        cur = self._conn.execute(
            "SELECT meta_key, meta_value FROM ticket_kb_meta WHERE meta_key LIKE %s;",
            (prefix + "%",),
        )
        out: Dict[str, str] = {}
        for k, v in cur.fetchall():
            out[k[len(prefix):]] = v
        return out

    def _write_meta(self, table: str, items: Dict[str, str]) -> None:
        for k, v in items.items():
            self._conn.execute(
                """
                INSERT INTO ticket_kb_meta (meta_key, meta_value)
                VALUES (%s, %s)
                ON CONFLICT (meta_key) DO UPDATE SET meta_value = EXCLUDED.meta_value;
                """,
                (f"{table}:{k}", str(v)),
            )

    def find_cached_dim(self, model_name: str, fingerprint: str, expected_count: int) -> Optional[int]:
        if not self.available:
            return None
        table = self._validated_table_name()
        try:
            meta = self._read_meta(table)
            if (
                meta.get("fingerprint") == fingerprint
                and meta.get("model") == model_name
                and meta.get("count") == str(expected_count)
                and meta.get("dim")
            ):
                reg_row = self._conn.execute("SELECT to_regclass(%s);", (table,)).fetchone()
                if reg_row and reg_row[0] is not None:
                    cnt_row = self._conn.execute(f"SELECT COUNT(*) FROM {table};").fetchone()
                    if cnt_row and int(cnt_row[0]) == expected_count:
                        return int(meta["dim"])
        except Exception as exc:
            logger.warning("Failed to inspect pgvector cache meta: %s", exc)
        return None

    def ensure_index(
        self,
        dim: int,
        model_name: str,
        fingerprint: str,
        rows: List[Dict[str, Any]],
    ) -> None:
        if not self.available:
            raise RuntimeError("pgvector store is not connected")
        table = self._validated_table_name()
        if self.find_cached_dim(model_name, fingerprint, len(rows)) == dim:
            return

        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS ticket_kb_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL);"
        )
        self._conn.execute(f"DROP TABLE IF EXISTS {table};")
        self._conn.execute(
            f"""
            CREATE TABLE {table} (
                chunk_id INT PRIMARY KEY,
                doc_code VARCHAR(64) NOT NULL,
                doc_title VARCHAR(255) NOT NULL,
                source_file VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                embedding vector({int(dim)}) NOT NULL,
                embedding_model VARCHAR(128) NOT NULL
            );
            """
        )
        self._conn.execute(
            f"CREATE INDEX idx_{table}_hnsw ON {table} USING hnsw (embedding vector_cosine_ops);"
        )

        insert_sql = f"""
            INSERT INTO {table} (chunk_id, doc_code, doc_title, source_file, content, embedding, embedding_model)
            VALUES (%s, %s, %s, %s, %s, %s::vector, %s);
        """
        for r in rows:
            self._conn.execute(
                insert_sql,
                (
                    r["idx"],
                    r["docCode"],
                    r["docTitle"],
                    r["sourceFile"],
                    r["content"],
                    _vec_to_pg_literal(r["vector"]),
                    model_name,
                ),
            )

        self._write_meta(
            table,
            {
                "fingerprint": fingerprint,
                "model": model_name,
                "dim": str(int(dim)),
                "count": str(len(rows)),
            },
        )

    def query(self, vec: List[float] | np.ndarray, limit: int) -> Dict[int, float]:
        if not self.available:
            raise RuntimeError("pgvector store is not connected")
        table = self._validated_table_name()
        vec_lit = _vec_to_pg_literal(vec)
        sql = (
            f"SELECT chunk_id, 1 - (embedding <=> %s::vector) AS cosine_sim "
            f"FROM {table} ORDER BY embedding <=> %s::vector LIMIT %s;"
        )
        rows = self._conn.execute(sql, (vec_lit, vec_lit, int(limit))).fetchall()
        return {int(row[0]): float(row[1]) for row in rows}


class TicketHybridRagEngine:
    """
    PulseTicket 票务知识库混合检索引擎 (Hybrid RAG):
    1. 按 Markdown 二级标题 (## ) + 长章节滑动窗口（500 字 / 50 字重叠）切分语义分片
    2. 稀疏检索 (Sparse): jieba 分词 + BM25Okapi 精确命中数字、站名、规则编号
    3. 稠密检索 (Dense): 远程 Embedding API（默认 BAAI/bge-m3）+ PostgreSQL pgvector (HNSW 余弦索引)
    4. 倒数秩融合 (RRF): score = 0.35*norm_bm25 + 0.45*cos + 0.20*coverage + rrf*2.0
    5. Cross-Encoder 精排: BAAI/bge-reranker-v2-m3（不覆盖 finalScore 取证尺度）
    - 未配置 EMBEDDING_API_KEY 时不使用假向量，诚实降级为 BM25-only 关键词检索并显式标记 degraded=True。
    """

    def __init__(self, knowledge_dir: Path = settings.KNOWLEDGE_DIR):
        self.knowledge_dir = knowledge_dir
        self._chunks: List[Dict[str, Any]] = []
        self._tokens: List[List[str]] = []
        self._bm25: BM25Okapi | None = None
        self._matrix: np.ndarray | None = None
        self._model_name: str = "none"
        self._dim: int = 0
        self._dense_enabled: bool = False
        self._use_pgvector: bool = False
        self._pgvector = _PgVectorDenseStore()
        self.backend_info: Dict[str, Any] = {
            "embedding": "none",
            "dim": 0,
            "vectorStore": "bm25-only-degraded",
            "rerank": False,
            "degraded": True,
        }

    @staticmethod
    def _compute_fingerprint(model_name: str, chunks: List[Dict[str, Any]]) -> str:
        joined = model_name + "\n" + "\n---\n".join(c["content"] for c in chunks)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def load_and_index(self) -> int:
        """读取 knowledge/*.md 切分并构建 BM25 + PostgreSQL pgvector 索引（无 Key 时降级 BM25-only）"""
        self._chunks.clear()
        self._tokens.clear()
        self._bm25 = None
        self._matrix = None
        self._model_name = "none"
        self._dim = 0
        self._dense_enabled = False
        self._use_pgvector = False

        if not self.knowledge_dir.exists():
            return 0

        md_files = sorted(self.knowledge_dir.glob("*.md"))
        chunk_id = 1
        for md_path in md_files:
            raw = md_path.read_text(encoding="utf-8").strip()
            if not raw:
                continue

            first_line = raw.splitlines()[0].lstrip("# ").strip()
            code_match = re.search(r"\[(POL-[A-Z]+-\d+)\]", first_line)
            doc_code = code_match.group(1) if code_match else md_path.stem.upper()
            doc_title = re.sub(r"\[POL-[A-Z]+-\d+\]\s*", "", first_line).strip()
            doc_prefix = f"[{doc_code} 《{doc_title}》]"

            sections = re.split(r"\n(?=##\s+)", raw)
            for sec in sections:
                sec_clean = sec.strip()
                if not sec_clean or sec_clean.startswith("# ["):
                    lines = [ln for ln in sec_clean.splitlines() if not ln.startswith("# ")]
                    if not "".join(lines).strip():
                        continue

                sub_chunks = _split_long_section(
                    sec_clean,
                    doc_prefix=doc_prefix,
                    max_chars=settings.CHUNK_MAX_CHARS,
                    overlap=settings.CHUNK_OVERLAP_CHARS,
                )
                for chunk_text in sub_chunks:
                    toks = tokenize_zh(chunk_text)
                    self._chunks.append({
                        "chunkId": chunk_id,
                        "docCode": doc_code,
                        "docTitle": doc_title,
                        "sourceFile": md_path.name,
                        "content": chunk_text,
                    })
                    self._tokens.append(toks)
                    chunk_id += 1

        if not self._chunks:
            return 0

        self._bm25 = BM25Okapi(self._tokens)

        if not settings.EMBEDDING_API_KEY:
            self.backend_info = {
                "embedding": "none",
                "dim": 0,
                "vectorStore": "bm25-only-degraded",
                "rerank": False,
                "degraded": True,
            }
            return len(self._chunks)

        target_model = settings.EMBEDDING_MODEL
        fp = self._compute_fingerprint(target_model, self._chunks)
        pg_ok = self._pgvector.connect()

        if pg_ok:
            cached_dim = self._pgvector.find_cached_dim(target_model, fp, len(self._chunks))
            if cached_dim is not None:
                self._model_name = target_model
                self._dim = cached_dim
                self._dense_enabled = True
                self._use_pgvector = True
                self.backend_info = {
                    "embedding": target_model,
                    "dim": cached_dim,
                    "vectorStore": "pgvector-hnsw",
                    "rerank": bool(settings.RERANK_ENABLED),
                    "degraded": False,
                }
                return len(self._chunks)

        try:
            vecs = _remote_embed_sync([c["content"] for c in self._chunks])
        except Exception as exc:
            logger.warning("Remote chunk embedding failed, degrading to BM25-only: %s", exc)
            self.backend_info = {
                "embedding": "none",
                "dim": 0,
                "vectorStore": "bm25-only-degraded",
                "rerank": False,
                "degraded": True,
            }
            return len(self._chunks)

        self._model_name = target_model
        self._dim = int(vecs.shape[1])
        self._matrix = vecs
        self._dense_enabled = True

        vector_store_label = "memory-fallback"
        if pg_ok:
            rows = [
                {
                    "idx": idx,
                    "docCode": self._chunks[idx]["docCode"],
                    "docTitle": self._chunks[idx]["docTitle"],
                    "sourceFile": self._chunks[idx]["sourceFile"],
                    "content": self._chunks[idx]["content"],
                    "vector": vecs[idx],
                }
                for idx in range(len(self._chunks))
            ]
            try:
                self._pgvector.ensure_index(self._dim, target_model, fp, rows)
                self._use_pgvector = True
                vector_store_label = "pgvector-hnsw"
            except Exception as exc:
                logger.warning("pgvector index build failed, falling back to memory matrix: %s", exc)
                self._use_pgvector = False

        self.backend_info = {
            "embedding": self._model_name,
            "dim": self._dim,
            "vectorStore": vector_store_label,
            "rerank": bool(settings.RERANK_ENABLED),
            "degraded": False,
        }
        return len(self._chunks)

    async def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        if not self._chunks or self._bm25 is None:
            self.load_and_index()
        if not self._chunks or self._bm25 is None:
            return []

        expanded_query = expand_ticket_concepts(query)
        q_tokens = tokenize_zh(expanded_query)
        bm25_scores = (
            self._bm25.get_scores(q_tokens) if q_tokens else np.zeros(len(self._chunks))
        )

        degraded = not self._dense_enabled
        vec_scores = np.zeros(len(self._chunks), dtype=np.float32)
        vec_rank = {i: 50 for i in range(len(self._chunks))}

        if self._dense_enabled:
            q_vec = _cache_get(self._model_name, query)
            if q_vec is None:
                try:
                    q_vecs = await _remote_embed_async([query])
                    q_vec = q_vecs[0]
                    _cache_put(self._model_name, query, q_vec)
                except Exception as exc:
                    logger.warning("Remote query embedding failed, degrading to BM25-only: %s", exc)
                    degraded = True
                    q_vec = None

            if q_vec is not None:
                if self._use_pgvector and self._pgvector.available:
                    try:
                        score_map = self._pgvector.query(q_vec, limit=len(self._chunks))
                        vec_scores = np.array(
                            [score_map.get(i, 0.0) for i in range(len(self._chunks))],
                            dtype=np.float32,
                        )
                    except Exception as exc:
                        logger.warning("pgvector query failed: %s", exc)
                        if self._matrix is not None:
                            vec_scores = self._matrix @ q_vec
                        else:
                            degraded = True
                elif self._matrix is not None:
                    vec_scores = self._matrix @ q_vec
                else:
                    degraded = True

                if not degraded:
                    vec_order = np.argsort(-vec_scores)
                    vec_rank = {int(idx): r + 1 for r, idx in enumerate(vec_order)}

        bm25_order = np.argsort(-bm25_scores)
        bm25_rank = {int(idx): r + 1 for r, idx in enumerate(bm25_order)}

        max_bm25 = float(np.max(bm25_scores)) if np.max(bm25_scores) > 0 else 1.0
        raw_q_tokens = set(tokenize_zh(query))
        rrf_k = 60

        results: List[Dict[str, Any]] = []
        for i, chunk in enumerate(self._chunks):
            r_b = bm25_rank.get(i, 50)
            r_v = vec_rank.get(i, 50)
            rrf_score = (1.0 / (rrf_k + r_b)) + (1.0 / (rrf_k + r_v))

            norm_bm25 = float(bm25_scores[i]) / max_bm25
            cos_sim = float(max(0.0, vec_scores[i]))
            coverage = len(raw_q_tokens & set(self._tokens[i])) / max(1, len(raw_q_tokens))
            final_score = round(0.35 * norm_bm25 + 0.45 * cos_sim + 0.20 * coverage + rrf_score * 2.0, 4)

            item: Dict[str, Any] = {
                **chunk,
                "bm25Score": round(float(bm25_scores[i]), 4),
                "vectorScore": round(cos_sim, 4),
                "rrfScore": round(rrf_score, 5),
                "finalScore": final_score,
            }
            if degraded:
                item["degraded"] = True
            results.append(item)

        results.sort(key=lambda x: x["finalScore"], reverse=True)
        pool = results[:max(top_k, 8)]
        reranked_pool, rerank_applied = await _rerank_async(query, pool)
        if degraded:
            backend_label = "bm25-only-degraded"
        else:
            backend_label = (
                f"{self.backend_info['embedding']}@{self.backend_info['vectorStore']}"
                + ("+cross-encoder-rerank" if rerank_applied else "")
            )
        final_hits = reranked_pool[:top_k]
        for h in final_hits:
            h["retrievalBackend"] = backend_label
        return final_hits


hybrid_rag_engine = TicketHybridRagEngine()

import os
from pathlib import Path
from dotenv import load_dotenv

# 依次尝试加载 agent/.env、pulseticket/.env、pulseflow/.env
AGENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AGENT_ROOT.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

for env_candidate in [
    AGENT_ROOT / ".env",
    PROJECT_ROOT / ".env",
    WORKSPACE_ROOT / "pulseflow" / ".env",
]:
    if env_candidate.exists():
        load_dotenv(env_candidate, override=False)


class Settings:
    # Agent 服务监听端口
    AGENT_PORT: int = int(os.getenv("AGENT_PORT", "8204"))

    # 开发离线联调开关（默认严格关闭！仅显式设置 DEV_MOCK=true 时才允许无 Java 环境下的本地模拟数据）
    DEV_MOCK: bool = os.getenv("DEV_MOCK", "false").strip().lower() in ("1", "true", "yes")

    # Java 微服务直连地址（开发联调默认直连 show-service:8202 与 order-service:8203）
    SHOW_SERVICE_URL: str = os.getenv("SHOW_SERVICE_URL", "http://127.0.0.1:8202").rstrip("/")
    ORDER_SERVICE_URL: str = os.getenv("ORDER_SERVICE_URL", "http://127.0.0.1:8203").rstrip("/")
    GATEWAY_URL: str = os.getenv("GATEWAY_URL", "http://127.0.0.1:8200").rstrip("/")

    # DeepSeek / OpenAI 兼容大模型配置（严禁在源码中硬编码密钥）
    LLM_API_KEY: str = (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_TIMEOUT_SEC: float = float(os.getenv("LLM_TIMEOUT_SEC", "25.0"))

    # 可选远程 Embedding / Rerank API（未配置时自动降级为 BM25-only 关键词检索，不使用假向量）
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "").strip()
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip()
    RERANK_ENABLED: bool = os.getenv("RERANK_ENABLED", "true").strip().lower() in ("1", "true", "yes")
    RERANK_MODEL: str = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3").strip()

    # PostgreSQL + pgvector 向量存储及长章节二次切分参数
    PGVECTOR_DSN: str = os.getenv("PGVECTOR_DSN", "").strip()
    PGVECTOR_TABLE: str = os.getenv("PGVECTOR_TABLE", "ticket_kb_chunks").strip()
    # LangGraph Checkpointer & Trace 审计统一复用 PostgreSQL 16（优先读 CHECKPOINT_POSTGRES_DSN，默认复用 PGVECTOR_DSN）
    CHECKPOINT_POSTGRES_DSN: str = os.getenv("CHECKPOINT_POSTGRES_DSN", "").strip() or PGVECTOR_DSN
    CHUNK_MAX_CHARS: int = int(os.getenv("CHUNK_MAX_CHARS", "500"))
    CHUNK_OVERLAP_CHARS: int = int(os.getenv("CHUNK_OVERLAP_CHARS", "50"))

    # 知识库 Markdown 目录与本地离线/单元测试兜底 SQLite 路径（未配置 PostgreSQL DSN 时才启用）
    KNOWLEDGE_DIR: Path = AGENT_ROOT / "knowledge"
    CHECKPOINT_DB_PATH: str = os.getenv(
        "CHECKPOINT_DB_PATH",
        str(AGENT_ROOT / "data" / "ticket_agent_checkpoints.sqlite"),
    )

    # 三层会话记忆与 7 天滑动过期清理策略
    CONVERSATION_TTL_DAYS: int = int(os.getenv("CONVERSATION_TTL_DAYS", "7"))
    CONVERSATION_MAX_THREADS_PER_USER: int = int(os.getenv("CONVERSATION_MAX_THREADS_PER_USER", "30"))
    CONVERSATION_MAX_MESSAGES_PER_THREAD: int = int(os.getenv("CONVERSATION_MAX_MESSAGES_PER_THREAD", "100"))
    CONFIRM_CARD_TTL_MINUTES: int = int(os.getenv("CONFIRM_CARD_TTL_MINUTES", "15"))
    LLM_CONTEXT_MAX_TURNS: int = int(os.getenv("LLM_CONTEXT_MAX_TURNS", "3"))
    LLM_CONTEXT_ASSISTANT_MAX_CHARS: int = int(os.getenv("LLM_CONTEXT_ASSISTANT_MAX_CHARS", "300"))
    CLEANUP_INTERVAL_SEC: int = int(os.getenv("CLEANUP_INTERVAL_SEC", "3600"))


settings = Settings()

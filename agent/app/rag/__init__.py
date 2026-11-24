"""
Hybrid RAG 票务知识库检索模块导出入口。

对外暴露单例 `hybrid_rag_engine`，封装了 Markdown 切分、jieba BM25 稀疏召回、
bge-m3 + PostgreSQL pgvector 稠密召回、RRF 倒数秩融合以及 Cross-Encoder 精排全链路。
"""

from app.rag.hybrid_rag import hybrid_rag_engine

__all__ = ["hybrid_rag_engine"]

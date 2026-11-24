"""
展示层会话历史存储模块导出入口。

对外暴露 `ConversationStore` 类与默认全局单例 `conversation_store`，
负责前端历史对话列表、思考过程/工具卡片回显、确认卡片 15 分钟过期及 7 天滑动过期清理，
与 LangGraph 内部 Checkpointer 状态图持久化解耦。
"""

from app.storage.conversation_store import ConversationStore, conversation_store

__all__ = ["ConversationStore", "conversation_store"]

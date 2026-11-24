"""
PulseTicket AI 智能购票与客服 Agent 服务包（FastAPI + LangChain + LangGraph + Hybrid RAG）。

整体定位：
- 作为独立 Python 微服务（默认监听 8204 端口）挂载在 Spring Cloud Gateway 之后；
- 内部采用「主图路由 + 安全护栏 + 3 个黑盒业务子图 + Interrupt 人工确认门控」的 v2 多智能体架构；
- 不直连业务 MySQL，所有演出库存、核价、下单与退票均通过 HTTP 调 Java `show-service` / `order-service` 完成。
"""

# PulseTicket AI Agent Service (FastAPI + LangChain + LangGraph + Hybrid RAG)

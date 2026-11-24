"""
LangGraph 智能体编排层统一导出入口。

架构版本说明（重要，排查问题或面试讲解时请区分）：
- **v2 生产主架构 (`main_graph`)**：`MultiAgentTicketManager` / `build_main_agent_graph` / `multi_agent_manager`
  —— 当前线上实际运行的「主图意图路由 + 安全护栏 + 知识/选座/订单 3 个隔离业务子图 + Interrupt 确认门控 + SSE 思考流」多智能体编排。
- **v1 历史兼容版 (`react_agent`)**：`TicketAgentManager` / `build_ticket_react_graph` / `ticket_agent_manager`
  —— 早期单 ReAct Agent 版本，仅保留用于历史兼容与回归测试，生产 FastAPI 入口 (`app/main.py`) 已全面切到 v2 `multi_agent_manager`。
"""

from app.graph.main_graph import MultiAgentTicketManager, build_main_agent_graph, multi_agent_manager
from app.graph.react_agent import TicketAgentManager, build_ticket_react_graph, ticket_agent_manager

__all__ = [
    "MultiAgentTicketManager",
    "build_main_agent_graph",
    "multi_agent_manager",
    "TicketAgentManager",
    "build_ticket_react_graph",
    "ticket_agent_manager",
]

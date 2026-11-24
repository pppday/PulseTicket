"""
Agent 可观测性与审计轨迹模块导出入口。

对外暴露 `AgentTracer` 类与全局单例 `agent_tracer`，用于把每次对话的路由、子图执行、
工具调用、RAG 引用、安全拦截及耗时落库，供管理员后台 Telemetry 面板实时查看。
"""

from app.observability.tracer import agent_tracer, AgentTracer

__all__ = ["agent_tracer", "AgentTracer"]

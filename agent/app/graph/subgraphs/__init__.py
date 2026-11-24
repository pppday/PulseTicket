"""
三大隔离业务子图导出入口（由主图 `app/graph/main_graph.py` 预编译并按需调度）。

按最小权限原则拆分为 3 个互不直接调用的黑盒子图：
1. `build_knowledge_subgraph`：规则与场馆问答子图（纯只读 Agentic RAG，支持自反思重试与零幻觉拒答）
2. `build_purchase_subgraph`：选座与购票子图（演出查询、连座推荐与 Java 服务端权威核价）
3. `build_order_subgraph`：订单与售后子图（查单、Decimal 阶梯退票算费与取消/退票前置状态核验）
"""

from app.graph.subgraphs.knowledge import build_knowledge_subgraph
from app.graph.subgraphs.purchase import build_purchase_subgraph
from app.graph.subgraphs.order import build_order_subgraph

__all__ = [
    "build_knowledge_subgraph",
    "build_purchase_subgraph",
    "build_order_subgraph",
]

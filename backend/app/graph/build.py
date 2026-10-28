"""组装 LangGraph。

图结构：
    START -> parse -> extract -> [rule_engine, agent_commercial, agent_liability, agent_compliance]
          -> aggregate -> redline -> gate -> END

三个 Agent + 规则引擎是同一超步的四个并行分支；它们只写 findings/warnings，
由 Annotated reducer 合并。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph.nodes.agents import AGENT_NODES
from app.graph.nodes.aggregate import aggregate_node
from app.graph.nodes.extract import extract_node
from app.graph.nodes.gate import gate_node
from app.graph.nodes.human_review import human_review_node
from app.graph.nodes.parse import parse_node
from app.graph.nodes.redline import redline_node
from app.graph.nodes.rules import rule_engine_node
from app.graph.state import ReviewState

PARALLEL_NODES = ["rule_engine", *AGENT_NODES.keys()]


def build_graph(*, checkpointer=None, enable_hitl: bool = False):  # type: ignore[no-untyped-def]
    """构建审查图。

    enable_hitl=True 时必须提供 checkpointer；否则 gate 后直接结束。
    图不再使用 lru_cache，避免缓存绑定到已关闭 event loop 的 saver。
    """
    builder = StateGraph(ReviewState)
    builder.add_node("parse", parse_node)
    builder.add_node("extract", extract_node)
    builder.add_node("rule_engine", rule_engine_node)
    for name, node in AGENT_NODES.items():
        builder.add_node(name, node)
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("redline", redline_node)
    builder.add_node("gate", gate_node)

    builder.add_edge(START, "parse")
    builder.add_edge("parse", "extract")
    for name in PARALLEL_NODES:
        builder.add_edge("extract", name)
        builder.add_edge(name, "aggregate")
    builder.add_edge("aggregate", "redline")
    builder.add_edge("redline", "gate")

    if enable_hitl and checkpointer is not None:
        builder.add_node("human_review", human_review_node)
        builder.add_edge("gate", "human_review")
        builder.add_edge("human_review", END)
    else:
        builder.add_edge("gate", END)

    return builder.compile(checkpointer=checkpointer)

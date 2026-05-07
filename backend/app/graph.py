"""
LangGraph orchestration.

Two graphs:

    build_extraction_graph()  ->  extract -> categorize -> calculate
    build_qa_graph()          ->  qa (single node, but kept as a graph for
                                       composition / future expansion)
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .nodes import (
    GraphState,
    calculate_node,
    categorize_node,
    extract_node,
    qa_node,
)


def build_extraction_graph():
    g = StateGraph(GraphState)
    g.add_node("extract", extract_node)
    g.add_node("categorize", categorize_node)
    g.add_node("calculate", calculate_node)
    g.set_entry_point("extract")
    g.add_edge("extract", "categorize")
    g.add_edge("categorize", "calculate")
    g.add_edge("calculate", END)
    return g.compile()


def build_qa_graph():
    g = StateGraph(GraphState)
    g.add_node("qa", qa_node)
    g.set_entry_point("qa")
    g.add_edge("qa", END)
    return g.compile()

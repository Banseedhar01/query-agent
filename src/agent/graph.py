"""LangGraph StateGraph wiring for the Impala query review agent."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from src.agent.nodes import (
    build_report_node,
    fetch_explain_node,
    fetch_metadata_node,
    llm_analyzer_node,
    parse_query_node,
    rewrite_proposer_node,
    rule_lint_node,
    validator_node,
)
from src.agent.state import AgentState

logger = logging.getLogger(__name__)


def _should_retry_rewrite(state: AgentState) -> str:
    """Route back to rewrite_proposer if there are still rejected candidates."""
    config = state.get("config", {})
    retry_limit = config.get("thresholds", {}).get("rewrite_retry_limit", 1)
    if (
        state.get("candidate_rewrites")
        and state.get("rewrite_retry_count", 0) <= retry_limit
    ):
        logger.debug("graph:routing → rewrite_proposer (retry)")
        return "rewrite_proposer"
    logger.debug("graph:routing → build_report")
    return "build_report"


def build_graph() -> Any:
    """Build and compile the review agent StateGraph."""
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("parse_query", parse_query_node)
    graph.add_node("fetch_metadata", fetch_metadata_node)
    graph.add_node("fetch_explain", fetch_explain_node)
    graph.add_node("rule_lint", rule_lint_node)
    graph.add_node("llm_analyzer", llm_analyzer_node)
    graph.add_node("rewrite_proposer", rewrite_proposer_node)
    graph.add_node("validator", validator_node)
    graph.add_node("build_report", build_report_node)

    # Edges — linear pipeline
    graph.set_entry_point("parse_query")
    graph.add_edge("parse_query", "fetch_metadata")
    graph.add_edge("fetch_metadata", "fetch_explain")
    graph.add_edge("fetch_explain", "rule_lint")
    graph.add_edge("rule_lint", "llm_analyzer")
    graph.add_edge("llm_analyzer", "rewrite_proposer")
    graph.add_edge("rewrite_proposer", "validator")

    # Conditional: retry or finish
    graph.add_conditional_edges(
        "validator",
        _should_retry_rewrite,
        {
            "rewrite_proposer": "rewrite_proposer",
            "build_report": "build_report",
        },
    )
    graph.add_edge("build_report", END)

    compiled = graph.compile()
    logger.info("graph:compiled nodes=%d", len(graph.nodes))
    return compiled


def run_review(
    sql: str,
    config: dict[str, Any],
    db_path: str = "metadata.duckdb",
    offline: bool = False,
) -> Any:
    """
    Run the full review pipeline for a SQL string.
    Returns the final AgentState (access .report for results).
    """
    graph = build_graph()

    initial_state: AgentState = {
        "raw_sql": sql,
        "offline": offline,
        "query_profile": None,
        "explain_plan": None,
        "lint_findings": [],
        "retrieved_metadata": {},
        "analyzer_messages": [],
        "candidate_rewrites": [],
        "validated_rewrites": [],
        "rewrite_retry_count": 0,
        "report": None,
        "db_path": db_path,
        "config": config,
    }

    logger.info("graph:run_review offline=%s sql_len=%d", offline, len(sql))
    final_state = graph.invoke(initial_state)
    return final_state

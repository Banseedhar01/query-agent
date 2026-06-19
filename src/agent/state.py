"""LangGraph TypedDict state for the query review agent."""

from __future__ import annotations

from typing import Annotated, Any
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from src.analysis.explain import ExplainPlan
from src.analysis.linter import Finding
from src.analysis.parser import QueryProfile
from src.report.schema import CandidateRewrite, LLMFinding, ReviewReport, ValidatedRewrite


class AgentState(TypedDict):
    # Input
    raw_sql: str
    offline: bool

    # Deterministic analysis results
    query_profile: QueryProfile | None
    explain_plan: ExplainPlan | None
    lint_findings: list[Finding]

    # Metadata retrieved from DuckDB
    retrieved_metadata: dict[str, Any]

    # LLM reasoning messages
    analyzer_messages: Annotated[list[BaseMessage], add_messages]

    # Structured findings extracted by LLM after analysis
    llm_findings: list[LLMFinding]

    # Rewrite pipeline
    candidate_rewrites: list[CandidateRewrite]
    validated_rewrites: list[ValidatedRewrite]
    rewrite_retry_count: int

    # Final output
    report: ReviewReport | None

    # Internal: db path and config
    db_path: str
    config: dict[str, Any]

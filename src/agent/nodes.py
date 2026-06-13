"""LangGraph node implementations for the query review pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from typing import Any

warnings.filterwarnings("ignore", message=".*Pydantic serializer warnings.*", category=UserWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import duckdb
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

from src.agent.state import AgentState
from src.agent.tools import ALL_TOOLS, configure_tools
from src.analysis.explain import ExplainPlan, get_plan, parse_explain_text
from src.analysis.linter import Finding, run_all_rules
from src.analysis.parser import QueryProfile, parse_query
from src.report.schema import (
    CandidateRewrite,
    CandidateRewriteList,
    Issue,
    ReviewReport,
    Severity,
    ValidatedRewrite,
)
from src.validation.plan_diff import Verdict, check_ast_equivalence, diff_plans

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_llm(config: dict[str, Any]) -> ChatOpenAI:
    model_cfg = config.get("model", {})
    return ChatOpenAI(
        model=model_cfg.get("name", "codex-2.5"),
        max_tokens=model_cfg.get("max_tokens", 4096),
        temperature=model_cfg.get("temperature", 0.0),
        timeout=model_cfg.get("timeout_seconds", 60),
        max_retries=model_cfg.get("max_retries", 3),
    )


def _query_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


def _findings_summary(findings: list[Finding]) -> str:
    if not findings:
        return "No rule-based findings."
    lines = [f"[{f.rule_id}] {f.severity.value}: {f.message}" for f in findings]
    return "\n".join(lines)


def _profile_summary(profile: QueryProfile) -> str:
    return json.dumps({
        "tables": profile.tables,
        "has_select_star": profile.has_select_star,
        "join_count": len(profile.join_graph),
        "filter_predicates": len(profile.filter_predicates),
        "cte_names": profile.cte_names,
        "subquery_count": profile.subquery_count,
        "parse_errors": profile.parse_errors,
    }, indent=2)


def _plan_summary(plan: ExplainPlan | None) -> str:
    if plan is None:
        return "No EXPLAIN plan (offline mode)."
    return json.dumps({
        "scan_bytes_per_table": plan.scan_bytes_per_table,
        "join_strategies": plan.join_strategies,
        "warnings": plan.warnings[:10],
        "missing_stats_tables": plan.missing_stats_tables,
    }, indent=2)


# ---------------------------------------------------------------------------
# Node: parse_query_node
# ---------------------------------------------------------------------------

def parse_query_node(state: AgentState) -> dict[str, Any]:
    sql = state["raw_sql"]
    logger.info("node:parse_query sql_len=%d", len(sql))
    profile = parse_query(sql)
    logger.info(
        "node:parse_query tables=%d joins=%d errors=%d",
        len(profile.tables), len(profile.join_graph), len(profile.parse_errors),
    )
    return {"query_profile": profile}


# ---------------------------------------------------------------------------
# Node: fetch_metadata_node
# ---------------------------------------------------------------------------

def fetch_metadata_node(state: AgentState) -> dict[str, Any]:
    profile: QueryProfile = state["query_profile"]  # type: ignore[assignment]
    db_path = state.get("db_path", "metadata.duckdb")

    logger.info("node:fetch_metadata tables=%d", len(profile.tables))

    metadata: dict[str, Any] = {}
    found_cols: set[str] = set()
    total_cols: set[str] = set()

    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception as exc:
        logger.warning("DuckDB open failed: %s", exc)
        return {"retrieved_metadata": metadata}

    # -- debug: parser output ------------------------------------------------
    logger.debug("  parsed tables     : %s", profile.tables)
    logger.debug("  columns_per_table : %s", dict(profile.columns_per_table))

    # -- debug: what's actually in DuckDB ------------------------------------
    try:
        stored = con.execute(
            "SELECT DISTINCT table_name FROM column_metadata ORDER BY table_name"
        ).fetchall()
        logger.debug("  duckdb tables (%d): %s", len(stored), [r[0] for r in stored])
    except Exception as exc:
        logger.warning("  duckdb table list failed: %s", exc)

    for table in profile.tables:
        cols = profile.columns_per_table.get(table, [])

        if not cols:
            # table-level probe to confirm whether table exists in DuckDB at all
            try:
                probe = con.execute(
                    "SELECT column_name FROM column_metadata WHERE LOWER(table_name) = LOWER(?) LIMIT 3",
                    [table],
                ).fetchall()
                if probe:
                    logger.debug("  %-30s  no cols resolved by parser | duckdb has %d cols (e.g. %s)",
                                 table, len(probe), [r[0] for r in probe])
                else:
                    logger.debug("  %-30s  no cols resolved | NOT in duckdb", table)
            except Exception as exc:
                logger.warning("  table probe failed for %s: %s", table, exc)

        for col in cols:
            fqcol = f"{table}.{col}"
            total_cols.add(fqcol)
            try:
                rows = con.execute(
                    """
                    SELECT column_name, data_type, pii, column_description
                    FROM column_metadata
                    WHERE LOWER(table_name) = LOWER(?) AND LOWER(column_name) = LOWER(?)
                    LIMIT 1
                    """,
                    [table, col],
                ).fetchall()
                if rows:
                    r = rows[0]
                    metadata.setdefault(table, {})[col] = {
                        "data_type": r[1],
                        "pii": r[2],
                        "description": r[3],
                    }
                    found_cols.add(fqcol)
                    logger.debug("  %-30s  %-25s  HIT  pii=%-8s type=%s", table, col, r[2], r[1])
                else:
                    logger.debug("  %-30s  %-25s  MISS (not in duckdb)", table, col)
            except Exception as exc:
                logger.debug("  lookup failed %s.%s: %s", table, col, exc)

    con.close()
    coverage = len(found_cols) / max(len(total_cols), 1)
    metadata["__coverage__"] = coverage

    logger.info(
        "node:fetch_metadata found=%d/%d coverage=%.2f",
        len(found_cols), len(total_cols), coverage,
    )
    return {"retrieved_metadata": metadata}


# ---------------------------------------------------------------------------
# Node: fetch_explain_node
# ---------------------------------------------------------------------------

def fetch_explain_node(state: AgentState) -> dict[str, Any]:
    if state.get("offline", False):
        logger.info("node:fetch_explain SKIPPED (offline)")
        return {"explain_plan": None}

    sql = state["raw_sql"]
    config = state.get("config", {})
    logger.info("node:fetch_explain sql_len=%d", len(sql))

    impala_cursor = _get_impala_cursor(config)
    if impala_cursor is None:
        logger.warning("node:fetch_explain no cursor available")
        return {"explain_plan": None}

    plan = get_plan(sql, impala_cursor)
    logger.info(
        "node:fetch_explain nodes=%d scan_tables=%d",
        len(plan.nodes), len(plan.scan_bytes_per_table),
    )
    return {"explain_plan": plan}


def _get_impala_cursor(config: dict[str, Any]) -> Any:
    """Create an impyla cursor. Returns None on failure."""
    impala_cfg = config.get("impala", {})
    try:
        from impala.dbapi import connect  # type: ignore[import]
        conn = connect(
            host=impala_cfg.get("host", "localhost"),
            port=impala_cfg.get("port", 21050),
            auth_mechanism=impala_cfg.get("auth_mechanism", "NOSASL"),
            timeout=impala_cfg.get("timeout_seconds", 30),
        )
        return conn.cursor()
    except Exception as exc:
        logger.warning("Impala connect failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Node: rule_lint_node
# ---------------------------------------------------------------------------

def rule_lint_node(state: AgentState) -> dict[str, Any]:
    profile: QueryProfile = state["query_profile"]  # type: ignore[assignment]
    plan: ExplainPlan | None = state.get("explain_plan")
    db_path = state.get("db_path", "metadata.duckdb")
    config = state.get("config", {})
    threshold = config.get("thresholds", {}).get("broadcast_join_bytes", 536870912)

    logger.info("node:rule_lint tables=%d", len(profile.tables))

    try:
        db_con = duckdb.connect(db_path, read_only=True)
    except Exception:
        db_con = None

    findings = run_all_rules(
        profile, plan, db_con,
        broadcast_threshold_bytes=threshold,
    )
    if db_con:
        db_con.close()

    logger.info("node:rule_lint findings=%d", len(findings))
    return {"lint_findings": findings}


# ---------------------------------------------------------------------------
# Node: llm_analyzer_node  (ToolNode loop inside)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an Apache Impala SQL query optimization expert.

Your job:
1. Analyze the provided AST summary, EXPLAIN plan, lint findings, and retrieved metadata.
2. Identify performance issues, correctness problems, and PII risks with specific evidence.
3. Use tools to look up any column or table facts you need — NEVER invent schema facts.
4. If a tool returns "found: false", state that the information is not in the metadata store.
5. Be concise and evidence-based. Reference rule IDs from the lint findings where applicable.

Constraints:
- Do NOT state table sizes, row counts, or column types unless a tool returned that data.
- Do NOT suggest rewriting unless you have evidence from the plan or metadata.
- If offline (no plan), work from AST + metadata only and note the limitation.
"""


def llm_analyzer_node(state: AgentState) -> dict[str, Any]:
    config = state.get("config", {})
    db_path = state.get("db_path", "metadata.duckdb")
    max_iters = config.get("thresholds", {}).get("llm_max_iterations", 4)

    configure_tools(db_path, None)  # tool access — no impala cursor needed here

    llm = _get_llm(config)
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    profile = state.get("query_profile")
    plan = state.get("explain_plan")
    findings = state.get("lint_findings", [])
    metadata = state.get("retrieved_metadata", {})

    human_content = f"""## SQL Query
```sql
{state['raw_sql']}
```

## AST Summary
{_profile_summary(profile) if profile else "Not available"}

## EXPLAIN Plan Summary
{_plan_summary(plan)}

## Lint Findings ({len(findings)} total)
{_findings_summary(findings)}

## Retrieved Metadata (sample)
{json.dumps({k: v for k, v in list(metadata.items())[:5] if k != '__coverage__'}, indent=2)}

Please analyze the query and use the available tools to look up any additional facts you need.
"""

    messages: list[Any] = list(state.get("analyzer_messages", []))
    if not messages:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=human_content),
        ]

    logger.info("node:llm_analyzer starting tool loop max_iters=%d", max_iters)

    for iteration in range(max_iters):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            logger.info("node:llm_analyzer finished at iteration %d", iteration + 1)
            break

        # Execute tool calls
        tool_map = {t.name: t for t in ALL_TOOLS}
        for tc in tool_calls:
            tool_fn = tool_map.get(tc["name"])
            if tool_fn is None:
                result = f"Unknown tool: {tc['name']}"
            else:
                try:
                    result = tool_fn.invoke(tc["args"])
                except Exception as exc:
                    result = f"Tool error: {exc}"
            messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )
        logger.debug("node:llm_analyzer iter=%d tool_calls=%d", iteration + 1, len(tool_calls))

    return {"analyzer_messages": messages}


# ---------------------------------------------------------------------------
# Node: rewrite_proposer_node
# ---------------------------------------------------------------------------

REWRITE_SYSTEM = """You are a SQL rewrite engine for Apache Impala.

Given the analysis conversation and lint findings, produce a list of concrete SQL rewrites.
Each rewrite must:
- Target one or more specific finding rule_ids.
- Be a complete, valid Impala SQL statement.
- Include a clear rationale tied to the evidence.
- NOT add columns or tables that weren't in the original query unless justified by metadata.

Output JSON matching: {"rewrites": [{"candidate_sql": "...", "rationale": "...", "targets_finding_ids": [...]}]}
"""


def rewrite_proposer_node(state: AgentState) -> dict[str, Any]:
    config = state.get("config", {})
    findings = state.get("lint_findings", [])

    if not findings:
        logger.info("node:rewrite_proposer no findings, skipping")
        return {"candidate_rewrites": []}

    llm = _get_llm(config)
    structured_llm = llm.with_structured_output(CandidateRewriteList)

    messages = list(state.get("analyzer_messages", []))
    finding_ids = [f.rule_id for f in findings]

    messages.append(HumanMessage(content=(
        f"Based on the analysis above, propose SQL rewrites targeting these findings: "
        f"{finding_ids}. Original SQL:\n```sql\n{state['raw_sql']}\n```"
    )))

    logger.info("node:rewrite_proposer findings=%d", len(findings))
    try:
        result: CandidateRewriteList = structured_llm.invoke(messages)
        rewrites = result.rewrites
    except Exception as exc:
        logger.error("node:rewrite_proposer structured output failed: %s", exc)
        rewrites = []

    logger.info("node:rewrite_proposer proposed=%d", len(rewrites))
    return {"candidate_rewrites": rewrites}


# ---------------------------------------------------------------------------
# Node: validator_node
# ---------------------------------------------------------------------------

def validator_node(state: AgentState) -> dict[str, Any]:
    candidates = state.get("candidate_rewrites", [])
    base_plan = state.get("explain_plan")
    base_sql = state["raw_sql"]
    config = state.get("config", {})
    offline = state.get("offline", False)

    logger.info("node:validator candidates=%d offline=%s", len(candidates), offline)

    validated: list[ValidatedRewrite] = []
    rejected: list[CandidateRewrite] = []

    impala_cursor = None if offline else _get_impala_cursor(config)

    for cand in candidates:
        # AST equivalence check
        ast_ok = check_ast_equivalence(base_sql, cand.candidate_sql)

        if offline or impala_cursor is None or base_plan is None:
            # Offline: accept with unverified flag
            validated.append(ValidatedRewrite(
                candidate_sql=cand.candidate_sql,
                rationale=cand.rationale,
                targets_finding_ids=cand.targets_finding_ids,
                verdict="UNVERIFIED",
                verified=False,
            ))
            continue

        # Get candidate plan
        cand_plan = get_plan(cand.candidate_sql, impala_cursor)
        diff = diff_plans(base_plan, cand_plan)
        diff.ast_equivalent = ast_ok

        vr = ValidatedRewrite(
            candidate_sql=cand.candidate_sql,
            rationale=cand.rationale,
            targets_finding_ids=cand.targets_finding_ids,
            scan_bytes_delta=diff.scan_bytes_delta,
            join_strategy_changes=diff.join_strategy_changes,
            verdict=diff.verdict.value,
            verified=diff.verdict == Verdict.IMPROVED and ast_ok,
        )

        if not vr.verified:
            rejected.append(cand)
            logger.debug(
                "node:validator REJECTED candidate: verdict=%s ast_ok=%s",
                diff.verdict, ast_ok,
            )
        validated.append(vr)

    # Handle retry for rejected rewrites (max once)
    retry_count = state.get("rewrite_retry_count", 0)
    if rejected and retry_count < config.get("thresholds", {}).get("rewrite_retry_limit", 1):
        logger.info("node:validator %d rejected, scheduling retry", len(rejected))
        return {
            "validated_rewrites": validated,
            "candidate_rewrites": rejected,
            "rewrite_retry_count": retry_count + 1,
        }

    logger.info(
        "node:validator validated=%d verified=%d",
        len(validated),
        sum(1 for v in validated if v.verified),
    )
    return {
        "validated_rewrites": validated,
        "candidate_rewrites": [],
        "rewrite_retry_count": retry_count,
    }


# ---------------------------------------------------------------------------
# Node: build_report_node
# ---------------------------------------------------------------------------

def build_report_node(state: AgentState) -> dict[str, Any]:
    findings = state.get("lint_findings", [])
    validated = state.get("validated_rewrites", [])
    metadata = state.get("retrieved_metadata", {})
    coverage = metadata.get("__coverage__", 0.0)

    # PII flags from lint findings
    pii_flags = [
        f.location for f in findings if f.rule_id == "R008_PII_UNMASKED"
    ]

    # Build issues — merge lint findings with validated rewrites
    rewrite_by_finding: dict[str, str] = {}
    for vr in validated:
        for fid in vr.targets_finding_ids:
            rewrite_by_finding[fid] = vr.candidate_sql

    issues: list[Issue] = []
    for f in findings:
        issues.append(Issue(
            issue=f.message,
            severity=f.severity,
            evidence_from_plan=f.evidence,
            suggested_rewrite=rewrite_by_finding.get(f.rule_id),
            expected_impact=_impact_for_rule(f.rule_id),
            verified=f.rule_id in rewrite_by_finding and any(
                v.verified for v in validated if f.rule_id in v.targets_finding_ids
            ),
        ))

    report = ReviewReport(
        query_hash=_query_hash(state["raw_sql"]),
        issues=issues,
        validated_rewrites=validated,
        pii_flags=pii_flags,
        metadata_coverage=coverage,
    )

    logger.info(
        "node:build_report issues=%d rewrites=%d pii_flags=%d coverage=%.2f",
        len(issues), len(validated), len(pii_flags), coverage,
    )
    return {"report": report}


def _impact_for_rule(rule_id: str) -> str:
    impacts = {
        "R001_SELECT_STAR": "Enables column pruning, reduces network transfer, hides future schema additions.",
        "R002_MISSING_PARTITION_FILTER": "Eliminates full table scan; partitioned reads can reduce I/O by orders of magnitude.",
        "R003_NON_SARGABLE_PREDICATE": "Allows predicate pushdown and partition pruning to activate.",
        "R004_IMPLICIT_CROSS_JOIN": "Avoids cartesian product; critical for correctness and performance.",
        "R005_ORDER_BY_NO_LIMIT": "Eliminates full result-set sort; reduces memory and shuffle cost.",
        "R006_MISSING_COMPUTE_STATS": "Enables accurate cardinality estimates for optimal join ordering.",
        "R007_BROADCAST_LARGE_TABLE": "Switches to partitioned join, avoids broadcasting large data to all nodes.",
        "R008_PII_UNMASKED": "Prevents accidental exposure of PII data downstream.",
    }
    return impacts.get(rule_id, "Improves query performance or data governance.")

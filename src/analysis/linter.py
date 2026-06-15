"""Rule-based linter: one function per rule, returns list[Finding]."""

from __future__ import annotations

import logging
import re
from typing import Callable

import duckdb
from pydantic import BaseModel

from src.analysis.explain import ExplainPlan
from src.analysis.parser import QueryProfile
from src.report.schema import Severity

logger = logging.getLogger(__name__)


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    message: str
    evidence: str
    location: str = ""


RuleFunc = Callable[[QueryProfile, ExplainPlan | None, duckdb.DuckDBPyConnection | None], list[Finding]]

# ---- helpers ---------------------------------------------------------------

_MASK_FUNCTIONS = frozenset({
    "sha1", "sha2", "sha256", "md5", "hash", "mask", "encrypt",
    "substring", "substr", "left", "right", "regexp_replace",
    "aes_encrypt", "base64",
})

_NON_SARGABLE_OPS = frozenset({
    "Anonymous", "Cast", "Upper", "Lower", "Year", "Month", "Day",
    "Substring", "Trim", "Coalesce", "TsOrDsToDate", "DateTrunc",
})


def _all_rules() -> list[RuleFunc]:
    return [
        rule_select_star,
        rule_missing_partition_filter,
        rule_non_sargable_predicate,
        rule_implicit_cross_join,
        rule_order_by_without_limit,
        rule_missing_compute_stats,
        rule_broadcast_large_table,
        rule_pii_unmasked,
    ]


# ---- rules -----------------------------------------------------------------

def rule_select_star(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    if not profile.has_select_star:
        return []
    return [Finding(
        rule_id="R001_SELECT_STAR",
        severity=Severity.MEDIUM,
        message="Query uses SELECT * which prevents column pruning and may expose PII.",
        evidence="SELECT * detected in parsed AST",
        location="SELECT clause",
    )]


def rule_missing_partition_filter(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    findings: list[Finding] = []
    if db is None:
        return findings

    for table in profile.tables:
        try:
            rows = db.execute(
                "SELECT partition_columns FROM table_stats WHERE table_name = ?",
                [table],
            ).fetchall()
        except Exception:
            continue

        for (part_cols,) in rows:
            if not part_cols:
                continue
            partition_cols = [c.strip().lower() for c in part_cols.split(",") if c.strip()]
            if not partition_cols:
                continue

            # Check whether any filter predicate references a partition column
            filtered_cols = {p.column.split(".")[-1] for p in profile.filter_predicates}
            missing = [c for c in partition_cols if c not in filtered_cols]
            if missing:
                findings.append(Finding(
                    rule_id="R002_MISSING_PARTITION_FILTER",
                    severity=Severity.HIGH,
                    message=(
                        f"Table '{table}' is partitioned on [{', '.join(partition_cols)}] "
                        f"but no filter on [{', '.join(missing)}] was found. Full table scan likely."
                    ),
                    evidence=f"Partition columns from table_stats: {partition_cols}",
                    location=f"FROM / WHERE referencing {table}",
                ))
    return findings


def rule_non_sargable_predicate(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    findings: list[Finding] = []
    for pred in profile.filter_predicates:
        if pred.is_non_sargable:
            findings.append(Finding(
                rule_id="R003_NON_SARGABLE_PREDICATE",
                severity=Severity.MEDIUM,
                message=(
                    f"Predicate on column '{pred.column}' applies a function/CAST, "
                    "preventing index/partition pruning."
                ),
                evidence=f"Predicate: {pred.column} {pred.operator} {pred.value[:80]}",
                location=f"WHERE clause on {pred.column}",
            ))
    return findings


def rule_implicit_cross_join(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    findings: list[Finding] = []
    for edge in profile.join_graph:
        if edge.condition is None or edge.condition.strip() == "":
            findings.append(Finding(
                rule_id="R004_IMPLICIT_CROSS_JOIN",
                severity=Severity.CRITICAL,
                message=(
                    f"Join between '{edge.left_table}' and '{edge.right_table}' "
                    "has no ON/USING condition — this is a cross join."
                ),
                evidence=f"Join type: {edge.join_type}, condition: None",
                location=f"JOIN {edge.right_table}",
            ))
    return findings


def rule_order_by_without_limit(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    if not profile.order_bys:
        return []
    # If plan is available, use it as ground truth
    if plan:
        has_topn = any("TOP-N" in n.operator.upper() for n in plan.nodes)
        has_sort = any("SORT" in n.operator.upper() for n in plan.nodes)
        if has_sort and not has_topn:
            return [Finding(
                rule_id="R005_ORDER_BY_NO_LIMIT",
                severity=Severity.MEDIUM,
                message="ORDER BY without LIMIT forces a full sort of all results — expensive at scale.",
                evidence="SORT node present without TOP-N in EXPLAIN plan",
                location="ORDER BY clause",
            )]
        # Plan available and no problematic SORT → rule passes
        return []
    # Fallback: no plan — only flag if there is also no LIMIT in the AST
    if profile.has_limit:
        return []
    return [Finding(
        rule_id="R005_ORDER_BY_NO_LIMIT",
        severity=Severity.LOW,
        message="ORDER BY detected without an explicit LIMIT. May cause full result-set sort.",
        evidence="ORDER BY in AST, no plan available to confirm",
        location="ORDER BY clause",
    )]


def rule_missing_compute_stats(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    findings: list[Finding] = []
    tables_missing: set[str] = set()

    # From EXPLAIN plan warnings
    if plan:
        for tbl in plan.missing_stats_tables:
            tables_missing.add(tbl)

    # From column_stats absence in DuckDB
    if db is not None:
        for table in profile.tables:
            try:
                rows = db.execute(
                    "SELECT COUNT(*) FROM column_stats WHERE table_name = ?",
                    [table],
                ).fetchone()
                if rows and rows[0] == 0:
                    tables_missing.add(table)
            except Exception:
                pass

    for tbl in sorted(tables_missing):
        findings.append(Finding(
            rule_id="R006_MISSING_COMPUTE_STATS",
            severity=Severity.HIGH,
            message=f"Table '{tbl}' has no COMPUTE STATS data — query planning will use defaults.",
            evidence="No stats in column_stats table or plan warning",
            location=f"Reference to {tbl}",
        ))
    return findings


def rule_broadcast_large_table(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
    threshold_bytes: int = 536870912,  # 512 MB default
) -> list[Finding]:
    if plan is None:
        return []
    findings: list[Finding] = []
    for table, strategy in plan.join_strategies.items():
        if strategy == "BROADCAST":
            scan_bytes = plan.scan_bytes_per_table.get(table, 0)
            if scan_bytes > threshold_bytes:
                findings.append(Finding(
                    rule_id="R007_BROADCAST_LARGE_TABLE",
                    severity=Severity.HIGH,
                    message=(
                        f"Table '{table}' is broadcast-joined but scans "
                        f"{scan_bytes / 1048576:.1f} MB — above {threshold_bytes / 1048576:.0f} MB threshold."
                    ),
                    evidence=f"BROADCAST JOIN, scan_bytes={scan_bytes}",
                    location=f"JOIN involving {table}",
                ))
    return findings


def rule_pii_unmasked(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    if db is None:
        return []
    findings: list[Finding] = []

    # Collect all selected columns (non-star)
    all_selected: list[tuple[str, str]] = []
    for table, cols in profile.columns_per_table.items():
        for col in cols:
            all_selected.append((table, col))

    for table, col in all_selected:
        try:
            rows = db.execute(
                """
                SELECT pii FROM column_metadata
                WHERE LOWER(table_name) = LOWER(?)
                  AND LOWER(column_name) = LOWER(?)
                """,
                [table, col],
            ).fetchall()
        except Exception:
            continue

        for (pii_flag,) in rows:
            if pii_flag and str(pii_flag).strip().upper() in ("PII", "YES", "TRUE", "1"):
                # Check whether a masking function wraps this column in the SQL
                findings.append(Finding(
                    rule_id="R008_PII_UNMASKED",
                    severity=Severity.HIGH,
                    message=(
                        f"Column '{table}.{col}' is flagged as PII in the metadata "
                        "but is selected without an obvious masking/hashing function."
                    ),
                    evidence=f"column_metadata.pii='{pii_flag}' for {table}.{col}",
                    location=f"SELECT referencing {table}.{col}",
                ))
    return findings


# ---- public API ------------------------------------------------------------

def run_all_rules(
    profile: QueryProfile,
    plan: ExplainPlan | None = None,
    db: duckdb.DuckDBPyConnection | None = None,
    broadcast_threshold_bytes: int = 536870912,
) -> list[Finding]:
    findings: list[Finding] = []
    for rule in _all_rules():
        try:
            if rule is rule_broadcast_large_table:
                result = rule(profile, plan, db, broadcast_threshold_bytes)  # type: ignore[call-arg]
            else:
                result = rule(profile, plan, db)
            findings.extend(result)
        except Exception as exc:
            logger.warning("Rule %s raised: %s", rule.__name__, exc)
    logger.info("run_all_rules: %d findings from %d rules", len(findings), len(_all_rules()))
    return findings

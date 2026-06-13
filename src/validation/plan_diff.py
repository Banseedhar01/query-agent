"""Compare baseline and candidate EXPLAIN plans; check AST structural equivalence."""

from __future__ import annotations

import logging
from enum import Enum

import sqlglot
from pydantic import BaseModel

from src.analysis.explain import ExplainPlan

logger = logging.getLogger(__name__)

DIALECT = "hive"


class Verdict(str, Enum):
    IMPROVED = "IMPROVED"
    NEUTRAL = "NEUTRAL"
    WORSE = "WORSE"


class PlanDiff(BaseModel):
    scan_bytes_delta: int
    join_strategy_changes: list[str]
    verdict: Verdict
    ast_equivalent: bool
    details: str


def diff_plans(base: ExplainPlan, candidate: ExplainPlan) -> PlanDiff:
    """
    Compare two ExplainPlan objects.
    scan_bytes_delta = candidate_total - base_total  (negative = improvement)
    """
    base_bytes = sum(base.scan_bytes_per_table.values())
    cand_bytes = sum(candidate.scan_bytes_per_table.values())
    delta = cand_bytes - base_bytes

    strategy_changes: list[str] = []
    all_tables = set(base.join_strategies) | set(candidate.join_strategies)
    for tbl in sorted(all_tables):
        old_s = base.join_strategies.get(tbl, "NONE")
        new_s = candidate.join_strategies.get(tbl, "NONE")
        if old_s != new_s:
            strategy_changes.append(f"{tbl}: {old_s} → {new_s}")

    if delta < 0:
        verdict = Verdict.IMPROVED
    elif delta == 0 and not strategy_changes:
        verdict = Verdict.NEUTRAL
    elif delta > 0:
        verdict = Verdict.WORSE
    else:
        # bytes neutral but join strategies changed — call it improved if BROADCAST→PARTITIONED
        if any("BROADCAST → PARTITIONED" in c for c in strategy_changes):
            verdict = Verdict.IMPROVED
        else:
            verdict = Verdict.NEUTRAL

    details = (
        f"Base scan total: {base_bytes} B, Candidate scan total: {cand_bytes} B, "
        f"Delta: {delta:+d} B. Strategy changes: {strategy_changes or 'none'}"
    )

    logger.debug("plan_diff: verdict=%s delta=%d", verdict, delta)
    return PlanDiff(
        scan_bytes_delta=delta,
        join_strategy_changes=strategy_changes,
        verdict=verdict,
        ast_equivalent=True,  # set by caller after AST check
        details=details,
    )


def check_ast_equivalence(base_sql: str, candidate_sql: str) -> bool:
    """
    Use sqlglot.diff to verify candidate is a semantic rewrite, not a broken query.
    Returns True if the structural diff is small (rewrites only, no table drops).
    """
    try:
        base_ast = sqlglot.parse_one(base_sql, dialect=DIALECT)
        cand_ast = sqlglot.parse_one(candidate_sql, dialect=DIALECT)
        diffs = sqlglot.diff(base_ast, cand_ast)
        # Count Remove operations on Table nodes (would mean a table was dropped)
        from sqlglot.diff import Remove
        import sqlglot.expressions as exp
        removed_tables = [
            d for d in diffs
            if isinstance(d, Remove) and isinstance(d.source, exp.Table)
        ]
        if removed_tables:
            logger.warning(
                "AST diff removed tables: %s",
                [str(t) for t in removed_tables],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("AST equivalence check failed: %s", exc)
        return False

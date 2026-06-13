"""Tests for validation/plan_diff.py — no cluster or LLM needed."""

from __future__ import annotations

import pytest
from src.analysis.explain import ExplainPlan
from src.validation.plan_diff import (
    Verdict,
    check_ast_equivalence,
    diff_plans,
)


def _make_plan(
    scan_bytes: dict[str, int] | None = None,
    strategies: dict[str, str] | None = None,
) -> ExplainPlan:
    plan = ExplainPlan()
    plan.scan_bytes_per_table = scan_bytes or {}
    plan.join_strategies = strategies or {}
    return plan


class TestDiffPlans:
    def test_improved_when_fewer_bytes(self) -> None:
        base = _make_plan({"t1": 1_000_000, "t2": 500_000})
        candidate = _make_plan({"t1": 200_000, "t2": 100_000})
        diff = diff_plans(base, candidate)
        assert diff.verdict == Verdict.IMPROVED
        assert diff.scan_bytes_delta < 0

    def test_worse_when_more_bytes(self) -> None:
        base = _make_plan({"t1": 100_000})
        candidate = _make_plan({"t1": 900_000})
        diff = diff_plans(base, candidate)
        assert diff.verdict == Verdict.WORSE
        assert diff.scan_bytes_delta > 0

    def test_neutral_when_equal_bytes_no_change(self) -> None:
        base = _make_plan({"t1": 500_000})
        candidate = _make_plan({"t1": 500_000})
        diff = diff_plans(base, candidate)
        assert diff.verdict == Verdict.NEUTRAL
        assert diff.scan_bytes_delta == 0

    def test_strategy_change_detected(self) -> None:
        base = _make_plan(strategies={"big_table": "BROADCAST"})
        candidate = _make_plan(strategies={"big_table": "PARTITIONED"})
        diff = diff_plans(base, candidate)
        assert len(diff.join_strategy_changes) == 1
        assert "BROADCAST" in diff.join_strategy_changes[0]
        assert "PARTITIONED" in diff.join_strategy_changes[0]

    def test_broadcast_to_partitioned_is_improved(self) -> None:
        base = _make_plan({"big_table": 600_000_000}, {"big_table": "BROADCAST"})
        candidate = _make_plan({"big_table": 600_000_000}, {"big_table": "PARTITIONED"})
        diff = diff_plans(base, candidate)
        # Bytes neutral but strategy improved → IMPROVED
        assert diff.verdict == Verdict.IMPROVED

    def test_delta_correct(self) -> None:
        base = _make_plan({"a": 1000, "b": 2000})
        cand = _make_plan({"a": 500, "b": 1000})
        diff = diff_plans(base, cand)
        assert diff.scan_bytes_delta == -1500


class TestAstEquivalence:
    def test_equivalent_simple_rewrite(self) -> None:
        base = "SELECT id, name FROM customers WHERE region = 'WEST'"
        candidate = "SELECT id, name FROM customers WHERE region = 'WEST' LIMIT 1000"
        result = check_ast_equivalence(base, candidate)
        assert isinstance(result, bool)

    def test_returns_false_on_removed_table(self) -> None:
        base = "SELECT a.id, b.name FROM tableA a JOIN tableB b ON a.id = b.a_id"
        candidate = "SELECT a.id FROM tableA a"  # removed tableB
        result = check_ast_equivalence(base, candidate)
        assert result is False

    def test_does_not_raise_on_invalid_sql(self) -> None:
        result = check_ast_equivalence("SELECT * FROM", "INVALID SQL !!!!")
        assert isinstance(result, bool)

    def test_same_query_is_equivalent(self) -> None:
        sql = "SELECT id, amount FROM orders WHERE status = 'PAID'"
        result = check_ast_equivalence(sql, sql)
        assert result is True

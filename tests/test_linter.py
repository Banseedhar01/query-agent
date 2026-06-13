"""Tests for analysis/linter.py — all rules tested without cluster or LLM."""

from __future__ import annotations

import pytest
import duckdb

from src.analysis.linter import (
    Finding,
    run_all_rules,
    rule_select_star,
    rule_missing_partition_filter,
    rule_non_sargable_predicate,
    rule_implicit_cross_join,
    rule_order_by_without_limit,
    rule_missing_compute_stats,
    rule_broadcast_large_table,
    rule_pii_unmasked,
)
from src.analysis.explain import ExplainPlan, PlanNode
from src.analysis.parser import QueryProfile, JoinEdge, FilterPredicate
from src.report.schema import Severity


# ---- fixtures ---------------------------------------------------------------

@pytest.fixture
def empty_profile() -> QueryProfile:
    return QueryProfile()


@pytest.fixture
def star_profile() -> QueryProfile:
    return QueryProfile(
        tables=["customers"],
        has_select_star=True,
    )


@pytest.fixture
def partition_profile() -> QueryProfile:
    return QueryProfile(
        tables=["sales.transactions"],
        filter_predicates=[
            FilterPredicate(column="customer_id", operator="EQ", value="123")
        ],
    )


@pytest.fixture
def non_sargable_profile() -> QueryProfile:
    return QueryProfile(
        tables=["sales.transactions"],
        filter_predicates=[
            FilterPredicate(
                column="transaction_date",
                operator="EQ",
                value="2024",
                is_non_sargable=True,
            )
        ],
    )


@pytest.fixture
def cross_join_profile() -> QueryProfile:
    return QueryProfile(
        tables=["customers", "orders"],
        join_graph=[
            JoinEdge(
                left_table="customers",
                right_table="orders",
                join_type="INNER",
                condition=None,
            )
        ],
    )


@pytest.fixture
def order_by_profile() -> QueryProfile:
    return QueryProfile(
        tables=["customers"],
        order_bys=["created_at desc"],
    )


@pytest.fixture
def broadcast_plan() -> ExplainPlan:
    plan = ExplainPlan()
    plan.join_strategies["big_table"] = "BROADCAST"
    plan.scan_bytes_per_table["big_table"] = 700 * 1024 * 1024  # 700 MB
    return plan


@pytest.fixture
def sort_plan() -> ExplainPlan:
    plan = ExplainPlan()
    plan.nodes = [PlanNode(operator="SORT", raw_text="SORT")]
    return plan


# ---- rule tests -------------------------------------------------------------

class TestRuleSelectStar:
    def test_fires_on_star(self, star_profile: QueryProfile) -> None:
        findings = rule_select_star(star_profile, None, None)
        assert len(findings) == 1
        assert findings[0].rule_id == "R001_SELECT_STAR"
        assert findings[0].severity == Severity.MEDIUM

    def test_silent_without_star(self, empty_profile: QueryProfile) -> None:
        findings = rule_select_star(empty_profile, None, None)
        assert findings == []


class TestRuleMissingPartitionFilter:
    def test_fires_when_partition_col_unfiltered(
        self, partition_profile: QueryProfile, in_memory_db: duckdb.DuckDBPyConnection
    ) -> None:
        findings = rule_missing_partition_filter(partition_profile, None, in_memory_db)
        assert any(f.rule_id == "R002_MISSING_PARTITION_FILTER" for f in findings)

    def test_silent_when_no_partition(
        self, empty_profile: QueryProfile, in_memory_db: duckdb.DuckDBPyConnection
    ) -> None:
        profile = QueryProfile(tables=["customers"])  # customers has no partition_columns
        findings = rule_missing_partition_filter(profile, None, in_memory_db)
        assert findings == []

    def test_silent_without_db(self, partition_profile: QueryProfile) -> None:
        findings = rule_missing_partition_filter(partition_profile, None, None)
        assert findings == []


class TestRuleNonSargable:
    def test_fires_on_function_predicate(self, non_sargable_profile: QueryProfile) -> None:
        findings = rule_non_sargable_predicate(non_sargable_profile, None, None)
        assert len(findings) >= 1
        assert findings[0].rule_id == "R003_NON_SARGABLE_PREDICATE"

    def test_silent_on_sargable_predicates(self, partition_profile: QueryProfile) -> None:
        findings = rule_non_sargable_predicate(partition_profile, None, None)
        assert findings == []


class TestRuleImplicitCrossJoin:
    def test_fires_on_no_condition(self, cross_join_profile: QueryProfile) -> None:
        findings = rule_implicit_cross_join(cross_join_profile, None, None)
        assert len(findings) == 1
        assert findings[0].rule_id == "R004_IMPLICIT_CROSS_JOIN"
        assert findings[0].severity == Severity.CRITICAL

    def test_silent_on_explicit_join(self) -> None:
        profile = QueryProfile(
            tables=["a", "b"],
            join_graph=[
                JoinEdge(left_table="a", right_table="b", join_type="INNER", condition="a.id = b.a_id")
            ],
        )
        findings = rule_implicit_cross_join(profile, None, None)
        assert findings == []


class TestRuleOrderByNoLimit:
    def test_fires_with_sort_node_no_topn(
        self, order_by_profile: QueryProfile, sort_plan: ExplainPlan
    ) -> None:
        findings = rule_order_by_without_limit(order_by_profile, sort_plan, None)
        assert any(f.rule_id == "R005_ORDER_BY_NO_LIMIT" for f in findings)

    def test_silent_with_topn(self, order_by_profile: QueryProfile) -> None:
        plan = ExplainPlan()
        plan.nodes = [
            PlanNode(operator="TOP-N", raw_text="TOP-N"),
            PlanNode(operator="SORT", raw_text="SORT"),
        ]
        findings = rule_order_by_without_limit(order_by_profile, plan, None)
        assert findings == []

    def test_silent_without_order_by(self, empty_profile: QueryProfile) -> None:
        findings = rule_order_by_without_limit(empty_profile, None, None)
        assert findings == []


class TestRuleMissingComputeStats:
    def test_fires_when_no_column_stats(
        self, in_memory_db: duckdb.DuckDBPyConnection
    ) -> None:
        # Use a table not in column_stats
        profile = QueryProfile(tables=["orphan_table"])
        findings = rule_missing_compute_stats(profile, None, in_memory_db)
        assert any(f.rule_id == "R006_MISSING_COMPUTE_STATS" for f in findings)

    def test_fires_from_plan_warnings(self) -> None:
        profile = QueryProfile(tables=["unknown_tbl"])
        plan = ExplainPlan(missing_stats_tables=["unknown_tbl"])
        findings = rule_missing_compute_stats(profile, plan, None)
        assert any(f.rule_id == "R006_MISSING_COMPUTE_STATS" for f in findings)

    def test_silent_when_stats_present(
        self, in_memory_db: duckdb.DuckDBPyConnection
    ) -> None:
        profile = QueryProfile(tables=["customers"])
        findings = rule_missing_compute_stats(profile, None, in_memory_db)
        assert findings == []


class TestRuleBroadcastLargeTable:
    def test_fires_above_threshold(
        self, empty_profile: QueryProfile, broadcast_plan: ExplainPlan
    ) -> None:
        findings = rule_broadcast_large_table(empty_profile, broadcast_plan, None, 536870912)
        assert any(f.rule_id == "R007_BROADCAST_LARGE_TABLE" for f in findings)

    def test_silent_below_threshold(self, empty_profile: QueryProfile) -> None:
        plan = ExplainPlan()
        plan.join_strategies["small_table"] = "BROADCAST"
        plan.scan_bytes_per_table["small_table"] = 10 * 1024 * 1024  # 10 MB
        findings = rule_broadcast_large_table(empty_profile, plan, None, 536870912)
        assert findings == []

    def test_silent_without_plan(self, empty_profile: QueryProfile) -> None:
        findings = rule_broadcast_large_table(empty_profile, None, None)
        assert findings == []


class TestRulePiiUnmasked:
    def test_fires_on_pii_column(
        self, in_memory_db: duckdb.DuckDBPyConnection
    ) -> None:
        profile = QueryProfile(
            tables=["customers"],
            columns_per_table={"customers": ["email", "customer_id"]},
        )
        findings = rule_pii_unmasked(profile, None, in_memory_db)
        assert any(f.rule_id == "R008_PII_UNMASKED" for f in findings)
        pii_finding = next(f for f in findings if f.rule_id == "R008_PII_UNMASKED")
        assert pii_finding.severity == Severity.HIGH

    def test_silent_on_non_pii(
        self, in_memory_db: duckdb.DuckDBPyConnection
    ) -> None:
        profile = QueryProfile(
            tables=["customers"],
            columns_per_table={"customers": ["customer_id", "region"]},
        )
        findings = rule_pii_unmasked(profile, None, in_memory_db)
        assert findings == []

    def test_silent_without_db(self) -> None:
        profile = QueryProfile(
            tables=["customers"],
            columns_per_table={"customers": ["email"]},
        )
        findings = rule_pii_unmasked(profile, None, None)
        assert findings == []


class TestRunAllRules:
    def test_select_star_and_pii_deterministic(
        self, in_memory_db: duckdb.DuckDBPyConnection
    ) -> None:
        """Acceptance criterion: SELECT * + PII found deterministically offline."""
        profile = QueryProfile(
            tables=["customers"],
            has_select_star=True,
            columns_per_table={"customers": ["email", "ssn", "customer_id"]},
            filter_predicates=[
                FilterPredicate(column="region", operator="EQ", value="'WEST'")
            ],
        )
        findings = run_all_rules(profile, None, in_memory_db)
        rule_ids = {f.rule_id for f in findings}
        assert "R001_SELECT_STAR" in rule_ids
        assert "R008_PII_UNMASKED" in rule_ids

    def test_returns_list_of_findings(
        self, empty_profile: QueryProfile
    ) -> None:
        findings = run_all_rules(empty_profile, None, None)
        assert isinstance(findings, list)

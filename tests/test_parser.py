"""Tests for analysis/parser.py — no cluster or LLM needed."""

from __future__ import annotations

import pytest
from src.analysis.parser import parse_query, QueryProfile


class TestBasicParsing:
    def test_single_table(self) -> None:
        profile = parse_query("SELECT id, name FROM customers WHERE region = 'WEST'")
        assert "customers" in profile.tables
        assert not profile.has_select_star
        assert len(profile.filter_predicates) == 1

    def test_select_star(self) -> None:
        profile = parse_query("SELECT * FROM customers")
        assert profile.has_select_star

    def test_fully_qualified_table(self) -> None:
        profile = parse_query("SELECT * FROM sales.transactions")
        assert "sales.transactions" in profile.tables

    def test_cte_not_in_tables(self) -> None:
        sql = """
        WITH base AS (SELECT id FROM raw_table)
        SELECT id FROM base
        """
        profile = parse_query(sql)
        assert "base" in profile.cte_names
        assert "base" not in profile.tables

    def test_join_edge_extracted(self) -> None:
        sql = """
        SELECT c.id, o.order_id
        FROM customers c
        JOIN orders o ON c.id = o.customer_id
        """
        profile = parse_query(sql)
        assert len(profile.join_graph) == 1
        edge = profile.join_graph[0]
        assert "customers" in edge.left_table
        assert "orders" in edge.right_table
        assert edge.condition is not None

    def test_cross_join_no_condition(self) -> None:
        sql = "SELECT a.id, b.id FROM table_a a, table_b b"
        profile = parse_query(sql)
        # Parser extracts tables; cross-join detection is done in linter
        assert "table_a" in profile.tables
        assert "table_b" in profile.tables

    def test_subquery_counted(self) -> None:
        sql = """
        SELECT *
        FROM (SELECT id FROM customers WHERE region = 'EAST') sub
        """
        profile = parse_query(sql)
        assert profile.subquery_count >= 1

    def test_group_by_order_by(self) -> None:
        sql = """
        SELECT region, COUNT(*) AS cnt
        FROM customers
        GROUP BY region
        ORDER BY cnt DESC
        """
        profile = parse_query(sql)
        assert len(profile.group_bys) >= 1
        assert len(profile.order_bys) >= 1

    def test_non_sargable_function(self) -> None:
        sql = """
        SELECT * FROM sales.transactions
        WHERE YEAR(transaction_date) = 2024
        """
        profile = parse_query(sql)
        non_sarg = [p for p in profile.filter_predicates if p.is_non_sargable]
        assert len(non_sarg) >= 1

    def test_invalid_sql_no_raise(self) -> None:
        """Parser must not raise on invalid SQL."""
        profile = parse_query("SELECT FROM WHERE AND")
        assert isinstance(profile, QueryProfile)
        # May have parse errors but should not raise

    def test_empty_string_no_raise(self) -> None:
        profile = parse_query("")
        assert isinstance(profile, QueryProfile)

    def test_multiple_joins(self) -> None:
        sql = """
        SELECT a.id, b.name, c.amount
        FROM tableA a
        JOIN tableB b ON a.id = b.a_id
        LEFT JOIN tableC c ON b.id = c.b_id
        """
        profile = parse_query(sql)
        assert len(profile.join_graph) == 2

    def test_columns_per_table(self) -> None:
        sql = "SELECT c.id, c.email, o.order_id FROM customers c JOIN orders o ON c.id = o.customer_id"
        profile = parse_query(sql)
        assert "c" in profile.columns_per_table or "customers" in profile.columns_per_table

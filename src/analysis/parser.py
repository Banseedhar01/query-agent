"""sqlglot-based SQL parser: extracts tables, columns, joins, predicates."""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
import sqlglot.expressions as exp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DIALECT = "hive"


class JoinEdge(BaseModel):
    left_table: str
    right_table: str
    join_type: str
    condition: str | None = None


class FilterPredicate(BaseModel):
    column: str
    operator: str
    value: str
    is_non_sargable: bool = False


class QueryProfile(BaseModel):
    tables: list[str] = Field(default_factory=list)
    columns_per_table: dict[str, list[str]] = Field(default_factory=dict)
    join_graph: list[JoinEdge] = Field(default_factory=list)
    filter_predicates: list[FilterPredicate] = Field(default_factory=list)
    group_bys: list[str] = Field(default_factory=list)
    order_bys: list[str] = Field(default_factory=list)
    has_select_star: bool = False
    subquery_count: int = 0
    cte_names: list[str] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)


def _fqn(node: exp.Expression) -> str:
    """Return db.table or table name, lowercased. Handles Alias wrappers."""
    # Unwrap Alias (e.g. `customers AS c`)
    if isinstance(node, exp.Alias):
        node = node.args.get("this", node)
    if isinstance(node, exp.Table):
        parts = [p for p in (node.args.get("db"), node.args.get("this")) if p]
        return ".".join(str(p).lower().strip("`\"' ") for p in parts)
    return str(node).lower().strip("`\"' ")


def _col_name(node: exp.Column) -> str:
    parts = []
    if node.args.get("table"):
        parts.append(str(node.args["table"]).lower().strip("`\"' "))
    parts.append(str(node.args["this"]).lower().strip("`\"' "))
    return ".".join(parts)


def _is_non_sargable(where_node: exp.Expression) -> bool:
    """Detect function or CAST applied to a column in a filter position."""
    for node in where_node.walk():
        if isinstance(node, (exp.Anonymous, exp.Cast, exp.Upper, exp.Lower,
                              exp.Year, exp.Month, exp.Day, exp.Substring,
                              exp.Trim, exp.Coalesce)):
            for child in node.walk():
                if isinstance(child, exp.Column):
                    return True
    return False


def _extract_predicates(where: exp.Expression | None) -> list[FilterPredicate]:
    if where is None:
        return []
    predicates: list[FilterPredicate] = []
    for node in where.walk():
        if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
                               exp.Like, exp.In, exp.Between)):
            left = node.args.get("this") or node.args.get("left")
            right = node.args.get("expression") or node.args.get("right")
            col_str = str(left) if left else ""
            val_str = str(right) if right else ""
            non_sarg = _is_non_sargable(node)
            predicates.append(FilterPredicate(
                column=col_str.lower(),
                operator=type(node).__name__,
                value=val_str[:200],
                is_non_sargable=non_sarg,
            ))
    return predicates


def _collect_join_edges(select: exp.Select) -> list[JoinEdge]:
    edges: list[JoinEdge] = []
    # sqlglot uses "from_" as the key for the FROM clause
    from_clause = select.args.get("from_") or select.args.get("from")
    if from_clause is None:
        return edges
    from_table = from_clause.args.get("this")
    left_name = _fqn(from_table) if from_table is not None else ""

    for join in select.args.get("joins") or []:
        join_table = join.args.get("this")
        right_name = _fqn(join_table) if join_table is not None else ""
        join_type = join.args.get("kind", "INNER")
        if isinstance(join_type, exp.Expression):
            join_type = str(join_type).upper()
        on_clause = join.args.get("on")
        edges.append(JoinEdge(
            left_table=left_name,
            right_table=right_name,
            join_type=str(join_type).upper(),
            condition=str(on_clause) if on_clause else None,
        ))
        left_name = right_name
    return edges


def parse_query(sql: str) -> QueryProfile:
    """Parse a SQL string and return a QueryProfile. Never raises."""
    profile = QueryProfile()
    try:
        statements = sqlglot.parse(sql, dialect=DIALECT, error_level=sqlglot.ErrorLevel.WARN)
    except Exception as exc:
        profile.parse_errors.append(f"parse failed: {exc}")
        logger.warning("sqlglot parse failed: %s", exc)
        return profile

    if not statements:
        profile.parse_errors.append("no statements parsed")
        return profile

    ast = statements[0]
    if ast is None:
        profile.parse_errors.append("null AST returned")
        return profile

    # Collect CTEs — sqlglot stores them as exp.CTE nodes in the walk
    for node in ast.walk():
        if isinstance(node, exp.CTE):
            cte_alias = node.alias  # string property on CTE
            if cte_alias:
                profile.cte_names.append(cte_alias.lower())

    # Walk all selects (including subqueries)
    seen_tables: set[str] = set()
    col_map: dict[str, list[str]] = {}

    for node in ast.walk():
        if isinstance(node, exp.Table):
            name = _fqn(node)
            if name and name not in profile.cte_names:
                seen_tables.add(name)

        if isinstance(node, exp.Star):
            profile.has_select_star = True

        if isinstance(node, exp.Subquery):
            profile.subquery_count += 1

        if isinstance(node, exp.Column):
            tbl = node.args.get("table")
            col = node.args.get("this")
            if tbl and col:
                tname = str(tbl).lower().strip("`\"'")
                cname = str(col).lower().strip("`\"'")
                col_map.setdefault(tname, [])
                if cname not in col_map[tname]:
                    col_map[tname].append(cname)

    profile.tables = sorted(seen_tables)
    profile.columns_per_table = col_map

    # Top-level select analysis
    if isinstance(ast, exp.Select):
        profile.join_graph = _collect_join_edges(ast)
        profile.filter_predicates = _extract_predicates(ast.args.get("where"))

        group_node = ast.args.get("group")
        if group_node:
            for gb in (group_node.expressions or []):
                profile.group_bys.append(str(gb).lower())

        order_node = ast.args.get("order")
        if order_node:
            for ob in (order_node.expressions or []):
                profile.order_bys.append(str(ob).lower())

    logger.debug(
        "parse_query: tables=%d joins=%d predicates=%d",
        len(profile.tables), len(profile.join_graph), len(profile.filter_predicates),
    )
    return profile

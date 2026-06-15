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
    has_limit: bool = False
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


def _build_cte_source_map(
    cte_nodes: list[exp.CTE], cte_name_set: set[str]
) -> dict[str, list[str]]:
    """Return {cte_name: [real_table, ...]} with transitive closure resolved.

    Example: npa_risk → active_agreements → dim_agreement
    Result:  {"npa_risk": ["dim_agreement", "dim_application"], ...}
    """
    # Step 1 — direct sources (may still contain other CTE names)
    direct: dict[str, list[str]] = {}
    for cte_node in cte_nodes:
        name = (cte_node.alias or "").lower()
        if not name:
            continue
        body = cte_node.args.get("this")
        sources: list[str] = []
        if body:
            for tnode in body.walk():
                if isinstance(tnode, exp.Table):
                    tname = _fqn(tnode)
                    if tname and tname != name:
                        sources.append(tname)
        direct[name] = list(dict.fromkeys(sources))  # dedupe, preserve order

    # Step 2 — transitive closure: replace CTE refs with their real tables
    resolved: dict[str, list[str]] = {}

    def _resolve(name: str, visiting: frozenset[str]) -> list[str]:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            return []  # cycle guard (recursive CTEs not supported in Hive/Impala anyway)
        visiting = visiting | {name}
        real: list[str] = []
        for src in direct.get(name, []):
            if src in direct:  # src is also a CTE — recurse
                real.extend(_resolve(src, visiting))
            else:
                real.append(src)
        result = list(dict.fromkeys(real))
        resolved[name] = result
        return result

    for name in direct:
        _resolve(name, frozenset())

    return resolved


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

    # ── Pass 1: collect table aliases and CTE metadata ───────────────────────
    # Must run before column resolution because ast.walk() visits SELECT
    # expressions before FROM — so aliases aren't known yet when columns appear.
    cte_nodes: list[exp.CTE] = []
    seen_tables: set[str] = set()
    alias_to_table: dict[str, str] = {}  # alias → table name (real or CTE)

    for node in ast.walk():
        if isinstance(node, exp.CTE):
            cte_alias = node.alias
            if cte_alias:
                profile.cte_names.append(cte_alias.lower())
                cte_nodes.append(node)

        if isinstance(node, exp.Table):
            name = _fqn(node)
            if not name:
                continue
            # Capture alias for ALL tables including CTE references (e.g. `FROM npa_accounts n`).
            # Using node.alias (string shortcut) is more reliable than args.get("alias").
            if node.alias:
                alias_str = node.alias.lower().strip("`\"' ")
                if alias_str:
                    alias_to_table[alias_str] = name

    cte_name_set = set(profile.cte_names)
    # Populate seen_tables separately so CTE names are already known
    for node in ast.walk():
        if isinstance(node, exp.Table):
            name = _fqn(node)
            if name and name not in cte_name_set:
                seen_tables.add(name)

    # Maps each CTE → real source tables (transitive, e.g. npa_risk → dim_agreement)
    cte_source_map = _build_cte_source_map(cte_nodes, cte_name_set)

    # ── Pass 2: collect columns, stars, subqueries ────────────────────────────
    col_map: dict[str, list[str]] = {}
    unqualified_cols: list[str] = []  # columns with no table prefix

    for node in ast.walk():
        if isinstance(node, exp.Star):
            # Only flag SELECT * — not COUNT(*) or other aggregate wildcards.
            # Stars inside any function call (exp.Func covers COUNT, SUM, etc.) are skipped.
            if not isinstance(node.parent, exp.Func):
                profile.has_select_star = True

        if isinstance(node, exp.Subquery):
            profile.subquery_count += 1

        if isinstance(node, exp.Column):
            tbl = node.args.get("table")
            col = node.args.get("this")
            if not col:
                continue
            cname = str(col).lower().strip("`\"'")
            if not tbl:
                # Unqualified column (e.g. SELECT crn FROM dim_agreement).
                # Defer attribution — assigned to the sole table after the walk.
                if cname not in unqualified_cols:
                    unqualified_cols.append(cname)
                continue
            tname = str(tbl).lower().strip("`\"'")
            # resolve alias → table name (may still be a CTE name after this)
            tname = alias_to_table.get(tname, tname)
            if tname in cte_name_set:
                # Trace through CTE chain to real source tables and store there.
                # e.g. n → npa_accounts → dim_agreement → store col under dim_agreement
                for real_table in cte_source_map.get(tname, []):
                    col_map.setdefault(real_table, [])
                    if cname not in col_map[real_table]:
                        col_map[real_table].append(cname)
                continue
            col_map.setdefault(tname, [])
            if cname not in col_map[tname]:
                col_map[tname].append(cname)

    # For single-table queries, safely attribute unqualified columns to that table.
    # For multi-table queries we can't know which table they belong to — skip.
    if unqualified_cols and len(seen_tables) == 1:
        sole = next(iter(seen_tables))
        col_map.setdefault(sole, [])
        for cname in unqualified_cols:
            if cname not in col_map[sole]:
                col_map[sole].append(cname)

    profile.tables = sorted(seen_tables)
    profile.columns_per_table = col_map

    # Top-level select analysis
    if isinstance(ast, exp.Select):
        profile.join_graph = _collect_join_edges(ast)
        profile.filter_predicates = _extract_predicates(ast.args.get("where"))
        profile.has_limit = ast.args.get("limit") is not None

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

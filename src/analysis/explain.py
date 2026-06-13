"""Run EXPLAIN LEVEL=2 on Impala and parse into structured nodes."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Patterns extracted from Impala EXPLAIN output
_SCAN_RE = re.compile(
    r"SCAN\s+(?:HDFS|KUDU|HBase)?\[?(?P<table>[\w.]+)\]?.*?(?:partitions=\S+)?",
    re.IGNORECASE,
)
_ROWS_RE = re.compile(r"cardinality=(\S+)", re.IGNORECASE)
_BYTES_RE = re.compile(r"(?:bytes_per_row|hdfs_scan).*?=(\S+)", re.IGNORECASE)
_WARN_RE = re.compile(r"WARNING:?\s*(.+)", re.IGNORECASE)
_JOIN_RE = re.compile(
    r"(?P<strategy>BROADCAST|PARTITIONED|SHUFFLE)\s+(?:HASH\s+)?JOIN",
    re.IGNORECASE,
)
_BYTES_UNIT_RE = re.compile(r"([\d.]+)\s*([KMGT]?B)?", re.IGNORECASE)


def _parse_bytes(s: str) -> int:
    """Convert '512.00 MB' → int bytes."""
    m = _BYTES_UNIT_RE.search(s)
    if not m:
        return 0
    val = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1048576, "GB": 1073741824, "TB": 1099511627776}
    return int(val * multipliers.get(unit, 1))


def _parse_rows(s: str) -> int:
    """Parse row count, handling K/M/B suffixes."""
    s = s.strip().upper()
    if s in ("UNAVAILABLE", "UNKNOWN", ""):
        return -1
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "G": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            try:
                return int(float(s[:-1]) * mult)
            except ValueError:
                return -1
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return -1


class PlanNode(BaseModel):
    operator: str
    estimated_rows: int = -1
    scan_bytes: int = 0
    table: str | None = None
    join_strategy: str | None = None
    warnings: list[str] = Field(default_factory=list)
    raw_text: str = ""


class ExplainPlan(BaseModel):
    nodes: list[PlanNode] = Field(default_factory=list)
    scan_bytes_per_table: dict[str, int] = Field(default_factory=dict)
    join_strategies: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    raw_plan: str = ""
    missing_stats_tables: list[str] = Field(default_factory=list)


def parse_explain_text(plan_text: str) -> ExplainPlan:
    """Parse raw Impala EXPLAIN output into an ExplainPlan."""
    plan = ExplainPlan(raw_plan=plan_text)
    current_node: PlanNode | None = None

    for line in plan_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Detect operator lines (they usually start with digits or dashes)
        op_match = re.match(r"^[\d:]+:(.+)$", stripped)
        if op_match:
            if current_node:
                plan.nodes.append(current_node)
            op_text = op_match.group(1).strip()
            current_node = PlanNode(operator=op_text, raw_text=stripped)

            scan_m = _SCAN_RE.search(op_text)
            if scan_m:
                current_node.table = scan_m.group("table").lower()

            join_m = _JOIN_RE.search(op_text)
            if join_m and current_node.table:
                current_node.join_strategy = join_m.group("strategy").upper()

        elif current_node:
            current_node.raw_text += "\n" + stripped

            row_m = _ROWS_RE.search(stripped)
            if row_m:
                current_node.estimated_rows = _parse_rows(row_m.group(1))

            byte_m = _BYTES_RE.search(stripped)
            if byte_m:
                current_node.scan_bytes = _parse_bytes(byte_m.group(1))

            warn_m = _WARN_RE.search(stripped)
            if warn_m:
                warn_text = warn_m.group(1).strip()
                current_node.warnings.append(warn_text)
                plan.warnings.append(warn_text)

        # Missing stats detection (outside a node block too)
        if re.search(r"missing\s+statistics|no\s+stats", stripped, re.IGNORECASE):
            tbl_m = re.search(r"table[\s:]+(\S+)", stripped, re.IGNORECASE)
            if tbl_m:
                tbl = tbl_m.group(1).lower().strip("[](),")
                if tbl not in plan.missing_stats_tables:
                    plan.missing_stats_tables.append(tbl)

        join_m = _JOIN_RE.search(stripped)
        if join_m:
            strategy = join_m.group("strategy").upper()
            if current_node and current_node.table:
                plan.join_strategies[current_node.table] = strategy

    if current_node:
        plan.nodes.append(current_node)

    # Aggregate scan bytes per table
    for node in plan.nodes:
        if node.table and node.scan_bytes > 0:
            existing = plan.scan_bytes_per_table.get(node.table, 0)
            plan.scan_bytes_per_table[node.table] = max(existing, node.scan_bytes)

    logger.debug(
        "parse_explain_text: nodes=%d scan_tables=%d warnings=%d",
        len(plan.nodes), len(plan.scan_bytes_per_table), len(plan.warnings),
    )
    return plan


def get_plan(sql: str, cursor: Any) -> ExplainPlan:
    """Execute EXPLAIN LEVEL=2 via impyla cursor and parse the result."""
    try:
        cursor.execute("SET EXPLAIN_LEVEL=2")
        cursor.execute(f"EXPLAIN {sql}")
        rows = cursor.fetchall()
        plan_text = "\n".join(str(r[0]) for r in rows if r)
        return parse_explain_text(plan_text)
    except Exception as exc:
        logger.error("get_plan failed: %s", exc)
        plan = ExplainPlan()
        plan.warnings.append(f"EXPLAIN failed: {exc}")
        return plan

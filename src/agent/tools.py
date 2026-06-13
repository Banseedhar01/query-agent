"""LangChain tools for the LLM analyzer: metadata and plan lookups."""

from __future__ import annotations

import json
import logging
from typing import Any

import duckdb
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Module-level state injected by graph before LLM invocation
_db_path: str = "metadata.duckdb"
_impala_cursor: Any = None


def configure_tools(db_path: str, impala_cursor: Any = None) -> None:
    global _db_path, _impala_cursor
    _db_path = db_path
    _impala_cursor = impala_cursor


@tool
def lookup_column_metadata(table: str, column: str) -> str:
    """
    Look up metadata for a specific table.column in the metadata store.
    Returns JSON with data_type, pii flag, description, and lineage info.
    Never infer facts; only return what is in the store.
    """
    try:
        con = duckdb.connect(_db_path, read_only=True)
        rows = con.execute(
            """
            SELECT column_name, data_type, pii, column_description,
                   nullable, mapping_type, source_table, source_column,
                   logical_transformation
            FROM column_metadata
            WHERE LOWER(table_name) = LOWER(?)
              AND LOWER(column_name) = LOWER(?)
            LIMIT 5
            """,
            [table, column],
        ).fetchall()
        con.close()
        if not rows:
            return json.dumps({"found": False, "table": table, "column": column})
        col_data = [
            {
                "column_name": r[0],
                "data_type": r[1],
                "pii": r[2],
                "description": r[3],
                "nullable": r[4],
                "mapping_type": r[5],
                "source_table": r[6],
                "source_column": r[7],
                "logical_transformation": r[8],
            }
            for r in rows
        ]
        logger.debug("lookup_column_metadata: %s.%s → %d rows", table, column, len(rows))
        return json.dumps({"found": True, "table": table, "column": column, "metadata": col_data})
    except Exception as exc:
        logger.error("lookup_column_metadata error: %s", exc)
        return json.dumps({"error": str(exc), "table": table, "column": column})


@tool
def get_table_stats(table: str) -> str:
    """
    Retrieve cached Impala statistics for a table: row count, size, partition columns.
    Returns JSON. Data comes only from the local stats cache — never invented.
    """
    try:
        con = duckdb.connect(_db_path, read_only=True)
        row = con.execute(
            """
            SELECT num_rows, num_files, size_bytes, partition_columns,
                   stats_available, collected_at
            FROM table_stats
            WHERE LOWER(table_name) = LOWER(?)
            """,
            [table],
        ).fetchone()

        col_rows = con.execute(
            """
            SELECT column_name, num_distinct, num_nulls, max_size, avg_size
            FROM column_stats
            WHERE LOWER(table_name) = LOWER(?)
            ORDER BY column_name
            """,
            [table],
        ).fetchall()
        con.close()

        if not row:
            return json.dumps({"found": False, "table": table})

        result = {
            "found": True,
            "table": table,
            "num_rows": row[0],
            "num_files": row[1],
            "size_bytes": row[2],
            "partition_columns": row[3],
            "stats_available": row[4],
            "collected_at": str(row[5]),
            "column_stats": [
                {
                    "column": r[0], "num_distinct": r[1],
                    "num_nulls": r[2], "max_size": r[3], "avg_size": r[4],
                }
                for r in col_rows
            ],
        }
        logger.debug("get_table_stats: %s → available=%s", table, row[4])
        return json.dumps(result)
    except Exception as exc:
        logger.error("get_table_stats error: %s", exc)
        return json.dumps({"error": str(exc), "table": table})


@tool
def run_explain(sql: str) -> str:
    """
    Run EXPLAIN LEVEL=2 on the provided SQL against the Impala cluster.
    Returns the structured plan as JSON. Only callable when cluster is available.
    """
    if _impala_cursor is None:
        return json.dumps({"error": "No Impala connection available (offline mode)"})
    try:
        from src.analysis.explain import get_plan
        plan = get_plan(sql, _impala_cursor)
        result = {
            "scan_bytes_per_table": plan.scan_bytes_per_table,
            "join_strategies": plan.join_strategies,
            "warnings": plan.warnings,
            "missing_stats_tables": plan.missing_stats_tables,
            "raw_plan": plan.raw_plan[:2000],  # truncate for token budget
        }
        logger.debug("run_explain: %d nodes, %d warnings", len(plan.nodes), len(plan.warnings))
        return json.dumps(result)
    except Exception as exc:
        logger.error("run_explain error: %s", exc)
        return json.dumps({"error": str(exc)})


ALL_TOOLS = [lookup_column_metadata, get_table_stats, run_explain]

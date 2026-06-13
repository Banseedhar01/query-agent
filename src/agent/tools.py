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
    Look up metadata for a specific table.column from the DuckDB metadata store.

    Returns JSON with the following fields (null if not available):
      - column_name            : exact column name as stored
      - data_type              : column data type (e.g. string, int, timestamp)
      - pii                    : PII flag — 'pii' means sensitive, 'non-pii' means safe
      - description            : human-readable business description of the column
      - nullable               : 'yes' or 'no' — whether nulls are allowed
      - mapping_type           : 'straight' (direct copy) or 'derived' (transformed)
      - source_table           : upstream source table this column originates from
      - source_column          : upstream source column name before transformation
      - logical_transformation  : business logic applied (e.g. 'masked email', 'sum of daily totals')
      - physical_transformation : actual SQL expression used to derive this column in the ETL
      - source_column_data_type : data type of the column in the source system (may differ from mart type)

    Use this tool to:
      - Confirm whether a column is PII before flagging R008
      - Understand what a column represents before suggesting a rewrite
      - Check source lineage when proposing joins or filter pushdowns
      - Verify data type before recommending CAST or comparison changes
      - Use physical_transformation to understand how a derived column is built before rewriting
      - Compare source_column_data_type vs data_type to flag implicit CAST risks (R003)

    Never infer or assume any of these facts — only use what this tool returns.
    If found=false, state that the column is not in the metadata store.
    """
    try:
        con = duckdb.connect(_db_path, read_only=True)
        rows = con.execute(
            """
            SELECT column_name, data_type, pii, column_description,
                   nullable, mapping_type, source_table, source_column,
                   logical_transformation, physical_transformation,
                   source_column_data_type
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
                "physical_transformation": r[9],
                "source_column_data_type": r[10],
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
    Retrieve cached Impala statistics for a table from the DuckDB stats store.

    Returns JSON with the following fields:
      Table-level:
        - num_rows           : total row count (use to assess join broadcast risk)
        - num_files          : number of HDFS files
        - size_bytes         : total table size in bytes
        - partition_columns  : comma-separated list of partition columns (use to verify R002)
        - stats_available    : true/false — whether COMPUTE STATS has been run (R006 check)
        - collected_at       : timestamp when stats were last collected

      Per-column stats (list):
        - column             : column name
        - num_distinct       : distinct value count (cardinality)
        - num_nulls          : null count
        - max_size           : max value size in bytes
        - avg_size           : average value size in bytes

    Use this tool to:
      - Check table size before recommending BROADCAST join (R007 threshold: 512 MB)
      - Verify partition columns exist to confirm R002 finding
      - Check stats_available=false to confirm R006 (missing COMPUTE STATS)
      - Use num_distinct to assess filter selectivity

    Data comes only from the local stats cache — never invented.
    If found=false, no stats have been collected for this table.
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
    Run EXPLAIN LEVEL=2 on the provided SQL against the live Impala cluster.

    Returns JSON with:
      - scan_bytes_per_table  : dict of table → estimated bytes scanned
      - join_strategies       : dict of table → join strategy (BROADCAST/PARTITIONED/SHUFFLE)
      - warnings              : list of planner warnings (e.g. missing stats, skewed data)
      - missing_stats_tables  : list of tables where COMPUTE STATS has not been run
      - raw_plan              : first 2000 chars of the raw EXPLAIN text

    Use this tool to:
      - Confirm actual scan size before flagging R007 (broadcast on large table)
      - Check join strategy chosen by Impala's cost-based optimizer
      - Identify planner warnings that indicate data quality or stats issues
      - Compare rewrite candidate plan vs original plan

    Only callable when an Impala cluster connection is available.
    Returns error JSON in offline mode — do not retry in that case.
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


@tool
def get_table_lineage(table: str) -> str:
    """
    Look up source-to-mart lineage for a given mart table from the DuckDB lineage store.

    Returns JSON with a list of lineage rows, each containing:
      - target_table   : mart/datamart table name (the table you queried)
      - target_column  : mart column name (Mart Field)
      - source_table   : upstream source table this mart column originates from
      - source_column  : upstream source column name before transformation
      - transformation : physical SQL transformation applied (if any)
      - org            : business unit / organisation this lineage belongs to

    Use this tool to:
      - Understand which source tables feed into a mart table before suggesting joins
      - Identify upstream source tables when a mart column has no direct filter
      - Check if a filter can be pushed down to the source table for better partition pruning
      - Trace data origin when the LLM needs to reason about derived or aggregated columns
      - Understand org-level data ownership when multiple orgs share the same mart table

    If found=false or lineage list is empty, no lineage has been ingested for this table.
    """
    try:
        con = duckdb.connect(_db_path, read_only=True)
        rows = con.execute(
            """
            SELECT target_table, target_column, source_table, source_column,
                   transformation, org
            FROM table_lineage
            WHERE LOWER(target_table) = LOWER(?)
            ORDER BY target_column
            """,
            [table],
        ).fetchall()
        con.close()

        if not rows:
            return json.dumps({"found": False, "table": table, "lineage": []})

        lineage = [
            {
                "target_table": r[0],
                "target_column": r[1],
                "source_table": r[2],
                "source_column": r[3],
                "transformation": r[4],
                "org": r[5],
            }
            for r in rows
        ]
        logger.debug("get_table_lineage: %s → %d rows", table, len(rows))
        return json.dumps({"found": True, "table": table, "lineage": lineage})
    except Exception as exc:
        logger.error("get_table_lineage error: %s", exc)
        return json.dumps({"error": str(exc), "table": table})


ALL_TOOLS = [lookup_column_metadata, get_table_stats, run_explain, get_table_lineage]

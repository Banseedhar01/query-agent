"""Collect SHOW TABLE/COLUMN STATS from Impala → DuckDB with TTL caching."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

_DDL_TABLE_STATS = """
CREATE TABLE IF NOT EXISTS table_stats (
    table_name VARCHAR PRIMARY KEY,
    num_rows BIGINT,
    num_files BIGINT,
    size_bytes BIGINT,
    partition_columns VARCHAR,
    stats_available BOOLEAN DEFAULT TRUE,
    collected_at TIMESTAMP
)
"""

_DDL_COLUMN_STATS = """
CREATE TABLE IF NOT EXISTS column_stats (
    table_name VARCHAR,
    column_name VARCHAR,
    num_distinct BIGINT,
    num_nulls BIGINT,
    max_size INTEGER,
    avg_size DOUBLE,
    collected_at TIMESTAMP,
    PRIMARY KEY (table_name, column_name)
)
"""


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_DDL_TABLE_STATS)
    con.execute(_DDL_COLUMN_STATS)


def _is_fresh(con: duckdb.DuckDBPyConnection, table: str, ttl_hours: int) -> bool:
    row = con.execute(
        "SELECT collected_at FROM table_stats WHERE table_name = ?", [table]
    ).fetchone()
    if not row or row[0] is None:
        return False
    collected_at = row[0]
    if isinstance(collected_at, str):
        collected_at = datetime.fromisoformat(collected_at)
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    return collected_at > cutoff


def _parse_show_table_stats(rows: list[Any]) -> dict[str, Any]:
    """Parse rows from SHOW TABLE STATS into a structured dict."""
    result: dict[str, Any] = {
        "num_rows": None,
        "num_files": None,
        "size_bytes": None,
        "partition_columns": None,
    }
    if not rows:
        return result
    # Impala SHOW TABLE STATS returns: #Rows, #Files, Size, Bytes Cached, Format, Incremental stats, Location
    # The exact column positions depend on partition vs non-partition tables
    for row in rows:
        row_vals = [str(v).strip() for v in row]
        # Last row is typically the total
        if len(row_vals) >= 3:
            try:
                result["num_rows"] = int(row_vals[0].replace(",", ""))
            except (ValueError, IndexError):
                pass
            try:
                result["num_files"] = int(row_vals[1].replace(",", ""))
            except (ValueError, IndexError):
                pass
    return result


def _parse_show_column_stats(rows: list[Any]) -> list[dict[str, Any]]:
    """Parse rows from SHOW COLUMN STATS."""
    columns: list[dict[str, Any]] = []
    if not rows:
        return columns
    # Impala: Column, Type, #Distinct Values, #Nulls, Max Size, Avg Size, #Trues, #Falses
    for row in rows:
        if not row:
            continue
        vals = [str(v).strip() for v in row]
        try:
            columns.append({
                "column_name": vals[0].lower() if vals else "",
                "num_distinct": int(vals[2].replace(",", "")) if len(vals) > 2 else -1,
                "num_nulls": int(vals[3].replace(",", "")) if len(vals) > 3 else -1,
                "max_size": int(float(vals[4])) if len(vals) > 4 else -1,
                "avg_size": float(vals[5]) if len(vals) > 5 else -1.0,
            })
        except (ValueError, IndexError):
            continue
    return columns


def collect_stats(
    tables: list[str],
    cursor: Any,
    db_path: str = "metadata.duckdb",
    ttl_hours: int = 24,
) -> dict[str, bool]:
    """
    For each table, run SHOW TABLE STATS + SHOW COLUMN STATS.
    Skips tables whose cached stats are within TTL.
    Returns {table_name: stats_available}.
    """
    con = duckdb.connect(db_path)
    _ensure_schema(con)

    availability: dict[str, bool] = {}
    now = datetime.now(timezone.utc).isoformat()

    for table in tables:
        table_lower = table.lower().strip()

        if _is_fresh(con, table_lower, ttl_hours):
            logger.debug("Stats fresh for %s, skipping", table_lower)
            availability[table_lower] = True
            continue

        try:
            # --- Table stats ---
            cursor.execute(f"SHOW TABLE STATS {table_lower}")
            trows = cursor.fetchall()
            tstats = _parse_show_table_stats(trows)

            con.execute(
                """
                INSERT OR REPLACE INTO table_stats
                    (table_name, num_rows, num_files, size_bytes, partition_columns,
                     stats_available, collected_at)
                VALUES (?, ?, ?, ?, ?, TRUE, ?)
                """,
                [
                    table_lower,
                    tstats.get("num_rows"),
                    tstats.get("num_files"),
                    tstats.get("size_bytes"),
                    tstats.get("partition_columns"),
                    now,
                ],
            )

            # --- Column stats ---
            cursor.execute(f"SHOW COLUMN STATS {table_lower}")
            crows = cursor.fetchall()
            col_stats = _parse_show_column_stats(crows)

            # Remove old entries for this table
            con.execute("DELETE FROM column_stats WHERE table_name = ?", [table_lower])
            for cs in col_stats:
                con.execute(
                    """
                    INSERT INTO column_stats
                        (table_name, column_name, num_distinct, num_nulls,
                         max_size, avg_size, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        table_lower, cs["column_name"], cs["num_distinct"],
                        cs["num_nulls"], cs["max_size"], cs["avg_size"], now,
                    ],
                )

            availability[table_lower] = True
            logger.info("Collected stats for %s: %d columns", table_lower, len(col_stats))

        except Exception as exc:
            logger.warning("Stats collection failed for %s: %s", table_lower, exc)
            # Record failure
            con.execute(
                """
                INSERT OR REPLACE INTO table_stats
                    (table_name, stats_available, collected_at)
                VALUES (?, FALSE, ?)
                """,
                [table_lower, now],
            )
            availability[table_lower] = False

    con.close()
    return availability

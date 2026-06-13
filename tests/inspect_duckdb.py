"""
Standalone DuckDB inspection script.
Run from project root:
    python tests/inspect_duckdb.py
    python tests/inspect_duckdb.py --db path/to/other.duckdb
    python tests/inspect_duckdb.py --table customers
    python tests/inspect_duckdb.py --column email
"""

from __future__ import annotations

import argparse
import sys

import duckdb


# ── helpers ──────────────────────────────────────────────────────────────────

def _header(title: str) -> None:
    width = 70
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _show(label: str, df) -> None:
    print(f"\n{label}")
    if df.empty:
        print("  (no rows)")
    else:
        print(df.to_string(index=False))


# ── sections ─────────────────────────────────────────────────────────────────

def section_tables(con: duckdb.DuckDBPyConnection) -> None:
    _header("1. Tables in metadata.duckdb")
    try:
        df = con.execute("SHOW TABLES").fetchdf()
        print(df.to_string(index=False))
    except Exception as e:
        print(f"  ERROR: {e}")


def section_row_counts(con: duckdb.DuckDBPyConnection) -> None:
    _header("2. Row counts")
    tables = ["column_metadata", "table_lineage", "table_stats", "column_stats"]
    for t in tables:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<25} {n:>8} rows")
        except Exception:
            print(f"  {t:<25}  NOT FOUND")


def section_sample_metadata(con: duckdb.DuckDBPyConnection) -> None:
    _header("3. column_metadata — first 10 rows")
    try:
        df = con.execute("""
            SELECT table_name, column_name, data_type, pii, column_description
            FROM column_metadata
            LIMIT 10
        """).fetchdf()
        _show("", df)
    except Exception as e:
        print(f"  ERROR: {e}")


def section_pii_columns(con: duckdb.DuckDBPyConnection) -> None:
    _header("4. PII-flagged columns")
    try:
        df = con.execute("""
            SELECT table_name, column_name, pii, column_description
            FROM column_metadata
            WHERE UPPER(COALESCE(pii, '')) IN ('PII', 'YES', 'TRUE', '1')
            ORDER BY table_name, column_name
        """).fetchdf()
        _show(f"  {len(df)} PII column(s) found:", df)
    except Exception as e:
        print(f"  ERROR: {e}")


def section_distinct_tables(con: duckdb.DuckDBPyConnection) -> None:
    _header("5. Distinct table names in column_metadata")
    try:
        df = con.execute("""
            SELECT table_name, COUNT(*) AS column_count
            FROM column_metadata
            GROUP BY table_name
            ORDER BY column_count DESC
        """).fetchdf()
        _show("", df)
    except Exception as e:
        print(f"  ERROR: {e}")


def section_lineage_sample(con: duckdb.DuckDBPyConnection) -> None:
    _header("6. table_lineage — first 10 rows")
    try:
        df = con.execute("SELECT * FROM table_lineage LIMIT 10").fetchdf()
        _show("", df)
    except Exception as e:
        print(f"  ERROR: {e}")


def section_lookup_table(con: duckdb.DuckDBPyConnection, table: str) -> None:
    _header(f"7. Lookup table: '{table}'")
    try:
        df = con.execute("""
            SELECT column_name, data_type, pii, nullable, column_description,
                   source_table, source_column, logical_transformation
            FROM column_metadata
            WHERE LOWER(table_name) = LOWER(?)
            ORDER BY column_name
        """, [table]).fetchdf()
        _show(f"  {len(df)} column(s) found for table '{table}':", df)
    except Exception as e:
        print(f"  ERROR: {e}")


def section_lookup_column(con: duckdb.DuckDBPyConnection, column: str) -> None:
    _header(f"8. Lookup column: '{column}' across all tables")
    try:
        df = con.execute("""
            SELECT table_name, column_name, data_type, pii, column_description
            FROM column_metadata
            WHERE LOWER(column_name) = LOWER(?)
            ORDER BY table_name
        """, [column]).fetchdf()
        _show(f"  {len(df)} match(es) for column '{column}':", df)
    except Exception as e:
        print(f"  ERROR: {e}")


def section_simulate_agent_lookup(con: duckdb.DuckDBPyConnection, table: str, column: str) -> None:
    _header(f"9. Simulate agent lookup — {table}.{column}")
    print("  This is the exact query the agent runs in fetch_metadata_node and lookup_column_metadata tool:\n")
    try:
        rows = con.execute("""
            SELECT column_name, data_type, pii, column_description
            FROM column_metadata
            WHERE LOWER(table_name) = LOWER(?) AND LOWER(column_name) = LOWER(?)
            LIMIT 1
        """, [table, column]).fetchall()
        if rows:
            r = rows[0]
            print(f"  column_name : {r[0]}")
            print(f"  data_type   : {r[1]}")
            print(f"  pii         : {r[2]}")
            print(f"  description : {r[3]}")
            print(f"\n  Result → agent WILL find this column (coverage +1)")
        else:
            print(f"  No rows found for {table}.{column}")
            print(f"  Result → agent will NOT find this column (coverage stays 0)")
    except Exception as e:
        print(f"  ERROR: {e}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect DuckDB metadata store")
    parser.add_argument("--db", default="metadata.duckdb", help="Path to DuckDB file")
    parser.add_argument("--table", default=None, help="Lookup all columns for this table")
    parser.add_argument("--column", default=None, help="Lookup this column across all tables")
    parser.add_argument("--simulate", nargs=2, metavar=("TABLE", "COLUMN"),
                        help="Simulate agent lookup for TABLE.COLUMN")
    args = parser.parse_args()

    print(f"\nConnecting to: {args.db}")
    try:
        con = duckdb.connect(args.db, read_only=True)
    except Exception as e:
        print(f"ERROR: Could not open {args.db}: {e}")
        sys.exit(1)

    section_tables(con)
    section_row_counts(con)
    section_sample_metadata(con)
    section_pii_columns(con)
    section_distinct_tables(con)
    section_lineage_sample(con)

    if args.table:
        section_lookup_table(con, args.table)

    if args.column:
        section_lookup_column(con, args.column)

    if args.simulate:
        section_simulate_agent_lookup(con, args.simulate[0], args.simulate[1])

    con.close()
    print(f"\n{'─' * 70}")
    print("  Done.")
    print(f"{'─' * 70}\n")


if __name__ == "__main__":
    main()

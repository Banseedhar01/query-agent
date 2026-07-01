"""Load metadata Excel workbook → DuckDB table: column_metadata.

Two product sheets (named by product):
  finnone  (Layout A — rich metadata with quality stats)
  sfdc     (Layout B — lightweight, table/column names only)

More products can be added by extending _SHEET_A_NAMES or _SHEET_B_NAMES.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

_CHUNK = 5_000

# ---------------------------------------------------------------------------
# Known sheet names per layout
# ---------------------------------------------------------------------------

# Layout A — rich metadata (finnone + legacy fallback names)
_SHEET_A_NAMES = {"finnone", "mapping", "mappings"}

# Layout B — table/column only (sfdc + legacy fallback names)
_SHEET_B_NAMES = {"sfdc", "mart", "org", "sheet2", "org_mart", "mart_mapping"}

# ---------------------------------------------------------------------------
# Fingerprints for auto-detection when sheet name is unrecognised
# ---------------------------------------------------------------------------

_LAYOUT_A_SIGNALS = {"dataset_name", "data_element_name", "data_type"}
_LAYOUT_B_SIGNALS = {"mart_table", "mart_field"}

# ---------------------------------------------------------------------------
# column_metadata DDL  (new schema)
# ---------------------------------------------------------------------------

_COL_META_DDL = """
CREATE TABLE column_metadata (
    schema_name            VARCHAR,
    table_name             VARCHAR,
    column_name            VARCHAR,
    data_type              VARCHAR,
    sample_data            VARCHAR,
    nullable               VARCHAR,
    key_information        VARCHAR,
    pii                    VARCHAR,
    dataset_partition_flag VARCHAR,
    partition_column       VARCHAR,
    total_count            VARCHAR,
    null_count             VARCHAR,
    blank_count            VARCHAR,
    min_length             VARCHAR,
    max_length             VARCHAR,
    completeness_score     VARCHAR,
    uniqueness_score       VARCHAR
)
"""

_COL_META_CANONICAL = [
    "schema_name", "table_name", "column_name",
    "data_type", "sample_data", "nullable", "key_information", "pii",
    "dataset_partition_flag", "partition_column",
    "total_count", "null_count", "blank_count",
    "min_length", "max_length", "completeness_score", "uniqueness_score",
]

# ---------------------------------------------------------------------------
# Column normalisation
# ---------------------------------------------------------------------------

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    1. Lowercase all headers, replace any non-alphanumeric run with '_',
       strip leading/trailing underscores.
       Handles special chars like parentheses in 'Personally Identifiable
       Information (PII)' cleanly.
    2. Deduplicate column names (pandas may already suffix with '.1' etc.).
    3. Strip / lowercase all string cell values; replace 'nan' strings with None.
    """
    # Step 1 — normalise headers
    normalized = []
    for c in df.columns:
        s = str(c).strip().lower()
        s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
        normalized.append(s)

    # Step 2 — deduplicate (second occurrence of name gets _2, _3, etc.)
    seen: dict[str, int] = {}
    final: list[str] = []
    for col in normalized:
        if col in seen:
            seen[col] += 1
            final.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            final.append(col)
    df.columns = final

    # Step 3 — normalise cell values
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip().str.lower()
        df[col] = df[col].replace({"nan": None, "none": None, "": None})

    return df


# ---------------------------------------------------------------------------
# Layout detection  (used only for unrecognised sheet names)
# ---------------------------------------------------------------------------

def _detect_layout(cols: set[str]) -> str:
    score_a = len(cols & _LAYOUT_A_SIGNALS)
    score_b = len(cols & _LAYOUT_B_SIGNALS)
    layout = "A" if score_a >= score_b else "B"
    logger.info("Layout detection: A_score=%d B_score=%d → Layout %s", score_a, score_b, layout)
    return layout


# ---------------------------------------------------------------------------
# Sheet 1 loader — Layout A (finnone) → column_metadata (full, fresh table)
# ---------------------------------------------------------------------------

# Excel header (after normalisation) → DB column name
_LAYOUT_A_RENAME: dict[str, str] = {
    "schema":                                  "schema_name",
    "dataset_name":                            "table_name",
    "data_element_name":                       "column_name",
    "data_type":                               "data_type",
    "example_values":                          "sample_data",
    "nullable":                                "nullable",
    "keyinformation":                          "key_information",
    # "Personally Identifiable Information (PII)" normalises to this:
    "personally_identifiable_information_pii": "pii",
    "dataset_partition_flag":                  "dataset_partition_flag",
    "partition_column":                        "partition_column",
    "total_count":                             "total_count",
    "null_count":                              "null_count",
    "blank_count":                             "blank_count",
    "min_length":                              "min_length",
    "max_length":                              "max_length",
    "completeness_score":                      "completeness_score",
    "uniqueness_score":                        "uniqueness_score",
}


def _load_layout_a(df: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> int:
    """Load Layout A (finnone) into column_metadata — recreates the table."""
    df = _normalize_columns(df)

    df = df.rename(columns={k: v for k, v in _LAYOUT_A_RENAME.items() if k in df.columns})

    for required in ("table_name", "column_name"):
        if required not in df.columns:
            raise ValueError(
                f"Layout A load failed: required column '{required}' not found. "
                f"Available after rename: {list(df.columns)}"
            )

    df = df.dropna(subset=["table_name", "column_name"])
    df = df[df["table_name"].str.len() > 0]
    df = df[df["column_name"].str.len() > 0]

    con.execute("DROP TABLE IF EXISTS column_metadata")
    con.execute(_COL_META_DDL)

    total = 0
    for i in range(0, len(df), _CHUNK):
        chunk = df.iloc[i: i + _CHUNK]
        insert_cols = [c for c in _COL_META_CANONICAL if c in chunk.columns]
        chunk_clean = chunk[insert_cols].copy()
        con.register("_chunk_df", chunk_clean)
        col_list = ", ".join(insert_cols)
        con.execute(
            f"INSERT INTO column_metadata ({col_list}) "
            f"SELECT {col_list} FROM _chunk_df"
        )
        total += len(chunk_clean)

    logger.info("column_metadata: inserted %d rows from Layout A", total)
    return total


# ---------------------------------------------------------------------------
# Sheet 2 loader — Layout B (sfdc) → appended into column_metadata
# Only table_name and column_name are extracted; all other columns stay NULL.
# ---------------------------------------------------------------------------

def _append_layout_b(df: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> int:
    """Append Layout B (sfdc) rows — table_name + column_name only."""
    rows_added = 0
    for i in range(0, len(df), _CHUNK):
        chunk = df.iloc[i: i + _CHUNK].copy()

        out = pd.DataFrame()
        out["table_name"]  = chunk.get("mart_table",  chunk.get("table_name"))
        out["column_name"] = chunk.get("mart_field",  chunk.get("column_name"))

        out = out.dropna(subset=["table_name"])
        out = out[out["table_name"].astype(str).str.len() > 0]

        if out.empty:
            continue

        con.register("_b_chunk", out)
        con.execute(
            "INSERT INTO column_metadata (table_name, column_name) "
            "SELECT table_name, column_name FROM _b_chunk"
        )
        rows_added += len(out)

    logger.info("column_metadata: appended %d rows from Layout B", rows_added)
    return rows_added


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_excel(
    path: str | Path,
    db_path: str = "metadata.duckdb",
    mart_path: str | Path | None = None,
) -> dict[str, int]:
    """
    Load metadata Excel workbook(s) into DuckDB → column_metadata table.

    Sheet resolution
    ─────────────────
    Layout A (finnone) — recognised sheet names: 'finnone', 'mapping', 'mappings'
        → column_metadata (full: PII, type, quality stats, partition info)

    Layout B (sfdc)    — recognised sheet names: 'sfdc', 'mart', 'org', 'sheet2'
        → APPENDed into column_metadata (table_name, column_name only)

    Unrecognised sheet names are auto-classified by column fingerprint.

    Returns dict of {table_name: row_count}.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    if mart_path is not None:
        mart_path = Path(mart_path)
        if not mart_path.exists():
            raise FileNotFoundError(f"Mart Excel file not found: {mart_path}")

    logger.info("Loading Excel: %s", path)
    if mart_path:
        logger.info("Mart/Org Excel: %s", mart_path)

    con = duckdb.connect(db_path)
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet_names_lower = {s.strip().lower(): s for s in xl.sheet_names}
    logger.info("Sheets in primary file: %s", list(sheet_names_lower.keys()))

    counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Sheet 1 — Layout A → column_metadata (fresh table)
    # ------------------------------------------------------------------
    sheet1_name: str | None = next(
        (sheet_names_lower[c] for c in _SHEET_A_NAMES if c in sheet_names_lower),
        None,
    )

    if sheet1_name is None:
        # Fingerprint fallback: find any sheet with Layout A signals
        skip = _SHEET_B_NAMES | {"metadata", "meta data", "meta"}
        for raw, original in sheet_names_lower.items():
            if raw in skip:
                continue
            probe = pd.read_excel(xl, sheet_name=original, dtype=str, nrows=5)
            probe_cols = set(_normalize_columns(probe.head(0)).columns)
            if probe_cols & _LAYOUT_A_SIGNALS:
                sheet1_name = original
                logger.info("Auto-detected Layout A sheet: '%s'", original)
                break

    if sheet1_name:
        logger.info("Reading Layout A sheet: '%s'", sheet1_name)
        df_a = pd.read_excel(xl, sheet_name=sheet1_name, dtype=str)
        counts["column_metadata"] = _load_layout_a(df_a, con)
    else:
        logger.warning("No Layout A sheet found — creating empty column_metadata")
        con.execute("DROP TABLE IF EXISTS column_metadata")
        con.execute(_COL_META_DDL)
        counts["column_metadata"] = 0

    # ------------------------------------------------------------------
    # Sheet 2 — Layout B → appended into column_metadata
    # ------------------------------------------------------------------
    mart_xl = pd.ExcelFile(mart_path, engine="openpyxl") if mart_path else xl
    mart_sheet_names_lower = (
        {s.strip().lower(): s for s in mart_xl.sheet_names}
        if mart_path
        else sheet_names_lower
    )

    if mart_path:
        logger.info("Sheets in mart file: %s", list(mart_sheet_names_lower.keys()))

    sheet2_name: str | None = next(
        (mart_sheet_names_lower[c] for c in _SHEET_B_NAMES if c in mart_sheet_names_lower),
        None,
    )

    if sheet2_name is None:
        # Fingerprint fallback
        skip_b = _SHEET_A_NAMES | {"metadata", "meta data", "meta"}
        for raw, original in mart_sheet_names_lower.items():
            if raw in skip_b:
                continue
            probe = pd.read_excel(mart_xl, sheet_name=original, dtype=str, nrows=5)
            probe_cols = set(_normalize_columns(probe.head(0)).columns)
            if probe_cols & _LAYOUT_B_SIGNALS:
                sheet2_name = original
                logger.info("Auto-detected Layout B sheet: '%s'", original)
                break

    if sheet2_name:
        logger.info("Reading Layout B sheet: '%s'", sheet2_name)
        df_b = pd.read_excel(mart_xl, sheet_name=sheet2_name, dtype=str)
        df_b = _normalize_columns(df_b)
        b_rows = _append_layout_b(df_b, con)
        counts["column_metadata"] = counts.get("column_metadata", 0) + b_rows
    else:
        logger.info("No Layout B sheet found — column_metadata contains Layout A rows only")

    # ------------------------------------------------------------------
    # MetaData reference sheet — stored as-is (unchanged)
    # ------------------------------------------------------------------
    for candidate in ("metadata", "meta data", "meta"):
        if candidate in sheet_names_lower:
            logger.info("Reading MetaData sheet: '%s'", sheet_names_lower[candidate])
            meta_df = pd.read_excel(xl, sheet_name=sheet_names_lower[candidate], dtype=str)
            meta_df = _normalize_columns(meta_df)
            con.execute("DROP TABLE IF EXISTS raw_metadata")
            con.register("_meta_df", meta_df)
            con.execute("CREATE TABLE raw_metadata AS SELECT * FROM _meta_df")
            counts["raw_metadata"] = len(meta_df)
            logger.info("raw_metadata: loaded %d rows", len(meta_df))
            break

    con.close()
    logger.info("Ingestion complete: %s", counts)
    return counts

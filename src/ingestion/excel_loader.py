"""Load metadata Excel workbook → DuckDB table: column_metadata.

Supports two real-world sheet layouts:

Layout A  (Mapping sheet — rich ETL metadata)
  Columns: Target Column, Target Column Description, Sample Data, Data Type,
           PII, Nullable, Mapping, Logical Transformation, Physical Transformation,
           Source Column, Source Column Sample Data, Source Columns Data Type,
           Source Table, Source Name, Datamart table name

Layout B  (Mart/Org sheet — lightweight, different mart database)
  Columns: Org, Mart Table, Mart Field, Source Table, Mart Field (duplicate header
           — second occurrence is the source column)
  → Appended into column_metadata with all other columns as NULL.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

_CHUNK = 5_000  # rows per insert batch

# ---------------------------------------------------------------------------
# Layout detection fingerprints
# ---------------------------------------------------------------------------

# Layout A — rich mapping sheet (your Sheet 1)
_LAYOUT_A_SIGNALS = {
    "target_column", "datamart_table_name", "pii",
    "data_type", "physical_transformation",
}

# Layout B — org/mart sheet (your Sheet 2)
_LAYOUT_B_SIGNALS = {
    "org", "mart_table", "mart_field",
}


# ---------------------------------------------------------------------------
# Column normalisation
# ---------------------------------------------------------------------------

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    1. Lowercase + underscore all column headers.
    2. Fix duplicate 'Mart Field' header from Layout B sheets
       (pandas reads second occurrence as 'mart_field.1' — rename to 'source_field').
    3. Alias 'Mapping' → 'mapping_type' (your Sheet 1 uses 'Mapping', not 'Mapping Type').
    4. Strip / lowercase all string cell values; replace 'nan' strings with None.
    """
    # Step 1 — normalise headers
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    # Step 2 — fix duplicate "Mart Field" header (Layout B)
    # pandas renames the second occurrence to "mart_field.1"
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    for col in df.columns:
        base = col.split(".")[0]          # strip pandas ".1", ".2" suffixes
        if base in seen:
            seen[base] += 1
            if base == "mart_field":
                # second "Mart Field" is actually the source/field column
                new_cols.append("source_field")
            else:
                new_cols.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 0
            new_cols.append(col)
    df.columns = new_cols

    # Step 3 — alias "mapping" → "mapping_type"
    if "mapping" in df.columns and "mapping_type" not in df.columns:
        df = df.rename(columns={"mapping": "mapping_type"})

    # Step 4 — normalise cell values
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip().str.lower()
        df[col] = df[col].replace({"nan": None, "none": None, "": None})

    return df


# ---------------------------------------------------------------------------
# Layout detection
# ---------------------------------------------------------------------------

def _detect_layout(cols: set[str]) -> str:
    """Return 'A' or 'B' based on which signal columns are present."""
    score_a = len(cols & _LAYOUT_A_SIGNALS)
    score_b = len(cols & _LAYOUT_B_SIGNALS)
    layout = "A" if score_a >= score_b else "B"
    logger.info(
        "Layout detection: A_score=%d B_score=%d → Layout %s", score_a, score_b, layout
    )
    return layout


# ---------------------------------------------------------------------------
# column_metadata DDL
# ---------------------------------------------------------------------------

_COL_META_DDL = """
CREATE TABLE column_metadata (
    table_name                VARCHAR,
    column_name               VARCHAR,
    column_description        VARCHAR,
    sample_data               VARCHAR,
    data_type                 VARCHAR,
    pii                       VARCHAR,
    nullable                  VARCHAR,
    mapping_type              VARCHAR,
    logical_transformation    VARCHAR,
    physical_transformation   VARCHAR,
    source_column             VARCHAR,
    source_table              VARCHAR,
    source_name               VARCHAR,
    source_column_sample_data VARCHAR,
    source_column_data_type   VARCHAR
)
"""

# Canonical column list in the same order as DDL above
_COL_META_CANONICAL = [
    "table_name", "column_name", "column_description", "sample_data",
    "data_type", "pii", "nullable", "mapping_type",
    "logical_transformation", "physical_transformation",
    "source_column", "source_table", "source_name",
    "source_column_sample_data", "source_column_data_type",
]


# ---------------------------------------------------------------------------
# Sheet 1 loader — Layout A → column_metadata (full)
# ---------------------------------------------------------------------------

def _load_layout_a(df: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> int:
    """Load Layout A (rich mapping sheet) into column_metadata."""
    df = _normalize_columns(df)
    layout = _detect_layout(set(df.columns))

    if layout == "B":
        logger.info("Layout B detected for primary sheet — skipping column_metadata load")
        con.execute("DROP TABLE IF EXISTS column_metadata")
        con.execute(_COL_META_DDL)
        return 0

    rename: dict[str, str] = {
        "datamart_table_name":        "table_name",
        "target_column":              "column_name",
        "target_column_description":  "column_description",
        "sample_data":                "sample_data",
        "data_type":                  "data_type",
        "pii":                        "pii",
        "nullable":                   "nullable",
        "mapping_type":               "mapping_type",
        "logical_transformation":     "logical_transformation",
        "physical_transformation":    "physical_transformation",
        "source_column":              "source_column",
        "source_table":               "source_table",
        "source_name":                "source_name",
        "source_column_sample_data":  "source_column_sample_data",
        "source_columns_data_type":   "source_column_data_type",
    }

    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    for required in ("table_name", "column_name"):
        if required not in df.columns:
            raise ValueError(
                f"column_metadata load failed: required column '{required}' not found. "
                f"Available columns: {list(df.columns)}"
            )

    df = df.dropna(subset=["table_name", "column_name"])
    df = df[df["table_name"].str.len() > 0]
    df = df[df["column_name"].str.len() > 0]

    con.execute("DROP TABLE IF EXISTS column_metadata")
    con.execute(_COL_META_DDL)

    total = 0
    for i in range(0, len(df), _CHUNK):
        chunk = df.iloc[i : i + _CHUNK]
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
# Sheet 2 loader — Layout B → appended into column_metadata
# ---------------------------------------------------------------------------

def _append_layout_b(df: pd.DataFrame, con: duckdb.DuckDBPyConnection) -> int:
    """
    Append Layout B (Mart/Org sheet) rows into column_metadata.

    Layout B → column_metadata mapping:
        Mart Table              → table_name
        Mart Field (1st)        → column_name
        Source Table            → source_table
        Mart Field (2nd)        → source_column  (renamed to source_field by _normalize_columns)
        All other columns       → NULL
    """
    rows_added = 0
    for i in range(0, len(df), _CHUNK):
        chunk = df.iloc[i : i + _CHUNK].copy()

        out = pd.DataFrame()
        out["table_name"]   = chunk["mart_table"]   if "mart_table"   in chunk.columns else None
        out["column_name"]  = chunk["mart_field"]   if "mart_field"   in chunk.columns else None
        out["source_table"] = chunk["source_table"] if "source_table" in chunk.columns else None
        out["source_column"]= chunk["source_field"] if "source_field" in chunk.columns else None

        out = out.dropna(subset=["table_name"])
        out = out[out["table_name"].str.len() > 0]

        if out.empty:
            continue

        con.register("_b_chunk", out)
        con.execute(
            "INSERT INTO column_metadata (table_name, column_name, source_table, source_column) "
            "SELECT table_name, column_name, source_table, source_column "
            "FROM _b_chunk"
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

    Both sheets represent different mart databases and are merged into a
    single column_metadata table. Sheet 2 rows have NULL for all fields
    not present in Layout B (description, PII, data_type, etc.).

    Parameters
    ----------
    path      : Path to the primary Excel file (must contain the Mapping sheet).
    db_path   : Path to the DuckDB file to write into.
    mart_path : Optional path to a second Excel file containing the Mart/Org sheet.
                If omitted, the loader looks for the sheet inside ``path`` itself.

    Sheet resolution
    ────────────────
    Sheet 1 (Mapping / Layout A) — looked up as: 'mapping', 'mappings'
        → column_metadata (full: PII, type, description, transformations)

    Sheet 2 (Mart/Org / Layout B) — looked up in mart_path first, then path.
        Recognised names: 'mart', 'org', 'sheet2', 'org_mart', 'mart_mapping'
        → APPENDed into column_metadata (table_name, column_name,
          source_table, source_column, org only; all other columns NULL)

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
    sheet1_loaded = False
    for candidate in ("mapping", "mappings"):
        if candidate in sheet_names_lower:
            logger.info("Reading Sheet 1 (mapping): '%s'", sheet_names_lower[candidate])
            mapping_df = pd.read_excel(
                xl, sheet_name=sheet_names_lower[candidate], dtype=str
            )
            counts["column_metadata"] = _load_layout_a(mapping_df, con)
            sheet1_loaded = True
            break

    if not sheet1_loaded:
        logger.warning("No 'Mapping' sheet found in %s — creating empty column_metadata", path)
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

    sheet2_candidates = ("mart", "org", "sheet2", "org_mart", "mart_mapping")
    sheet2_name = next(
        (mart_sheet_names_lower[c] for c in sheet2_candidates if c in mart_sheet_names_lower),
        None,
    )

    # Fallback: auto-detect any sheet that looks like Layout B
    if sheet2_name is None:
        skip = {"mapping", "mappings", "metadata", "meta data", "meta"}
        for raw_name, original_name in mart_sheet_names_lower.items():
            if raw_name in skip:
                continue
            probe = pd.read_excel(mart_xl, sheet_name=original_name, dtype=str, nrows=5)
            probe_cols = {str(c).strip().lower().replace(" ", "_") for c in probe.columns}
            if probe_cols & _LAYOUT_B_SIGNALS:
                sheet2_name = original_name
                logger.info("Auto-detected Layout B sheet: '%s'", original_name)
                break

    if sheet2_name:
        logger.info("Reading Sheet 2 (org/mart): '%s'", sheet2_name)
        mart_df = pd.read_excel(mart_xl, sheet_name=sheet2_name, dtype=str)
        mart_df_norm = _normalize_columns(mart_df)

        layout = _detect_layout(set(mart_df_norm.columns))
        if layout == "B":
            b_rows = _append_layout_b(mart_df_norm, con)
            counts["column_metadata"] = counts.get("column_metadata", 0) + b_rows
        else:
            logger.warning("Sheet '%s' did not resolve to Layout B — skipped", sheet2_name)
    else:
        logger.info("No Sheet 2 (org/mart) found — column_metadata contains Layout A rows only")

    # ------------------------------------------------------------------
    # MetaData reference sheet — stored as-is
    # ------------------------------------------------------------------
    for candidate in ("metadata", "meta data", "meta"):
        if candidate in sheet_names_lower:
            logger.info("Reading MetaData sheet: '%s'", sheet_names_lower[candidate])
            meta_df = pd.read_excel(
                xl, sheet_name=sheet_names_lower[candidate], dtype=str
            )
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

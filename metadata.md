# Metadata Guide — Excel Sheets, DuckDB Tables & Agent Usage

---

## 1. Concepts

### Mart (Datamart)
The final curated database that analysts and BI tools query. Built by ETL jobs that extract from source systems, clean the data, rename columns, and load into HDFS as Parquet/ORC files. Impala queries only hit Mart tables. Each product sheet in the Excel file represents a different mart database.

### What Impala Actually Queries
Impala is a SQL engine that reads files stored in HDFS. ETL jobs extract data from source systems and build Mart tables. The agent reviews SQL queries that hit those Mart tables.

---

## 2. Your Two Excel Sheets

Both sheets represent **different mart databases** and are loaded into the same `column_metadata` table.
The sheet names are **product names** — `finnone` for Sheet 1, `sfdc` for Sheet 2.

| Sheet | Product | Layout | Mart DB |
|---|---|---|---|
| `finnone` | Finnone LMS | Full metadata + quality stats | Finnone Datamart |
| `sfdc` | Salesforce | Table/column names only | SFDC Datamart |

---

### Sheet 1 — `finnone` (Full Metadata)

Contains rich metadata for every column in the Finnone mart tables.
**One row = one column.**

| Excel Column | DB Column | Example |
|---|---|---|
| Schema | `schema_name` | `finnone_datamart` |
| Dataset Name | `table_name` | `Dim_agreement`, `Dim_application` |
| Data Element Name | `column_name` | `agreementid`, `crn`, `customerid` |
| Data Type | `data_type` | `string`, `Date`, `decimal` |
| Example Values | `sample_data` | `116542001`, `WEST` |
| Nullable | `nullable` | `yes` / `no` |
| KeyInformation | `key_information` | Business context / key facts about the column |
| Personally Identifiable Information (PII) | `pii` | `pii` / `non-pii` |
| Dataset Partition Flag | `dataset_partition_flag` | `yes` / `no` |
| Partition Column | `partition_column` | `transaction_date`, `load_date` |
| Total Count | `total_count` | `5000000` |
| Null Count | `null_count` | `12340` |
| Blank Count | `blank_count` | `450` |
| Min Length | `min_length` | `1` |
| Max Length | `max_length` | `36` |
| Completeness Score | `completeness_score` | `0.9975` |
| Uniqueness Score | `uniqueness_score` | `1.0` |

**Purpose:** Grounds the LLM with PII flags, data types, partition info, and quality stats. Used for metadata coverage scoring and all 8 lint rules.

---

### Sheet 2 — `sfdc` (Table/Column Names Only)

Contains the column inventory for the Salesforce mart. No quality stats, no PII flags — only what is available is stored; all other `column_metadata` columns are NULL.
**One row = one column.**

| Excel Column | DB Column |
|---|---|
| Mart Table | `table_name` |
| Mart Field | `column_name` |
| *(all other columns)* | NULL |

**Purpose:** Ensures the agent recognises SFDC mart table/column names during query parsing and metadata coverage scoring. PII classification for SFDC columns must come from a future enriched sheet.

---

## 3. Excel → DuckDB Mapping

### Sheet 1 (`finnone`) → `column_metadata`

```
Excel Column                               →  DuckDB Column
────────────────────────────────────────────────────────────────
Schema                                     →  schema_name
Dataset Name                               →  table_name
Data Element Name                          →  column_name
Data Type                                  →  data_type
Example Values                             →  sample_data
Nullable                                   →  nullable
KeyInformation                             →  key_information
Personally Identifiable Information (PII)  →  pii
Dataset Partition Flag                     →  dataset_partition_flag
Partition Column                           →  partition_column
Total Count                                →  total_count
Null Count                                 →  null_count
Blank Count                                →  blank_count
Min Length                                 →  min_length
Max Length                                 →  max_length
Completeness Score                         →  completeness_score
Uniqueness Score                           →  uniqueness_score
```

---

### Sheet 2 (`sfdc`) → `column_metadata` (appended)

Sheet 2 rows are **appended** into the same `column_metadata` table. Only `table_name` and `column_name` are populated; all other columns are NULL.

```
Excel Column  →  DuckDB Column
──────────────────────────────
Mart Table    →  table_name
Mart Field    →  column_name
(rest)        →  NULL
```

---

### Column Population by Sheet

| Column | `finnone` | `sfdc` | Queried by |
|---|---|---|---|
| `schema_name` | ✅ | NULL | node (pre-fetch) + tool |
| `table_name` | ✅ | ✅ | node + tool (WHERE filter) |
| `column_name` | ✅ | ✅ | node + tool (WHERE filter) |
| `data_type` | ✅ | NULL | node (pre-fetch) + tool |
| `sample_data` | ✅ | NULL | tool (on-demand) |
| `nullable` | ✅ | NULL | node (pre-fetch) + tool |
| `key_information` | ✅ | NULL | node (pre-fetch) + tool |
| `pii` | ✅ | NULL | node (pre-fetch) + tool + R008 linter |
| `dataset_partition_flag` | ✅ | NULL | node → `__partition_info__` (R002 offline) |
| `partition_column` | ✅ | NULL | node → `__partition_info__` (R002 offline) |
| `total_count` | ✅ | NULL | tool (on-demand) |
| `null_count` | ✅ | NULL | tool (on-demand) |
| `blank_count` | ✅ | NULL | tool (on-demand) |
| `min_length` | ✅ | NULL | tool (on-demand) |
| `max_length` | ✅ | NULL | tool (on-demand) |
| `completeness_score` | ✅ | NULL | tool (on-demand) |
| `uniqueness_score` | ✅ | NULL | tool (on-demand) |

---

### Final DuckDB State After Ingestion

```
metadata.duckdb
│
├── column_metadata     ← finnone (full) + sfdc (appended, partial)
│     17 columns, one row per mart column
│     finnone rows: full metadata — PII, type, quality stats, partition info
│     sfdc rows:    table_name + column_name only; all other columns NULL
│
├── table_stats         ← From Impala cluster via SHOW TABLE STATS (not from Excel)
│
└── column_stats        ← From Impala cluster via SHOW COLUMN STATS (not from Excel)
```

---

## 4. Which Sheet Feeds Which DuckDB Table

```
Sheet 1 (finnone)                    Sheet 2 (sfdc)
─────────────────                    ──────────────
Full metadata                        Partial metadata
Finnone mart DB                      SFDC mart DB
Has: pii, data_type,                 Has: table_name,
     key_information,                     column_name
     partition info,
     quality stats

        │                                   │
        └──────────▶ column_metadata ◀──────┘
                     finnone: full rows (17 cols)
                     sfdc: appended, NULL for unavailable columns
```

---

## 5. Where Each DuckDB Column Is Used by the Agent

### `column_metadata`

| Column | Used By | Purpose |
|---|---|---|
| `table_name` | `fetch_metadata` node, R008 linter, `lookup_column_metadata` tool | WHERE filter — match query table to metadata |
| `column_name` | `fetch_metadata` node, R008 linter, `lookup_column_metadata` tool | WHERE filter — match query column to metadata |
| `pii` | **R008 linter** (fires HIGH finding), `lookup_column_metadata` tool | PII detection — `'pii'` triggers R008; NULL = unknown, rule skipped |
| `data_type` | `fetch_metadata` node (pre-load), `lookup_column_metadata` tool | Type context for LLM rewrite proposals |
| `key_information` | `fetch_metadata` node (pre-load), `lookup_column_metadata` tool | Business meaning / key facts for LLM reasoning |
| `nullable` | `fetch_metadata` node (pre-load), `lookup_column_metadata` tool | Null handling — LLM avoids suggesting IS NOT NULL on non-nullable columns |
| `schema_name` | `fetch_metadata` node (pre-load), `lookup_column_metadata` tool | Schema disambiguation when same table name exists across products |
| `partition_column` | `fetch_metadata` node → `__partition_info__` | **R002 offline support** — passed as partition context so LLM can flag missing partition filters without a live cluster |
| `dataset_partition_flag` | `fetch_metadata` node → `__partition_info__` | Confirms whether the table is partitioned at all |
| `sample_data` | `lookup_column_metadata` tool | Data pattern reasoning — LLM validates type assumptions against real values |
| `total_count` | `lookup_column_metadata` tool | Row volume context when `table_stats` unavailable (offline) |
| `null_count` | `lookup_column_metadata` tool | Null distribution — LLM flags data quality issues and filter selectivity |
| `blank_count` | `lookup_column_metadata` tool | Blank vs null distinction — affects COALESCE / NULLIF suggestions |
| `min_length` | `lookup_column_metadata` tool | Value range — LLM flags CAST risks and string truncation |
| `max_length` | `lookup_column_metadata` tool | Value range — LLM flags CAST risks and string truncation |
| `completeness_score` | `lookup_column_metadata` tool | Data quality flag — LLM notes incomplete columns in findings |
| `uniqueness_score` | `lookup_column_metadata` tool | Cardinality hint — replaces `column_stats.num_distinct` in offline mode |

> **Note:** `sfdc` rows have NULL for `pii`, `data_type`, `key_information`, and all stat columns. R008 will not fire for SFDC columns since `pii` is NULL — treat as unknown, not safe.

---

### `table_stats`

| Column | Used By | Purpose |
|---|---|---|
| `table_name` | R002 linter | WHERE filter |
| `partition_columns` | **R002 linter** (fires HIGH finding) | Checks if query filters on the partition column |
| `num_rows` | `get_table_stats` tool | Table size for join broadcast reasoning |
| `size_bytes` | `get_table_stats` tool | **R007** — broadcasts tables > 512 MB flagged |
| `stats_available` | `get_table_stats` tool | `false` = **R006** missing COMPUTE STATS |
| `collected_at` | Stats freshness check | TTL check before re-collecting stats |

---

### `column_stats`

| Column | Used By | Purpose |
|---|---|---|
| `table_name` + `COUNT(*)` | **R006 linter** (fires HIGH finding) | 0 rows = no stats ever run |
| `column_name` | `get_table_stats` tool | Per-column stats for LLM |
| `num_distinct` | `get_table_stats` tool | Cardinality reasoning for join selectivity |
| `num_nulls` | `get_table_stats` tool | Null distribution context |
| `max_size`, `avg_size` | `get_table_stats` tool | Storage and memory size context |

---

## 6. Complete Usage Map — One View

```
DuckDB Table        Column                   Used By                   Purpose
──────────────────  ───────────────────────  ────────────────────────  ──────────────────────────────────────
column_metadata     table_name               fetch_metadata node        match query tables to store
                    column_name              fetch_metadata node        match query columns to store
                    pii                      R008 rule                  fire PII finding if exposed unmasked
                    pii                      lookup_column_metadata     LLM confirms PII before stating fact
                    data_type                fetch_metadata + tool      type context for rewrite proposals
                    key_information          fetch_metadata + tool      business context for LLM reasoning
                    nullable                 fetch_metadata + tool      avoid IS NOT NULL on non-nullable cols
                    schema_name              fetch_metadata + tool      multi-schema disambiguation
                    partition_column         fetch_metadata node        R002 offline — partition context to LLM
                    dataset_partition_flag   fetch_metadata node        confirms table is partitioned
                    sample_data              lookup_column_metadata     validate type assumptions with real data
                    total_count              lookup_column_metadata     row volume when table_stats unavailable
                    null_count               lookup_column_metadata     null distribution / selectivity
                    blank_count              lookup_column_metadata     blank vs null — COALESCE/NULLIF hints
                    min_length               lookup_column_metadata     CAST risk / string truncation detection
                    max_length               lookup_column_metadata     CAST risk / string truncation detection
                    completeness_score       lookup_column_metadata     data quality flag in LLM findings
                    uniqueness_score         lookup_column_metadata     cardinality hint (offline NDV proxy)

table_stats         partition_columns        R002 rule                  check partition filter present
                    table_name               R002 rule                  match tables in query
                    size_bytes               get_table_stats tool       R007 broadcast threshold (512 MB)
                    stats_available          get_table_stats tool       R006 missing COMPUTE STATS
                    num_rows, num_files      get_table_stats tool       table size for join reasoning
                    collected_at             stats freshness check      TTL before re-collecting

column_stats        table_name + COUNT(*)    R006 rule                  0 rows = no stats = HIGH finding
                    num_distinct             get_table_stats tool       cardinality for join selectivity
                    num_nulls                get_table_stats tool       null distribution context
                    max_size, avg_size       get_table_stats tool       storage / memory size context
```

---

## 7. LLM Tools and What They Return

### `lookup_column_metadata(table, column)`
Queries `column_metadata`. Returns for one column:
```json
{
  "column_name": "crn",
  "data_type": "string",
  "pii": "pii",
  "key_information": "Customer Reference Number — unique identifier for the borrower",
  "sample_data": "CRN00012345",
  "nullable": "no",
  "total_count": "5000000",
  "null_count": "0",
  "blank_count": "0",
  "min_length": "12",
  "max_length": "12",
  "completeness_score": "1.0",
  "uniqueness_score": "0.9998",
  "schema_name": "finnone_datamart"
}
```

### `get_table_stats(table)`
Queries `table_stats` + `column_stats`. Returns table size, partition columns, per-column NDV.

### `run_explain(sql)`
Runs `EXPLAIN LEVEL=2` on live Impala cluster. Returns scan bytes, join strategies, warnings.

---

## 8. Minimum Viable Metadata for Query Review

The agent needs only these columns to run all 8 lint rules:

```sql
-- Minimum required in column_metadata:
table_name    -- which mart table
column_name   -- which column
pii           -- 'pii' or 'non-pii'   ← required for R008 (sfdc rows will be NULL)
data_type     -- string / int / decimal

-- Minimum required in table_stats:
table_name          -- which table
partition_columns   -- comma-separated ← required for R002
stats_available     -- true / false    ← required for R006
```

Everything else (`key_information`, quality stats, `schema_name`, etc.) is enrichment — valuable for LLM context and reasoning quality, not required for core lint rules.

---

## 9. Adding a New Product Sheet

To add a new product (e.g. `oracle_fin`):

1. Name the Excel sheet with the product name (e.g. `oracle_fin`).
2. Add the name to `_SHEET_A_NAMES` or `_SHEET_B_NAMES` in `excel_loader.py`, depending on which layout it follows.
3. If it follows Layout A, ensure the column headers match the expected names (or extend `_LAYOUT_A_RENAME` with any aliases).
4. Run `agent ingest` — rows from the new sheet are appended into `column_metadata` alongside finnone and sfdc.

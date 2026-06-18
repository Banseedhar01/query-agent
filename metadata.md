# Metadata Guide — Excel Sheets, DuckDB Tables & Agent Usage

---

## 1. Concepts

### Source
The raw upstream system where data originates — a Loan Management System (LMS), Salesforce CRM, Oracle database, etc. Source tables have ugly column names (`VC_ACCTOUNT_OFF_CD`, `AGREEMENTID`), messy structure, and are not meant for reporting. In your Excel, `source_table` and `source_column` refer to these.

### Target
The clean, renamed, business-friendly column in the final Mart table. What an analyst actually sees and queries. In your Excel, `Target Column` and `Datamart Table Name` refer to these.

### Mart (Datamart)
The final curated database that analysts and BI tools query. Built by ETL jobs that extract from source systems, clean the data, rename columns, and load into HDFS as Parquet/ORC files. Impala queries only hit Mart tables — never Salesforce or LMS directly. Examples from your data: `Dim_agreement`, `Dim_application`, `sfdc_applicant`.

### Straight Mapping
Column is taken directly from source with no changes — just renamed.
Example: `AGREEMENTID` → `agreementid`

### Derived Column
Column is calculated, hardcoded, or built from logic — has no single source column.
Example: `citipool` is always hardcoded as `'FCH'` for every row regardless of source data.

### What Impala Actually Queries
Impala is a SQL engine that reads files stored in HDFS. It cannot connect to Salesforce, LMS, or any live operational system directly. ETL jobs (Informatica, Spark, Talend, etc.) extract data from source systems, land it in HDFS as staging tables, transform it, and build the final Mart tables. Impala then queries those Mart tables. The agent reviews the SQL queries that hit those Mart tables.

---

## 2. Your Two Excel Sheets

Both sheets represent **different mart databases**. Both are loaded into the same `column_metadata` table — Sheet 1 with full detail, Sheet 2 with the columns that are available (remainder NULL).

### Sheet 1 — Mapping Sheet (Rich ETL Metadata)

Contains detailed ETL metadata for every column in the Datamart tables.
**One row = one target column.**

| Excel Column | Meaning | Example from your data |
|---|---|---|
| Target Column | Final column name in the Mart table | `agreementid`, `crn`, `customerid` |
| Target Column Description | Human-readable meaning of that column | `Unique agreement identifier` |
| Sample Data | Example value from the Mart table | `116542001`, `5875906319` |
| Data Type | Data type of the target column | `string`, `Date` |
| PII | Whether the column contains personal data | `PII` (crn, customerid, pii_crn) / `Non-PII` |
| Nullable | Whether the column can be null | `yes` / `no` |
| Mapping | How the column was built | `straight` / `derived` |
| Logical Transformation | Business rule in plain English | `Directly taken from the agreement details table` |
| Physical Transformation | Actual SQL expression used to build the column | `COALESCE(src.contract_id, src.legacy_id)` |
| Source Column | Raw column name in the upstream system | `AGREEMENTID`, `LAD_FW_CUSTOMER_ID_C` |
| Source Column Sample Data | Example value from the source column | *(often blank)* |
| Source Columns Data Type | Data type in the source system | *(often blank)* |
| Source Table | Raw upstream table | `fch_lms.lea_agreement_dtl`, `asset_classification.npa_staging_table` |
| Source Name | Source system/database name | `fch_lms`, `Finnone` |
| Datamart Table Name | Which Mart table this column belongs to | `Dim_agreement`, `Dim_application` |

**Purpose:** Documents what every Mart column is, where it came from, and how it was transformed. Used by the agent for PII detection, LLM grounding, and metadata coverage scoring.

**PII columns in your data:**
- `Dim_agreement`: `crn`, `customerid`, `pii_crn`, `piicustomerid`
- `Dim_application`: `CustomerID`

**Derived columns in your data (no source column):**
- `citipool` → hardcoded `'FCH'`
- `periodflag` → hardcoded `'POST_OCT13'`
- `foreclosureflag` → `CASE WHEN INS.AUTHORIZEDON < v_agreement.MATURITYDATE THEN 1 ELSE 0`
- `customerprofile` → `CASE on CUSTOMER_CATG_DESC`
- `load_date` → `current_date()`

---

### Sheet 2 — Mart/Org Sheet (Different Mart Database)

Contains column mappings for a different mart database. No PII flags, no data types, no transformation details — only what is available in this sheet is stored; all other `column_metadata` columns are NULL.
**One row = one mart column.**

| Excel Column | Meaning | Example from your data |
|---|---|---|
| Mart Table | Target Mart table name | `sfdc_applicant` |
| Mart Field *(1st occurrence)* | Target column name in the Mart table | `email`, `salary`, `applicant_id` |
| Source Table | Upstream raw table | `applicant__c` (Salesforce object) |
| Mart Field *(2nd occurrence — duplicate header)* | Source column name in the upstream system | `email__c`, `Gross_Monthly_Income__c`, `id` |

**Important:** pandas reads the duplicate `Mart Field` header as `Mart Field.1`. The loader renames it to `source_field` so it maps correctly to `source_column` in DuckDB.

**Purpose:** Documents columns from a second mart database. Appended into `column_metadata` so all mart tables across both databases are queryable by the agent in one place.

---

## 3. Excel → DuckDB Mapping

### Sheet 1 → `column_metadata`

Every row in Sheet 1 becomes one row in `column_metadata`. Column-by-column mapping:

```
Sheet 1 Column                  →   DuckDB column_metadata Column
──────────────────────────────────────────────────────────────────
Datamart table name             →   table_name
Target Column                   →   column_name
Target Column Description       →   column_description
Sample Data                     →   sample_data              ← stored, not queried yet
Data Type                       →   data_type
PII                             →   pii
Nullable                        →   nullable
Mapping                         →   mapping_type
Logical Transformation          →   logical_transformation
Physical Transformation         →   physical_transformation
Source Column                   →   source_column
Source Table                    →   source_table
Source Name                     →   source_name              ← stored, not queried yet
Source Column Sample Data       →   source_column_sample_data← stored, not queried yet
Source Columns Data Type        →   source_column_data_type
```

**Result in `column_metadata` (Sheet 1 rows):**

```
table_name       column_name     data_type  pii      mapping_type  source_table                  source_column
───────────────  ──────────────  ─────────  ───────  ────────────  ────────────────────────────  ──────────────────────
dim_agreement    agreementid     string     non-pii  straight      fch_lms.lea_agreement_dtl     agreementid
dim_agreement    crn             string     pii      straight      fch_lms.lea_agreement_dtl     lad_fw_customer_id_c
dim_agreement    customerid      string     pii      straight      fch_lms.lea_agreement_dtl     lesseeid
dim_agreement    citipool        string     non-pii  derived       NULL                          NULL
dim_application  customerid      string     pii      straight      finnone_datamart.s_application customerid
dim_application  cibilscore      string     non-pii  derived       finnone.fch_castransaction... score (max, case)
```

---

### Column Population by Sheet

| Column | Sheet 1 | Sheet 2 | Queried by |
|---|---|---|---|
| `table_name` | ✅ | ✅ | node + tool (WHERE filter) |
| `column_name` | ✅ | ✅ | node + tool (WHERE filter) |
| `source_table` | ✅ | ✅ | — (stored only) |
| `source_column` | ✅ | ✅ | — (stored only) |
| `data_type` | ✅ | NULL | node + tool |
| `pii` | ✅ | NULL | node + tool + R008 linter |
| `column_description` | ✅ | NULL | node + tool |
| `nullable` | ✅ | NULL | — (stored only) |
| `mapping_type` | ✅ | NULL | — (stored only) |
| `sample_data` | ✅ | NULL | — (stored only) |
| `logical_transformation` | ✅ | NULL | — (stored only) |
| `physical_transformation` | ✅ | NULL | — (stored only) |
| `source_name` | ✅ | NULL | — (stored only) |
| `source_column_sample_data` | ✅ | NULL | — (stored only) |
| `source_column_data_type` | ✅ | NULL | — (stored only) |

---

### Sheet 2 → `column_metadata` (appended)

Sheet 2 rows are **appended** into the same `column_metadata` table. Only the columns available in Sheet 2 are populated; all others are NULL.

```
Sheet 2 Column                  →   DuckDB column_metadata Column
──────────────────────────────────────────────────────────────────
Mart Table                      →   table_name
Mart Field (1st occurrence)     →   column_name
Source Table                    →   source_table
Mart Field (2nd occurrence)     →   source_column   ← duplicate header renamed to source_field
(not available)                 →   all other columns  ← NULL
```

**Result in `column_metadata` (Sheet 2 rows appended):**

```
table_name      column_name          source_table   source_column              data_type  pii
──────────────  ───────────────────  ─────────────  ─────────────────────────  ─────────  ─────
sfdc_applicant  aadhar_verified      applicant__c   aadhar_verified__c         NULL       NULL
sfdc_applicant  email                applicant__c   email__c                   NULL       NULL
sfdc_applicant  salary               applicant__c   gross_monthly_income__c    NULL       NULL
sfdc_applicant  applicant_id         applicant__c   id                         NULL       NULL
sfdc_applicant  years_of_experience  applicant__c   no_of_years_in_current_…   NULL       NULL
```

---

### Final DuckDB State After Ingestion

```
metadata.duckdb
│
├── column_metadata     ← Sheet 1 (full) + Sheet 2 (appended, partial)
│     16 columns, one row per mart column
│     Sheet 1 rows: dim_agreement, dim_application (full metadata: PII, type, etc.)
│     Sheet 2 rows: sfdc_applicant (table_name, column_name, source_table, source_column, org only)
│
├── table_stats         ← From Impala cluster via SHOW TABLE STATS (not from Excel)
│
└── column_stats        ← From Impala cluster via SHOW COLUMN STATS (not from Excel)
```

---

## 4. Which Sheet Feeds Which DuckDB Table

```
Sheet 1 (Mapping)                    Sheet 2 (Mart/Org)
────────────────                     ──────────────────
Rich metadata                        Partial metadata
Different mart DB                    Different mart DB
Has: PII, data_type,                 Has: org, mart_table,
     nullable, description,               mart_field, source_table,
     transformations                      source_field (renamed)

        │                                      │
        └──────────▶ column_metadata ◀─────────┘
                     Sheet 1: full rows
                     Sheet 2: appended, NULL for unavailable columns
```

---

## 5. Where Each DuckDB Column Is Used by the Agent

### `column_metadata`

| Column | Used By | Purpose |
|---|---|---|
| `table_name` | `fetch_metadata` node, R008 linter, `lookup_column_metadata` tool | WHERE filter — match query table to metadata |
| `column_name` | `fetch_metadata` node, R008 linter, `lookup_column_metadata` tool | WHERE filter — match query column to metadata |
| `pii` | **R008 linter** (fires HIGH finding), `lookup_column_metadata` tool | PII detection — `'pii'` triggers R008 |
| `data_type` | `fetch_metadata` node (pre-load), `lookup_column_metadata` tool | Type context for LLM rewrite proposals |
| `column_description` | `fetch_metadata` node (pre-load), `lookup_column_metadata` tool | Business meaning for LLM reasoning |
| `nullable` | `lookup_column_metadata` tool | Null handling context for LLM |
| `mapping_type` | `lookup_column_metadata` tool | LLM uses `derived` to avoid suggesting filters on hardcoded columns |
| `logical_transformation` | `lookup_column_metadata` tool | Business rule context for LLM |
| `physical_transformation` | `lookup_column_metadata` tool | Actual SQL — LLM uses to validate or improve rewrites |
| `source_column` | `lookup_column_metadata` tool | Lineage context for LLM |
| `source_table` | `lookup_column_metadata` tool | Lineage context — which upstream table feeds this column |
| `source_column_data_type` | `lookup_column_metadata` tool | Source vs mart type comparison — LLM flags implicit CAST risks |
| `sample_data` | *(stored, not queried yet)* | Could help LLM reason about data patterns |
| `source_name` | *(stored, not queried yet)* | Source system identifier |
| `source_column_sample_data` | *(stored, not queried yet)* | Source-side example values |
| `sample_data` | *(stored, not queried yet)* | Could help LLM reason about data patterns |

> **Note:** Sheet 2 rows will have NULL for `pii`, `data_type`, `column_description`, and transformation fields. The R008 rule will not fire for Sheet 2 columns since `pii` is NULL — PII classification must come from Sheet 1.

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
                    column_description       fetch_metadata + tool      business context for LLM reasoning
                    mapping_type             tool                       derived vs straight — rewrite guidance
                    source_table             tool                       upstream table context
                    source_column            tool                       upstream column context
                    logical_transformation   tool                       business rule context
                    physical_transformation  tool                       actual SQL — LLM validates rewrites
                    source_column_data_type  tool                       source vs mart type — flag CAST risks
                    nullable                 tool                       null handling context
                    sample_data              (not queried yet)          future: data pattern reasoning
                    source_name              (not queried yet)          future: source system context

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
  "description": "Customer Reference Number",
  "nullable": null,
  "mapping_type": "straight",
  "source_table": "fch_lms.lea_agreement_dtl",
  "source_column": "lad_fw_customer_id_c",
  "logical_transformation": "Customer Reference Number, joined from the customer master",
  "physical_transformation": "joined from the customer master using the agreement's customer ID",
  "source_column_data_type": null
}
```

### `get_table_stats(table)`
Queries `table_stats` + `column_stats`. Returns table size, partition columns, per-column NDV.

### `run_explain(sql)`
Runs `EXPLAIN LEVEL=2` on live Impala cluster. Returns scan bytes, join strategies, warnings.

---

## 8. Minimum Viable Metadata for Query Review

Source columns, transformation text, and org info are stored but secondary.
The agent needs only these columns to run all 8 lint rules:

```sql
-- Minimum required in column_metadata:
table_name    -- which mart table
column_name   -- which column
pii           -- 'pii' or 'non-pii'        ← required for R008 (Sheet 2 rows will be NULL here)
data_type     -- string / int / decimal
mapping_type  -- straight / derived         ← useful LLM context

-- Minimum required in table_stats:
table_name          -- which table
partition_columns   -- comma-separated      ← required for R002
stats_available     -- true / false         ← required for R006
```

Everything else (`source_table`, `source_column`, `physical_transformation`, `source_column_data_type`, etc.)
is enrichment — valuable for LLM context, not required for core lint rules.

# Metadata Guide — Excel Sheets, DuckDB Tables & Agent Usage

---

## 1. Concepts

### Source
The raw upstream system where data originates — a Loan Management System (LMS), Salesforce CRM, Oracle database, etc. Source tables have ugly column names (`VC_ACCTOUNT_OFF_CD`, `AGREEMENTID`), messy structure, and are not meant for reporting. In your Excel, `source_table` and `source_column` refer to these.

### Target
The clean, renamed, business-friendly column in the final Mart table. What an analyst actually sees and queries. In your Excel, `Target Column` and `Datamart Table Name` refer to these.

### Mart (Datamart)
The final curated database that analysts and BI tools query. Built by ETL jobs that extract from source systems, clean the data, rename columns, and load into HDFS as Parquet/ORC files. Impala queries only hit Mart tables — never Salesforce or LMS directly. Examples from your data: `Dim_agreement`, `sfdc_applicant`.

### Lineage
The complete traceable trail of where a column came from and how it was transformed. Answers: *"this column in my report — which raw system table and field did it originally come from, and what happened to it along the way?"*

Example: `Dim_agreement.agreementid` came from `fch_lms.lea_agreement_dtl.AGREEMENTID` with no transformation (straight mapping).

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

### Sheet 1 — Mapping Sheet (Rich ETL Metadata)

Contains detailed ETL metadata for every column in the Datamart tables.
**One row = one target column.**

| Excel Column | Meaning |
|---|---|
| Target Column | Final column name in the Mart table (what analysts see) |
| Target Column Description | Human-readable meaning of that column |
| Sample Data | Example value from the Mart table |
| Data Type | Data type of the target column (string, int, decimal) |
| PII | Whether the column contains personal data (PII / Non-PII) |
| Nullable | Whether the column can be null (yes / no) |
| Mapping | How the column was built — `straight` (direct copy) or `derived` (calculated/hardcoded) |
| Logical Transformation | Business rule in plain English |
| Physical Transformation | Actual SQL expression used to build the column |
| Source Column | Raw column name in the upstream system |
| Source Column Sample Data | Example value from the source column |
| Source Columns Data Type | Data type in the source system |
| Source Table | Raw upstream table (e.g. `fch_lms.lea_agreement_dtl`) |
| Source Name | Source system/database name (e.g. `fch_lms`) |
| Datamart Table Name | Which Mart table this column belongs to (e.g. `Dim_agreement`) |

**Purpose:** Documents what every Mart column is, where it came from, and how it was transformed. Used by the agent for PII detection, LLM grounding, and metadata coverage scoring.

---

### Sheet 2 — Mart/Org Sheet (Lightweight Lineage)

Contains lightweight lineage mapping only. No PII flags, no data types, no transformation details.
**One row = one source-to-target field mapping.**

| Excel Column | Meaning |
|---|---|
| Org | Organisation or business unit that owns this table (e.g. `Org 1`) |
| Mart Table | Target Mart table name (e.g. `sfdc_applicant`) |
| Mart Field | Target column name in the Mart table |
| Source Table | Upstream raw table (e.g. `applicant__c` from Salesforce) |
| Mart Field *(duplicate header)* | Actually the Source Field — the raw column name in the source table |

**Purpose:** Documents which Salesforce/CRM fields map to which Mart columns. Used for lineage tracing only. The agent stores it but does not actively query it for lint rules today.

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
Sample Data                     →   sample_data
Data Type                       →   data_type
PII                             →   pii
Nullable                        →   nullable
Mapping                         →   mapping_type
Logical Transformation          →   logical_transformation
Physical Transformation         →   physical_transformation
Source Column                   →   source_column
Source Table                    →   source_table
Source Name                     →   source_name
Source Column Sample Data       →   source_column_sample_data
Source Columns Data Type        →   source_column_data_type
(not in Sheet 1)                →   org  ← NULL for Sheet 1 rows
```

**Result in `column_metadata`:**

```
table_name     column_name              data_type  pii      mapping_type  source_table                  source_column       source_name
─────────────  ───────────────────────  ─────────  ───────  ────────────  ────────────────────────────  ──────────────────  ───────────
dim_agreement  account_closure_reason   string     non-pii  straight      fch_lms.lea_termination_dtl   closure_reason      fch_lms
dim_agreement  accountingofficer        string     non-pii  straight      fch_lms.lea_agreement_dtl     vc_acctount_off_cd  fch_lms
dim_agreement  agreementid              string     non-pii  straight      fch_lms.lea_agreement_dtl     agreementid         fch_lms
dim_agreement  citipool                 string     non-pii  derived       NULL                          NULL                NULL
```

---

### Sheet 1 → `table_lineage` (also)

Same Sheet 1 rows, but only lineage columns extracted:

```
Sheet 1 Column                  →   DuckDB table_lineage Column
──────────────────────────────────────────────────────────────────
Datamart table name             →   target_table
Target Column                   →   target_column
Source Table                    →   source_table
Source Column                   →   source_column
Physical Transformation         →   transformation
(not in Sheet 1)                →   org  ← NULL for Sheet 1 rows
```

**Result in `table_lineage` (Sheet 1 rows):**

```
target_table   target_column            source_table                  source_column      transformation                org
─────────────  ───────────────────────  ────────────────────────────  ─────────────────  ────────────────────────────  ────
dim_agreement  account_closure_reason   fch_lms.lea_termination_dtl   closure_reason     If the agreement is closed…   NULL
dim_agreement  agreementid              fch_lms.lea_agreement_dtl     agreementid        Directly taken from…          NULL
dim_agreement  citipool                 NULL                          NULL               'FCH' (static value)          NULL
```

---

### Sheet 2 → `table_lineage` (appended)

Sheet 2 rows are **appended** into the same `table_lineage` table after Sheet 1 rows:

```
Sheet 2 Column                  →   DuckDB table_lineage Column
──────────────────────────────────────────────────────────────────
Mart Table                      →   target_table
Mart Field (1st occurrence)     →   target_column
Source Table                    →   source_table
Mart Field (2nd occurrence)     →   source_column   ← duplicate header renamed to source_field
Org                             →   org
(nothing)                       →   transformation  ← always NULL for Sheet 2 rows
```

**Result in `table_lineage` (Sheet 2 rows appended):**

```
target_table    target_column        source_table   source_column           transformation  org
──────────────  ───────────────────  ─────────────  ──────────────────────  ──────────────  ─────
sfdc_applicant  aadhar_verified      applicant__c   aadhar_verified__c      NULL            org 1
sfdc_applicant  aadharlinkedemailid  applicant__c   aadharlinkedemailid__c  NULL            org 1
sfdc_applicant  applicant_id         applicant__c   id                      NULL            org 1
sfdc_applicant  annual_gross_income  applicant__c   annual_gross_income__c  NULL            org 1
```

---

### Final DuckDB State After Ingestion

```
metadata.duckdb
│
├── column_metadata     ← Sheet 1 only
│     16 columns, one row per mart column
│     Contains: dim_agreement rows + any other mart tables from Sheet 1
│     Does NOT contain: sfdc_applicant (Sheet 2 has no PII/type/description info)
│
├── table_lineage       ← Sheet 1 + Sheet 2 combined
│     6 columns
│     Sheet 1 rows: dim_agreement lineage (with transformation text, org = NULL)
│     Sheet 2 rows: sfdc_applicant lineage (with org = 'org 1', transformation = NULL)
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
Rich metadata                        Lineage only
Has: PII, data_type,                 Has: org, mart_table,
     nullable, description,               mart_field, source_table,
     transformations                      source_field (renamed)

        │                                      │
        ├──▶ column_metadata ◀─── Sheet 1 only (Sheet 2 skipped — no PII/type info)
        │
        └──▶ table_lineage   ◀─── Sheet 1 rows + Sheet 2 rows (both appended)
```

---

## 5. Where Each DuckDB Table and Column Is Used by the Agent

### `column_metadata`

| Used By | File | Columns Queried | Purpose |
|---|---|---|---|
| `fetch_metadata` node | `agent/nodes.py` | `table_name`, `column_name`, `data_type`, `pii`, `column_description` | Pre-loads metadata for every table.column referenced in the query. Calculates `metadata_coverage` score. |
| Rule R008 — PII Unmasked | `analysis/linter.py` | `table_name`, `column_name`, `pii` | Fires HIGH finding if `pii = 'pii'` and column is selected without a masking function. |
| LLM Tool `lookup_column_metadata` | `agent/tools.py` | All columns | LLM calls this tool to verify any column fact before making a claim in its analysis. Never invents schema facts. |

---

### `table_lineage`

| Used By | File | Columns Queried | Purpose |
|---|---|---|---|
| *(none actively today)* | — | — | Stored and ready. Not queried by any current lint rule or agent node. |
| *(future)* Impact analysis | — | `source_table`, `target_table`, `target_column` | "If this source table changes, which mart columns are affected?" |
| *(future)* PII upstream trace | — | `source_table`, `source_column` | "Where did this PII column originally come from?" |
| *(future)* Derived column detection | — | `transformation` | "Is this column hardcoded? Tell LLM not to suggest filtering on it." |

---

### `table_stats`

| Used By | File | Columns Queried | Purpose |
|---|---|---|---|
| Rule R002 — Missing Partition Filter | `analysis/linter.py` | `table_name`, `partition_columns` | Checks if the query filters on the partition column. If not → HIGH finding. |
| LLM Tool `get_table_stats` | `agent/tools.py` | All columns | LLM uses row count, size, partition info before reasoning about join strategy or scan cost. |

---

### `column_stats`

| Used By | File | Columns Queried | Purpose |
|---|---|---|---|
| Rule R006 — Missing Compute Stats | `analysis/linter.py` | `table_name` + `COUNT(*)` | If count = 0 → no stats exist → HIGH finding. |
| LLM Tool `get_table_stats` | `agent/tools.py` | `column_name`, `num_distinct`, `num_nulls`, `max_size`, `avg_size` | LLM uses NDV and null counts to reason about cardinality and join selectivity. |

---

## 6. Complete Usage Map — One View

```
DuckDB Table        Column(s)              Used By                  Purpose
──────────────────  ─────────────────────  ───────────────────────  ─────────────────────────────────────
column_metadata     table_name             fetch_metadata node       match query tables to metadata store
                    column_name            fetch_metadata node       match query columns to metadata store
                    data_type              LLM tool                  type context for rewrite proposals
                    pii                    R008 rule                 fire PII finding if column is exposed
                    pii                    LLM tool                  confirm PII before stating it as fact
                    column_description     LLM tool                  business context for reasoning
                    mapping_type           LLM tool                  derived vs straight — rewrite guidance
                    source_table           LLM tool                  lineage context (informational)
                    source_column          LLM tool                  lineage context (informational)
                    logical_transformation LLM tool                  business rule context
                    nullable               LLM tool                  null handling context
                    ALL columns            metadata_coverage score   % of query columns found in store

table_lineage       (none actively)        —                         stored, populated, ready for future rules

table_stats         partition_columns      R002 rule                 check partition filter is present
                    table_name             R002 rule                 match to tables in the query
                    ALL columns            LLM tool                  table size / partition / row count
                    stats_available        R006 (indirect)           false = trigger missing stats finding

column_stats        table_name + COUNT(*)  R006 rule                 0 rows = no stats = HIGH finding
                    num_distinct           LLM tool                  cardinality reasoning for joins
                    num_nulls              LLM tool                  null distribution context
                    max_size, avg_size     LLM tool                  storage and memory size context
```

---

## 7. Minimum Viable Metadata for Query Review

Source columns, transformation text, and lineage info are stored but secondary.
The agent needs only these columns to run all 8 lint rules:

```sql
-- Minimum required in column_metadata:
table_name    -- which mart table
column_name   -- which column
pii           -- 'pii' or 'non-pii'        ← required for R008
data_type     -- string / int / decimal
mapping_type  -- straight / derived         ← useful LLM context

-- Minimum required in table_stats:
table_name          -- which table
partition_columns   -- comma-separated      ← required for R002
stats_available     -- true / false         ← required for R006
```

Everything else (`source_table`, `source_column`, `transformation`, `source_name`, etc.)
is enrichment — valuable for lineage tracing and LLM context, not required for core lint rules.

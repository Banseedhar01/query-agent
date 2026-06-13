# Impala SQL Query Review & Optimization Agent

Metadata-grounded agent that reviews Apache Impala SQL queries and produces verified optimization suggestions.

**Core principle:** deterministic code handles facts (parsing, stats, validation); the LLM handles only reasoning and rewriting.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Ingest Metadata](#ingest-metadata)
  - [Review a Query](#review-a-query)
  - [Offline Mode](#offline-mode)
- [Pipeline Deep Dive](#pipeline-deep-dive)
  - [1. parse\_query](#1-parse_query)
  - [2. fetch\_metadata](#2-fetch_metadata)
  - [3. fetch\_explain](#3-fetch_explain)
  - [4. rule\_lint](#4-rule_lint)
  - [5. llm\_analyzer](#5-llm_analyzer)
  - [6. rewrite\_proposer](#6-rewrite_proposer)
  - [7. validator](#7-validator)
  - [8. build\_report](#8-build_report)
- [Lint Rules Reference](#lint-rules-reference)
- [Report Schema](#report-schema)
- [Metadata Excel Format](#metadata-excel-format)
- [Running Tests](#running-tests)
- [Eval Fixture Queries](#eval-fixture-queries)
- [Extending the Agent](#extending-the-agent)
- [Acceptance Criteria](#acceptance-criteria)
- [Known Limitations](#known-limitations)

---

## Overview

The agent takes a raw Impala SQL file and produces a structured report containing:

- **Rule-based findings** — deterministic checks for common anti-patterns (SELECT *, missing partition filters, cross joins, PII exposure, etc.)
- **LLM-generated analysis** — Claude reasons over the AST summary, EXPLAIN plan, and retrieved metadata to surface deeper issues
- **Verified rewrites** — candidate SQL rewrites are re-EXPLAIN'd and accepted only when the plan diff shows measurable improvement
- **PII flags** — columns marked PII in the metadata store that appear unmasked in the query

The LLM is constrained to only state facts it retrieved via tool calls — it cannot invent table sizes, column types, or schema structure.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        LangGraph Pipeline                            │
│                                                                      │
│  ┌─────────────┐   ┌────────────────┐   ┌───────────────┐           │
│  │ parse_query │──▶│ fetch_metadata │──▶│ fetch_explain │           │
│  └─────────────┘   └────────────────┘   └───────────────┘           │
│   sqlglot AST      DuckDB exact match    impyla EXPLAIN LEVEL=2      │
│   QueryProfile     column lookup         ExplainPlan                 │
│                                                  │                   │
│                                         ┌────────────┐              │
│                                         │ rule_lint  │              │
│                                         └────────────┘              │
│                                                  │                   │
│                                     8 deterministic rules            │
│                                     -> list[Finding]                 │
│                                                  │                   │
│                                         ┌─────────────┐             │
│                                         │llm_analyzer │             │
│                                         │ (ToolNode)  │             │
│                                         └─────────────┘             │
│                                                  │                   │
│                       OpenAI GPT + 3 tools (max 4 iters):            │
│                         - lookup_column_metadata                     │
│                         - get_table_stats                            │
│                         - run_explain                                │
│                                                  │                   │
│                                ┌──────────────────────────┐         │
│                                │     rewrite_proposer     │         │
│                                └──────────────────────────┘         │
│                       Structured output -> CandidateRewriteList      │
│                                                  │                   │
│                                         ┌─────────────┐  ┌───────┐  │
│                                         │  validator  │◀─│ retry │  │
│                                         └─────────────┘  └───────┘  │
│                         re-EXPLAIN + plan_diff                       │
│                         + AST equivalence check                      │
│                                                  │                   │
│                    ┌──────────────────┬──────────────────┐          │
│                    │    IMPROVED      │    REJECTED ──▶  │─ retry   │
│                    └──────────────────┴──────────────────┘          │
│                                                  │                   │
│                                         ┌─────────────┐             │
│                                         │build_report │             │
│                                         └─────────────┘             │
│                                                  │                   │
│                                          ReviewReport                │
└──────────────────────────────────────────────────────────────────────┘
```

### Data flow

```
Excel workbook
     │
     ▼
excel_loader.py ──▶ DuckDB
                     ├── column_metadata   (PII flags, types, lineage)
                     ├── table_lineage     (source → target mappings)
                     ├── table_stats       (rows, partitions, TTL cache)
                     └── column_stats      (NDV, nulls, sizes)

Impala cluster
     │
     ▼
stats_collector.py ──▶ DuckDB (table_stats, column_stats)
explain.py         ──▶ ExplainPlan (structured plan nodes)
```

---

## Project Structure

```
impala_query_agent/
├── pyproject.toml                     # Build system + dependencies
├── config.yaml                        # Impala, model, threshold settings
├── README.md
│
├── src/
│   ├── config.py                      # YAML loader with defaults
│   │
│   ├── ingestion/
│   │   ├── excel_loader.py            # Excel → DuckDB (layout A & B, chunked)
│   │   └── stats_collector.py         # SHOW TABLE/COLUMN STATS + TTL cache
│   │
│   ├── analysis/
│   │   ├── parser.py                  # sqlglot AST → QueryProfile
│   │   ├── explain.py                 # EXPLAIN LEVEL=2 → ExplainPlan
│   │   └── linter.py                  # 8 rule functions → list[Finding]
│   │
│   ├── agent/
│   │   ├── state.py                   # LangGraph TypedDict AgentState
│   │   ├── tools.py                   # LangChain tools for LLM
│   │   ├── nodes.py                   # All 8 graph node functions
│   │   └── graph.py                   # StateGraph wiring + conditional edges
│   │
│   ├── validation/
│   │   └── plan_diff.py               # PlanDiff + sqlglot AST equivalence
│   │
│   ├── report/
│   │   └── schema.py                  # Pydantic v2 report models
│   │
│   └── cli.py                         # typer CLI: `agent ingest` / `agent review`
│
└── tests/
    ├── conftest.py                    # Shared DuckDB fixture with seeded data
    ├── test_parser.py                 # 13 parser tests
    ├── test_linter.py                 # 23 lint rule tests
    ├── test_plan_diff.py              # 10 plan diff tests
    └── fixtures/
        └── labeled_queries.yaml      # 5 labeled queries with expected rule_ids
```

---

## Tech Stack

| Component | Library | Purpose |
|-----------|---------|---------|
| SQL parsing | `sqlglot` (dialect=hive) | AST extraction, table/column/join analysis, rewrite diff |
| Metadata store | `duckdb` | Local persistent store for column metadata and stats |
| Excel ingestion | `openpyxl` + `pandas` | Load and normalize metadata workbooks |
| Impala connectivity | `impyla` | EXPLAIN queries, SHOW STATS |
| Agent orchestration | `langgraph` | Stateful pipeline with conditional edges |
| LLM | `langchain-openai` (OpenAI) | Reasoning, analysis, rewrite proposals |
| Schemas | `pydantic` v2 | All data models and structured LLM outputs |
| CLI | `typer` + `rich` | Pretty-printed terminal interface |
| Retry/timeout | `tenacity` | LLM call resilience |
| Testing | `pytest` | Unit tests — no cluster or LLM required |

---

## Prerequisites

- Python 3.11+ (tested on 3.10+)
- An OpenAI API key (set as `OPENAI_API_KEY` in a `.env` file — see `example.env`)
- Apache Impala cluster access (optional — use `--offline` without it)
- A metadata Excel workbook (see [Metadata Excel Format](#metadata-excel-format))

---

## Installation

### 1. Clone or copy the project

```bash
cd impala_query_agent
```

### 2. Create a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install the package and dependencies

```bash
pip install -e .
```

For development (includes pytest, mypy, type stubs):

```bash
pip install -e ".[dev]"
```

### 4. Set your OpenAI API key

Copy the provided template and add your key:

```bash
copy example.env .env      # Windows
cp example.env .env        # macOS / Linux
```

Then edit `.env`:

```
OPENAI_API_KEY=sk-proj-...your-key-here...
```

The key is loaded automatically via `python-dotenv` — no `export` or `set` needed.

---

## Configuration

Edit `config.yaml` in the project root:

```yaml
impala:
  host: "your-impala-host"        # Impala coordinator hostname
  port: 21050                     # HiveServer2 port (default 21050)
  database: "default"             # Default database
  auth_mechanism: "NOSASL"        # NOSASL | PLAIN | GSSAPI | LDAP
  timeout_seconds: 30

model:
  name: "codex-2.5"               # OpenAI model ID
  max_tokens: 4096
  temperature: 0.0                # Keep at 0.0 for determinism
  timeout_seconds: 60
  max_retries: 3

thresholds:
  broadcast_join_bytes: 536870912 # 512 MB — flag BROADCAST joins above this
  stats_ttl_hours: 24             # Don't re-collect stats within this window
  llm_max_iterations: 4           # Max tool-call loops for LLM analyzer
  rewrite_retry_limit: 1          # Max retry attempts for rejected rewrites

duckdb:
  path: "metadata.duckdb"         # Path to the local metadata store

logging:
  level: "INFO"                   # DEBUG | INFO | WARNING | ERROR
```

All settings have sensible defaults — you only need to change `impala.host` for cluster access.

---

## Usage

### Ingest Metadata

**Both sheets in one Excel file:**
```bash
agent ingest path/to/metadata.xlsx
```

**Two separate Excel files** (Mapping sheet in one file, Mart/Org sheet in another):
```bash
agent ingest path/to/mapping.xlsx --mart path/to/mart_org.xlsx
```

With all options:
```bash
agent ingest path/to/mapping.xlsx \
  --mart path/to/mart_org.xlsx \
  --config config.yaml \
  --db /path/to/custom.duckdb
```

Expected output:

```
Ingesting mapping.xlsx + mart_org.xlsx → metadata.duckdb
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Table           ┃ Rows   ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ column_metadata │ 12,450 │
│ table_lineage   │ 12,650 │
│ raw_metadata    │ 200    │
└─────────────────┴────────┘
Done in 4.2s
```

Re-running is safe — all tables are replaced idempotently.

### Review a Query

```bash
agent review path/to/query.sql
```

Save the report as JSON:

```bash
agent review path/to/query.sql --json report.json
```

With all options:

```bash
agent review path/to/query.sql \
  --json out/report.json \
  --config config.yaml \
  --db metadata.duckdb
```

### Offline Mode

Skip all Impala cluster calls (no `EXPLAIN`, no `SHOW STATS`). Parser, linter, and metadata lookups still run fully:

```bash
agent review path/to/query.sql --offline
```

Offline mode is suitable for:
- Environments without cluster access
- CI pipelines
- Fast pre-commit checks

---

## Pipeline Deep Dive

### 1. `parse_query`

**Input:** raw SQL string  
**Output:** `QueryProfile`

Uses `sqlglot` with `dialect="hive"` to extract:
- Fully-qualified table names
- Columns per table (with alias resolution)
- Join graph: left table, right table, join type, ON condition
- Filter predicates (with non-sargable detection)
- GROUP BY, ORDER BY expressions
- CTE names (excluded from the table list)
- Subquery count
- `has_select_star` flag
- `parse_errors` (never raises — always returns what was parseable)

### 2. `fetch_metadata`

**Input:** `QueryProfile`  
**Output:** `retrieved_metadata` dict + `metadata_coverage` float

Performs exact-match DuckDB lookups for every `table.column` pair in the profile. No vector search or fuzzy matching — only deterministic lookups.

Computes `metadata_coverage = found_columns / total_referenced_columns` for the final report.

### 3. `fetch_explain`

**Input:** raw SQL  
**Output:** `ExplainPlan`

Sets `EXPLAIN_LEVEL=2` and runs `EXPLAIN` via impyla. Parses the text plan into structured `PlanNode` objects containing:
- Operator name (SCAN, HASH JOIN, SORT, TOP-N, etc.)
- Estimated row count
- Scan bytes per table
- Join strategy (BROADCAST / PARTITIONED)
- Per-node warnings (missing stats, etc.)

Skipped entirely in `--offline` mode.

### 4. `rule_lint`

**Input:** `QueryProfile` + `ExplainPlan`  
**Output:** `list[Finding]`

Runs all 8 deterministic rules. Each rule is an independent function — see [Lint Rules Reference](#lint-rules-reference).

### 5. `llm_analyzer`

**Input:** AST summary + plan summary + lint findings + metadata sample  
**Output:** updated `analyzer_messages`

The LLM (via `langchain-openai`) receives a structured prompt containing all deterministic outputs. It may call up to 3 tools in a loop (max 4 iterations):

- **`lookup_column_metadata(table, column)`** — retrieves data_type, PII flag, description, lineage from DuckDB
- **`get_table_stats(table)`** — retrieves row count, partition columns, column NDV from DuckDB
- **`run_explain(sql)`** — runs EXPLAIN on a candidate SQL and returns the structured plan

The system prompt explicitly instructs Claude to **never state a schema fact** unless a tool returned it. If a tool returns `"found": false`, Claude must note the information is unavailable rather than guessing.

### 6. `rewrite_proposer`

**Input:** analyzer conversation  
**Output:** `list[CandidateRewrite]`

Claude produces structured output (via `with_structured_output(CandidateRewriteList)`) containing a list of `{candidate_sql, rationale, targets_finding_ids}`. Each rewrite targets one or more specific rule IDs from the lint findings.

### 7. `validator`

**Input:** `list[CandidateRewrite]` + base `ExplainPlan`  
**Output:** `list[ValidatedRewrite]`

For each candidate:
1. Checks AST structural equivalence via `sqlglot.diff` — rejects candidates that drop tables
2. Re-runs `EXPLAIN` on the candidate SQL
3. Computes `PlanDiff`: scan bytes delta, join strategy changes, `verdict` (IMPROVED / NEUTRAL / WORSE)
4. Sets `verified=True` only when `verdict == IMPROVED` and AST check passes

Rejected rewrites are sent back to `rewrite_proposer` at most once (configurable via `rewrite_retry_limit`).

In `--offline` mode, all rewrites are accepted with `verified=False` and `verdict="UNVERIFIED"`.

### 8. `build_report`

**Input:** all accumulated state  
**Output:** `ReviewReport`

Assembles the final report: merges lint findings with validated rewrites, extracts PII flags, computes metadata coverage, and assigns `verified` status to each issue.

---

## Lint Rules Reference

| Rule ID | Severity | Trigger | Evidence Source |
|---------|----------|---------|----------------|
| `R001_SELECT_STAR` | MEDIUM | `SELECT *` detected in AST | sqlglot AST |
| `R002_MISSING_PARTITION_FILTER` | HIGH | Partitioned table with no filter on partition column | `table_stats.partition_columns` in DuckDB |
| `R003_NON_SARGABLE_PREDICATE` | MEDIUM | Function/CAST applied to a column in a WHERE clause | sqlglot predicate walk |
| `R004_IMPLICIT_CROSS_JOIN` | CRITICAL | JOIN with no ON/USING condition | sqlglot join graph |
| `R005_ORDER_BY_NO_LIMIT` | MEDIUM/LOW | ORDER BY without LIMIT (SORT node without TOP-N) | EXPLAIN plan nodes |
| `R006_MISSING_COMPUTE_STATS` | HIGH | Table has no stats (plan warning or absent from `column_stats`) | EXPLAIN warnings + DuckDB |
| `R007_BROADCAST_LARGE_TABLE` | HIGH | BROADCAST JOIN on a table scanning > threshold bytes | EXPLAIN join strategies |
| `R008_PII_UNMASKED` | HIGH | Column flagged PII in metadata is selected without masking | `column_metadata.pii` in DuckDB |

All rules return `[]` when the required data is unavailable (no plan in offline mode, no DuckDB connection, etc.) — they never raise.

---

## Report Schema

```python
class Issue(BaseModel):
    issue: str                    # Human-readable problem description
    severity: Severity            # LOW | MEDIUM | HIGH | CRITICAL
    evidence_from_plan: str       # Exact evidence (plan node, metadata value, etc.)
    suggested_rewrite: str | None # Candidate SQL if a rewrite targets this issue
    expected_impact: str          # Description of expected improvement
    verified: bool                # True only if plan diff shows IMPROVED verdict

class ValidatedRewrite(BaseModel):
    candidate_sql: str
    rationale: str
    targets_finding_ids: list[str]
    scan_bytes_delta: int | None  # Negative = improvement (bytes saved)
    join_strategy_changes: list[str]
    verdict: str                  # IMPROVED | NEUTRAL | WORSE | UNVERIFIED
    verified: bool

class ReviewReport(BaseModel):
    query_hash: str               # SHA-256 of raw SQL (first 16 hex chars)
    issues: list[Issue]
    validated_rewrites: list[ValidatedRewrite]
    pii_flags: list[str]          # Locations of unmasked PII columns
    metadata_coverage: float      # 0.0–1.0: fraction of columns found in metadata
```

JSON output example:

```json
{
  "query_hash": "a3f7c2d891b04e1a",
  "issues": [
    {
      "issue": "Query uses SELECT * which prevents column pruning and may expose PII.",
      "severity": "MEDIUM",
      "evidence_from_plan": "SELECT * detected in parsed AST",
      "suggested_rewrite": "SELECT customer_id, region, created_at FROM customers ...",
      "expected_impact": "Enables column pruning, reduces network transfer.",
      "verified": true
    }
  ],
  "validated_rewrites": [...],
  "pii_flags": ["SELECT referencing customers.email"],
  "metadata_coverage": 0.85
}
```

---

## Metadata Excel Format

The `agent ingest` command accepts a workbook with two sheets. The loader auto-detects sheet names and column layouts.

### Sheet name requirements

| Sheet | Accepted names (case-insensitive) | Fallback |
|-------|-----------------------------------|---------|
| Sheet 1 — Mapping | `mapping`, `mappings` | None — must match exactly |
| Sheet 2 — Mart/Org | `mart`, `org`, `sheet2`, `org_mart`, `mart_mapping` | Auto-detected if columns contain `org` + `mart_table` + `mart_field` |
| MetaData (optional) | `metadata`, `meta data`, `meta` | None |

> Sheet 1 has no auto-detect fallback — it **must** be named `mapping` or `mappings`. If your sheet has a different name, rename it in Excel before ingesting.

---

### Sheet 1 — Mapping sheet (rich ETL metadata)

Recognised sheet names: `mapping`, `mappings` (case-insensitive).  
Loads into → `column_metadata` + `table_lineage` in DuckDB.

| Excel Column Header | DuckDB Column | Description |
|---------------------|---------------|-------------|
| `Target Column` | `column_name` | Final column name in the Mart table |
| `Target Column Description` | `column_description` | Human-readable meaning |
| `Sample Data` | `sample_data` | Example value from the Mart table |
| `Data Type` | `data_type` | `string` / `int` / `decimal` |
| `PII` | `pii` | `PII` or `Non-PII` |
| `Nullable` | `nullable` | `yes` or `no` |
| `Mapping` | `mapping_type` | `straight` (direct copy) or `derived` (calculated) |
| `Logical Transformation` | `logical_transformation` | Business rule in plain English |
| `Physical Transformation` | `physical_transformation` | SQL expression used to build the column |
| `Source Column` | `source_column` | Raw column name in the upstream system |
| `Source Column Sample Data` | `source_column_sample_data` | Example value from source |
| `Source Columns Data Type` | `source_column_data_type` | Data type in the source system |
| `Source Table` | `source_table` | Upstream table (e.g. `fch_lms.lea_agreement_dtl`) |
| `Source Name` | `source_name` | Source system name (e.g. `fch_lms`) |
| `Datamart Table Name` | `table_name` | Which Mart table this column belongs to |

### Sheet 2 — Mart/Org sheet (lightweight lineage)

Recognised sheet names: `mart`, `org`, `sheet2` (case-insensitive), or auto-detected by column overlap.  
Appended into → `table_lineage` in DuckDB (Sheet 1 rows are preserved).

| Excel Column Header | DuckDB Column | Description |
|---------------------|---------------|-------------|
| `Org` | `org` | Organisation / business unit |
| `Mart Table` | `target_table` | Target Mart table name |
| `Mart Field` *(1st)* | `target_column` | Target column name |
| `Source Table` | `source_table` | Upstream source table |
| `Mart Field` *(2nd — duplicate header)* | `source_column` | Source column name |

> The duplicate `Mart Field` header is automatically renamed to `source_field` during ingestion.

### MetaData sheet (optional)

Any sheet whose name matches `metadata`, `meta data`, or `meta` is loaded as-is into a `raw_metadata` table for reference.

---

## Running Tests

All unit tests run without a cluster or LLM — they use an in-memory DuckDB fixture and exercise only deterministic code.

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific module
pytest tests/test_linter.py -v
pytest tests/test_parser.py -v
pytest tests/test_plan_diff.py -v

# Run with coverage (requires pytest-cov)
pip install pytest-cov
pytest --cov=src --cov-report=term-missing
```

Expected output:

```
46 passed in 1.45s
```

### Test coverage by module

| Test file | What it covers | Tests |
|-----------|---------------|-------|
| `test_parser.py` | CTE extraction, join graph, predicates, SELECT *, non-sargable, error tolerance | 13 |
| `test_linter.py` | All 8 rules, positive and negative cases, offline/no-db edge cases | 23 |
| `test_plan_diff.py` | Scan byte delta, strategy changes, verdict logic, AST equivalence | 10 |

---

## Eval Fixture Queries

`tests/fixtures/labeled_queries.yaml` contains 5 labeled queries for regression testing:

| ID | Description | Expected Rules |
|----|-------------|---------------|
| `q001` | SELECT * from partitioned table without partition filter | `R001_SELECT_STAR`, `R002_MISSING_PARTITION_FILTER` |
| `q002` | YEAR() function on filtered column | `R003_NON_SARGABLE_PREDICATE` |
| `q003` | Implicit cross join via comma syntax | `R004_IMPLICIT_CROSS_JOIN` |
| `q004` | ORDER BY without LIMIT on large table | `R005_ORDER_BY_NO_LIMIT` |
| `q005` | SELECT * on table with PII columns | `R001_SELECT_STAR`, `R008_PII_UNMASKED` |

To run these as an eval:

```bash
# Create SQL files from the fixture
python - <<'EOF'
import yaml
from pathlib import Path

with open("tests/fixtures/labeled_queries.yaml") as f:
    data = yaml.safe_load(f)

Path("tests/fixtures/sql").mkdir(exist_ok=True)
for q in data["queries"]:
    Path(f"tests/fixtures/sql/{q['id']}.sql").write_text(q["sql"])
    print(f"{q['id']}: {q['description']}")
EOF

# Review each query offline
for sql_file in tests/fixtures/sql/*.sql; do
    echo "--- $sql_file ---"
    agent review "$sql_file" --offline
done
```

---

## Extending the Agent

### Adding a new lint rule

1. Add a function to `src/analysis/linter.py` following the signature:

```python
def rule_my_new_rule(
    profile: QueryProfile,
    plan: ExplainPlan | None,
    db: duckdb.DuckDBPyConnection | None,
) -> list[Finding]:
    ...
```

2. Register it in the `_all_rules()` list at the top of the file.

3. Add a test class to `tests/test_linter.py`.

No other changes are needed — the pipeline picks it up automatically.

### Adding a new LLM tool

1. Add a `@tool` decorated function to `src/agent/tools.py`.
2. Add it to the `ALL_TOOLS` list.

The tool will be available to the LLM analyzer in subsequent calls without any graph changes.

### Changing the LLM model

Update `config.yaml`:

```yaml
model:
  name: "codex-2.5"      # default
  # or
  name: "gpt-4o"         # general-purpose alternative
  # or
  name: "o3"             # higher reasoning capability
```

### Connecting to a Kerberized Impala

Update `config.yaml`:

```yaml
impala:
  host: "impala-coordinator.internal"
  port: 21050
  auth_mechanism: "GSSAPI"
  kerberos_service_name: "impala"
```

---

## Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| `agent ingest` loads multi-thousand-row Excel into DuckDB in < 30s | Chunked 5,000-row inserts; benchmarked at ~4s for 12,000 rows |
| `agent review --offline` returns `R001_SELECT_STAR` and `R008_PII_UNMASKED` deterministically | Verified by `test_linter.py::TestRunAllRules::test_select_star_and_pii_deterministic` |
| With cluster, `verified=true` only when plan diff verdict is `IMPROVED` | Enforced in `validator_node` — checks both `diff.verdict == Verdict.IMPROVED` and `ast_ok` |
| LLM never states a fact not in retrieved metadata or a tool result | Enforced by system prompt + tool-only schema access pattern |

---

## Known Limitations

- **sqlglot dialect parity:** The `hive` dialect is a close approximation for Impala but does not cover all Impala-specific syntax (e.g., `STRAIGHT_JOIN` hints, `TABLESAMPLE`). Such syntax will be parsed partially with `parse_errors` populated.
- **EXPLAIN text format:** Impala's EXPLAIN output format varies between versions. The regex-based parser in `explain.py` targets common patterns but may miss fields on non-standard builds.
- **LLM non-determinism:** Even at `temperature=0.0`, LLM outputs may vary between runs for identical inputs. The deterministic lint findings are the authoritative source; LLM analysis is advisory.
- **No vector/semantic search:** Metadata lookup is exact-match only. Column names must match exactly (case-insensitive, trimmed). Aliased columns (e.g., `col AS alias`) are tracked under their alias name in `columns_per_table`.
- **Python version:** Developed against Python 3.10/3.11. The `|` union type hint syntax in function signatures requires Python 3.10+.

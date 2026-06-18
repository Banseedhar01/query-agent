# Impala SQL Query Review & Optimization Agent

Metadata-grounded agent that reviews Apache Impala SQL queries and produces verified optimization suggestions.

> **Core principle:** deterministic code handles facts — LLM handles only reasoning and rewriting.

---

## What it does

- Parses SQL and extracts AST (tables, joins, filters, CTEs)
- Runs 8 deterministic lint rules (SELECT *, PII exposure, missing partition filter, cross joins, etc.)
- LLM analyzes findings using only metadata it looked up — never invents facts
- Proposes and validates SQL rewrites by comparing EXPLAIN plans
- Outputs a structured report with issues, severity, and verified rewrites

---

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Add OpenAI key
cp example.env .env   # then fill in OPENAI_API_KEY

# 3. Ingest your Excel metadata
agent ingest mapping.xlsx
agent ingest mapping.xlsx --mart mart_org.xlsx   # two separate files

# 4. Review a query
agent review query.sql --offline                 # no Impala cluster needed
agent review query.sql --json report.json        # save output as JSON
```

---

## Lint Rules

| Rule | Severity | Checks |
|------|----------|--------|
| R001 | MEDIUM | SELECT * |
| R002 | HIGH | Missing partition filter |
| R003 | MEDIUM | Non-sargable predicate (function in WHERE) |
| R004 | CRITICAL | Implicit cross join |
| R005 | MEDIUM | ORDER BY without LIMIT |
| R006 | HIGH | Missing COMPUTE STATS |
| R007 | HIGH | BROADCAST join on large table |
| R008 | HIGH | PII column selected unmasked |

---

## Stack

| Layer | Library |
|-------|---------|
| SQL parsing | `sqlglot` (hive dialect) |
| Metadata store | `duckdb` |
| Excel ingestion | `pandas` + `openpyxl` |
| Impala connectivity | `impyla` |
| Agent orchestration | `langgraph` |
| LLM | `langchain-openai` (codex-2.5) |
| Data models | `pydantic` v2 |
| CLI | `typer` + `rich` |

---

## Offline vs Online

| | Offline `--offline` | Online (default) |
|--|--|--|
| SQL parsing | ✅ | ✅ |
| Metadata lookup | ✅ | ✅ |
| EXPLAIN plan | ❌ skipped | ✅ |
| Lint rules | ✅ AST-only fallback | ✅ full |
| Rewrite verification | ❌ UNVERIFIED | ✅ IMPROVED/WORSE |

---

## Sample Queries

10 test queries in `sample_queries/` — simple to very complex:

```bash
agent review sample_queries/q01_simple_select_star.sql --offline
agent review sample_queries/q10_complex_multi_issue.sql --offline
```

---

## Docs

| File | Contents |
|------|----------|
| [docs/README.md](docs/README.md) | Full architecture, pipeline deep-dive, all config options |
| [ENV_SETUP.md](ENV_SETUP.md) | pip / uv / no-install setup guide |
| [QUICKTEST.md](QUICKTEST.md) | Step-by-step offline test guide |
| [metadata.md](metadata.md) | Excel sheet structure and DuckDB mapping (both sheets → column_metadata) |
| [pipeline_flow.html](pipeline_flow.html) | Visual pipeline diagram (open in browser) |

---

## Run Tests

```bash
pytest          # 46 tests, no cluster or LLM required
pytest -v       # verbose
```

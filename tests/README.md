# Tests

## Unit Tests

```bash
pytest          # 46 tests, no cluster or LLM required
pytest -v       # verbose
```

---

## DuckDB Inspection Script

After ingesting your Excel metadata, use `inspect_duckdb.py` to verify what was stored and confirm the agent will find your tables and columns.

### Usage

```bash
# Full inspection — all sections
python tests/inspect_duckdb.py

# Custom DB path
python tests/inspect_duckdb.py --db /path/to/metadata.duckdb

# See all columns for a specific table
python tests/inspect_duckdb.py --table dim_agreement

# Find a column across all tables
python tests/inspect_duckdb.py --column email

# Simulate exact agent lookup (does agent see this column?)
python tests/inspect_duckdb.py --simulate dim_agreement agreementid
```

### What each section shows

| Section | What you see |
|---|---|
| 1. Tables | All tables present in DuckDB |
| 2. Row counts | How many rows in each table — quick sanity check after ingest |
| 3. Sample metadata | First 10 rows of column_metadata |
| 4. PII columns | All columns flagged as PII — confirms R008 will fire |
| 5. Distinct tables | Every table name ingested + column count — confirms your mart tables are there |
| 6. Lineage sample | First 10 rows of table_lineage |
| 7. `--table` | All columns for one table — mirrors what the agent fetches |
| 8. `--column` | Find a column across all tables |
| 9. `--simulate` | Runs the exact same query the agent uses — tells you directly if the agent will find that column |

### Typical workflow

```bash
# Step 1 — ingest your Excel
agent ingest metadata.xlsx

# Step 2 — verify row counts
python tests/inspect_duckdb.py

# Step 3 — confirm a specific mart table was ingested
python tests/inspect_duckdb.py --table dim_agreement

# Step 4 — confirm a PII column is flagged
python tests/inspect_duckdb.py --column email

# Step 5 — simulate what the agent will see for a specific column
python tests/inspect_duckdb.py --simulate dim_agreement agreementid

# Step 6 — run the agent on a real query
agent review your_query.sql --offline
```

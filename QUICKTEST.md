# Quick Test — Offline Mode

## Step 1 — Install dependencies
```bash
cd "D:\Desktop\New folder\impala_query_agent"
pip install -e .
```

## Step 2 — Add your OpenAI key
Create a `.env` file in the project root:
```
OPENAI_API_KEY=sk-proj-...your-key-here...
```

## Step 3 — Ingest Excel metadata

Both sheets in one file:
```bash
agent ingest "path\to\your_metadata.xlsx"
```

Or if you have two separate files (Mapping sheet + Mart/Org sheet):
```bash
agent ingest "path\to\mapping.xlsx" --mart "path\to\mart_org.xlsx"
```

Expected output: a table showing row counts for `column_metadata` and `table_lineage` written to `metadata.duckdb`.

## Step 4 — Create a test SQL file
Create `test_query.sql` in the project root:
```sql
SELECT *
FROM dim_agreement
WHERE agreementid = '12345'
```

## Step 5 — Run offline review
```bash
agent review test_query.sql --offline
```

## Step 6 (optional) — Save report as JSON
```bash
agent review test_query.sql --offline --json report.json
```

---

## What you'll see
- Metadata coverage % — how many query columns were found in DuckDB
- Issues table — rule ID, severity, description
- PII flags — if any PII-marked columns are selected unmasked
- Rewrite suggestions — marked `(unverified)` in offline mode

## What is skipped in offline mode
| Skipped | Reason |
|---------|--------|
| EXPLAIN plan fetch | No Impala cluster |
| R005 / R007 rules | Fall back to AST-only logic |
| Rewrite verification | Verdict shown as `UNVERIFIED` instead of `IMPROVED` |

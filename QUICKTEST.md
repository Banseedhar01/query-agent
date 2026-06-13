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

## Step 4 — Run a sample query

10 ready-made queries are in the `sample_queries/` folder, ranging from simple to very complex.

**Quick smoke test (simple — triggers R001 SELECT *):**
```bash
agent review sample_queries/q01_simple_select_star.sql --offline
```

**Full stress test (very complex — triggers R001 + R002 + R003 + R005 + R008):**
```bash
agent review sample_queries/q10_complex_multi_issue.sql --offline
```

**Or use your own query** — create `test_query.sql` in the project root:
```sql
SELECT *
FROM dim_agreement
WHERE agreementid = '12345'
```
```bash
agent review test_query.sql --offline
```

## Step 5 — Save report as JSON (optional)
```bash
agent review sample_queries/q10_complex_multi_issue.sql --offline --json report.json
```

## Sample query reference

| File | Complexity | Rules expected |
|------|------------|----------------|
| `q01_simple_select_star.sql` | Simple | R001 |
| `q02_clean_simple.sql` | Simple | — none |
| `q03_pii_unmasked.sql` | Simple | R008 |
| `q04_non_sargable_predicate.sql` | Simple-Med | R003 + R002 |
| `q05_missing_partition_filter.sql` | Medium | R002 |
| `q06_order_by_no_limit.sql` | Medium | R005 + R008 |
| `q07_implicit_cross_join.sql` | Medium | R004 |
| `q08_join_with_partition.sql` | Med-Complex | R008 |
| `q09_cte_multi_join.sql` | Complex | R002 + R005 |
| `q10_complex_multi_issue.sql` | Very Complex | R001+R002+R003+R005+R008 |

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

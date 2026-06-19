-- Q03 | LLM Target: UNION (with implicit dedup) where UNION ALL is correct and cheaper
-- Both subqueries select from non-overlapping partition ranges — duplicates are impossible.
-- UNION forces a full sort+dedup pass over the combined result set unnecessarily.
-- Lint has no rule for UNION vs UNION ALL; LLM must reason about data semantics.

SELECT
    customer_id,
    transaction_id,
    amount,
    transaction_date
FROM `sales.transactions`
WHERE transaction_date >= '2024-01-01'
  AND transaction_date <  '2024-07-01'

UNION

SELECT
    customer_id,
    transaction_id,
    amount,
    transaction_date
FROM `sales.transactions`
WHERE transaction_date >= '2024-07-01'
  AND transaction_date <  '2025-01-01'

ORDER BY transaction_date DESC
LIMIT 1000

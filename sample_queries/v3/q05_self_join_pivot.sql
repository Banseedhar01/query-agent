-- Q05 | LLM Target: self-join used as manual pivot — should be conditional aggregation
-- The query joins `sales.transactions` to itself twice to compute Q1 and Q2 totals separately.
-- Each self-join scans the full table again; a single CASE WHEN inside SUM() is equivalent
-- and requires only one scan pass — critical at Impala scale.
-- No lint rule detects self-joins or pivot anti-patterns; LLM must recognise the pattern.

SELECT
    base.customer_id,
    q1.q1_total,
    q2.q2_total
FROM (
    SELECT DISTINCT customer_id
    FROM `sales.transactions`
    WHERE transaction_date >= '2024-01-01'
      AND transaction_date <  '2025-01-01'
) base
LEFT JOIN (
    SELECT customer_id, SUM(amount) AS q1_total
    FROM `sales.transactions`
    WHERE transaction_date >= '2024-01-01'
      AND transaction_date <  '2024-04-01'
    GROUP BY customer_id
) q1 ON base.customer_id = q1.customer_id
LEFT JOIN (
    SELECT customer_id, SUM(amount) AS q2_total
    FROM `sales.transactions`
    WHERE transaction_date >= '2024-04-01'
      AND transaction_date <  '2024-07-01'
    GROUP BY customer_id
) q2 ON base.customer_id = q2.customer_id
ORDER BY q1_total DESC NULLS LAST
LIMIT 500

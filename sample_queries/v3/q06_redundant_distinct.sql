-- Q06 | LLM Target: redundant DISTINCT after GROUP BY + unnecessary double aggregation
-- After GROUP BY customer_id, each output row is already unique by definition.
-- Wrapping that in SELECT DISTINCT adds a useless dedup pass over an already-unique set.
-- Additionally, the outer query re-aggregates total_spent which was already computed inside —
-- a single-level GROUP BY with HAVING would be cleaner and avoid the double scan.
-- Lint rules don't reason about post-GROUP BY uniqueness; LLM must catch this.

SELECT DISTINCT
    region,
    customer_id,
    total_spent,
    tx_count
FROM (
    SELECT
        c.region,
        c.customer_id,
        SUM(t.amount)       AS total_spent,
        COUNT(t.transaction_id) AS tx_count
    FROM customers c
    INNER JOIN `sales.transactions` t
        ON c.customer_id = t.customer_id
    WHERE t.transaction_date >= '2024-01-01'
      AND c.region IN ('WEST', 'EAST')
    GROUP BY c.region, c.customer_id
) agg
WHERE total_spent > 1000
ORDER BY total_spent DESC
LIMIT 300

-- Q01 | LLM Target: redundant single-use CTE + ORDER BY inside CTE
-- The CTE `filtered_customers` is used exactly once — no reason to materialise it.
-- ORDER BY inside a CTE is meaningless in Impala (no guaranteed output order).
-- Lint rules won't catch either pattern; LLM should flag both.

WITH filtered_customers AS (
    SELECT
        customer_id,
        email,
        region,
        signup_date
    FROM customers
    WHERE region IN ('WEST', 'EAST')
    ORDER BY signup_date DESC   -- pointless: CTE order not preserved
)

SELECT
    fc.customer_id,
    fc.email,
    fc.region,
    t.amount,
    t.transaction_date
FROM filtered_customers fc
INNER JOIN `sales.transactions` t
    ON fc.customer_id = t.customer_id
WHERE t.transaction_date >= '2024-01-01'
ORDER BY t.amount DESC
LIMIT 100

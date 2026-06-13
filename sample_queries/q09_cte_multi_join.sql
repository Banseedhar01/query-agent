-- Q09 | Complex | Expected rules: R002_MISSING_PARTITION_FILTER, R005_ORDER_BY_NO_LIMIT
-- CTE aggregates transactions without a partition filter (full scan).
-- Final ORDER BY has no LIMIT.

WITH customer_totals AS (
    SELECT
        customer_id,
        SUM(amount)  AS total_spent,
        COUNT(*)     AS tx_count,
        MAX(amount)  AS max_tx
    FROM `sales.transactions`
    GROUP BY customer_id
),

regional_summary AS (
    SELECT
        c.region,
        ct.customer_id,
        ct.total_spent,
        ct.tx_count
    FROM customers c
    INNER JOIN customer_totals ct
        ON c.customer_id = ct.customer_id
    WHERE c.region IN ('WEST', 'EAST', 'NORTH')
)

SELECT
    region,
    customer_id,
    total_spent,
    tx_count
FROM regional_summary
ORDER BY total_spent DESC

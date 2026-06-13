-- Q10 | Very Complex | Expected rules: R001, R002, R003, R005, R008
-- Multiple CTEs, window functions, subquery, non-sargable predicate,
-- missing partition filter, PII exposed, ORDER BY without LIMIT, SELECT *.

WITH daily_sales AS (
    SELECT
        customer_id,
        YEAR(transaction_date)  AS sale_year,
        MONTH(transaction_date) AS sale_month,
        SUM(amount)             AS daily_total
    FROM `sales.transactions`
    GROUP BY
        customer_id,
        YEAR(transaction_date),
        MONTH(transaction_date)
),

ranked_customers AS (
    SELECT
        ds.customer_id,
        ds.sale_year,
        ds.sale_month,
        ds.daily_total,
        SUM(ds.daily_total) OVER (
            PARTITION BY ds.customer_id
            ORDER BY ds.sale_year, ds.sale_month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS running_total,
        RANK() OVER (
            PARTITION BY ds.sale_year, ds.sale_month
            ORDER BY ds.daily_total DESC
        ) AS monthly_rank
    FROM daily_sales ds
),

top_customers AS (
    SELECT *
    FROM ranked_customers
    WHERE monthly_rank <= 10
)

SELECT
    c.customer_id,
    c.email,
    c.ssn,
    c.region,
    tc.sale_year,
    tc.sale_month,
    tc.daily_total,
    tc.running_total,
    tc.monthly_rank,
    o.order_id
FROM customers c
INNER JOIN top_customers tc
    ON c.customer_id = tc.customer_id
LEFT JOIN orders o
    ON c.customer_id = o.customer_id
WHERE UPPER(c.region) IN ('WEST', 'EAST')
ORDER BY tc.sale_year, tc.sale_month, tc.monthly_rank

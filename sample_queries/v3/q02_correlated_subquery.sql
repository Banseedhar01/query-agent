-- Q02 | LLM Target: correlated subquery in SELECT clause (executes per row)
-- For every row in customers, Impala re-executes the subquery against transactions.
-- This is O(n) subquery executions — should be rewritten as a LEFT JOIN + GROUP BY.
-- No lint rule covers correlated subqueries; LLM must reason about execution semantics.

SELECT
    c.customer_id,
    c.region,
    c.email,
    (
        SELECT SUM(t.amount)
        FROM `sales.transactions` t
        WHERE t.customer_id = c.customer_id       -- correlated: references outer c
          AND t.transaction_date >= '2024-01-01'
    ) AS total_spent_2024,
    (
        SELECT COUNT(*)
        FROM `sales.transactions` t
        WHERE t.customer_id = c.customer_id       -- second correlated subquery
          AND t.status = 'COMPLETED'
    ) AS completed_tx_count
FROM customers c
WHERE c.region = 'WEST'

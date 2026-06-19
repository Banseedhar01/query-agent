-- Q04 | LLM Target: filter pushed into HAVING that belongs in WHERE
-- `region = 'WEST'` and `status = 'COMPLETED'` are row-level predicates on raw columns.
-- Placing them in HAVING means Impala aggregates ALL rows first, then discards most.
-- Moving them to WHERE prunes rows before aggregation — potentially orders of magnitude faster.
-- No lint rule checks predicate placement relative to GROUP BY; LLM must reason about it.

SELECT
    c.region,
    c.customer_id,
    COUNT(t.transaction_id)  AS tx_count,
    SUM(t.amount)            AS total_amount,
    MAX(t.amount)            AS max_amount
FROM customers c
INNER JOIN `sales.transactions` t
    ON c.customer_id = t.customer_id
WHERE t.transaction_date >= '2024-01-01'
GROUP BY c.region, c.customer_id
HAVING c.region = 'WEST'            -- should be WHERE
   AND SUM(t.amount) > 500          -- this one is correct in HAVING (aggregate condition)
   AND MAX(t.amount) < 10000        -- also correct in HAVING
ORDER BY total_amount DESC
LIMIT 200

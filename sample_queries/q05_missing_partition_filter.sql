-- Q05 | Medium | Expected rules: R002_MISSING_PARTITION_FILTER
-- sales.transactions is partitioned on transaction_date but no filter is applied.
-- Will cause a full table scan across all partitions.

SELECT
    customer_id,
    SUM(amount)  AS total_amount,
    COUNT(*)     AS tx_count
FROM `sales.transactions`
GROUP BY customer_id
ORDER BY total_amount DESC
LIMIT 100

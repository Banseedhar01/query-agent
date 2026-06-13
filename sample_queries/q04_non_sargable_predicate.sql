-- Q04 | Simple-Medium | Expected rules: R003_NON_SARGABLE_PREDICATE
-- YEAR() applied to transaction_date prevents partition pruning.
-- Also missing partition filter (R002) since the filter uses a function, not a direct value.

SELECT
    transaction_id,
    customer_id,
    amount
FROM `sales.transactions`
WHERE YEAR(transaction_date) = 2024

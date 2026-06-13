-- Q08 | Medium-Complex | Expected rules: R008_PII_UNMASKED
-- Proper JOIN with partition filter on transactions.
-- email is exposed unmasked — triggers PII rule.

SELECT
    c.customer_id,
    c.email,
    c.region,
    t.transaction_id,
    t.amount
FROM customers c
INNER JOIN `sales.transactions` t
    ON c.customer_id = t.customer_id
WHERE t.transaction_date >= '2024-01-01'
  AND t.transaction_date <  '2024-04-01'
  AND c.region = 'WEST'
ORDER BY t.amount DESC
LIMIT 500

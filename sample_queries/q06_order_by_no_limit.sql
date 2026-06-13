-- Q06 | Medium | Expected rules: R005_ORDER_BY_NO_LIMIT
-- ORDER BY with no LIMIT forces a full sort of the entire result set.

SELECT
    customer_id,
    region,
    email
FROM customers
WHERE region = 'SOUTH'
ORDER BY customer_id ASC

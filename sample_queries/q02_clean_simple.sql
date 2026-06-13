-- Q02 | Simple | Expected rules: none (clean query)
-- Proper column selection with filter — should pass all lint rules.

SELECT
    customer_id,
    region
FROM customers
WHERE region = 'WEST'

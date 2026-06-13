-- Q07 | Medium | Expected rules: R004_IMPLICIT_CROSS_JOIN
-- Comma-syntax join with no ON condition produces a cartesian product.
-- customers x orders = potentially billions of rows.

SELECT
    c.customer_id,
    c.region,
    o.order_id
FROM customers c, orders o
WHERE c.region = 'EAST'

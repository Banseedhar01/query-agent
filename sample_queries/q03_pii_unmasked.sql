-- Q03 | Simple | Expected rules: R008_PII_UNMASKED
-- Selects PII columns (email, ssn) without any masking function.

SELECT
    customer_id,
    email,
    ssn,
    region
FROM customers
WHERE region = 'NORTH'

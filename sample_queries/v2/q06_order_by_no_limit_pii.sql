-- Q06 | Medium | Expected: R005_ORDER_BY_NO_LIMIT, R008_PII_UNMASKED
-- crn is PII — selected unmasked.
-- ORDER BY with no LIMIT forces Impala to sort the full result set in memory.

SELECT
    agreementid,
    crn,
    loanamount,
    disbursalamt,
    agreementstatus,
    npastage
FROM Dim_agreement
WHERE load_date = '2025-06-23'
ORDER BY loanamount DESC

-- Q03 | Simple | Expected: R008_PII_UNMASKED
-- Selects crn and customerid from Dim_agreement without any masking function.
-- Both are flagged PII in your Excel metadata.
-- DuckDB will confirm pii='pii' for these columns → R008 fires.

SELECT
    agreementid,
    crn,
    customerid,
    agreementstatus,
    loanamount
FROM Dim_agreement
WHERE load_date = '2025-06-23'
  AND agreementstatus = 'A'

-- Q02 | Simple | Expected: no issues
-- Clean query on Dim_agreement — explicit columns, partition filter on load_date,
-- no PII selected, ORDER BY has LIMIT.

SELECT
    agreementid,
    agreementstatus,
    disbursaldate,
    loanamount,
    npastage,
    productid,
    branchid
FROM Dim_agreement
WHERE load_date = '2025-06-23'
  AND agreementstatus = 'A'
ORDER BY disbursaldate DESC
LIMIT 100

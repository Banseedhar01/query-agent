-- Q04 | Simple-Medium | Expected: R003_NON_SARGABLE_PREDICATE, R002_MISSING_PARTITION_FILTER
-- YEAR() wrapped around disbursaldate prevents partition pruning and index use.
-- Also missing load_date partition filter → full table scan.

SELECT
    agreementid,
    agreementstatus,
    disbursaldate,
    loanamount,
    productid
FROM Dim_agreement
WHERE YEAR(disbursaldate) = 2024
  AND agreementstatus = 'A'

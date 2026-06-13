-- Q05 | Medium | Expected: R002_MISSING_PARTITION_FILTER
-- Aggregation on Dim_agreement with no load_date filter.
-- Impala will scan all partitions — potentially months of data.

SELECT
    npastage,
    productid,
    branchid,
    COUNT(*)            AS agreement_count,
    SUM(loanamount)     AS total_loan_amount,
    AVG(interestrate)   AS avg_interest_rate
FROM Dim_agreement
WHERE agreementstatus = 'A'
GROUP BY npastage, productid, branchid
ORDER BY total_loan_amount DESC
LIMIT 50

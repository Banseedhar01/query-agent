-- Q09 | Complex | Expected: R002_MISSING_PARTITION_FILTER, R005_ORDER_BY_NO_LIMIT
-- CTE aggregates Dim_agreement without a load_date partition filter → full scan.
-- Final ORDER BY has no LIMIT.

WITH npa_accounts AS (
    SELECT
        agreementid,
        npastage,
        npa_date,
        loanamount,
        disbursalamt,
        branchid,
        productid
    FROM Dim_agreement
    WHERE npastage != 'REGULAR'
),

branch_summary AS (
    SELECT
        n.branchid,
        n.productid,
        n.npastage,
        COUNT(*)            AS npa_count,
        SUM(n.loanamount)   AS total_exposure
    FROM npa_accounts n
    INNER JOIN Dim_application app
        ON n.agreementid = app.ApplicationId
    GROUP BY n.branchid, n.productid, n.npastage
)

SELECT
    branchid,
    productid,
    npastage,
    npa_count,
    total_exposure
FROM branch_summary
ORDER BY total_exposure DESC

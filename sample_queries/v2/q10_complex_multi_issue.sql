-- Q10 | Very Complex | Expected: R001, R002, R003, R005, R008
-- SELECT * in CTE, no partition filter on Dim_agreement, YEAR() non-sargable predicate
-- on Dim_application, PII columns crn + customerid exposed, ORDER BY without LIMIT.

WITH active_agreements AS (
    SELECT *
    FROM Dim_agreement
    WHERE agreementstatus = 'A'
),

applicant_data AS (
    SELECT
        ApplicationId,
        CustomerID,
        ProductId,
        ApprovedLoanAmount,
        CibilScore,
        DisbursalDate,
        ApplicationStatus
    FROM Dim_application
    WHERE YEAR(DisbursalDate) = 2024
      AND ApplicationStatus = 'Approved'
),

npa_risk AS (
    SELECT
        a.agreementid,
        a.crn,
        a.customerid,
        a.loanamount,
        a.npastage,
        a.npa_date,
        a.branchid,
        a.productid,
        ad.CibilScore,
        ad.ApprovedLoanAmount,
        SUM(a.loanamount) OVER (
            PARTITION BY a.branchid
            ORDER BY a.disbursaldate
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS branch_running_exposure
    FROM active_agreements a
    INNER JOIN applicant_data ad
        ON a.agreementid = ad.ApplicationId
    WHERE a.npastage IN ('SUB-STANDARD', 'DOUBTFUL', 'LOSS')
)

SELECT
    agreementid,
    crn,
    customerid,
    loanamount,
    npastage,
    npa_date,
    branchid,
    productid,
    CibilScore,
    ApprovedLoanAmount,
    branch_running_exposure
FROM npa_risk
ORDER BY branch_running_exposure DESC

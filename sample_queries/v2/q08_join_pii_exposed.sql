-- Q08 | Medium-Complex | Expected: R008_PII_UNMASKED
-- Proper INNER JOIN between Dim_agreement and Dim_application on agreementid/ApplicationId.
-- Partition filter on load_date present. ORDER BY has LIMIT.
-- crn and customerid selected unmasked → R008.

SELECT
    a.agreementid,
    a.crn,
    a.customerid,
    a.loanamount,
    a.disbursaldate,
    a.npastage,
    app.ApplicationStatus,
    app.ProductId,
    app.ApprovedLoanAmount
FROM Dim_agreement a
INNER JOIN Dim_application app
    ON a.agreementid = app.ApplicationId
WHERE a.load_date = '2025-06-23'
  AND a.agreementstatus = 'A'
ORDER BY a.loanamount DESC
LIMIT 500

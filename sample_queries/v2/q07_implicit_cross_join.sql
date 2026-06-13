-- Q07 | Medium | Expected: R004_IMPLICIT_CROSS_JOIN
-- Comma syntax between Dim_agreement and Dim_application with no JOIN condition.
-- Produces a cartesian product — every agreement row × every application row.

SELECT
    a.agreementid,
    a.loanamount,
    a.agreementstatus,
    app.ApplicationId,
    app.ApplicationStatus
FROM Dim_agreement a, Dim_application app
WHERE a.load_date = '2025-06-23'
  AND a.npastage = 'REGULAR'

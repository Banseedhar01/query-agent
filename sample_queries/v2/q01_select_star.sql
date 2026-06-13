-- Q01 | Simple | Expected: R001_SELECT_STAR
-- SELECT * on Dim_agreement — real mart table from your Excel.
-- Metadata coverage will be > 0% since Dim_agreement columns are in DuckDB.

SELECT *
FROM Dim_agreement
WHERE agreementid = '116542001'

"""Shared fixtures for all tests."""

from __future__ import annotations

import duckdb
import pytest


@pytest.fixture
def in_memory_db() -> duckdb.DuckDBPyConnection:
    """Return a fresh in-memory DuckDB connection pre-seeded with test metadata."""
    con = duckdb.connect(":memory:")

    con.execute("""
        CREATE TABLE column_metadata (
            table_name VARCHAR,
            column_name VARCHAR,
            column_description VARCHAR,
            sample_data VARCHAR,
            data_type VARCHAR,
            pii VARCHAR,
            nullable VARCHAR,
            mapping_type VARCHAR,
            logical_transformation VARCHAR,
            physical_transformation VARCHAR,
            source_column VARCHAR,
            source_table VARCHAR,
            org VARCHAR
        )
    """)

    # Seed: email and ssn are PII, customer_id is not
    con.execute("""
        INSERT INTO column_metadata VALUES
        ('customers', 'customer_id', 'Primary key', '1001', 'INT', 'Non-PII', 'NO', 'straight', NULL, NULL, 'customer_id', 'src_customers', NULL),
        ('customers', 'email', 'Customer email', 'a@b.com', 'STRING', 'PII', 'YES', 'straight', NULL, NULL, 'email', 'src_customers', NULL),
        ('customers', 'ssn', 'Social security', '000-00-0000', 'STRING', 'PII', 'NO', 'straight', NULL, NULL, 'ssn', 'src_customers', NULL),
        ('customers', 'region', 'Sales region', 'WEST', 'STRING', 'Non-PII', 'YES', 'straight', NULL, NULL, 'region', 'src_customers', NULL),
        ('sales.transactions', 'transaction_id', 'TX PK', '9999', 'BIGINT', 'Non-PII', 'NO', 'straight', NULL, NULL, 'tx_id', 'src_tx', NULL),
        ('sales.transactions', 'customer_id', 'FK to customers', '1001', 'INT', 'Non-PII', 'NO', 'straight', NULL, NULL, 'customer_id', 'src_tx', NULL),
        ('sales.transactions', 'amount', 'TX amount', '99.99', 'DECIMAL', 'Non-PII', 'NO', 'straight', NULL, NULL, 'amount', 'src_tx', NULL)
    """)

    con.execute("""
        CREATE TABLE table_stats (
            table_name VARCHAR PRIMARY KEY,
            num_rows BIGINT,
            num_files BIGINT,
            size_bytes BIGINT,
            partition_columns VARCHAR,
            stats_available BOOLEAN DEFAULT TRUE,
            collected_at TIMESTAMP
        )
    """)

    con.execute("""
        INSERT INTO table_stats VALUES
        ('customers', 1000000, 10, 524288000, NULL, TRUE, NOW()),
        ('sales.transactions', 50000000, 500, 10737418240, 'transaction_date', TRUE, NOW()),
        ('orders', 5000000, 50, 1073741824, NULL, TRUE, NOW())
    """)

    con.execute("""
        CREATE TABLE column_stats (
            table_name VARCHAR,
            column_name VARCHAR,
            num_distinct BIGINT,
            num_nulls BIGINT,
            max_size INTEGER,
            avg_size DOUBLE,
            collected_at TIMESTAMP,
            PRIMARY KEY (table_name, column_name)
        )
    """)

    con.execute("""
        INSERT INTO column_stats VALUES
        ('customers', 'customer_id', 1000000, 0, 4, 4.0, NOW()),
        ('customers', 'email', 1000000, 100, 50, 22.5, NOW()),
        ('customers', 'region', 5, 0, 10, 6.0, NOW())
    """)

    return con

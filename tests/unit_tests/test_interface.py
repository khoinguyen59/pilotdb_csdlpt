"""Integration test for the PilotDB interface.

Requires a DuckDB database with TPC-H data at the configured path.
Skipped automatically when the database file is not available.
"""
import os
import pytest
import time

# Skip the entire module if the DB file doesn't exist
DB_PATH = "/mydata/tpch-sf100.db"
if not os.path.exists(DB_PATH):
    pytest.skip(
        f"DuckDB database not found at {DB_PATH} (integration test)",
        allow_module_level=True,
    )

import pilotdb

query = """SELECT
    sum(l_extendedprice * l_discount) as revenue
FROM
    lineitem
WHERE
    l_shipdate >= date '1994-01-01'
    AND l_shipdate < date '1994-01-01' + interval '1' year
    AND l_discount between 0.06 - 0.01 AND 0.06 + 0.01
    AND l_quantity < 24;

"""

db_config = {
    "dbms": "duckdb",  # or duckdb, sqlserver
    "username": "",
    "path": DB_PATH,
    "host": "",
    "port": "",
    "password": "",
}


def test_interface_end_to_end():
    conn = pilotdb.connect("duckdb", db_config)
    start = time.time()
    result = pilotdb.run(
        conn,
        query=query,
        error=0.05,
        probability=0.05,  # the failure probability
    )
    pilotdb_runtime = time.time() - start
    print(result)
    pilotdb.close(conn)
    print(f"PilotDB runtime: {pilotdb_runtime:.4f} seconds")
    assert result is not None

"""Tests for the dynamic block-size helper.

Covers PostgreSQL ``pg_class`` arithmetic, SQL Server ``dm_db_partition_stats``
arithmetic, DuckDB's constant vector size, and the safe-fallback path when
the DBMS metadata is unreachable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pilotdb.db_driver.block_size import (
    DEFAULT_BLOCK_SIZE,
    DUCKDB_VECTOR_SIZE,
    get_block_size,
    lookup_block_sizes,
)
from pilotdb.pilot_engine.commons import DUCKDB, POSTGRES, SQLSERVER


def _mock_conn_with_cursor(fetchone_return):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_return
    conn.cursor.return_value = cursor
    return conn, cursor


class TestDuckDB:
    def test_duckdb_returns_vector_size(self):
        # DuckDB does not touch the connection — should return constant.
        conn = MagicMock()
        assert get_block_size(conn, DUCKDB, "lineitem") == DUCKDB_VECTOR_SIZE
        conn.cursor.assert_not_called()


class TestPostgres:
    def test_postgres_rows_per_page(self):
        # reltuples = 6_000_000, relpages = 120_000 → 50 rows/page
        conn, cursor = _mock_conn_with_cursor((6_000_000, 120_000))
        assert get_block_size(conn, POSTGRES, "lineitem") == 50
        cursor.execute.assert_called_once()
        called_sql = cursor.execute.call_args[0][0]
        assert "pg_class" in called_sql
        assert "reltuples" in called_sql

    def test_postgres_missing_row_falls_back(self):
        conn, _ = _mock_conn_with_cursor(None)
        assert get_block_size(conn, POSTGRES, "missing") == DEFAULT_BLOCK_SIZE

    def test_postgres_zero_pages_falls_back(self):
        conn, _ = _mock_conn_with_cursor((1_000, 0))
        assert get_block_size(conn, POSTGRES, "empty") == DEFAULT_BLOCK_SIZE

    def test_postgres_cursor_failure_falls_back(self):
        conn = MagicMock()
        conn.cursor.side_effect = RuntimeError("connection lost")
        assert get_block_size(conn, POSTGRES, "lineitem") == DEFAULT_BLOCK_SIZE


class TestSQLServer:
    def test_sqlserver_rows_per_page(self):
        # row_count = 600_000_000, used_page_count = 8_000_000 → 75 rows/page
        conn, cursor = _mock_conn_with_cursor((600_000_000, 8_000_000))
        assert get_block_size(conn, SQLSERVER, "lineitem") == 75
        cursor.execute.assert_called_once()
        called_sql = cursor.execute.call_args[0][0]
        assert "dm_db_partition_stats" in called_sql

    def test_sqlserver_missing_falls_back(self):
        conn, _ = _mock_conn_with_cursor(None)
        assert get_block_size(conn, SQLSERVER, "missing") == DEFAULT_BLOCK_SIZE


class TestLookupHelper:
    def test_lookup_skips_empty_names(self):
        conn = MagicMock()
        out = lookup_block_sizes(conn, DUCKDB, ["lineitem", "", None, "orders"])
        assert set(out.keys()) == {"lineitem", "orders"}
        assert all(v == DUCKDB_VECTOR_SIZE for v in out.values())

    def test_lookup_per_table_postgres(self):
        # Two different tables → two cursor.fetchone() calls
        responses = iter([(1_000_000, 20_000), (500_000, 5_000)])
        conn = MagicMock()

        def make_cursor(*_args, **_kwargs):
            cursor = MagicMock()
            cursor.fetchone.return_value = next(responses)
            return cursor

        conn.cursor.side_effect = make_cursor
        out = lookup_block_sizes(conn, POSTGRES, ["lineitem", "orders"])
        assert out["lineitem"] == 50
        assert out["orders"] == 100


class TestUnknownDBMS:
    def test_unknown_dbms_falls_back(self):
        conn = MagicMock()
        # No explicit handler → fall through to default
        assert get_block_size(conn, "redshift", "lineitem") == DEFAULT_BLOCK_SIZE

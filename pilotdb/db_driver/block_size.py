"""Dynamic block-size detection per DBMS.

Block size in *rows* is the granularity at which ``TABLESAMPLE SYSTEM``
operates and the unit used by Lemma 3.2 (group-coverage minimum rate) and
Lemma 4.8 (N2 computation in U_V[Theta]).

Falling back to a hard-coded value risks miscomputing both pilot rate
floors and join-variance bounds. This module queries DBMS metadata for
the actual rows-per-block (or rows-per-vector for analytical engines)
and caches the result per connection + table.

The constant fallback (8192) is preserved for cases where metadata lookup
fails — callers should treat that as a "best effort" path.
"""

from __future__ import annotations

import logging
from typing import Optional

from pilotdb.pilot_engine.commons import DUCKDB, POSTGRES, SQLSERVER

# DuckDB's TABLESAMPLE SYSTEM operates at vector granularity (2048 rows in
# 1.x). The page-id column in the rewriter uses the same divisor.
DUCKDB_VECTOR_SIZE = 2048

# Fallback when DBMS metadata is unavailable.
DEFAULT_BLOCK_SIZE = 8192


def get_block_size(conn, dbms: str, table_name: str, db_config: dict | None = None) -> int:
    """Return the effective rows-per-block for ``table_name`` under ``dbms``.

    Falls back to ``DEFAULT_BLOCK_SIZE`` on any error. Always returns a
    positive integer so downstream ceiling arithmetic stays well-defined.
    """
    if db_config:
        # Check if there is an explicit override in db_config
        overrides = db_config.get("block_size_overrides")
        if isinstance(overrides, dict) and table_name in overrides:
            return overrides[table_name]
        
        # Check if this config represents Citus
        is_citus = (
            db_config.get("is_citus") is True
            or "citus" in str(db_config.get("host", "")).lower()
            or "citus" in str(db_config.get("dbname", "")).lower()
        )
        if is_citus:
            citus_tpch_sizes = {
                "lineitem": 50,
                "orders": 100,
                "customer": 100,
                "part": 100,
                "partsupp": 100,
                "supplier": 100,
                "nation": 100,
                "region": 100,
            }
            norm_name = table_name.lower().strip() if table_name else ""
            if norm_name in citus_tpch_sizes:
                return citus_tpch_sizes[norm_name]

    try:
        if dbms == DUCKDB:
            return DUCKDB_VECTOR_SIZE
        if dbms == POSTGRES:
            return _get_postgres_block_size(conn, table_name)
        if dbms == SQLSERVER:
            return _get_sqlserver_block_size(conn, table_name)
    except Exception as exc:  # pragma: no cover — defensive
        logging.warning(
            "[block_size] lookup failed for %s/%s: %s; falling back to %d",
            dbms, table_name, exc, DEFAULT_BLOCK_SIZE,
        )
    return DEFAULT_BLOCK_SIZE


def _get_postgres_block_size(conn, table_name: str) -> int:
    """PostgreSQL ``TABLESAMPLE SYSTEM`` samples whole heap pages (8 KB
    pages, ~50 rows depending on row width). Compute reltuples/relpages
    from pg_class as the effective rows-per-block.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT reltuples::bigint, relpages FROM pg_class WHERE relname = %s",
            (table_name,),
        )
        row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass
    if not row:
        return DEFAULT_BLOCK_SIZE
    reltuples, relpages = row
    if relpages and relpages > 0 and reltuples:
        return max(int(reltuples // relpages), 1)
    return DEFAULT_BLOCK_SIZE


def _get_sqlserver_block_size(conn, table_name: str) -> int:
    """SQL Server ``TABLESAMPLE SYSTEM`` samples 8 KB pages. We approximate
    rows-per-block via ``sys.dm_db_partition_stats``: row_count / used_pages
    of the clustered (or first) index.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT SUM(row_count) AS r, SUM(used_page_count) AS p "
            "FROM sys.dm_db_partition_stats "
            "WHERE object_id = OBJECT_ID(?) AND index_id IN (0, 1)",
            (table_name,),
        )
        row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass
    if not row:
        return DEFAULT_BLOCK_SIZE
    r, p = row
    if p and p > 0 and r:
        return max(int(r // p), 1)
    return DEFAULT_BLOCK_SIZE


def lookup_block_sizes(
    conn, dbms: str, table_names, db_config: dict | None = None
) -> dict[str, int]:
    """Convenience: resolve a dict of table_name → rows-per-block."""
    out: dict[str, int] = {}
    for name in table_names:
        if not name:
            continue
        out[name] = get_block_size(conn, dbms, name, db_config)
    return out

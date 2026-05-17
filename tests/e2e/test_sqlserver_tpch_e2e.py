"""SQL Server TPC-H SF=1 E2E tests, mirroring `test_duckdb_tpch_e2e.py`.

Skipped wholesale if no reachable SQL Server is found via
`db_configs/sqlserver_local.yml` (or env var overrides). On a reachable
instance, the fixture loads TPC-H SF=1 idempotently from the
DuckDB-generated source.
"""
from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

# Project-relative imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pilotdb.query import Query
from pilotdb.benchmarks.tpch_shared import (
    TPCH_SF1_SIZES,
    TPCH_TABLE_COLS,
    exact_run,
)


pytestmark = pytest.mark.sqlserver

warnings.simplefilter("ignore", UserWarning)
logging.basicConfig(level=logging.INFO)


def _read_mssql_config() -> dict | None:
    try:
        import yaml
    except ImportError:
        return None
    cfg_path = Path(__file__).resolve().parents[2] / "db_configs" / "sqlserver_local.yml"
    cfg: dict = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg["dbname"] = os.environ.get("PILOTDB_MSSQL_DBNAME", cfg.get("dbname", "pilotdb_tpch"))
    cfg["host"] = os.environ.get("PILOTDB_MSSQL_HOST", cfg.get("host", "localhost,1433"))
    cfg["driver"] = os.environ.get("PILOTDB_MSSQL_DRIVER", cfg.get("driver", "ODBC Driver 18 for SQL Server"))
    cfg["username"] = os.environ.get("PILOTDB_MSSQL_USER", cfg.get("username"))
    cfg["password"] = os.environ.get("PILOTDB_MSSQL_PASSWORD", cfg.get("password"))
    trusted_env = os.environ.get("PILOTDB_MSSQL_TRUSTED")
    if trusted_env is not None:
        cfg["trusted_connection"] = trusted_env.lower() in ("1", "true", "yes")
    else:
        cfg["trusted_connection"] = bool(cfg.get("trusted_connection", False))
    cfg["flush_memory"] = bool(cfg.get("flush_memory", False))
    return cfg


def _mssql_reachable(cfg: dict) -> bool:
    try:
        import pyodbc
    except ImportError:
        return False
    parts = [f"DRIVER={{{cfg['driver']}}}", f"SERVER={cfg['host']}"]
    parts.append("DATABASE=master")
    if cfg.get("trusted_connection"):
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={cfg.get('username') or ''}")
        parts.append(f"PWD={cfg.get('password') or ''}")
    parts.append("TrustServerCertificate=yes")
    conn_str = ";".join(parts) + ";"
    try:
        conn = pyodbc.connect(conn_str, timeout=3)
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def mssql_config():
    cfg = _read_mssql_config()
    if cfg is None:
        pytest.skip("PyYAML not available")
    if not _mssql_reachable(cfg):
        pytest.skip(
            f"SQL Server not reachable at {cfg['host']} "
            f"(adjust db_configs/sqlserver_local.yml or PILOTDB_MSSQL_* env vars)."
        )
    return cfg


@pytest.fixture(scope="module")
def mssql_tpch_loaded(mssql_config):
    from pilotdb.benchmarks.load_tpch_sqlserver import load as mssql_load
    duckdb_src = Path(__file__).resolve().parents[2] / "bench_out_full" / "tpch_sf1.duckdb"
    if not duckdb_src.exists():
        pytest.skip(
            f"DuckDB source for SQL Server load not found at {duckdb_src}. "
            f"Run `python -m pilotdb.benchmarks.run_duckdb_tpch --queries q6 --sf 1` first."
        )
    mssql_load(mssql_config, str(duckdb_src), sf=1, if_exists="skip")
    return mssql_config


# ------------------------------------------------------------------ helpers

def _relative_error(aqp_val: float, exact_val: float) -> float:
    if exact_val == 0:
        return 0.0 if aqp_val == 0 else float("inf")
    return abs(aqp_val - exact_val) / abs(exact_val)


def _q6_sql_mssql() -> str:
    """SQL Server doesn't accept Postgres-style INTERVAL; use DATEADD."""
    return """
        SELECT SUM(l_extendedprice * l_discount) AS revenue
        FROM lineitem
        WHERE l_shipdate >= '1994-01-01'
          AND l_shipdate < DATEADD(year, 1, '1994-01-01')
          AND l_discount BETWEEN 0.05 AND 0.07
          AND l_quantity < 24
    """


def _q1_sql_mssql() -> str:
    return """
        SELECT l_returnflag, l_linestatus,
               SUM(l_quantity) AS sum_qty,
               SUM(l_extendedprice) AS sum_base_price
        FROM lineitem
        WHERE l_shipdate <= DATEADD(day, -90, '1998-12-01')
        GROUP BY l_returnflag, l_linestatus
    """


# ------------------------------------------------------------------ tests

class TestExactBaseline:

    def test_q6_exact(self, mssql_tpch_loaded):
        df = exact_run(_q6_sql_mssql(), "sqlserver", config=mssql_tpch_loaded)
        assert len(df) == 1
        revenue = float(df["revenue"].iloc[0])
        assert 100_000_000 < revenue < 200_000_000

    def test_q1_exact(self, mssql_tpch_loaded):
        df = exact_run(_q1_sql_mssql(), "sqlserver", config=mssql_tpch_loaded)
        assert len(df) >= 3


class TestFullPipelineE2E:

    def test_q6_aqp_within_error_bounds(self, mssql_tpch_loaded, tmp_path, monkeypatch):
        from pilotdb.execute import execute_aqp
        monkeypatch.chdir(tmp_path)

        q6_sql = _q6_sql_mssql()
        exact_df = exact_run(q6_sql, "sqlserver", config=mssql_tpch_loaded)
        exact_revenue = float(exact_df["revenue"].iloc[0])

        query = Query(
            name="tpch-q6-mssql-e2e",
            query=q6_sql,
            table_cols={"lineitem": TPCH_TABLE_COLS["lineitem"]},
            table_size={"lineitem": TPCH_SF1_SIZES["lineitem"]},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = dict(mssql_tpch_loaded)
        db_config["dbms"] = "sqlserver"

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)
        fsr = timing.get("final_sample_rate")
        reason = timing.get("fallback_reason")

        assert aqp_df is not None
        assert len(aqp_df) > 0
        assert "revenue" in aqp_df.columns, (
            f"Q6 MSSQL must produce 'revenue' column; got {list(aqp_df.columns)}; "
            f"fsr={fsr}, reason={reason}"
        )

        aqp_revenue = float(aqp_df["revenue"].iloc[0])
        rel_err = _relative_error(aqp_revenue, exact_revenue)
        logging.info(
            f"Q6 MSSQL E2E: exact={exact_revenue:.2f} aqp={aqp_revenue:.2f} "
            f"rel_err={rel_err:.4f} fsr={fsr} reason={reason}"
        )
        if fsr == 1:
            assert rel_err < 1e-6
        else:
            assert rel_err < 0.10

    def test_q1_aqp_group_by_accuracy(self, mssql_tpch_loaded, tmp_path, monkeypatch):
        from pilotdb.execute import execute_aqp
        monkeypatch.chdir(tmp_path)

        q1_sql = _q1_sql_mssql()
        exact_df = exact_run(q1_sql, "sqlserver", config=mssql_tpch_loaded)

        query = Query(
            name="tpch-q1-mssql-e2e",
            query=q1_sql,
            table_cols={"lineitem": TPCH_TABLE_COLS["lineitem"]},
            table_size={"lineitem": TPCH_SF1_SIZES["lineitem"]},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = dict(mssql_tpch_loaded)
        db_config["dbms"] = "sqlserver"

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)
        fsr = timing.get("final_sample_rate")
        reason = timing.get("fallback_reason")
        assert aqp_df is not None
        assert len(aqp_df) > 0

        exact_total = float(exact_df["sum_qty"].sum())
        aqp_total = float(aqp_df["sum_qty"].sum())
        rel_err = _relative_error(aqp_total, exact_total)
        logging.info(
            f"Q1 MSSQL E2E: exact_total_qty={exact_total:.0f} "
            f"aqp_total_qty={aqp_total:.0f} rel_err={rel_err:.4f} "
            f"fsr={fsr} reason={reason}"
        )
        if fsr == 1:
            assert rel_err < 1e-6
        else:
            assert rel_err < 0.15

"""PostgreSQL TPC-H SF=1 E2E tests, mirroring `test_duckdb_tpch_e2e.py`.

Skipped wholesale if no reachable Postgres is found via either
`db_configs/postgres_local.yml` or the `PILOTDB_PG_HOST`/related env vars.
On a reachable instance, the fixture loads TPC-H SF=1 idempotently from
the DuckDB-generated source.
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


pytestmark = pytest.mark.postgres

warnings.simplefilter("ignore", UserWarning)
logging.basicConfig(level=logging.INFO)


def _read_pg_config() -> dict | None:
    """Load `db_configs/postgres_local.yml` with env var overrides, or None."""
    try:
        import yaml
    except ImportError:
        return None
    cfg_path = Path(__file__).resolve().parents[2] / "db_configs" / "postgres_local.yml"
    cfg: dict = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg["dbname"] = os.environ.get("PILOTDB_PG_DBNAME", cfg.get("dbname", "tpch"))
    cfg["username"] = os.environ.get("PILOTDB_PG_USER", cfg.get("username", "pilotdb"))
    cfg["host"] = os.environ.get("PILOTDB_PG_HOST", cfg.get("host", "localhost"))
    cfg["port"] = int(os.environ.get("PILOTDB_PG_PORT", cfg.get("port", 5432)))
    cfg["password"] = os.environ.get("PILOTDB_PG_PASSWORD", cfg.get("password"))
    return cfg


def _postgres_reachable(cfg: dict) -> bool:
    import socket
    try:
        s = socket.create_connection((cfg["host"], int(cfg["port"])), timeout=2)
        s.close()
    except Exception:
        return False
    # Also verify auth + DB.
    try:
        import psycopg2
        kwargs = dict(
            dbname=cfg["dbname"],
            user=cfg["username"],
            host=cfg["host"],
            port=int(cfg["port"]),
            connect_timeout=2,
        )
        if cfg.get("password"):
            kwargs["password"] = cfg["password"]
        conn = psycopg2.connect(**kwargs)
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def pg_config():
    cfg = _read_pg_config()
    if cfg is None:
        pytest.skip("PyYAML not available")
    if not _postgres_reachable(cfg):
        pytest.skip(
            f"Postgres not reachable at {cfg['host']}:{cfg['port']} "
            f"(set PILOTDB_PG_HOST or start docker compose postgres)."
        )
    return cfg


@pytest.fixture(scope="module")
def pg_tpch_loaded(pg_config):
    """Ensure the target Postgres has TPC-H SF=1 loaded."""
    from pilotdb.benchmarks.load_tpch_postgres import load as pg_load
    duckdb_src = Path(__file__).resolve().parents[2] / "bench_out_full" / "tpch_sf1.duckdb"
    if not duckdb_src.exists():
        pytest.skip(
            f"DuckDB source for PG load not found at {duckdb_src}. "
            f"Run `python -m pilotdb.benchmarks.run_duckdb_tpch --queries q6 --sf 1` first."
        )
    pg_load(pg_config, str(duckdb_src), sf=1, if_exists="skip")
    return pg_config


# ------------------------------------------------------------------ helpers

def _relative_error(aqp_val: float, exact_val: float) -> float:
    if exact_val == 0:
        return 0.0 if aqp_val == 0 else float("inf")
    return abs(aqp_val - exact_val) / abs(exact_val)


# ------------------------------------------------------------------ tests

class TestExactBaseline:

    def test_q6_exact(self, pg_tpch_loaded):
        sql = """
            SELECT SUM(l_extendedprice * l_discount) AS revenue
            FROM lineitem
            WHERE l_shipdate >= date '1994-01-01'
              AND l_shipdate < date '1994-01-01' + interval '1' year
              AND l_discount BETWEEN 0.05 AND 0.07
              AND l_quantity < 24
        """
        df = exact_run(sql, "postgres", config=pg_tpch_loaded)
        assert len(df) == 1
        revenue = float(df["revenue"].iloc[0])
        assert 100_000_000 < revenue < 200_000_000

    def test_q1_exact(self, pg_tpch_loaded):
        sql = """
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty,
                   SUM(l_extendedprice) AS sum_base_price,
                   COUNT(*) AS count_order
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
            ORDER BY l_returnflag, l_linestatus
        """
        df = exact_run(sql, "postgres", config=pg_tpch_loaded)
        assert len(df) >= 3
        assert df["count_order"].sum() > 5_000_000


class TestFullPipelineE2E:
    """Full execute_aqp vs exact comparison on Postgres."""

    def test_q6_aqp_within_error_bounds(self, pg_tpch_loaded, tmp_path, monkeypatch):
        from pilotdb.execute import execute_aqp
        monkeypatch.chdir(tmp_path)

        q6_sql = """
            SELECT SUM(l_extendedprice * l_discount) AS revenue
            FROM lineitem
            WHERE l_shipdate >= date '1994-01-01'
              AND l_shipdate < date '1994-01-01' + interval '1' year
              AND l_discount BETWEEN 0.05 AND 0.07
              AND l_quantity < 24
        """
        exact_df = exact_run(q6_sql, "postgres", config=pg_tpch_loaded)
        exact_revenue = float(exact_df["revenue"].iloc[0])

        query = Query(
            name="tpch-q6-pg-e2e",
            query=q6_sql,
            table_cols={"lineitem": TPCH_TABLE_COLS["lineitem"]},
            table_size={"lineitem": TPCH_SF1_SIZES["lineitem"]},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = dict(pg_tpch_loaded)
        db_config["dbms"] = "postgres"

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)
        fsr = timing.get("final_sample_rate")
        reason = timing.get("fallback_reason")

        assert aqp_df is not None
        assert len(aqp_df) > 0
        assert "revenue" in aqp_df.columns, (
            f"Q6 PG must produce 'revenue' column; got {list(aqp_df.columns)}; "
            f"fsr={fsr}, reason={reason}"
        )

        aqp_revenue = float(aqp_df["revenue"].iloc[0])
        rel_err = _relative_error(aqp_revenue, exact_revenue)
        logging.info(
            f"Q6 PG E2E: exact={exact_revenue:.2f} aqp={aqp_revenue:.2f} "
            f"rel_err={rel_err:.4f} fsr={fsr} reason={reason}"
        )
        if fsr == 1:
            assert rel_err < 1e-6, (
                f"Q6 PG exact fallback should be numerically equal; got {rel_err}"
            )
        else:
            assert rel_err < 0.10, (
                f"Q6 PG AQP rel_err {rel_err:.4f} > 10%"
            )

    def test_q1_aqp_group_by_accuracy(self, pg_tpch_loaded, tmp_path, monkeypatch):
        from pilotdb.execute import execute_aqp
        monkeypatch.chdir(tmp_path)

        q1_sql = """
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty,
                   SUM(l_extendedprice) AS sum_base_price
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
        """
        exact_df = exact_run(q1_sql, "postgres", config=pg_tpch_loaded)

        query = Query(
            name="tpch-q1-pg-e2e",
            query=q1_sql,
            table_cols={"lineitem": TPCH_TABLE_COLS["lineitem"]},
            table_size={"lineitem": TPCH_SF1_SIZES["lineitem"]},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = dict(pg_tpch_loaded)
        db_config["dbms"] = "postgres"

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)
        fsr = timing.get("final_sample_rate")
        reason = timing.get("fallback_reason")
        assert aqp_df is not None
        assert len(aqp_df) > 0

        # Total sum_qty comparison (same convention as DuckDB Q1 test).
        exact_total = float(exact_df["sum_qty"].sum())
        aqp_total = float(aqp_df["sum_qty"].sum())
        rel_err = _relative_error(aqp_total, exact_total)
        logging.info(
            f"Q1 PG E2E: exact_total_qty={exact_total:.0f} "
            f"aqp_total_qty={aqp_total:.0f} rel_err={rel_err:.4f} "
            f"fsr={fsr} reason={reason}"
        )
        if fsr == 1:
            assert rel_err < 1e-6
        else:
            assert rel_err < 0.15

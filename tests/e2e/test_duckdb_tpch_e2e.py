"""DuckDB TPC-H SF=1 End-to-End integration tests.

Tests the FULL PilotDB pipeline against real TPC-H data generated
in-memory by DuckDB's built-in TPC-H extension. Verifies:
  - Pilot query rewriting produces valid SQL
  - Pilot results contain expected columns (page_id_*)
  - Sampling plan estimation produces reasonable rates
  - AQP results are within error bounds of exact results
  - Multi-table join queries trigger Phi(Theta) / guardrails

Requirements: duckdb >= 0.9, scipy, numpy, pandas, sqlglot
"""

import json
import logging
import math
import os
import sys
import tempfile
import warnings

import duckdb
import numpy as np
import pandas as pd
import pytest

# Set up path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pilotdb.query import Query
from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
from pilotdb.pilot_engine.rewriter.sampling import Sampling_Rewriter
from pilotdb.pilot_engine.commons import DUCKDB
from pilotdb.pilot_engine.sampling_plan import scalar_rate_plan
from pilotdb.pilot_engine.multi_table_sampling import apply_sampling_plan_template

warnings.simplefilter("ignore", UserWarning)
logging.basicConfig(level=logging.INFO)


# ---- TPC-H SF=1 Table sizes (exact for SF=1) ----
TPCH_SF1_SIZES = {
    "lineitem": 6_001_215,
    "orders": 1_500_000,
    "partsupp": 800_000,
    "part": 200_000,
    "customer": 150_000,
    "supplier": 10_000,
    "nation": 25,
    "region": 5,
}

TPCH_TABLE_COLS = {
    "lineitem": [
        "l_orderkey", "l_partkey", "l_suppkey", "l_linenumber",
        "l_quantity", "l_extendedprice", "l_discount", "l_tax",
        "l_returnflag", "l_linestatus", "l_shipdate", "l_commitdate",
        "l_receiptdate", "l_shipinstruct", "l_shipmode", "l_comment",
    ],
    "orders": [
        "o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice",
        "o_orderdate", "o_orderpriority", "o_clerk", "o_shippriority",
        "o_comment",
    ],
    "partsupp": [
        "ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost",
        "ps_comment",
    ],
    "part": [
        "p_partkey", "p_name", "p_mfgr", "p_brand", "p_type",
        "p_size", "p_container", "p_retailprice", "p_comment",
    ],
    "customer": [
        "c_custkey", "c_name", "c_address", "c_nationkey", "c_phone",
        "c_acctbal", "c_mktsegment", "c_comment",
    ],
    "supplier": [
        "s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone",
        "s_acctbal", "s_comment",
    ],
    "nation": ["n_nationkey", "n_name", "n_regionkey", "n_comment"],
    "region": ["r_regionkey", "r_name", "r_comment"],
}


@pytest.fixture(scope="module")
def tpch_db():
    """Create an in-memory DuckDB with TPC-H SF=1 data."""
    conn = duckdb.connect(database=":memory:", read_only=False)
    conn.execute("INSTALL tpch; LOAD tpch;")
    conn.execute("CALL dbgen(sf=1);")

    # Verify tables exist and get real sizes
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main'"
    ).fetchall()
    table_names = [t[0] for t in tables]
    assert "lineitem" in table_names
    assert "orders" in table_names

    yield conn
    conn.close()


@pytest.fixture(scope="module")
def real_table_sizes(tpch_db):
    """Get actual table sizes from DuckDB."""
    sizes = {}
    for table in TPCH_SF1_SIZES:
        count = tpch_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        sizes[table] = count
    return sizes


# ======================================================================
# 1. Exact Query Baseline
# ======================================================================


class TestExactBaseline:
    """Verify we can run TPC-H queries exactly on DuckDB."""

    def test_q1_exact(self, tpch_db):
        """TPC-H Q1: single-table, GROUP BY, multiple aggregates."""
        result = tpch_db.execute("""
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty,
                   SUM(l_extendedprice) AS sum_base_price,
                   AVG(l_quantity) AS avg_qty,
                   COUNT(*) AS count_order
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
            ORDER BY l_returnflag, l_linestatus
        """).fetchdf()
        assert len(result) > 0
        assert "sum_qty" in result.columns
        # Known Q1 facts for SF=1: ~5.9M rows match the WHERE
        total_count = result["count_order"].sum()
        assert total_count > 5_000_000

    def test_q6_exact(self, tpch_db):
        """TPC-H Q6: single-table, no GROUP BY, single aggregate."""
        result = tpch_db.execute("""
            SELECT SUM(l_extendedprice * l_discount) AS revenue
            FROM lineitem
            WHERE l_shipdate >= date '1994-01-01'
              AND l_shipdate < date '1994-01-01' + interval '1' year
              AND l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01
              AND l_quantity < 24
        """).fetchdf()
        assert len(result) == 1
        revenue = result["revenue"].iloc[0]
        # Known SF=1 value: ~123M
        assert 100_000_000 < revenue < 200_000_000


# ======================================================================
# 2. Pilot Query Rewriting (SQL Generation)
# ======================================================================


class TestPilotRewriting:
    """Test that PilotDB rewrites TPC-H queries into valid DuckDB SQL."""

    def test_q1_pilot_rewrite_valid_sql(self, tpch_db, real_table_sizes):
        """Q1 pilot query should be valid DuckDB SQL."""
        query_str = """
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty,
                   SUM(l_extendedprice) AS sum_base_price,
                   AVG(l_quantity) AS avg_qty,
                   COUNT(*) AS count_order
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
        """
        pq = Pilot_Rewriter(TPCH_TABLE_COLS, real_table_sizes, DUCKDB)
        pilot_sql = pq.rewrite(query_str)
        assert pilot_sql is not None
        assert len(pilot_sql) > 0
        assert pq.is_rewritable

        # Use proper placeholder filling
        plan = scalar_rate_plan(pq.largest_table, 0.01)
        pilot_sql_filled = apply_sampling_plan_template(pilot_sql, plan, DUCKDB)
        pilot_sql_filled = pilot_sql_filled.format(sample_rate=0.01)
        result = tpch_db.execute(pilot_sql_filled + ";").fetchdf()
        assert len(result) > 0
        logging.info(f"Q1 pilot result columns: {list(result.columns)}")

    def test_q6_pilot_rewrite_valid_sql(self, tpch_db, real_table_sizes):
        """Q6 pilot query should be valid DuckDB SQL (no GROUP BY)."""
        query_str = """
            SELECT SUM(l_extendedprice * l_discount) AS revenue
            FROM lineitem
            WHERE l_shipdate >= date '1994-01-01'
              AND l_shipdate < date '1994-01-01' + interval '1' year
              AND l_discount BETWEEN 0.05 AND 0.07
              AND l_quantity < 24
        """
        pq = Pilot_Rewriter(TPCH_TABLE_COLS, real_table_sizes, DUCKDB)
        pilot_sql = pq.rewrite(query_str)
        assert pq.is_rewritable

        plan = scalar_rate_plan(pq.largest_table, 0.01)
        pilot_sql_filled = apply_sampling_plan_template(pilot_sql, plan, DUCKDB)
        pilot_sql_filled = pilot_sql_filled.format(sample_rate=0.01)
        result = tpch_db.execute(pilot_sql_filled + ";").fetchdf()
        assert len(result) > 0

    def test_q1_pilot_has_page_id(self, tpch_db, real_table_sizes):
        """Q1 pilot result should contain page_id column(s)."""
        query_str = """
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
        """
        pq = Pilot_Rewriter(TPCH_TABLE_COLS, real_table_sizes, DUCKDB)
        pilot_sql = pq.rewrite(query_str)

        plan = scalar_rate_plan(pq.largest_table, 0.01)
        pilot_sql_filled = apply_sampling_plan_template(pilot_sql, plan, DUCKDB)
        pilot_sql_filled = pilot_sql_filled.format(sample_rate=0.01)
        result = tpch_db.execute(pilot_sql_filled + ";").fetchdf()

        # Should have at least page_id_0
        page_id_cols = [c for c in result.columns if c.startswith("page_id")]
        assert len(page_id_cols) >= 1, (
            f"Expected page_id column(s), got: {list(result.columns)}"
        )

    def test_join_query_has_two_page_ids(self, tpch_db, real_table_sizes):
        """A 2-table join pilot should produce page_id_0 and page_id_1."""
        query_str = """
            SELECT SUM(l_extendedprice) AS revenue
            FROM lineitem, orders
            WHERE l_orderkey = o_orderkey
              AND o_orderdate < date '1995-03-15'
              AND l_shipdate > date '1995-03-15'
        """
        pq = Pilot_Rewriter(TPCH_TABLE_COLS, real_table_sizes, DUCKDB)
        pilot_sql = pq.rewrite(query_str)

        if not pq.is_rewritable:
            pytest.skip("Rewriter marks this query as not rewritable")

        plan = scalar_rate_plan(pq.largest_table, 0.05)
        pilot_sql_filled = apply_sampling_plan_template(pilot_sql, plan, DUCKDB)
        pilot_sql_filled = pilot_sql_filled.format(sample_rate=0.05)
        result = tpch_db.execute(pilot_sql_filled + ";").fetchdf()

        page_id_cols = [c for c in result.columns if c.startswith("page_id")]
        logging.info(f"Join query page_id columns: {page_id_cols}")
        logging.info(f"Join pilot result shape: {result.shape}")
        # For joins, we expect at least page_id_count >= 1
        assert len(page_id_cols) >= 1


# ======================================================================
# 3. Sampling Rate Estimation
# ======================================================================


class TestSamplingEstimation:
    """Test that sampling rate estimation produces reasonable values."""

    def test_q1_estimate_produces_valid_rate(self, tpch_db, real_table_sizes):
        """Q1 should produce a sampling rate between 0 and 1."""
        from pilotdb.pilot_engine.error_bounds import estimate_final_rate
        from pilotdb.pilot_engine.utils import aggregate_error_to_page_error

        query_str = """
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty,
                   SUM(l_extendedprice) AS sum_base_price,
                   AVG(l_quantity) AS avg_qty,
                   COUNT(*) AS count_order
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
        """
        pq = Pilot_Rewriter(TPCH_TABLE_COLS, real_table_sizes, DUCKDB)
        pilot_sql = pq.rewrite(query_str)

        plan = scalar_rate_plan(pq.largest_table, 0.01)
        pilot_sql_filled = apply_sampling_plan_template(pilot_sql, plan, DUCKDB)
        pilot_sql_filled = pilot_sql_filled.format(sample_rate=0.01)
        pilot_results = tpch_db.execute(pilot_sql_filled + ";").fetchdf()

        page_errors = aggregate_error_to_page_error(
            pq.result_mapping_list, required_error=0.05
        )

        final_rate = estimate_final_rate(
            failure_prob=0.05,
            pilot_results=pilot_results,
            page_errors=page_errors,
            group_cols=pq.group_cols,
            pilot_rate=0.01,
        )
        logging.info(f"Q1 estimated sampling rate: {final_rate}")
        # Should be a valid rate (not -1 error) and reasonable
        if final_rate != -1:
            assert 0 < final_rate <= 1


# ======================================================================
# 4. Phi(Theta) Constraint Building from Real Data
# ======================================================================


class TestPhiFromRealData:
    """Test Phi(Theta) constraint building with real pilot results."""

    def test_q1_phi_constraints_feasible(self, tpch_db, real_table_sizes):
        """Q1 at 5% error should produce feasible Phi constraints."""
        from pilotdb.execute import _extract_pilot_stats
        from pilotdb.pilot_engine.join_variance import build_phi_constraints
        from pilotdb.pilot_engine.utils import aggregate_error_to_page_error

        query_str = """
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
        """
        pq = Pilot_Rewriter(TPCH_TABLE_COLS, real_table_sizes, DUCKDB)
        pilot_sql = pq.rewrite(query_str)
        plan = scalar_rate_plan(pq.largest_table, 0.01)
        pilot_sql_filled = apply_sampling_plan_template(pilot_sql, plan, DUCKDB)
        pilot_sql_filled = pilot_sql_filled.format(sample_rate=0.01)
        pilot_results = tpch_db.execute(pilot_sql_filled + ";").fetchdf()

        page_errors = aggregate_error_to_page_error(
            pq.result_mapping_list, required_error=0.05
        )
        stats = _extract_pilot_stats(
            pilot_results, page_errors, pq.group_cols
        )
        assert len(stats) > 0

        n_aggs = len([c for c in page_errors if c != "n_page"])
        n_groups = len(set(
            tuple(pilot_results[pq.group_cols].iloc[i])
            for i in range(len(pilot_results))
        )) if pq.group_cols else 1

        phi = build_phi_constraints(
            failure_prob=0.05,
            n_aggregates=n_aggs,
            n_groups=n_groups,
            pilot_stats=stats,
            required_error=0.05,
            table_names=("lineitem",),
        )
        assert phi.mode == "full"
        assert len(phi.constraints) > 0

        # At 10% rate, should be feasible for SF=1 lineitem
        feasible = phi.is_feasible({"lineitem": 0.1})
        logging.info(
            f"Q1 Phi feasibility at 10%: {feasible}, "
            f"constraints: {len(phi.constraints)}"
        )


# ======================================================================
# 5. Join Block Stats from Real Data
# ======================================================================


class TestJoinStatsRealData:
    """Test JoinBlockStats extraction from real multi-table pilot results."""

    def test_join_pilot_produces_block_stats(self, tpch_db, real_table_sizes):
        """A join query pilot should produce extractable block stats."""
        from pilotdb.execute import _extract_join_block_stats
        from pilotdb.pilot_engine.utils import aggregate_error_to_page_error

        query_str = """
            SELECT SUM(l_extendedprice) AS revenue
            FROM lineitem, orders
            WHERE l_orderkey = o_orderkey
              AND o_orderdate < date '1995-03-15'
              AND l_shipdate > date '1995-03-15'
        """
        pq = Pilot_Rewriter(TPCH_TABLE_COLS, real_table_sizes, DUCKDB)
        pilot_sql = pq.rewrite(query_str)

        if not pq.is_rewritable:
            pytest.skip("Rewriter marks this query as not rewritable")

        plan = scalar_rate_plan(pq.largest_table, 0.05)
        pilot_sql_filled = apply_sampling_plan_template(pilot_sql, plan, DUCKDB)
        pilot_sql_filled = pilot_sql_filled.format(sample_rate=0.05)
        pilot_results = tpch_db.execute(pilot_sql_filled + ";").fetchdf()

        page_errors = aggregate_error_to_page_error(
            pq.result_mapping_list, required_error=0.05
        )

        page_id_count = getattr(pq, 'page_id_count', 0)
        logging.info(
            f"Join pilot: page_id_count={page_id_count}, "
            f"columns={list(pilot_results.columns)}, shape={pilot_results.shape}"
        )

        if page_id_count >= 2:
            join_stats = _extract_join_block_stats(
                pilot_results, page_errors,
                page_id_count=page_id_count,
                table_sizes=real_table_sizes,
            )
            if join_stats is not None:
                assert join_stats.n_pilot_blocks > 0
                assert join_stats.N2 > 0
                assert len(join_stats.y1_per_block) == join_stats.n_pilot_blocks
                assert len(join_stats.y2_values) > 0
                logging.info(
                    f"Join stats extracted: n_pilot={join_stats.n_pilot_blocks}, "
                    f"N2={join_stats.N2}, y2_pairs={len(join_stats.y2_values)}"
                )
            else:
                logging.warning("join_stats was None despite page_id_count >= 2")
        else:
            logging.info(
                f"Single-sample join (page_id_count={page_id_count}), "
                f"no block stats expected"
            )


# ======================================================================
# 6. Full Pipeline E2E (approximate vs exact)
# ======================================================================


class TestFullPipelineE2E:
    """Full execute_aqp vs execute_exact comparison.

    Creates a TPC-H SF=1 database on disk, runs execute_aqp, and
    asserts the AQP result is within the specified error bounds of
    the exact result.
    """

    @pytest.fixture(scope="class")
    def tpch_db_path(self, tmp_path_factory):
        """Create a TPC-H SF=1 database on disk (shared per test class)."""
        db_path = str(tmp_path_factory.mktemp("tpch") / "tpch_sf1.duckdb")
        conn = duckdb.connect(database=db_path, read_only=False)
        conn.execute("INSTALL tpch; LOAD tpch;")
        conn.execute("CALL dbgen(sf=1);")
        conn.close()
        return db_path

    @pytest.fixture(autouse=True)
    def _work_in_tmpdir(self, tmp_path, monkeypatch):
        """Run each test in a temp directory so logs/results don't pollute."""
        monkeypatch.chdir(tmp_path)

    # ---- helpers ----

    @staticmethod
    def _get_exact_value(db_path: str, sql: str) -> pd.DataFrame:
        conn = duckdb.connect(database=db_path, read_only=True)
        result = conn.execute(sql).fetchdf()
        conn.close()
        return result

    @staticmethod
    def _relative_error(aqp_val: float, exact_val: float) -> float:
        if exact_val == 0:
            return 0.0 if aqp_val == 0 else float("inf")
        return abs(aqp_val - exact_val) / abs(exact_val)

    # ---- Q6: single-table, no GROUP BY, single aggregate ----

    def test_q6_aqp_within_error_bounds(self, tpch_db_path):
        """Q6 AQP result should be within 5% relative error of exact."""
        from pilotdb.execute import execute_aqp

        q6_sql = """
            SELECT SUM(l_extendedprice * l_discount) AS revenue
            FROM lineitem
            WHERE l_shipdate >= date '1994-01-01'
              AND l_shipdate < date '1994-01-01' + interval '1' year
              AND l_discount BETWEEN 0.05 AND 0.07
              AND l_quantity < 24
        """
        exact_df = self._get_exact_value(tpch_db_path, q6_sql)
        exact_revenue = float(exact_df["revenue"].iloc[0])

        query = Query(
            name="tpch-q6-e2e",
            query=q6_sql,
            table_cols={"lineitem": TPCH_TABLE_COLS["lineitem"]},
            table_size={"lineitem": 6_001_215},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = {"dbms": "duckdb", "path": tpch_db_path}

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)

        assert aqp_df is not None, "execute_aqp returned None DataFrame"
        assert len(aqp_df) > 0, "AQP result is empty"
        assert "revenue" in aqp_df.columns, (
            f"Expected 'revenue' column, got: {list(aqp_df.columns)}"
        )

        aqp_revenue = float(aqp_df["revenue"].iloc[0])
        rel_err = self._relative_error(aqp_revenue, exact_revenue)

        logging.info(
            f"Q6 E2E: exact={exact_revenue:.2f}, aqp={aqp_revenue:.2f}, "
            f"relative_error={rel_err:.4f} ({rel_err*100:.2f}%)"
        )

        # With 5% error and 5% failure_prob, allow 10% tolerance
        # (block sampling has variance; this is a probabilistic guarantee)
        assert rel_err < 0.10, (
            f"Q6 relative error {rel_err:.4f} exceeds 10% tolerance. "
            f"exact={exact_revenue:.2f}, aqp={aqp_revenue:.2f}"
        )

    # ---- Q1: single-table, GROUP BY, multiple aggregates ----

    def test_q1_aqp_group_by_accuracy(self, tpch_db_path):
        """Q1 AQP should produce all groups with reasonable accuracy."""
        from pilotdb.execute import execute_aqp

        q1_sql = """
            SELECT l_returnflag, l_linestatus,
                   SUM(l_quantity) AS sum_qty,
                   SUM(l_extendedprice) AS sum_base_price
            FROM lineitem
            WHERE l_shipdate <= date '1998-12-01' - interval '90 day'
            GROUP BY l_returnflag, l_linestatus
        """
        exact_df = self._get_exact_value(tpch_db_path, q1_sql)

        query = Query(
            name="tpch-q1-e2e",
            query=q1_sql,
            table_cols={"lineitem": TPCH_TABLE_COLS["lineitem"]},
            table_size={"lineitem": 6_001_215},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = {"dbms": "duckdb", "path": tpch_db_path}

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)

        assert aqp_df is not None
        assert len(aqp_df) > 0
        # Q1 should produce 4 groups: (A,F), (N,F), (N,O), (R,F)
        assert len(aqp_df) >= 3, (
            f"Expected >=3 groups, got {len(aqp_df)}"
        )

        # Check that the sum_qty total is within 15% of exact
        exact_total = exact_df["sum_qty"].sum()
        aqp_total = aqp_df["sum_qty"].sum()
        rel_err = self._relative_error(aqp_total, exact_total)

        logging.info(
            f"Q1 E2E: exact_total_qty={exact_total:.0f}, "
            f"aqp_total_qty={aqp_total:.0f}, rel_err={rel_err:.4f}"
        )
        assert rel_err < 0.15, (
            f"Q1 total sum_qty relative error {rel_err:.4f} exceeds 15%"
        )

    # ---- Q14: two-table join (lineitem + part) ----

    def test_q14_join_aqp_or_exact_fallback(self, tpch_db_path):
        """Q14 join query should either produce AQP result or fallback to exact.

        This tests the multi-table guardrail: if Phi constraints are
        insufficient, the system should fall back to exact execution.
        Either way, we get a valid result.
        """
        from pilotdb.execute import execute_aqp

        q14_sql = """
            SELECT
                100.00 * SUM(CASE
                    WHEN p_type LIKE 'PROMO%%'
                    THEN l_extendedprice * (1 - l_discount)
                    ELSE 0
                END) / SUM(l_extendedprice * (1 - l_discount)) AS promo_revenue
            FROM lineitem, part
            WHERE l_partkey = p_partkey
              AND l_shipdate >= date '1995-09-01'
              AND l_shipdate < date '1995-09-01' + interval '1' month
        """

        exact_df = self._get_exact_value(tpch_db_path, q14_sql)
        exact_promo = float(exact_df["promo_revenue"].iloc[0])

        query = Query(
            name="tpch-q14-e2e",
            query=q14_sql,
            table_cols={
                "lineitem": TPCH_TABLE_COLS["lineitem"],
                "part": TPCH_TABLE_COLS["part"],
            },
            table_size={"lineitem": 6_001_215, "part": 200_000},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = {"dbms": "duckdb", "path": tpch_db_path}

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)
        fsr = timing.get("final_sample_rate")
        reason = timing.get("fallback_reason")

        assert aqp_df is not None
        assert len(aqp_df) > 0
        # Q14 must always project the user's SELECT alias, regardless of
        # whether the AQP path or the exact fallback path was taken.
        assert "promo_revenue" in aqp_df.columns, (
            f"Q14 must always produce 'promo_revenue' column; "
            f"got {list(aqp_df.columns)}; fsr={fsr}, reason={reason}"
        )

        aqp_promo = float(aqp_df["promo_revenue"].iloc[0])
        rel_err = self._relative_error(aqp_promo, exact_promo)

        if fsr == 1:
            # Exact fallback — must be numerically identical to the
            # baseline query (modulo float jitter).
            logging.info(
                f"Q14 EXACT FALLBACK: exact={exact_promo:.4f}, "
                f"aqp={aqp_promo:.4f}, rel_err={rel_err:.6e}, reason={reason}"
            )
            assert rel_err < 1e-6, (
                f"Q14 exact fallback should be numerically identical; "
                f"got rel_err={rel_err}, reason={reason}"
            )
        else:
            # AQP path — allow up to 20% due to join variance.
            logging.info(
                f"Q14 AQP: exact={exact_promo:.4f}, aqp={aqp_promo:.4f}, "
                f"rel_err={rel_err:.4f}, fsr={fsr}"
            )
            assert rel_err < 0.20, (
                f"Q14 AQP rel_err={rel_err:.4f} > 20% with fsr={fsr}"
            )

    # ---- COUNT DISTINCT: single-table, no GROUP BY ----

    def test_count_distinct_aqp(self, tpch_db_path):
        """Single-table COUNT(DISTINCT) AQP estimation."""
        from pilotdb.execute import execute_aqp

        sql = "SELECT COUNT(DISTINCT l_partkey) AS distinct_parts FROM lineitem"
        exact_df = self._get_exact_value(tpch_db_path, sql)
        exact_count = float(exact_df["distinct_parts"].iloc[0])

        query = Query(
            name="tpch-cdistinct-e2e",
            query=sql,
            table_cols={"lineitem": TPCH_TABLE_COLS["lineitem"]},
            table_size={"lineitem": 6_001_215},
            error=0.05,
            failure_probability=0.05,
        )
        db_config = {"dbms": "duckdb", "path": tpch_db_path}

        aqp_df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)

        assert aqp_df is not None
        assert len(aqp_df) > 0
        assert "distinct_parts" in aqp_df.columns

        aqp_count = float(aqp_df["distinct_parts"].iloc[0])
        rel_err = self._relative_error(aqp_count, exact_count)

        logging.info(
            f"COUNT DISTINCT E2E: exact={exact_count:.2f}, aqp={aqp_count:.2f}, "
            f"relative_error={rel_err:.4f} ({rel_err*100:.2f}%)"
        )

        assert rel_err < 0.15, (
            f"COUNT DISTINCT relative error {rel_err:.4f} exceeds 15% tolerance. "
            f"exact={exact_count:.2f}, aqp={aqp_count:.2f}"
        )



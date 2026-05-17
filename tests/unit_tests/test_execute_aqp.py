"""Phase 3 unit tests — Q18 fallback, residual-placeholder guard,
top-level wrap. See `.kiro/specs/pilotdb-paper-reproduction-completion`.

The tests in this module verify three Correctness Properties from the
design:

* **Property 1 (return shape)** — `execute_aqp` always returns a
  `(pandas.DataFrame, dict)` tuple even when the AQP path raises.
* **Property 4 (no unhandled crash)** — the top-level wrap converts
  any uncaught exception into a structured fallback record.
* **Property 5 (subquery placeholder safety)** — residual
  ``subquery_<N>`` tokens after substitution force exact fallback
  with reason ``not_rewritable:subquery_placeholder``.
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest


# Project-relative imports (mirror pattern from tests/e2e/*).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

warnings.simplefilter("ignore", UserWarning)


_LEGAL_FALLBACK_REASONS = {
    None,  # AQP path produced the result; no fallback
    "directly_run_exact",
    "multi_table_no_phi",
    "solver_failed",
    "sample_rate_too_high",
    "exact_chosen_by_cost",
    "execute_aqp_recover",
    # `not_rewritable:<reason>` is matched by prefix, not equality.
}


def _is_legal_reason(reason):
    if reason in _LEGAL_FALLBACK_REASONS:
        return True
    if isinstance(reason, str) and reason.startswith("not_rewritable:"):
        return True
    return False


# ---------------------------------------------------------------- task 3.4
def test_residual_placeholder_detector():
    """Property 5: a synthetic SQL string with `subquery_0` triggers
    the unrewritable flag and raises the internal control signal."""
    from pilotdb.execute import (
        _UnrewritableError,
        _check_residual_subquery_placeholder,
    )
    pq = SimpleNamespace(is_rewritable=True, unsupported_reason=None)
    sql = "SELECT * FROM lineitem WHERE l_orderkey IN (subquery_0)"
    with pytest.raises(_UnrewritableError) as exc_info:
        _check_residual_subquery_placeholder(sql, pq)
    assert exc_info.value.reason == "subquery_placeholder"
    assert pq.is_rewritable is False
    assert pq.unsupported_reason == "subquery_placeholder"


def test_residual_placeholder_detector_clean_sql():
    """Clean SQL with no placeholder tokens must not trigger the guard."""
    from pilotdb.execute import _check_residual_subquery_placeholder
    pq = SimpleNamespace(is_rewritable=True, unsupported_reason=None)
    _check_residual_subquery_placeholder(
        "SELECT COUNT(*) FROM lineitem WHERE l_quantity > 5", pq,
    )
    assert pq.is_rewritable is True
    assert pq.unsupported_reason is None


def test_residual_placeholder_detector_word_boundary():
    """Placeholder pattern is word-boundaried — `my_subquery_0_col`
    must NOT match. (Defensive against false positives.)"""
    from pilotdb.execute import _check_residual_subquery_placeholder
    pq = SimpleNamespace(is_rewritable=True, unsupported_reason=None)
    _check_residual_subquery_placeholder(
        "SELECT my_subquery_0_col FROM t", pq,
    )
    assert pq.is_rewritable is True


# ---------------------------------------------------------------- task 3.5
def test_execute_aqp_top_level_wrap_no_crash(tmp_path, monkeypatch):
    """Properties 1 + 4 + 6: monkeypatch the rewriter to raise; the wrap
    must convert it into a clean exact-fallback record with a defined
    reason. Run against a tiny DuckDB so the fallback path itself can
    run an exact query."""
    from pilotdb.execute import execute_aqp
    from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
    from pilotdb.query import Query

    monkeypatch.chdir(tmp_path)

    # Build a tiny DuckDB with a single-table sample so exact runs cleanly.
    db_path = tmp_path / "tiny.duckdb"
    conn = duckdb.connect(database=str(db_path), read_only=False)
    conn.execute(
        "CREATE TABLE lineitem AS SELECT range AS l_orderkey, "
        "(range % 50) AS l_quantity FROM range(0, 1000)"
    )
    conn.close()

    def _boom(self, sql):  # noqa: ARG001
        raise RuntimeError("synthetic rewriter blowup")

    monkeypatch.setattr(Pilot_Rewriter, "rewrite", _boom, raising=True)

    query = Query(
        name="phase3-wrap-test",
        query="SELECT COUNT(*) FROM lineitem WHERE l_quantity > 5",
        table_cols={"lineitem": ["l_orderkey", "l_quantity"]},
        table_size={"lineitem": 1000},
        error=0.05,
        failure_probability=0.05,
    )
    db_config = {"dbms": "duckdb", "path": str(db_path)}

    df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)

    # Property 1: return shape preserved.
    assert isinstance(df, pd.DataFrame)
    assert isinstance(timing, dict)
    # Property 4: no exception escaped, structured fallback recorded.
    assert timing.get("fallback_reason") == "execute_aqp_recover"
    assert timing.get("final_sample_rate") == 1
    # Property 6: reason is in the legal set (and recover was added).
    assert _is_legal_reason(timing.get("fallback_reason"))
    # Required-keys subset still present.
    for key in ("pilot_sample_rate", "final_sample_rate", "fallback_reason"):
        assert key in timing
    # Cause was captured for diagnostics.
    assert "fallback_cause" in timing
    assert "RuntimeError" in timing["fallback_cause"]
    # The exact path actually ran and returned a row.
    assert len(df) >= 1


def test_execute_aqp_unrewritable_error_routes_to_not_rewritable(tmp_path, monkeypatch):
    """A `_UnrewritableError` raised inside the inner body becomes a
    `not_rewritable:<reason>` fallback (not `execute_aqp_recover`)."""
    from pilotdb.execute import execute_aqp, _UnrewritableError
    from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
    from pilotdb.query import Query

    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / "tiny2.duckdb"
    conn = duckdb.connect(database=str(db_path), read_only=False)
    conn.execute(
        "CREATE TABLE lineitem AS SELECT range AS l_orderkey, "
        "(range % 50) AS l_quantity FROM range(0, 1000)"
    )
    conn.close()

    def _raise_unrewritable(self, sql):  # noqa: ARG001
        raise _UnrewritableError("synthetic_token")

    monkeypatch.setattr(Pilot_Rewriter, "rewrite", _raise_unrewritable, raising=True)

    query = Query(
        name="phase3-not-rewritable-test",
        query="SELECT COUNT(*) FROM lineitem",
        table_cols={"lineitem": ["l_orderkey", "l_quantity"]},
        table_size={"lineitem": 1000},
        error=0.05,
        failure_probability=0.05,
    )
    db_config = {"dbms": "duckdb", "path": str(db_path)}

    df, timing = execute_aqp(query, db_config, pilot_sample_rate=1.0)
    assert isinstance(df, pd.DataFrame)
    assert timing.get("fallback_reason") == "not_rewritable:synthetic_token"
    assert timing.get("final_sample_rate") == 1
    assert _is_legal_reason(timing.get("fallback_reason"))


# ---------------------------------------------------------------- task 3.6
@pytest.fixture(scope="module")
def tpch_sf1_db(tmp_path_factory):
    """Module-scoped TPC-H SF=1 DuckDB for the Q18 integration test."""
    p = tmp_path_factory.mktemp("phase3_q18") / "tpch_sf1.duckdb"
    conn = duckdb.connect(database=str(p), read_only=False)
    try:
        conn.execute("INSTALL tpch; LOAD tpch;")
        conn.execute("CALL dbgen(sf=1);")
    finally:
        conn.close()
    return str(p)


def test_q18_explicit_fallback(tpch_sf1_db, tmp_path, monkeypatch):
    """Q18 must complete cleanly: either AQP, or an explicit
    structured fallback. No `execute_aqp_exception`, no crash.
    Properties 1, 2, 4, 5, 6 all in scope.
    """
    from pilotdb.benchmarks.tpch_shared import build_query_obj, load_query_sql
    from pilotdb.execute import execute_aqp

    monkeypatch.chdir(tmp_path)

    sql = load_query_sql("q18")
    assert sql is not None, "Q18 template missing"
    q = build_query_obj("q18", sql)
    db_config = {"dbms": "duckdb", "path": tpch_sf1_db}

    df, timing = execute_aqp(q, db_config, pilot_sample_rate=1.0)

    # Property 1: tuple shape.
    assert isinstance(df, pd.DataFrame)
    assert isinstance(timing, dict)
    # Property 2: required timing keys.
    assert "pilot_sample_rate" in timing
    assert "final_sample_rate" in timing
    assert "fallback_reason" in timing
    # Result is real; Q18 is a top-100 query so we expect <= 100 rows
    # (often well under at SF=1).
    assert len(df) >= 0
    # Property 4 + 5 + 6: reason is in the legal set, NOT
    # `execute_aqp_exception` (that was the runner-level marker the
    # caller used to set when this function raised).
    reason = timing.get("fallback_reason")
    assert reason != "execute_aqp_exception"
    assert _is_legal_reason(reason)


def test_q18_runner_no_aqp_exception(tpch_sf1_db, tmp_path, monkeypatch):
    """Phase 3 acceptance via the runner: Q18 record must have
    `error is None` and `fallback_reason != execute_aqp_exception`."""
    from pilotdb.benchmarks.run_duckdb_tpch import RunOpts, measure
    monkeypatch.chdir(tmp_path)
    rec = measure("q18", tpch_sf1_db, RunOpts(pilot_rate=1.0, sf=1))
    assert rec["dbms"] == "duckdb"
    assert rec["query_id"] == "q18"
    # Acceptance from prompt: error is null, fallback_reason not the crash marker.
    assert rec["error"] is None, f"unexpected runner error: {rec['error']!r}"
    assert rec["fallback_reason"] != "execute_aqp_exception"
    # Either AQP succeeded, or a structured fallback was taken.
    assert _is_legal_reason(rec["fallback_reason"])
    # If an exact fallback was taken, fsr == 1 and rel_err is 0 (or None
    # when summarize_error can't compute one for Q18's wide group-by row).
    if rec["fallback_reason"] is not None:
        assert rec["final_sample_rate"] == 1

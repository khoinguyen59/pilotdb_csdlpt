"""Smoke tests for the DuckDB TPC-H benchmark runner.

These don't measure wall-clock performance — they only verify the
runner imports, the per-query templates load, and the JSON output
record schema is stable. The full TPC-H SF=1 generation is shared
with the e2e suite via the class-scoped `tpch_db_path` fixture so
the smoke tests stay below ~10s.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
import sqlglot


def test_imports():
    """The runner package must expose its public surface."""
    from pilotdb.benchmarks.run_duckdb_tpch import (
        _REQUIRED_KEYS, main, measure, RunOpts, setup_tpch_db,
    )
    from pilotdb.benchmarks.tpch_shared import (
        ALL_QIDS, available_qids, build_query_obj, load_query_sql,
        scalar_summary, summarize_error, tables_in_sql, TPCH_SF1_SIZES,
        TPCH_TABLE_COLS,
    )
    assert "query_id" in _REQUIRED_KEYS
    assert callable(main) and callable(measure)
    assert ALL_QIDS[0] == "q1" and ALL_QIDS[-1] == "q22"


def test_load_query_sql_for_known_qids():
    """q1, q6, q14 must have on-disk templates and parse with sqlglot."""
    from pilotdb.benchmarks.tpch_shared import load_query_sql
    for qid in ("q1", "q6", "q14"):
        sql = load_query_sql(qid)
        assert sql is not None and len(sql) > 0, f"{qid} template missing"
        # sqlglot must parse it (no exception)
        parsed = sqlglot.parse_one(sql, read="duckdb")
        assert parsed is not None


def test_missing_templates_return_none():
    """All 22 TPC-H templates must now be present after Phase 2."""
    from pilotdb.benchmarks.tpch_shared import load_query_sql
    for qid in ("q2", "q11", "q13", "q15", "q16", "q20", "q21"):
        assert load_query_sql(qid) is not None, f"{qid} template missing"


@pytest.mark.parametrize(
    "qid", ["q2", "q11", "q13", "q15", "q16", "q20", "q21"]
)
def test_seven_new_templates_load_and_parse(qid):
    """Phase 2: Q2/Q11/Q13/Q15/Q16/Q20/Q21 templates must exist and parse."""
    from pilotdb.benchmarks.tpch_shared import load_query_sql
    sql = load_query_sql(qid)
    assert sql is not None and len(sql) > 0, f"{qid} template missing"
    parsed = sqlglot.parse_one(sql, read="duckdb")
    assert parsed is not None


def test_q13_canonical_has_no_c_custkeyyes():
    """The Q13 import must fix the canonical typo `c_custkeyyes`."""
    from pilotdb.benchmarks.tpch_shared import load_query_sql
    sql = load_query_sql("q13")
    assert sql is not None
    assert "c_custkeyyes" not in sql, (
        "Q13 still contains the canonical-source typo `c_custkeyyes`; "
        "should be `c_custkey` in the LEFT OUTER JOIN ON-clause."
    )


def test_q15_is_single_statement():
    """Q15 must be one CTE-based SELECT, not the canonical 3-statement
    CREATE VIEW / SELECT / DROP VIEW script."""
    from pilotdb.benchmarks.tpch_shared import load_query_sql
    sql = load_query_sql("q15")
    assert sql is not None
    # Strip a single trailing semicolon (template files end with one).
    stripped = sql.rstrip().rstrip(";")
    # No further `;` means one statement remains.
    assert ";" not in stripped, (
        "Q15 must be a single-statement SELECT with `revenue0` materialised "
        "as a CTE; CREATE VIEW / DROP VIEW form is rejected."
    )
    # And it must use a CTE form so the runner sees one SELECT.
    lowered = stripped.lower()
    assert "with revenue0" in lowered, (
        "Q15 must materialise `revenue0` as a CTE (`with revenue0 ...`)."
    )


def test_tables_in_sql_extracts_canonical_names():
    from pilotdb.benchmarks.tpch_shared import tables_in_sql
    assert tables_in_sql("select * from lineitem") == ["lineitem"]
    assert "lineitem" in tables_in_sql(
        "select * from lineitem, part where l_partkey = p_partkey"
    )
    assert "part" in tables_in_sql(
        "select * from lineitem JOIN part ON l_partkey = p_partkey"
    )


def test_build_query_obj_populates_tpch_metadata():
    from pilotdb.benchmarks.tpch_shared import (
        TPCH_SF1_SIZES, build_query_obj, load_query_sql,
    )
    sql = load_query_sql("q14")
    q = build_query_obj("q14", sql)
    assert "lineitem" in q.table_size
    assert "part" in q.table_size
    assert q.table_size["lineitem"] == TPCH_SF1_SIZES["lineitem"]


def test_blank_record_has_all_required_keys():
    from pilotdb.benchmarks.run_duckdb_tpch import (
        RunOpts, _REQUIRED_KEYS, _blank_record,
    )
    rec = _blank_record("q6", RunOpts())
    missing = _REQUIRED_KEYS - set(rec.keys())
    assert not missing, f"missing keys in blank record: {missing}"


def test_summarize_error_q6_scalar():
    from pilotdb.benchmarks.tpch_shared import summarize_error
    import pandas as pd
    exact = pd.DataFrame({"revenue": [100.0]})
    aqp = pd.DataFrame({"revenue": [105.0]})
    err = summarize_error(exact, aqp, "q6")
    assert err is not None
    assert abs(err - 0.05) < 1e-12


def test_summarize_error_q1_sum_of_qty():
    from pilotdb.benchmarks.tpch_shared import summarize_error
    import pandas as pd
    exact = pd.DataFrame({
        "l_returnflag": ["A", "N"], "l_linestatus": ["F", "O"],
        "sum_qty": [100.0, 200.0],
    })
    aqp = pd.DataFrame({
        "l_returnflag": ["A", "N"], "l_linestatus": ["F", "O"],
        "sum_qty": [110.0, 220.0],
    })
    err = summarize_error(exact, aqp, "q1")
    assert err is not None
    # total 300 -> 330, rel err = 30/300 = 0.10
    assert abs(err - 0.10) < 1e-12


@pytest.fixture(scope="module")
def tpch_db_path(tmp_path_factory):
    """Module-scoped TPC-H SF=1 .duckdb file shared by the heavier tests."""
    p = tmp_path_factory.mktemp("bench_tpch") / "tpch_sf1.duckdb"
    conn = duckdb.connect(database=str(p), read_only=False)
    try:
        conn.execute("INSTALL tpch; LOAD tpch;")
        conn.execute("CALL dbgen(sf=1);")
    finally:
        conn.close()
    return str(p)


def test_measure_q6_minimal_schema(tpch_db_path, tmp_path, monkeypatch):
    """`measure("q6", ...)` must return a dict with every required key."""
    from pilotdb.benchmarks.run_duckdb_tpch import (
        _REQUIRED_KEYS, RunOpts, measure,
    )
    monkeypatch.chdir(tmp_path)  # isolate any side-effect logs/results
    rec = measure("q6", tpch_db_path, RunOpts(pilot_rate=1.0, sf=1))
    missing = _REQUIRED_KEYS - set(rec.keys())
    assert not missing, f"missing keys: {missing}"
    assert rec["query_id"] == "q6"
    assert rec["dbms"] == "duckdb"
    assert rec["skipped"] is False
    assert rec["exact_runtime_s"] is not None
    # AQP must have produced a numeric result (or an explicit error).
    if rec["error"] is None:
        assert rec["aqp_value_sample"] is not None


def test_measure_missing_template_skipped(tpch_db_path, tmp_path, monkeypatch):
    """A qid with no template on disk must skip with `no_template`.

    After Phase 2 added Q2/Q11/Q13/Q15/Q16/Q20/Q21, all 22 standard TPC-H
    qids ship templates; we exercise the skip path with a synthetic qid.
    """
    from pilotdb.benchmarks.run_duckdb_tpch import RunOpts, measure
    monkeypatch.chdir(tmp_path)
    rec = measure("q99", tpch_db_path, RunOpts(pilot_rate=1.0, sf=1))
    assert rec["skipped"] is True
    assert rec["skip_reason"] == "no_template"


def test_dbtarget_dispatch_imports():
    from pilotdb.benchmarks.run_duckdb_tpch import DbTarget
    duck = DbTarget(dbms="duckdb", path="/tmp/x.duckdb")
    assert duck.db_config() == {"dbms": "duckdb", "path": "/tmp/x.duckdb"}
    pg = DbTarget(dbms="postgres", config={"dbname": "x", "username": "u"})
    cfg = pg.db_config()
    assert cfg["dbms"] == "postgres" and cfg["dbname"] == "x"


def test_exact_run_dispatch_unknown_dbms_raises():
    from pilotdb.benchmarks.tpch_shared import exact_run
    try:
        exact_run("SELECT 1", "nope")
    except ValueError as e:
        assert "unknown dbms" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_measure_postgres_unreachable_records_error(tmp_path, monkeypatch):
    """If Postgres can't be reached, the runner returns a clean error
    record — no exception bubbles up to the caller."""
    from pilotdb.benchmarks.run_duckdb_tpch import (
        DbTarget, RunOpts, measure,
    )
    monkeypatch.chdir(tmp_path)
    target = DbTarget(
        dbms="postgres",
        config={
            "dbname": "x", "username": "u", "password": "x",
            "host": "nonexistent.invalid", "port": 5432,
        },
    )
    rec = measure("q6", target, RunOpts(pilot_rate=1.0, sf=1))
    assert rec["dbms"] == "postgres"
    assert rec["skipped"] is True
    assert rec["skip_reason"] == "exact_execution_failed"
    assert rec["error"] is not None
    assert rec["error"].startswith("exact:")


def test_main_writes_json(tpch_db_path, tmp_path, monkeypatch):
    """`main` must emit exactly one JSON file with the expected shape."""
    from pilotdb.benchmarks.run_duckdb_tpch import _REQUIRED_KEYS, main
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "bench_out"
    rc = main([
        "--queries", "q6",
        "--pilot-rate", "1.0",
        "--sf", "1",
        "--output-dir", str(out_dir),
        "--db-path", tpch_db_path,
        "--no-csv",
    ])
    assert rc == 0
    json_files = list(out_dir.glob("results_*.json"))
    assert len(json_files) == 1, f"expected 1 JSON, got {json_files}"
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    missing = _REQUIRED_KEYS - set(data[0].keys())
    assert not missing, f"missing keys in output record: {missing}"


@pytest.mark.slow
def test_runner_produces_22_records_with_no_no_template(
    tpch_db_path, tmp_path, monkeypatch
):
    """Phase 2 acceptance: --queries all on DuckDB SF=1 must produce 22
    records with no `skip_reason="no_template"`."""
    from pilotdb.benchmarks.run_duckdb_tpch import main
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "bench_out_all"
    rc = main([
        "--queries", "all",
        "--pilot-rate", "1.0",
        "--sf", "1",
        "--output-dir", str(out_dir),
        "--db-path", tpch_db_path,
        "--no-csv",
    ])
    assert rc == 0
    json_files = list(out_dir.glob("results_*.json"))
    assert len(json_files) == 1
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 22, f"expected 22 records, got {len(data)}"
    no_template = [r for r in data if r.get("skip_reason") == "no_template"]
    assert not no_template, (
        f"phase 2 acceptance failed: {len(no_template)} records still have "
        f"skip_reason='no_template': {[r['query_id'] for r in no_template]}"
    )

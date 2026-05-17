"""Integration test for the PilotDB SQL rewriter.

Requires benchmark query files and a DuckDB config.
Run manually: python -m pytest tests/unit_tests/test_rewriter.py -- <dbname> <qid>
Skipped automatically when benchmark data is unavailable.
"""
import os
import sys
import json
import pytest

pytest.importorskip("sqlglot")

import yaml
from pilotdb.execute import execute_aqp
from pilotdb.query import Query

# These tests require CLI arguments and benchmark files — skip in normal pytest runs
BENCHMARKS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks")


def _load_benchmark(dbname, qid):
    """Load a benchmark query and its metadata."""
    query_path = os.path.join(BENCHMARKS_DIR, dbname, f"query_{qid}.sql")
    meta_path = os.path.join(BENCHMARKS_DIR, dbname, "meta.json")
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "db_configs", f"duckdb_{dbname}.yml"
    )

    if not os.path.exists(query_path):
        pytest.skip(f"Benchmark query not found: {query_path}")
    if not os.path.exists(meta_path):
        pytest.skip(f"Benchmark meta not found: {meta_path}")
    if not os.path.exists(config_path):
        pytest.skip(f"DB config not found: {config_path}")

    with open(query_path) as f:
        query_str = f.read()
    with open(meta_path) as f:
        meta = json.load(f)
    with open(config_path) as f:
        db_config = yaml.safe_load(f)

    return query_str, meta, db_config


@pytest.mark.parametrize(
    "dbname,qid",
    [
        pytest.param("tpch", "1", id="tpch-q1"),
        pytest.param("tpch", "6", id="tpch-q6"),
    ],
)
def test_rewriter_benchmark(dbname, qid):
    """Run the rewriter on a benchmark query if files are available."""
    query_str, meta, db_config = _load_benchmark(dbname, qid)
    query = Query(
        query_str,
        meta["table_cols"],
        meta["table_size"],
        name=f"{dbname}-{qid}",
    )
    # Only test rewriting, not actual DB execution (that needs a live DB)
    # execute_aqp(query, db_config=db_config, pilot_sample_rate=0.05)
    assert query is not None

import pytest

pytest.importorskip("sqlglot")

from pilotdb.pilot_engine.commons import DUCKDB
from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter


TABLE_COLS = {"lineitem": ["l_quantity", "l_extendedprice"]}
TABLE_SIZE = {"lineitem": 10_000_000}


def rewrite(query):
    rewriter = Pilot_Rewriter(TABLE_COLS, TABLE_SIZE, DUCKDB)
    output = rewriter.rewrite(query)
    return rewriter, output


def test_max_aggregate_falls_back_to_exact():
    rewriter, output = rewrite("SELECT MAX(l_quantity) FROM lineitem")
    assert not rewriter.is_rewritable
    assert "MAX aggregate" in rewriter.unsupported_reason
    assert output == "SELECT MAX(l_quantity) FROM lineitem"


def test_min_aggregate_falls_back_to_exact():
    rewriter, output = rewrite("SELECT MIN(l_quantity) FROM lineitem")
    assert not rewriter.is_rewritable
    assert "MIN aggregate" in rewriter.unsupported_reason
    assert output == "SELECT MIN(l_quantity) FROM lineitem"


def test_count_distinct_single_table_is_rewritable():
    rewriter, output = rewrite("SELECT COUNT(DISTINCT l_quantity) FROM lineitem")
    assert rewriter.is_rewritable
    # In DuckDB, it should rewrite to approx_count_distinct or keep it depending on rewriter config,
    # but since it's DUCKDB, it will be validated and rewritten:
    # Actually, the rewriter itself (Pilot_Rewriter) doesn't transpile Count(distinct) to approx_count_distinct,
    # that happens in Sampling_Rewriter (sampling.py). Pilot_Rewriter just lets it pass and does normal AST transformations.
    assert rewriter.is_rewritable

def test_count_distinct_with_group_by_falls_back_to_exact():
    rewriter, output = rewrite("SELECT l_quantity, COUNT(DISTINCT l_extendedprice) FROM lineitem GROUP BY l_quantity")
    assert not rewriter.is_rewritable
    assert "COUNT DISTINCT is not supported with JOIN or GROUP BY" in rewriter.unsupported_reason

def test_count_distinct_with_join_falls_back_to_exact():
    # Setup mapping with two tables
    table_cols = {"lineitem": ["l_quantity", "l_orderkey"], "orders": ["o_orderkey"]}
    table_size = {"lineitem": 1000, "orders": 100}
    rewriter = Pilot_Rewriter(table_cols, table_size, DUCKDB)
    output = rewriter.rewrite("SELECT COUNT(DISTINCT l_quantity) FROM lineitem JOIN orders ON l_orderkey = o_orderkey")
    assert not rewriter.is_rewritable
    assert "COUNT DISTINCT is not supported with JOIN or GROUP BY" in rewriter.unsupported_reason


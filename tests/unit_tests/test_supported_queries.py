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


def test_count_distinct_falls_back_to_exact():
    rewriter, output = rewrite("SELECT COUNT(DISTINCT l_quantity) FROM lineitem")
    assert not rewriter.is_rewritable
    assert "COUNT DISTINCT" in rewriter.unsupported_reason
    assert output == "SELECT COUNT(DISTINCT l_quantity) FROM lineitem"

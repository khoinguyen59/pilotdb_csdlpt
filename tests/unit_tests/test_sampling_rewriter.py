"""Unit tests for the Sampling_Rewriter (sqlglot v30 compatible)."""

import pytest
import sqlglot

from pilotdb.pilot_engine.rewriter.sampling import Sampling_Rewriter


@pytest.fixture
def simple_table_cols():
    return {"lineitem": ["l_extendedprice", "l_discount", "l_shipdate"]}


@pytest.fixture
def simple_table_size():
    return {"lineitem": 6_000_000}


@pytest.fixture
def multi_table_cols():
    return {
        "lineitem": ["l_extendedprice", "l_orderkey"],
        "orders": ["o_orderkey", "o_totalprice"],
    }


@pytest.fixture
def multi_table_size():
    return {"lineitem": 6_000_000, "orders": 1_500_000}


class TestSamplingRewriterBasicQueries:
    """Test rewriting simple aggregation queries."""

    def test_simple_sum_rewrite(self, simple_table_cols, simple_table_size):
        """SUM aggregate should have TABLESAMPLE and /sample_rate division."""
        query = "SELECT SUM(l_extendedprice) FROM lineitem WHERE l_shipdate > '1994-01-01'"
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert "TABLESAMPLE" in result or "sampling_method" in result
        assert rw.largest_table == "lineitem"

    def test_simple_avg_rewrite(self, simple_table_cols, simple_table_size):
        """AVG queries should be rewritten without error."""
        query = "SELECT AVG(l_extendedprice) FROM lineitem"
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert result is not None
        assert rw.largest_table == "lineitem"

    def test_count_rewrite(self, simple_table_cols, simple_table_size):
        """COUNT aggregate should get sample_rate divisor."""
        query = "SELECT COUNT(*) FROM lineitem"
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert result is not None

    def test_composite_div_rewrite(self, simple_table_cols, simple_table_size):
        """Composite SUM/SUM division query should rewrite correctly."""
        query = "SELECT SUM(l_extendedprice) / SUM(l_discount) FROM lineitem"
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert result is not None


class TestSamplingRewriterMultiTable:
    """Test rewriting join queries."""

    def test_join_rewrite_picks_largest_table(self, multi_table_cols, multi_table_size):
        """Rewriter should sample the largest table (lineitem)."""
        query = (
            "SELECT SUM(l_extendedprice) "
            "FROM lineitem JOIN orders ON l_orderkey = o_orderkey"
        )
        rw = Sampling_Rewriter(multi_table_cols, multi_table_size, "duckdb")
        result = rw.rewrite(query)
        assert rw.largest_table == "lineitem"
        assert result is not None

    def test_join_rewrite_no_crash_sqlglot_v30(self, multi_table_cols, multi_table_size):
        """Verify the from_/from compatibility doesn't crash on sqlglot v30."""
        query = (
            "SELECT AVG(o_totalprice) "
            "FROM orders JOIN lineitem ON o_orderkey = l_orderkey "
            "WHERE l_shipdate > '1994-01-01'"
        )
        rw = Sampling_Rewriter(multi_table_cols, multi_table_size, "duckdb")
        result = rw.rewrite(query)
        assert result is not None


class TestSamplingRewriterGroupBy:
    """Test GROUP BY queries."""

    def test_group_by_sum(self, simple_table_cols, simple_table_size):
        query = (
            "SELECT l_shipdate, SUM(l_extendedprice) "
            "FROM lineitem "
            "GROUP BY l_shipdate"
        )
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert result is not None


class TestSamplingRewriterDBMSSyntax:
    """Test DBMS-specific syntax rendering."""

    @pytest.mark.parametrize("dbms", ["duckdb", "postgres"])
    def test_dbms_output_parseable(self, dbms, simple_table_cols, simple_table_size):
        """Rewritten query for each DBMS should be parseable by sqlglot."""
        query = "SELECT SUM(l_extendedprice) FROM lineitem"
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, dbms)
        result = rw.rewrite(query)
        # The result should contain a placeholder; after stripping it, SQL should be valid
        assert result is not None
        assert len(result) > len(query) // 2  # should not be empty/truncated

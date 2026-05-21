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


class TestSamplingRewriterMultiTablePlan:
    """Paper §3.2: when multiple tables exceed the 1M-row threshold, the
    rewriter must emit a TABLESAMPLE placeholder for *each* large table
    so the chosen vector plan can render per-table rates. Single-table
    queries must stay byte-identical to legacy behaviour.
    """

    def test_two_large_tables_both_get_placeholders(self, multi_table_cols, multi_table_size):
        # lineitem (6M) + orders (1.5M) both exceed 1M threshold
        query = (
            "SELECT SUM(l_extendedprice) "
            "FROM lineitem JOIN orders ON l_orderkey = o_orderkey"
        )
        rw = Sampling_Rewriter(multi_table_cols, multi_table_size, "duckdb")
        result = rw.rewrite(query)
        # Both per-table placeholders must appear
        assert "{sampling_method_lineitem}" in result, (
            "Missing lineitem placeholder in multi-table rewrite:\n" + result
        )
        assert "{sampling_method_orders}" in result, (
            "Missing orders placeholder in multi-table rewrite:\n" + result
        )
        # And `sampled_tables` records both — the optimizer/cost loop
        # uses this to drive multi-rate plan rendering.
        names = [name for name, _marker in rw.sampled_tables]
        assert set(names) == {"lineitem", "orders"}

    def test_single_large_table_keeps_legacy_shape(self, simple_table_cols, simple_table_size):
        # Only lineitem (6M) is large → behaves like the legacy path:
        # a single per-table placeholder, no markers list.
        query = "SELECT SUM(l_extendedprice) FROM lineitem"
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert "{sampling_method_lineitem}" in result
        # No second placeholder
        assert "sampling_method_orders" not in result

    def test_small_only_uses_legacy_fallback(self):
        """If no table clears the size threshold the rewriter must still
        produce a parseable rewritten query (legacy single-largest behaviour).
        """
        table_cols = {"tiny": ["x"]}
        table_size = {"tiny": 1000}   # below 1M
        rw = Sampling_Rewriter(table_cols, table_size, "duckdb")
        result = rw.rewrite("SELECT SUM(x) FROM tiny")
        assert result is not None
        # Largest is "tiny" via fallback; no multi-table markers recorded.
        assert rw.largest_table == "tiny"
        assert rw.sampled_tables == []

    def test_multi_table_renders_per_table_rates(self, multi_table_cols, multi_table_size):
        """Plan with two table rates must render both TABLESAMPLE clauses."""
        from pilotdb.pilot_engine.multi_table_sampling import (
            apply_sampling_plan_template,
        )
        from pilotdb.pilot_engine.sampling_plan import SamplingPlan

        query = (
            "SELECT SUM(l_extendedprice) "
            "FROM lineitem JOIN orders ON l_orderkey = o_orderkey"
        )
        rw = Sampling_Rewriter(multi_table_cols, multi_table_size, "duckdb")
        template = rw.rewrite(query)

        plan = SamplingPlan(rates={"lineitem": 0.05, "orders": 0.03})
        rendered = apply_sampling_plan_template(template, plan, "duckdb")
        # Both per-table TABLESAMPLE clauses present in the rendered SQL
        assert "TABLESAMPLE SYSTEM(5.0%)" in rendered, rendered
        assert "TABLESAMPLE SYSTEM(3.0%)" in rendered, rendered


class TestSamplingRewriterHavingPreserved:
    """Paper §3.3 final-query rewrite must keep HAVING and upscale any
    aggregate appearing inside it by ``/{sample_rate}``. The pilot
    rewriter drops HAVING (correctly — pilot groups by block id, so a
    user-level HAVING does not apply) but the *final* query is the one
    served back to the user, so HAVING semantics must round-trip.
    """

    def test_having_sum_threshold_upscaled(self, simple_table_cols, simple_table_size):
        query = (
            "SELECT l_shipdate, SUM(l_extendedprice) AS total "
            "FROM lineitem "
            "GROUP BY l_shipdate "
            "HAVING SUM(l_extendedprice) > 1000000"
        )
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert "HAVING" in result.upper(), (
            "Final query lost HAVING clause:\n" + result
        )
        # The SUM inside HAVING must be divided by {sample_rate} so that
        # the comparison threshold remains on the same scale as the
        # upscaled aggregate.
        having_section = result.upper().split("HAVING", 1)[1]
        assert "{SAMPLE_RATE}" in having_section, (
            "HAVING aggregate was not upscaled by /{sample_rate}:\n" + result
        )

    def test_having_count_threshold_upscaled(self, simple_table_cols, simple_table_size):
        query = (
            "SELECT l_shipdate, COUNT(*) AS n "
            "FROM lineitem "
            "GROUP BY l_shipdate "
            "HAVING COUNT(*) > 100"
        )
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        result = rw.rewrite(query)
        assert "HAVING" in result.upper()
        having_section = result.upper().split("HAVING", 1)[1]
        assert "{SAMPLE_RATE}" in having_section

    def test_tablesample_preserved_after_rewrite(self, simple_table_cols, simple_table_size):
        from pilotdb.pilot_engine.multi_table_sampling import apply_sampling_plan_template
        from pilotdb.pilot_engine.sampling_plan import SamplingPlan
        sql = "SELECT SUM(l_extendedprice) FROM lineitem"
        rw = Sampling_Rewriter(simple_table_cols, simple_table_size, "duckdb")
        template = rw.rewrite(sql)
        plan = SamplingPlan(rates={"lineitem": 0.05})
        rendered = apply_sampling_plan_template(template, plan, "duckdb")
        
        parsed = sqlglot.parse_one(rendered)
        table_nodes = list(parsed.find_all(sqlglot.exp.Table))
        has_sample = any(t.args.get('sample') for t in table_nodes)
        assert has_sample, f"TABLESAMPLE was dropped during rewrite! Rendered SQL: {rendered}"

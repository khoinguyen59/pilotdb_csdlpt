"""Tests for real JoinBlockStats extraction from pilot query results.

Verifies that _extract_join_block_stats correctly reconstructs
y(1)/y(2)/y(3)/N2 from pilot DataFrame with page_id columns,
as required by Lemma 4.8.
"""

import math

import numpy as np
import pandas as pd
import pytest

from pilotdb.execute import _extract_join_block_stats


class TestJoinBlockStatsExtraction:
    """Test _extract_join_block_stats with synthetic pilot DataFrames."""

    @pytest.fixture
    def two_table_pilot_df(self):
        """Synthetic pilot results for a 2-table join query.

        Simulates:
          SELECT SUM(l.price * o.qty) FROM lineitem l JOIN orders o ...
          with page_id_0 (T1 block), page_id_1 (T2 block), and agg_col
        """
        np.random.seed(42)
        n_rows = 200
        t1_blocks = np.random.choice(["page_id_0:10", "page_id_0:20",
                                       "page_id_0:30", "page_id_0:40",
                                       "page_id_0:50"], n_rows)
        t2_blocks = np.random.choice(["page_id_1:100", "page_id_1:200",
                                       "page_id_1:300"], n_rows)
        agg_values = np.random.exponential(50, n_rows)
        return pd.DataFrame({
            "page_id_0": t1_blocks,
            "page_id_1": t2_blocks,
            "page_sum_1": agg_values,
        })

    @pytest.fixture
    def table_sizes(self):
        return {"lineitem": 6_000_000, "orders": 1_500_000}

    def test_returns_join_stats_for_two_table(self, two_table_pilot_df, table_sizes):
        """Should return JoinBlockStats for a 2-table join pilot result."""
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            two_table_pilot_df, page_errors,
            page_id_count=2,
            table_sizes=table_sizes,
        )
        assert stats is not None
        assert stats.n_pilot_blocks == 5  # 5 unique T1 blocks
        assert stats.N2 >= 3  # at least 3 unique T2 blocks observed
        assert len(stats.y1_per_block) == 5
        assert len(stats.y3_per_block) == 5
        assert len(stats.y2_values) > 0

    def test_y1_is_squared_sums(self, two_table_pilot_df, table_sizes):
        """y(1)_i should be (sum across T2 blocks)^2 for each T1 block."""
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            two_table_pilot_df, page_errors,
            page_id_count=2, table_sizes=table_sizes,
        )
        # Manually compute expected y1 for one block
        df = two_table_pilot_df.copy()
        df["_bid"] = df["page_id_0"].str.split(":").str[1]
        block_10_sum = df[df["_bid"] == "10"]["page_sum_1"].sum()
        # y1 for block 10 should be block_10_sum^2
        assert any(
            abs(v - block_10_sum**2) < 1e-6 for v in stats.y1_per_block
        )

    def test_y2_is_per_pair_sums(self, two_table_pilot_df, table_sizes):
        """y(2) should have entries for each (T1-block, T2-block) pair."""
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            two_table_pilot_df, page_errors,
            page_id_count=2, table_sizes=table_sizes,
        )
        # Number of pairs ≤ n_t1_blocks × n_t2_blocks
        assert len(stats.y2_values) <= 5 * 3
        assert len(stats.y2_values) > 0

    def test_y3_is_sum_of_squared_pair_sums(self, two_table_pilot_df, table_sizes):
        """y(3)_i should be Σ_{i2} J(t1_i, t2_i2)^2 for each T1 block."""
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            two_table_pilot_df, page_errors,
            page_id_count=2, table_sizes=table_sizes,
        )
        assert all(v >= 0 for v in stats.y3_per_block)
        assert len(stats.y3_per_block) == 5

    def test_returns_none_for_single_table(self, table_sizes):
        """Single table (page_id_count < 2) should return None."""
        df = pd.DataFrame({
            "page_id_0": ["page_id_0:10"] * 20,
            "page_sum_1": np.random.normal(100, 10, 20),
        })
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            df, page_errors, page_id_count=1, table_sizes=table_sizes,
        )
        assert stats is None

    def test_returns_none_when_no_page_id_cols(self, table_sizes):
        """Missing page_id columns should return None gracefully."""
        df = pd.DataFrame({
            "some_col": [1, 2, 3],
            "page_sum_1": [10, 20, 30],
        })
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            df, page_errors, page_id_count=2, table_sizes=table_sizes,
        )
        assert stats is None

    def test_n2_uses_table_sizes(self, two_table_pilot_df, table_sizes):
        """N2 should be derived from table_sizes, not just observed blocks."""
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            two_table_pilot_df, page_errors,
            page_id_count=2, table_sizes=table_sizes,
        )
        # N2 should be ceil(1_500_000 / 8192) = 184 (much larger than 3 observed)
        expected_N2 = math.ceil(1_500_000 / 8192)
        assert stats.N2 == expected_N2

    def test_pilot_rate_computed_from_table_size(self, two_table_pilot_df, table_sizes):
        """pilot_rate should reflect fraction of T1 blocks sampled."""
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_join_block_stats(
            two_table_pilot_df, page_errors,
            page_id_count=2, table_sizes=table_sizes,
        )
        total_t1_blocks = math.ceil(6_000_000 / 8192)
        expected_rate = 5 / total_t1_blocks  # 5 pilot blocks
        assert abs(stats.pilot_rate - expected_rate) < 1e-6


class TestJoinStatsIntegrationWithPhi:
    """Test that extracted JoinBlockStats integrates with PhiConstraintSet."""

    def test_join_stats_attached_to_constraints(self):
        """_extract_pilot_stats with join_block_stats should attach to each stat."""
        from pilotdb.execute import _extract_pilot_stats
        from pilotdb.pilot_engine.join_variance import JoinBlockStats

        np.random.seed(42)
        join_stats = JoinBlockStats(
            y1_per_block=np.array([100.0, 200.0, 300.0]),
            y2_values=np.array([10.0, 20.0, 30.0]),
            y3_per_block=np.array([50.0, 60.0, 70.0]),
            n_pilot_blocks=3,
            N2=100,
            pilot_rate=0.05,
        )

        pilot_df = pd.DataFrame({
            "page_sum_1": np.random.normal(100, 10, 50),
        })
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_pilot_stats(
            pilot_df, page_errors, group_cols=[],
            join_block_stats=join_stats,
        )
        assert len(stats) == 1
        assert "join_stats" in stats[0]
        assert stats[0]["join_stats"].n_pilot_blocks == 3
        assert stats[0]["join_stats"].N2 == 100

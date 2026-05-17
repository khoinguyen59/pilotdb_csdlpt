"""Integration tests for the execute.py runtime wiring.

Tests the full pipeline: pilot_results → _extract_pilot_stats →
build_phi_constraints → optimizer → guardrail, WITHOUT requiring a
live database connection.
"""

import math

import numpy as np
import pandas as pd
import pytest

from pilotdb.execute import _extract_pilot_stats, _translate_pilot_results
from pilotdb.pilot_engine.join_variance import build_phi_constraints
from pilotdb.pilot_engine.aqp_guarantee import check_guarantee_mode
from pilotdb.pilot_engine.optimizer import (
    build_optimization_context,
    generate_candidate_plans,
)


class TestExtractPilotStats:
    """Test _extract_pilot_stats bridges pilot DataFrame → constraint dicts."""

    def test_single_aggregate_no_group(self):
        """Single SUM aggregate, no GROUP BY."""
        pilot_df = pd.DataFrame({
            "page_sum_1": np.random.normal(100, 10, 50),
        })
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_pilot_stats(pilot_df, page_errors, group_cols=[])
        assert len(stats) == 1
        assert "sample_mean" in stats[0]
        assert "sample_std" in stats[0]
        assert stats[0]["sample_size"] == 50
        assert stats[0]["aggregate_index"] == 0
        assert stats[0]["group_index"] == 0

    def test_multiple_aggregates_no_group(self):
        """Two aggregates (SUM, AVG), no GROUP BY."""
        pilot_df = pd.DataFrame({
            "page_sum_1": np.random.normal(100, 10, 50),
            "page_avg_1": np.random.normal(50, 5, 50),
        })
        page_errors = {"page_sum_1": 0.05, "page_avg_1": 0.03}
        stats = _extract_pilot_stats(pilot_df, page_errors, group_cols=[])
        assert len(stats) == 2
        assert stats[0]["aggregate_index"] == 0
        assert stats[1]["aggregate_index"] == 1

    def test_with_group_by(self):
        """Single aggregate with 3 groups."""
        np.random.seed(42)
        pilot_df = pd.DataFrame({
            "group_col": ["A"] * 20 + ["B"] * 20 + ["C"] * 10,
            "page_sum_1": np.random.normal(100, 10, 50),
        })
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_pilot_stats(
            pilot_df, page_errors, group_cols=["group_col"]
        )
        assert len(stats) == 3  # 1 aggregate × 3 groups
        # Verify group_index varies
        group_indices = [s["group_index"] for s in stats]
        assert group_indices == [0, 1, 2]

    def test_nan_std_replaced_with_zero(self):
        """Single-row groups produce NaN std; should be replaced with 0."""
        pilot_df = pd.DataFrame({
            "group_col": ["X"],
            "page_sum_1": [100.0],
        })
        page_errors = {"page_sum_1": 0.05}
        stats = _extract_pilot_stats(
            pilot_df, page_errors, group_cols=["group_col"]
        )
        assert len(stats) == 1
        assert stats[0]["sample_std"] == 0.0

    def test_n_page_excluded_from_stats(self):
        """n_page key in page_errors should be skipped as aggregate."""
        pilot_df = pd.DataFrame({
            "page_sum_1": np.random.normal(100, 10, 50),
        })
        page_errors = {"page_sum_1": 0.05, "n_page": 0.02}
        stats = _extract_pilot_stats(pilot_df, page_errors, group_cols=[])
        assert len(stats) == 1  # only page_sum_1, not n_page


class TestPhiConstraintBuild:
    """Test build_phi_constraints from _extract_pilot_stats output."""

    def test_build_and_check_feasibility(self):
        """Build constraints from synthetic stats and check feasibility."""
        pilot_stats = [
            {"sample_mean": 100.0, "sample_std": 5.0, "sample_size": 50,
             "aggregate_index": 0, "group_index": 0},
        ]
        phi = build_phi_constraints(
            failure_prob=0.05,
            n_aggregates=1,
            n_groups=1,
            pilot_stats=pilot_stats,
            required_error=0.05,
            table_names=("lineitem",),
        )
        assert phi.mode == "full"
        assert len(phi.constraints) == 1
        # At high rate, should be feasible
        assert phi.is_feasible({"lineitem": 0.99})


class TestGuardrailIntegration:
    """Test guardrail integration with optimizer context."""

    def test_single_table_always_proceeds(self):
        """Single-table queries should always proceed without guardrail block."""
        mode = check_guarantee_mode(
            has_phi_constraints=False,
            n_sampled_tables=1,
            pilot_block_count=50,
        )
        assert mode == "full-vector"

    def test_multi_table_without_phi_blocks(self):
        """Multi-table without Phi → exact-required."""
        mode = check_guarantee_mode(
            has_phi_constraints=False,
            n_sampled_tables=2,
            pilot_block_count=50,
        )
        assert mode == "exact-required"

    def test_multi_table_with_phi_proceeds(self):
        """Multi-table with Phi constraints → full-vector."""
        mode = check_guarantee_mode(
            has_phi_constraints=True,
            n_sampled_tables=3,
            pilot_block_count=100,
        )
        assert mode == "full-vector"


class TestOptimizerWithPhiConstraints:
    """Test optimizer accepts and uses PhiConstraintSet."""

    def test_optimizer_context_notes_phi_mode(self):
        """build_optimization_context should note full Phi mode."""
        from pilotdb.pilot_engine.join_variance import PhiConstraintSet, AggregateConstraint
        from scipy.stats import norm
        c = AggregateConstraint(
            aggregate_index=0, group_index=0,
            z_value=norm.ppf(0.975), L_mu=1000.0,
            required_error=0.05, pilot_sample_std=10.0,
            pilot_sample_size=50, sampled_tables=("lineitem",),
        )
        phi = PhiConstraintSet(
            constraints=[c], table_names=("lineitem",), mode="full"
        )
        ctx = build_optimization_context(
            query_tables=["lineitem"],
            table_sizes={"lineitem": 6_000_000},
            phi_constraints=phi,
        )
        assert "Phi(Theta)" in ctx.notes
        assert "full" in ctx.notes.lower() or "Full" in ctx.notes

    def test_optimizer_context_warns_without_phi(self):
        """Without Phi, context notes should indicate proxy mode."""
        ctx = build_optimization_context(
            query_tables=["lineitem"],
            table_sizes={"lineitem": 6_000_000},
        )
        assert "proxy" in ctx.notes.lower() or "Scalar" in ctx.notes

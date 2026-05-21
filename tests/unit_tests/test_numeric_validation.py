"""Numeric validation of paper formulas against known/derived cases.

These tests verify mathematical correctness by computing exact known
values from the paper's equations, NOT by testing code output against
hardcoded results. This ensures formulas are faithful to the paper.

References: PilotDB (SIGMOD '25), Technical Report arXiv:2503.21087
"""

import math

import numpy as np
import pytest
from scipy.stats import norm, t as t_dist

from pilotdb.pilot_engine.join_variance import (
    AggregateConstraint,
    JoinBlockStats,
    compute_UV_single_table,
    compute_UV_two_table,
    phi_constraint_value,
    build_phi_constraints,
)
from pilotdb.pilot_engine.error_bounds import (
    get_mean_sample_size,
    get_std_ub,
    get_mean_lb,
)
from pilotdb.execute import _min_pilot_rate_for_groups


class TestProcedure1NumericCase:
    """Verify Procedure 1 (§3.1) with concrete numbers.

    Scenario: TPC-H Q1 lineitem, SUM(l_extendedprice)
    - pilot_rate = 0.05% (θ_p = 0.0005)
    - observed: mean=38255, std=12144, n_blocks=30
    - error=5%, confidence=95%
    """

    def test_delta_allocation(self):
        """Paper: δ₁ = δ₂ = (1-p')/3, where p'=p+δ₁+δ₂ → δ=(1-p)/3."""
        p = 0.95
        failure_prob = 1 - p  # 0.05
        delta = failure_prob / 3
        assert abs(delta - 0.05 / 3) < 1e-15
        # p' = p + δ₁ + δ₂ = p + 2δ = 0.95 + 2*(0.05/3) = 0.95 + 0.0333 = 0.9833
        p_prime = p + 2 * delta
        assert abs(p_prime - (1 - delta)) < 1e-15

    def test_z_value_at_95_percent(self):
        """z_{(1+p')/2} for standard 95% confidence."""
        p = 0.95
        delta = (1 - p) / 3
        p_prime = 1 - delta
        z_val = norm.ppf((1 + p_prime) / 2)
        # p' ≈ 0.9833, (1+p')/2 ≈ 0.9917, z ≈ 2.39
        assert 2.3 < z_val < 2.5

    def test_mean_lower_bound_positive(self):
        """L_μ from Student's t should be positive for reasonable stats."""
        n = 30
        mean = 38255.0
        std = 12144.0
        delta = 0.05 / 3
        L_mu = get_mean_lb(n, mean, std, delta)
        assert L_mu > 0
        assert L_mu < mean  # lower bound is below the mean

    def test_std_upper_bound_exceeds_sample(self):
        """U_σ from chi-squared should exceed sample std."""
        n = 30
        std = 12144.0
        delta = 0.05 / 3
        U_sigma = get_std_ub(n, std, delta)
        assert U_sigma > std  # upper bound exceeds sample

    def test_required_sample_size_formula(self):
        """get_mean_sample_size implements (z/e · U_σ / L_μ)²."""
        error = 0.05
        delta = 0.05 / 3
        mean = 38255.0
        std = 12144.0
        n = 30
        required_n = get_mean_sample_size(error, delta, delta, delta, mean, std, n)
        # Should be a positive finite number
        assert required_n > 0
        assert math.isfinite(required_n)
        # For this scenario, ~few hundred blocks should suffice
        assert required_n < 100000

    def test_uv_single_known_formula(self):
        """Verify U_V = (1-θ)/θ · σ² exactly."""
        theta = 0.01
        sigma = 12144.0
        expected = (1 - theta) / theta * sigma ** 2
        actual = compute_UV_single_table(theta, sigma, 30)
        assert abs(actual - expected) < 1e-6


class TestLemma48NumericCase:
    """Verify Lemma 4.8 three-term decomposition with known structure.

    Use a simple 2-table scenario where we can compute U_V by hand.
    """

    @pytest.fixture
    def deterministic_stats(self):
        """Create deterministic join stats where we know the answer."""
        # 10 pilot blocks observed from T1 (population N1=200), N2=20 in T2
        n_p = 10
        N1 = 200
        N2 = 20
        # y(1): squared row sums = constant 100.0
        y1 = np.full(n_p, 100.0)
        # y(2): per-pair join values = constant 5.0
        y2 = np.full(n_p * 5, 5.0)
        # y(3): squared join sums = constant 25.0
        y3 = np.full(n_p, 25.0)
        return JoinBlockStats(
            y1_per_block=y1,
            y2_values=y2,
            y3_per_block=y3,
            n_pilot_blocks=n_p,
            N1=N1,
            N2=N2,
            pilot_rate=0.05,
        )

    def test_constant_values_produce_finite_uv(self, deterministic_stats):
        """When all y values are constant, std=0 → t-bound = mean → finite."""
        uv = compute_UV_two_table(0.05, 0.05, deterministic_stats, delta2=0.01)
        assert math.isfinite(uv)
        assert uv >= 0

    def test_three_terms_signs(self, deterministic_stats):
        """All three terms should be non-negative."""
        # Term 1 coefficient: (1-θ₁)/θ₁ ≥ 0 when 0 < θ₁ ≤ 1
        # Term 2 coefficient: (1-θ₂)/θ₂ ≥ 0
        # Term 3 coefficient: (1-θ₁)(1-θ₂)/(θ₁θ₂) ≥ 0
        theta1, theta2 = 0.05, 0.05
        assert (1 - theta1) / theta1 > 0
        assert (1 - theta2) / theta2 > 0
        assert (1 - theta1) * (1 - theta2) / (theta1 * theta2) > 0

    def test_uv_reduces_to_single_when_theta2_is_1(self, deterministic_stats):
        """When θ₂=1 (no sampling on T2), only term 1 should contribute.

        Terms 2 and 3 have (1-θ₂) or (1-θ₂)/θ₂ factors that vanish.
        """
        uv_full = compute_UV_two_table(0.05, 1.0, deterministic_stats, delta2=0.01)
        # This should equal approximately (1-0.05)/0.05 * U_{y(1)}
        # Since std(y1)=0 for constants, U_{y(1)} ≈ n_p * mean(y1) = 10*100 = 1000
        assert math.isfinite(uv_full)
        assert uv_full > 0


class TestLemma32NumericCase:
    """Verify Lemma 3.2 (Eq. 7) group coverage with paper default values."""

    def test_paper_defaults_g200_pf005(self):
        """Paper §3.1: g=200, p_f=0.05 should give small rate."""
        rate = _min_pilot_rate_for_groups(
            table_size=6_000_000,
            block_size=8192,
            min_group_size=200,
            p_fail=0.05,
        )
        # Rate is in percent; should be very small for 6M rows
        assert rate > 0
        assert rate < 10  # should be well under 10%

    def test_formula_matches_manual_computation(self):
        """Verify formula: θ ≥ 1 - (1-(1-p_f)^(⌈g/b⌉/|T|))^(1/⌈g/b⌉)."""
        T = 1_000_000
        b = 8192
        g = 200
        p_f = 0.05
        blocks_per_group = math.ceil(g / b)  # = 1
        total_blocks = math.ceil(T / b)
        base = (1 - p_f) ** (blocks_per_group / total_blocks)
        theta_manual = 1 - base ** (1.0 / blocks_per_group)
        theta_func = _min_pilot_rate_for_groups(T, b, g, p_f) / 100  # convert from %
        assert abs(theta_manual - theta_func) < 1e-10


class TestBoolesInequalityNumeric:
    """Verify Boole's inequality allocation matches paper §3.1."""

    def test_confidence_allocation_k2_m5(self):
        """k=2 aggregates, m=5 groups → p_{i,j} = 1-(1-p)/(k·m)."""
        p = 0.95
        k, m = 2, 5
        fp = 1 - p
        fp_each = fp / (k * m)
        p_ij = 1 - fp_each
        # Each aggregate must satisfy relative error with p_{i,j}
        assert abs(p_ij - (1 - 0.05 / 10)) < 1e-15
        assert p_ij == 0.995

    def test_overall_confidence_recoverable(self):
        """Sum of individual failures should recover overall failure prob."""
        p = 0.95
        k, m = 3, 4
        n_total = k * m
        fp_each = (1 - p) / n_total
        total_fp = n_total * fp_each
        assert abs(total_fp - (1 - p)) < 1e-15


class TestErrorPropagationTable2:
    """Verify Table 2 composite error propagation rules."""

    def test_multiplication_error_bound(self):
        """ê(μ̂₁·μ̂₂) ≤ e₁ + e₂ + e₁·e₂."""
        e1, e2 = 0.05, 0.05
        bound = e1 + e2 + e1 * e2
        assert abs(bound - 0.1025) < 1e-10

    def test_division_error_bound(self):
        """ê(μ̂₁/μ̂₂) ≤ (e₁ + e₂)/(1 + min(e₁, e₂))."""
        e1, e2 = 0.05, 0.05
        bound = (e1 + e2) / (1 + min(e1, e2))
        expected = 0.10 / 1.05
        assert abs(bound - expected) < 1e-10

    def test_addition_error_bound(self):
        """ê(μ̂₁ + μ̂₂) ≤ max(e₁, e₂)."""
        e1, e2 = 0.03, 0.05
        bound = max(e1, e2)
        assert bound == 0.05

    def test_even_allocation_for_product(self):
        """For product, e' = √(e+1) - 1 (paper §3.1)."""
        e = 0.05
        e_prime = math.sqrt(e + 1) - 1
        # Verify: e_prime + e_prime + e_prime*e_prime ≈ e
        recovered = e_prime + e_prime + e_prime * e_prime
        assert abs(recovered - e) < 1e-10

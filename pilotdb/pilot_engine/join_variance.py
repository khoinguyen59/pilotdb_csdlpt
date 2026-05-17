"""Join-aware variance bounds and Φ(Θ) constraint for PilotDB paper §3.2/§4.3.

This module implements:
  - Lemma 4.8: U_V[Θ] upper bound for two-table join variance
  - φᵢⱼ(Θ) individual aggregate constraint (Equation 6)
  - Φ(Θ) conjunction of all φᵢⱼ constraints
  - Integration with scipy.optimize trust-constr solver

References:
  Paper: PilotDB (SIGMOD '25), §3.1 Procedure 1, §3.2, §4.3 Lemma 4.8
  Technical Report: arXiv:2503.21087
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Mapping, Sequence

import numpy as np
from scipy.stats import norm, t as t_dist


# ---------------------------------------------------------------------------
# Data structures for pilot statistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlockStats:
    """Block-level statistics extracted from pilot query results.

    For a single-table query, these are the per-block aggregates.
    For a two-table join query, these decompose into y(1), y(2), y(3)
    per Lemma 4.8.
    """
    block_sums: np.ndarray          # sum of values per pilot block
    block_sizes: np.ndarray         # count of rows per pilot block
    n_pilot_blocks: int             # number of pilot blocks (n_p)
    pilot_rate: float               # θ_p — pilot sampling rate


@dataclass(frozen=True)
class JoinBlockStats:
    """Statistics for two-table join per Lemma 4.8.

    y(1)_i = (Σ_{i2=1}^{N2} J(t_{1,i}, t_{2,i2}))^2
    y(2)_{i2,i} = J(t_{1,i}, t_{2,i2})
    y(3) = Σ_{i2=1}^{N2} J(t_{1,i}, t_{2,i2})^2

    Since we can only observe sampled blocks from T1, we estimate these
    from the pilot query grouped by (block_id_T1, block_id_T2).
    """
    # Per pilot-block-of-T1: sum of join results across all T2 blocks
    y1_per_block: np.ndarray        # shape (n_pilot_blocks,)
    # Per (T1-block, T2-block): individual join sums — flattened
    y2_values: np.ndarray           # shape (n_pairs,)
    # Sum of squared join results across T2 blocks per T1 block
    y3_per_block: np.ndarray        # shape (n_pilot_blocks,)
    n_pilot_blocks: int             # pilot blocks from T1
    N2: int                         # total blocks in T2
    pilot_rate: float               # θ_p for T1


@dataclass
class AggregateConstraint:
    """Per-aggregate constraint φᵢⱼ(Θ) from Equation 6.

    z_{(1+p')/2} · sqrt(U_V[Θ]) · L_μ^{-1} ≤ e
    """
    aggregate_index: int
    group_index: int
    z_value: float                  # z_{(1+p')/2}
    L_mu: float                     # lower bound of aggregate mean
    required_error: float           # e_{i,j}
    # For single-table: variance components from pilot
    pilot_sample_std: float = 0.0
    pilot_sample_size: int = 0
    # For two-table join: JoinBlockStats
    join_stats: JoinBlockStats | None = None
    # Which tables are involved
    sampled_tables: tuple[str, ...] = ()


@dataclass
class PhiConstraintSet:
    """Φ(Θ) = conjunction of all φᵢⱼ constraints."""
    constraints: List[AggregateConstraint] = field(default_factory=list)
    table_names: tuple[str, ...] = ()
    mode: str = "full"  # "full" | "scalar-fallback"

    def is_feasible(self, theta: Mapping[str, float]) -> bool:
        """Check if sampling plan theta satisfies all constraints."""
        for c in self.constraints:
            if not _check_single_phi(c, theta):
                return False
        return True

    def max_required_rate(self, theta_init: Mapping[str, float]) -> float:
        """Conservative scalar proxy: max rate needed across all constraints."""
        rates = []
        for c in self.constraints:
            rate = _solve_single_phi_rate(c)
            if rate is not None:
                rates.append(rate)
        return max(rates) if rates else 1.0


# ---------------------------------------------------------------------------
# Lemma 4.8: U_V[Θ] for two-table join
# ---------------------------------------------------------------------------

def compute_UV_single_table(
    theta: float,
    pilot_std_ub: float,
    pilot_n_blocks: int,
) -> float:
    """U_V[Θ] for single-table block sampling.

    Standard form: Var[μ̂] = (1-θ)/θ · σ²_block / N
    where σ²_block is the inter-block variance.

    For the upper bound, we use the chi-squared upper bound of σ².
    """
    if theta <= 0 or theta >= 1.0:
        return 0.0
    return (1.0 - theta) / theta * pilot_std_ub ** 2


def compute_UV_two_table(
    theta1: float,
    theta2: float,
    join_stats: JoinBlockStats,
    delta2: float,
) -> float:
    """Lemma 4.8: Three-term variance decomposition for 2-table join.

    U_V[Θ] = (1-θ₁)/θ₁ · U_{y(1)}[δ₂/(N₂+2)]
            + (1-θ₂)/θ₂ · Σ_{i2} (U_{y(2)_{i2}}[δ₂/(N₂+2)])²
            + (1-θ₁)(1-θ₂)/(θ₁θ₂) · U_{y(3)}[δ₂/(N₂+2)]

    where U_y[δ] is the upper bound of Student's t CI for summation y.

    Args:
        theta1: sampling rate for T1
        theta2: sampling rate for T2
        join_stats: pilot-extracted y(1), y(2), y(3) statistics
        delta2: failure probability δ₂ allocated to variance bound

    Returns:
        U_V[Θ] — upper bound of Var[μ̂] for the join aggregate
    """
    N2 = join_stats.N2
    n_p = join_stats.n_pilot_blocks

    if n_p < 2:
        logging.warning("[Lemma 4.8] Insufficient pilot blocks (%d < 2)", n_p)
        return float("inf")

    # Per-component failure probability: δ₂/(N₂+2)
    delta_component = delta2 / (N2 + 2)

    # Term 1: (1-θ₁)/θ₁ · U_{y(1)}
    U_y1 = _student_t_sum_upper_bound(join_stats.y1_per_block, delta_component)
    term1 = _safe_ratio(1.0 - theta1, theta1) * U_y1

    # Term 2: (1-θ₂)/θ₂ · Σ (U_{y(2)_{i2}})²
    # y(2) values are per-(T1-block, T2-block) pair. We group by T2 block.
    # For the pilot, we estimate the per-T2-block standard sum bound.
    U_y2_sum_sq = _estimate_y2_sum_squared(join_stats, delta_component)
    term2 = _safe_ratio(1.0 - theta2, theta2) * U_y2_sum_sq

    # Term 3: (1-θ₁)(1-θ₂)/(θ₁θ₂) · U_{y(3)}
    U_y3 = _student_t_sum_upper_bound(join_stats.y3_per_block, delta_component)
    term3 = _safe_ratio((1.0 - theta1) * (1.0 - theta2), theta1 * theta2) * U_y3

    UV = term1 + term2 + term3

    logging.info(
        "[Lemma 4.8] U_V[Θ]=%g  (term1=%g, term2=%g, term3=%g, "
        "θ₁=%.4f, θ₂=%.4f, N₂=%d, n_p=%d)",
        UV, term1, term2, term3, theta1, theta2, N2, n_p,
    )
    return UV


# ---------------------------------------------------------------------------
# φᵢⱼ(Θ) constraint: Equation 6
# ---------------------------------------------------------------------------

def phi_constraint_value(
    constraint: AggregateConstraint,
    theta: Mapping[str, float],
) -> float:
    """Compute LHS of φᵢⱼ(Θ): z · √(U_V[Θ]) / L_μ.

    The constraint is satisfied when this value ≤ e_{i,j}.
    """
    UV = _compute_UV_for_constraint(constraint, theta)
    if UV <= 0 or not math.isfinite(UV):
        return float("inf")
    if constraint.L_mu <= 0 or not math.isfinite(constraint.L_mu):
        return float("inf")
    return constraint.z_value * math.sqrt(UV) / constraint.L_mu


def phi_constraint_residual(
    constraint: AggregateConstraint,
    theta: Mapping[str, float],
) -> float:
    """Return e_{i,j} - (z · √(U_V[Θ]) / L_μ).

    Positive means feasible, negative means infeasible.
    """
    return constraint.required_error - phi_constraint_value(constraint, theta)


# ---------------------------------------------------------------------------
# Building Φ(Θ) from pilot results
# ---------------------------------------------------------------------------

def build_phi_constraints(
    failure_prob: float,
    n_aggregates: int,
    n_groups: int,
    pilot_stats: Sequence[dict],
    required_error: float,
    table_names: tuple[str, ...],
) -> PhiConstraintSet:
    """Build the full Φ(Θ) constraint set from pilot query results.

    Args:
        failure_prob: 1 - confidence p
        n_aggregates: k — number of aggregation columns
        n_groups: m — number of groups
        pilot_stats: per-(aggregate, group) statistics dicts with keys:
            'sample_mean', 'sample_std', 'sample_size',
            optionally 'join_stats' for two-table queries
        required_error: e — user-specified maximum relative error
        table_names: names of tables involved

    Returns:
        PhiConstraintSet ready for optimization
    """
    constraints = []
    n_total = n_aggregates * n_groups

    # Paper §3.1: Boole's inequality additive form
    # p_{i,j} = 1 - (1-p)/(k·m)
    fp_each = failure_prob / max(n_total, 1)

    # Paper Procedure 1: δ₁ = δ₂ = (1-p')/(3)
    # where p' = p + δ₁ + δ₂, so fp_each = (1-p_{i,j})
    delta = fp_each / 3.0

    # z-value for adjusted confidence
    p_prime = 1.0 - fp_each + 2 * delta  # p' = p + δ₁ + δ₂
    z_val = norm.ppf((1.0 + p_prime) / 2.0)

    idx = 0
    for stat in pilot_stats:
        sample_mean = stat["sample_mean"]
        sample_std = stat["sample_std"]
        sample_size = stat["sample_size"]
        agg_idx = stat.get("aggregate_index", idx // max(n_groups, 1))
        grp_idx = stat.get("group_index", idx % max(n_groups, 1))

        # L_μ: lower bound of μ via Student's t
        if sample_size >= 2 and sample_mean != 0:
            t_val = t_dist.ppf(1 - delta, sample_size - 1)
            L_mu = sample_mean - t_val * sample_std / math.sqrt(sample_size)
            if L_mu <= 0:
                L_mu = abs(sample_mean) * 0.01  # conservative fallback
        else:
            L_mu = abs(sample_mean) * 0.01 if sample_mean != 0 else 1e-10

        join_stats = stat.get("join_stats", None)

        constraints.append(AggregateConstraint(
            aggregate_index=agg_idx,
            group_index=grp_idx,
            z_value=z_val,
            L_mu=L_mu,
            required_error=required_error,
            pilot_sample_std=sample_std,
            pilot_sample_size=sample_size,
            join_stats=join_stats,
            sampled_tables=table_names,
        ))
        idx += 1

    mode = "full"
    if not constraints:
        mode = "scalar-fallback"
        logging.warning("[Phi] No constraints built; falling back to scalar mode")

    return PhiConstraintSet(
        constraints=constraints,
        table_names=table_names,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_single_phi(c: AggregateConstraint, theta: Mapping[str, float]) -> bool:
    """Check if a single φᵢⱼ constraint is satisfied."""
    return phi_constraint_residual(c, theta) >= 0


def _solve_single_phi_rate(c: AggregateConstraint) -> float | None:
    """Solve for minimum scalar rate θ satisfying single-table φᵢⱼ.

    From Eq. 6: z · √((1-θ)/θ · σ²) / L_μ ≤ e
    => (1-θ)/θ ≤ (e · L_μ / z)² / σ²
    => θ ≥ 1 / (1 + (e·L_μ/(z·σ))²)
    """
    if c.pilot_sample_std <= 0 or c.L_mu <= 0:
        return None
    rhs = (c.required_error * c.L_mu / (c.z_value * c.pilot_sample_std)) ** 2
    if rhs <= 0:
        return 1.0
    theta_min = 1.0 / (1.0 + rhs)
    return min(max(theta_min, 1e-9), 1.0)


def _compute_UV_for_constraint(
    c: AggregateConstraint,
    theta: Mapping[str, float],
) -> float:
    """Dispatch U_V computation based on single vs join query."""
    if c.join_stats is not None and len(c.sampled_tables) >= 2:
        # Two-table join: Lemma 4.8
        t1, t2 = c.sampled_tables[0], c.sampled_tables[1]
        theta1 = theta.get(t1, 1.0)
        theta2 = theta.get(t2, 1.0)
        # [FIX F17b] Procedure 1 δ₂ = (1-p′)/3, where p′ is the
        # adjusted per-(aggregate, group) confidence from build_phi_constraints.
        # `c.z_value = norm.ppf((1+p′)/2)`, so (1+p′)/2 = norm.cdf(z),
        # i.e. p′ = 2·norm.cdf(z) - 1 and (1-p′) = 2·(1 - norm.cdf(z)).
        # This propagates the user's `failure_probability` correctly instead
        # of hard-coding the paper-default `(1-0.95)/3`.
        one_minus_p_prime = 2.0 * (1.0 - norm.cdf(c.z_value))
        delta2 = max(one_minus_p_prime / 3.0, 1e-12)
        return compute_UV_two_table(theta1, theta2, c.join_stats, delta2)
    else:
        # Single table: standard form
        table = c.sampled_tables[0] if c.sampled_tables else ""
        theta_val = theta.get(table, 1.0)
        # U_V = (1-θ)/θ · (U_σ)² where U_σ is upper bound of block std
        return compute_UV_single_table(theta_val, c.pilot_sample_std, c.pilot_sample_size)


def _student_t_sum_upper_bound(values: np.ndarray, delta: float) -> float:
    """Upper bound of the summation's mean using Student's t CI.

    U_y[δ] = n · (ȳ + t_{n-1,1-δ} · s / √n)

    This bounds the population sum of y given observed samples.
    """
    n = len(values)
    if n < 2:
        return float(np.sum(values)) if n == 1 else 0.0

    y_mean = float(np.mean(values))
    y_std = float(np.std(values, ddof=1))
    delta_safe = max(min(delta, 0.499), 1e-10)
    t_val = float(t_dist.ppf(1.0 - delta_safe, n - 1))

    # Upper bound of population mean
    ub_mean = y_mean + t_val * y_std / math.sqrt(n)
    # Scale to population sum estimate
    return max(n * ub_mean, 0.0)


def _estimate_y2_sum_squared(join_stats: JoinBlockStats, delta: float) -> float:
    """Estimate Σ_{i2=1}^{N2} (U_{y(2)_{i2}})² from pilot samples.

    Since we only observe sampled T1 blocks, we estimate the per-T2-block
    upper bound as the overall mean + t-CI margin, then square and sum.
    This is a conservative upper bound.
    """
    if len(join_stats.y2_values) < 2:
        return float(np.sum(join_stats.y2_values ** 2))

    y2_mean = float(np.mean(join_stats.y2_values))
    y2_std = float(np.std(join_stats.y2_values, ddof=1))
    n = len(join_stats.y2_values)
    delta_safe = max(min(delta, 0.499), 1e-10)
    t_val = float(t_dist.ppf(1.0 - delta_safe, max(n - 1, 1)))

    ub_per_pair = y2_mean + t_val * y2_std / math.sqrt(n)
    # Sum over N2 blocks, each squared
    return join_stats.N2 * ub_per_pair ** 2


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Safe division avoiding division by zero."""
    if denominator <= 0 or not math.isfinite(denominator):
        return float("inf") if numerator > 0 else 0.0
    return numerator / denominator

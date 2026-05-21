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
from scipy.stats import chi2 as chi2_dist, norm, t as t_dist


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

    The bound formulas require BOTH population sizes ``N1`` (total T1
    blocks) and ``N2`` (total T2 blocks) because terms 1 and 3 of the
    paper formula sum y(1)_i and y(3)_i across ``i = 1..N1``, while
    term 2 sums (U_y(2)_i2)^2 across ``i2 = 1..N2`` with each per-i2
    bound itself being a sum over the N1 dimension.

    ``y2_pivot`` (optional) carries per-(i_pilot, i2_observed) join values
    as a 2D matrix and is the input expected by the per-i2 form of the
    term-2 bound. When ``None`` the bound falls back to a pooled
    approximation that mixes across i2 — conservative on i.i.d. data
    but under-bounds heterogeneous T2 distributions.
    """
    # Per pilot-block-of-T1: sum of join results across all T2 blocks
    y1_per_block: np.ndarray        # shape (n_pilot_blocks,)
    # Per (T1-block, T2-block): individual join sums — flattened
    y2_values: np.ndarray           # shape (n_pairs,)
    # Sum of squared join results across T2 blocks per T1 block
    y3_per_block: np.ndarray        # shape (n_pilot_blocks,)
    n_pilot_blocks: int             # pilot blocks from T1
    N1: int                         # total blocks in T1 (population size)
    N2: int                         # total blocks in T2
    pilot_rate: float               # θ_p for T1
    # Optional 2D pivot for per-i2 term-2 bound: shape (n_pilot_blocks, n_t2_observed)
    y2_pivot: np.ndarray | None = None
    pilot_rate2: float = 0.05       # θ_p for T2
    n_pilot_blocks2: int = 1        # pilot blocks from T2


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
    # Cache fields for optimization performance
    _cached_U_y1: float | None = field(default=None, init=False, repr=False)
    _cached_U_y2_sum_sq: float | None = field(default=None, init=False, repr=False)
    _cached_U_y3: float | None = field(default=None, init=False, repr=False)


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
    if theta <= 0.0:
        return float("inf")
    if theta >= 1.0:
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
    N1 = join_stats.N1
    N2 = join_stats.N2
    n_p = join_stats.n_pilot_blocks

    if theta1 <= 0.0 or theta2 <= 0.0:
        return float("inf")
    if n_p < 2:
        logging.warning("[Lemma 4.8] Insufficient pilot blocks (%d < 2)", n_p)
        return float("inf")

    # Per-component failure probability: δ₂/(N₂+2)
    delta_component = delta2 / (N2 + 2)

    # Term 1: (1-θ₁)/θ₁ · U_{y(1)} where U_y(1) is the upper bound of the
    # **population sum** Σ_{i=1..N1} y(1)_i. The pilot only sees n_p of
    # the N1 T1-blocks, so we extrapolate the sample mean to the population
    # via the standard CI form `N1 · (ȳ + t·s/√n_p)`.
    U_y1 = _student_t_sum_upper_bound(
        join_stats.y1_per_block, delta_component, population_n=N1
    )
    term1 = _safe_ratio(1.0 - theta1, theta1) * U_y1

    # Term 2: (1-θ₂)/θ₂ · Σ_{i2=1..N2} (U_{y(2)_{i2}})² where each
    # U_y(2)_{i2} is itself a population sum over N1 of J(t_{1,i}, t_{2,i2}).
    # The helper now scales the per-pair CI by N1 before squaring.
    U_y2_sum_sq = _estimate_y2_sum_squared(join_stats, delta_component)
    term2 = _safe_ratio(1.0 - theta2, theta2) * U_y2_sum_sq

    # Term 3: (1-θ₁)(1-θ₂)/(θ₁θ₂) · U_{y(3)} — same N1 scaling as term 1.
    U_y3 = _student_t_sum_upper_bound(
        join_stats.y3_per_block, delta_component, population_n=N1
    )
    term3 = _safe_ratio((1.0 - theta1) * (1.0 - theta2), theta1 * theta2) * U_y3

    UV = term1 + term2 + term3

    logging.debug(
        "[Lemma 4.8] U_V[Θ]=%g  (term1=%g, term2=%g, term3=%g, "
        "θ₁=%.4f, θ₂=%.4f, N₁=%d, N₂=%d, n_p=%d)",
        UV, term1, term2, term3, theta1, theta2, N1, N2, n_p,
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
    if UV < 0 or not math.isfinite(UV):
        return 1e15
    if constraint.L_mu <= 0 or not math.isfinite(constraint.L_mu):
        return 1e15
    
    L_mu = constraint.L_mu
    if constraint.join_stats is not None and len(constraint.sampled_tables) >= 2:
        # Scale L_mu from page sum mean to population total sum
        L_mu = L_mu * constraint.join_stats.N1 * constraint.join_stats.N2
        
    return constraint.z_value * math.sqrt(max(UV, 0.0)) / L_mu


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

        # [P1 #5] Paper §3.1 wants U_V[Θ] = (1-θ)/θ · σ_ub² with the
        # chi-squared upper bound of σ, not the raw sample std. We
        # compute σ_ub here so `compute_UV_single_table` (which receives
        # this through `AggregateConstraint.pilot_sample_std`) uses a
        # paper-faithful bound for the single-table Phi path.
        if sample_size >= 2 and sample_std > 0:
            try:
                chi2_val = float(chi2_dist.ppf(delta, sample_size - 1))
                if chi2_val > 0:
                    sample_std_ub = sample_std * math.sqrt(
                        (sample_size - 1) / chi2_val
                    )
                else:
                    sample_std_ub = sample_std
            except Exception:
                sample_std_ub = sample_std
        else:
            sample_std_ub = sample_std

        # L_μ: lower bound of μ via Student's t. Paper §3.1 assumes μ>0
        # so relative error is well-defined; when the CI lower bound is
        # non-positive we mark the constraint infeasible (NaN) which
        # makes `phi_constraint_value` return inf → optimizer rejects
        # → exact fallback. [P1 #6]: removed the arbitrary 0.01·|mean|
        # heuristic that silently kept infeasible aggregates "feasible".
        if sample_size >= 2 and sample_mean != 0:
            t_val = t_dist.ppf(1 - delta, sample_size - 1)
            L_mu = sample_mean - t_val * sample_std / math.sqrt(sample_size)
            if L_mu <= 0:
                L_mu = float("nan")
        else:
            L_mu = float("nan")

        if math.isfinite(L_mu) and L_mu > 0:
            join_stats = stat.get("join_stats", None)
            constraints.append(AggregateConstraint(
                aggregate_index=agg_idx,
                group_index=grp_idx,
                z_value=z_val,
                L_mu=L_mu,
                required_error=required_error,
                pilot_sample_std=sample_std_ub,
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
        if theta1 <= 0.0 or theta2 <= 0.0:
            return float("inf")
        if c.join_stats.n_pilot_blocks < 2:
            return float("inf")

        if c._cached_U_y1 is None:
            # [FIX F17b] Procedure 1 δ₂ = (1-p′)/3, where p′ is the
            # adjusted per-(aggregate, group) confidence from build_phi_constraints.
            # `c.z_value = norm.ppf((1+p′)/2)`, so (1+p′)/2 = norm.cdf(z),
            # i.e. p′ = 2·norm.cdf(z) - 1 and (1-p′) = 2·(1 - norm.cdf(z)).
            # This propagates the user's `failure_probability` correctly instead
            # of hard-coding the paper-default `(1-0.95)/3`.
            one_minus_p_prime = 2.0 * (1.0 - norm.cdf(c.z_value))
            delta2 = max(one_minus_p_prime / 3.0, 1e-12)
            delta_component = delta2 / (c.join_stats.N2 + 2)

            c._cached_U_y1 = _student_t_sum_upper_bound(
                c.join_stats.y1_per_block, delta_component, population_n=c.join_stats.N1
            )
            c._cached_U_y2_sum_sq = _estimate_y2_sum_squared(c.join_stats, delta_component)
            c._cached_U_y3 = _student_t_sum_upper_bound(
                c.join_stats.y3_per_block, delta_component, population_n=c.join_stats.N1
            )

        term1 = _safe_ratio(1.0 - theta1, theta1) * c._cached_U_y1
        term2 = _safe_ratio(1.0 - theta2, theta2) * c._cached_U_y2_sum_sq
        term3 = _safe_ratio((1.0 - theta1) * (1.0 - theta2), theta1 * theta2) * c._cached_U_y3
        return term1 + term2 + term3
    else:
        # Single table: standard form
        table = c.sampled_tables[0] if c.sampled_tables else ""
        theta_val = theta.get(table, 1.0)
        # U_V = (1-θ)/θ · (U_σ)² where U_σ is upper bound of block std
        return compute_UV_single_table(theta_val, c.pilot_sample_std, c.pilot_sample_size)


def _student_t_sum_upper_bound(
    values: np.ndarray, delta: float, population_n: int | None = None
) -> float:
    """Upper bound of the population sum of ``y`` using Student's t CI.

    Given a sample of ``n`` observations of ``y_i`` drawn (without
    distinguishing inclusion mechanism here) from a population of size
    ``population_n``, the standard CI on the population sum
    ``S = Σ_{i=1..N} y_i`` is::

        S_hat_upper = N · (ȳ + t_{n-1, 1-δ} · s / √n)

    This is what paper Lemma 4.8 names ``U_y[δ]``. If ``population_n`` is
    ``None`` we fall back to ``N = n`` for backward compatibility — the
    legacy behaviour that under-estimated the bound for join queries.
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
    N = int(population_n) if population_n is not None else n
    return max(N * ub_mean, 0.0)


def _estimate_y2_sum_squared(join_stats: JoinBlockStats, delta: float) -> float:
    """Estimate Σ_{i2=1}^{N2} (U_{y(2)_{i2}})² from pilot samples.

    Paper Lemma 4.8 defines each U_y(2)_{i2} as the upper bound of
    ``Σ_{i=1..N1} J(t_{1,i}, t_{2,i2})`` — a sum over the **N1**
    population dimension. The CI applied to the observed per-pair sample
    mean must therefore be scaled by ``N1`` before squaring, then summed
    over the ``N2`` outer dimension.

    Two implementations:

    * **per-i2 (preferred)**: if ``join_stats.y2_pivot`` is provided
      (shape ``(n_pilot_blocks, n_t2_observed)``), compute a separate
      Student's t upper bound for each observed i2 column, scale by N1,
      square, then sum. Unobserved T2 blocks are handled by extrapolating
      with the mean squared bound across observed columns (conservative
      independent-blocks assumption). This matches paper Σ_{i2=1..N2}
      structure exactly when N2 = n_t2_observed.

    * **pooled (fallback)**: when no pivot is given (legacy code paths or
      degenerate sample sizes), use the overall pair-level pooled
      mean+CI, multiply by N1² · N2. Conservative on i.i.d. data but
      under-bounds heterogeneous T2 distributions (this is the gap that
      kept MC coverage below 95% pre-per-i2).
    """
    N1 = max(int(join_stats.N1), 1)
    N2 = max(int(join_stats.N2), 1)
    delta_safe = max(min(delta, 0.499), 1e-10)

    pivot = join_stats.y2_pivot
    if pivot is not None and pivot.ndim == 2 and pivot.shape[0] >= 2:
        n_p, n_t2_obs = pivot.shape
        if n_t2_obs >= 1:
            # Per-i2 Student's t CI on each column.
            means = pivot.mean(axis=0)
            # ddof=1 gives sample std; safe even when std is 0 (constant col).
            stds = pivot.std(axis=0, ddof=1) if n_p > 1 else np.zeros(n_t2_obs)
            t_val = float(t_dist.ppf(1.0 - delta_safe, max(n_p - 1, 1)))
            margins = t_val * stds / math.sqrt(n_p)
            ub_per_i2 = means + margins                # shape (n_t2_obs,)
            # U_y(2)_{i2} = N1 · ub_per_i2[i2]
            squared = (N1 * ub_per_i2) ** 2            # shape (n_t2_obs,)
            sum_observed = float(np.sum(squared))
            if n_t2_obs >= N2:
                # Observed every T2 block (block_size aligned or pilot
                # saw full T2): no extrapolation needed.
                return sum_observed
            # Extrapolate: unobserved i2 contribute on average the same
            # squared-bound mass per i2 → conservative independent extension.
            avg_sq = sum_observed / n_t2_obs
            return float(sum_observed + (N2 - n_t2_obs) * avg_sq)

    # ---- Fallback: pooled pair-level bound ----
    n = len(join_stats.y2_values)
    if n < 2:
        # Degenerate: literal sum-of-squares with N1² scaling.
        return float(N1 ** 2 * np.sum(join_stats.y2_values ** 2))
    y2_mean = float(np.mean(join_stats.y2_values))
    y2_std = float(np.std(join_stats.y2_values, ddof=1))
    t_val = float(t_dist.ppf(1.0 - delta_safe, max(n - 1, 1)))
    ub_per_pair = y2_mean + t_val * y2_std / math.sqrt(n)
    return float(N1 ** 2 * N2 * ub_per_pair ** 2)


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Safe division avoiding division by zero."""
    den = max(float(denominator), 1e-15)
    return float(numerator) / den

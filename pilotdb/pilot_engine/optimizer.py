"""Sampling-plan optimization helpers for PilotDB paper §3.2."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Mapping

import numpy as np
from scipy.optimize import NonlinearConstraint, minimize

from pilotdb.pilot_engine.sampling_plan import SamplingPlan
from pilotdb.pilot_engine.join_variance import (
    PhiConstraintSet,
    phi_constraint_residual,
)


DEFAULT_HIGH_CARDINALITY_THRESHOLD = 1_000_000


@dataclass(frozen=True)
class CandidateTable:
    name: str
    size: int
    scanned: bool = True
    high_cardinality: bool = True


@dataclass(frozen=True)
class OptimizationContext:
    candidate_tables: tuple[CandidateTable, ...]
    exact_cost: float | None = None
    notes: str = ""


def optimize_sampling_plan(candidate_sample_rates):
    """Paper ?3.2 trust-region solver for the legacy scalar-rate path.

    For a single sampled table, minimizing theta subject to theta >= c_i is
    equivalent to the old max(candidate_sample_rates), but this keeps the
    optimization mechanism explicit and paper-aligned.
    """
    if not candidate_sample_rates:
        return -1

    valid_rates = [r for r in candidate_sample_rates if r > 0 and not np.isnan(r)]
    if not valid_rates:
        return -1

    def objective(x):
        return x[0]

    def jacobian(x):
        return np.array([1.0])

    def hessian(x):
        return np.array([[0.0]])

    x0 = np.array([max(0.05, min(max(valid_rates), 1.0))])
    constraints = [
        NonlinearConstraint(lambda x, c=c_i: x[0] - c, 0, np.inf)
        for c_i in valid_rates
    ]

    try:
        res = minimize(
            objective,
            x0,
            method="trust-constr",
            jac=jacobian,
            hess=hessian,
            constraints=constraints,
            bounds=[(0.000001, 1.0)],
            options={"disp": False, "maxiter": 100},
        )
        if res.success:
            optimal_rate = float(res.x[0])
            logging.info(
                "[optimizer] Trust-region solver converged to optimal rate: %.4f",
                optimal_rate,
            )
            return optimal_rate
        logging.warning("[optimizer] Trust-region solver failed; falling back to max()")
        return max(valid_rates)
    except Exception as exc:
        logging.error("[optimizer] SciPy optimization failed: %s. Falling back to max()", exc)
        return max(valid_rates)


def identify_candidate_tables(
    query_tables: Iterable[str],
    table_sizes: Mapping[str, int] | None,
    scanned_tables: Iterable[str] | None = None,
    high_cardinality_threshold: int = DEFAULT_HIGH_CARDINALITY_THRESHOLD,
) -> tuple[CandidateTable, ...]:
    """Filter tables according to the paper's scanned/high-cardinality rule."""
    if not table_sizes:
        return tuple()

    query_table_set = set(query_tables)
    scanned_table_set = set(scanned_tables) if scanned_tables is not None else query_table_set
    candidates = []
    for table in query_tables:
        if table not in table_sizes:
            continue
        size = int(table_sizes[table])
        scanned = table in scanned_table_set
        high_cardinality = size >= high_cardinality_threshold
        if scanned and high_cardinality:
            candidates.append(CandidateTable(table, size, scanned, high_cardinality))
    return tuple(candidates)


def enumerate_table_subsets(
    candidate_tables: Iterable[CandidateTable],
) -> tuple[tuple[str, ...], ...]:
    table_names = [candidate.name for candidate in candidate_tables]
    subsets = []
    for size in range(1, len(table_names) + 1):
        subsets.extend(combinations(table_names, size))
    return tuple(subsets)


def make_legacy_plan(table: str | None, sample_rate: float, reason: str) -> SamplingPlan:
    if table is None:
        return SamplingPlan(rates={}, reason=reason)
    return SamplingPlan(rates={table: sample_rate}, reason=reason)


def build_optimization_context(
    query_tables: Iterable[str],
    table_sizes: Mapping[str, int] | None,
    scanned_tables: Iterable[str] | None = None,
    exact_cost: float | None = None,
    phi_constraints: PhiConstraintSet | None = None,
) -> OptimizationContext:
    candidates = identify_candidate_tables(query_tables, table_sizes, scanned_tables)
    has_full_phi = phi_constraints is not None and phi_constraints.mode == "full"
    notes = (
        "Full Phi(Theta) vector constraints active."
        if has_full_phi
        else "Scalar proxy fallback — Phi(Theta) constraints not provided."
    )
    if not has_full_phi:
        logging.warning(
            "[optimizer] %s This is NOT paper-100%% mode. "
            "Provide PhiConstraintSet for full guarantee.",
            notes,
        )
    return OptimizationContext(
        candidate_tables=candidates,
        exact_cost=exact_cost,
        notes=notes,
    )


def _plan_cost_objective(table_names: tuple[str, ...], table_sizes: Mapping[str, int]):
    sizes = np.array([float(table_sizes[name]) for name in table_names], dtype=float)

    def objective(theta):
        return float(np.dot(theta, sizes))

    return objective


def _table_weighted_objective(
    table_names: tuple[str, ...],
    table_sizes: Mapping[str, int],
    primary_idx: int,
):
    """Create an objective that heavily weights table `primary_idx`.

    Paper §3.2: for each subset S of k tables, generate k candidate
    plans by varying which table's sampling cost dominates the objective.
    The primary table gets 10× weight relative to others, biasing the
    optimizer toward minimizing *that* table's rate.
    """
    sizes = np.array([float(table_sizes[name]) for name in table_names], dtype=float)
    weights = np.ones(len(table_names), dtype=float)
    weights[primary_idx] = 10.0
    weighted = sizes * weights

    def objective(theta):
        return float(np.dot(theta, weighted))

    return objective


def solve_trust_region_plan(
    subset: Iterable[str],
    table_sizes: Mapping[str, int],
    min_rate: float,
    max_rate: float = 0.1,
    phi_constraints: PhiConstraintSet | None = None,
    objective_fn=None,
    reason_suffix: str = "",
) -> SamplingPlan | None:
    """Solve a bounded sampled-volume objective with trust-constr.

    When phi_constraints is provided (full paper mode), each φᵢⱼ(Θ) from
    Equation 6 is added as a NonlinearConstraint. Otherwise, falls back to
    the conservative scalar BSAP lower bound with an explicit warning.

    If objective_fn is provided, uses it directly; otherwise defaults to
    the standard cost objective (sum of table_size * theta).
    """
    table_names = tuple(subset)
    if not table_names:
        return None
    lower = max(float(min_rate), 1e-9)
    upper = min(float(max_rate), 1.0)
    if lower > upper:
        return None

    bounds = [(lower, upper) for _ in table_names]
    x0 = np.array([(lower + upper) / 2 for _ in table_names], dtype=float)

    scipy_constraints = []
    reason = "trust-constr bounded proxy for §3.2"

    if phi_constraints is not None and phi_constraints.mode == "full":
        # Full paper mode: add φᵢⱼ(Θ) constraints
        for c in phi_constraints.constraints:
            def make_phi_fn(constraint):
                def fn(x):
                    theta_map = {t: float(x[i]) for i, t in enumerate(table_names)}
                    # For tables not in subset, rate = 1.0
                    return phi_constraint_residual(constraint, theta_map)
                return fn

            scipy_constraints.append(
                NonlinearConstraint(make_phi_fn(c), 0.0, np.inf)
            )
        reason = "trust-constr with full Phi(Theta) constraints §3.2"
        logging.info(
            "[optimizer] Using %d Phi(Theta) constraints for %s",
            len(phi_constraints.constraints), table_names,
        )
    else:
        # Scalar fallback — log explicitly per user requirement
        logging.warning(
            "[optimizer] No Phi(Theta) constraints for %s; "
            "using scalar lower-bound proxy. THIS IS NOT PAPER-100%% MODE.",
            table_names,
        )

    if objective_fn is None:
        objective_fn = _plan_cost_objective(table_names, table_sizes)

    result = minimize(
        objective_fn,
        x0,
        method="trust-constr",
        bounds=bounds,
        constraints=scipy_constraints,
        options={"verbose": 0},
    )
    if not result.success:
        return None
    rates = {table: float(rate) for table, rate in zip(table_names, result.x)}
    full_reason = reason + (f" ({reason_suffix})" if reason_suffix else "")
    return SamplingPlan(
        rates=rates,
        estimated_cost=float(result.fun),
        reason=full_reason,
    )


def generate_candidate_plans(
    context: OptimizationContext,
    table_sizes: Mapping[str, int],
    min_rate: float,
    max_rate: float = 0.1,
    phi_constraints: PhiConstraintSet | None = None,
) -> tuple[SamplingPlan, ...]:
    """Paper §3.2: for each subset S, produce |S| candidates.

    [FIX F13] Each candidate uses a different table-weighted objective,
    so the optimizer explores plans that preferentially minimize each
    table's sampling rate. This gives the cost model more options to
    choose from, matching the paper's specification.
    """
    plans = []
    for subset in enumerate_table_subsets(context.candidate_tables):
        table_names = tuple(subset)
        n_tables = len(table_names)

        if n_tables == 1:
            # Single-table: only one plan possible
            plan = solve_trust_region_plan(
                subset, table_sizes, min_rate, max_rate,
                phi_constraints=phi_constraints,
            )
            if plan is not None:
                plans.append(plan)
        else:
            # [FIX F13] Multi-table: |S| candidates with varying objectives
            for primary_idx in range(n_tables):
                obj_fn = _table_weighted_objective(
                    table_names, table_sizes, primary_idx
                )
                plan = solve_trust_region_plan(
                    subset, table_sizes, min_rate, max_rate,
                    phi_constraints=phi_constraints,
                    objective_fn=obj_fn,
                    reason_suffix=f"primary={table_names[primary_idx]}",
                )
                if plan is not None:
                    plans.append(plan)

            # Also add the uniform-weight (default) plan
            plan = solve_trust_region_plan(
                subset, table_sizes, min_rate, max_rate,
                phi_constraints=phi_constraints,
                reason_suffix="uniform",
            )
            if plan is not None:
                plans.append(plan)

    return tuple(plans)


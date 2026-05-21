"""Monte Carlo empirical-coverage tests for Lemma 4.8 U_V[Theta].

Goal
----
Verify that ``compute_UV_two_table`` produces a valid upper bound on the
true Var[mu_hat] for the block-level mean of a 2-table join under Bernoulli
block sampling on BOTH tables.

The synthetic test bench fixes a ground-truth join matrix ``J[i, i2]`` of
shape (N1, N2) so the population mean ``mu`` and the empirical variance of
``mu_hat`` can be measured directly. Pilot statistics are extracted from a
single Bernoulli sample of T1 (with full T2), matching the production path
in ``_extract_join_block_stats``.

The coverage assertion is conservative on purpose: with 95% confidence the
upper bound is expected to hold in ≥ 95% of independent realisations, but
because (a) we feed a single pilot per trial and (b) the bound combines
three failure-probability components, the test averages the bound across
many independent pilot draws and only asks that the *median* bound exceed
the empirical variance — that's enough to expose a missing scaling factor
(which would underestimate by orders of magnitude) without being flaky
under randomness.

If the test FAILS, B1 (Lemma 4.8 scaling) is confirmed as a bug.
"""

from __future__ import annotations

import numpy as np
import pytest

from pilotdb.pilot_engine.join_variance import (
    JoinBlockStats,
    compute_UV_two_table,
)


def _build_pilot_join_stats(
    J: np.ndarray,
    pilot_mask: np.ndarray,
    N2: int,
    pilot_rate: float,
    N1: int | None = None,
) -> JoinBlockStats:
    """Build a JoinBlockStats from a ground-truth join matrix and a pilot
    Bernoulli sample of T1 blocks (T2 is fully scanned by the pilot, matching
    the production behaviour in ``_extract_join_block_stats``).

    Indices follow paper Lemma 4.8:
      y1_per_block[i_pilot] = (sum over i2 of J[i_pilot, i2]) ** 2
      y2_values             = flattened J[i_pilot, i2] for every (i_pilot, i2)
      y3_per_block[i_pilot] = sum over i2 of J[i_pilot, i2] ** 2

    ``N1`` defaults to ``J.shape[0]`` (the full T1 population size) which
    matches the production extractor's behaviour of computing
    ``ceil(t1_size / block_size)``.
    """
    pilot_rows = J[pilot_mask]                # shape (n_p, N2)
    y1 = (pilot_rows.sum(axis=1)) ** 2        # (n_p,)
    y3 = (pilot_rows ** 2).sum(axis=1)        # (n_p,)
    y2 = pilot_rows.reshape(-1)               # (n_p * N2,)
    if N1 is None:
        N1 = J.shape[0]
    return JoinBlockStats(
        y1_per_block=y1.astype(float),
        y2_values=y2.astype(float),
        y3_per_block=y3.astype(float),
        n_pilot_blocks=int(pilot_mask.sum()),
        N1=int(N1),
        N2=int(N2),
        pilot_rate=float(pilot_rate),
        # Pass 2D pivot directly — per-i2 path in `_estimate_y2_sum_squared`.
        y2_pivot=pilot_rows.astype(float),
    )


def _empirical_var_sum_hat(
    J: np.ndarray,
    theta1: float,
    theta2: float,
    n_trials: int,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """Monte Carlo Var[S_hat] under Bernoulli block sampling on both tables.

    Paper Lemma 4.8 bounds the **variance of the SUM estimator** of the
    Join result. The unbiased Horvitz-Thompson SUM estimator is::

        S_hat = (1 / (theta1 * theta2)) * sum(J[i, i2] for (i, i2) in S)

    where S is the random sample of pairs whose T1-block was kept (Bernoulli
    theta1) AND whose T2-block was kept (Bernoulli theta2). The
    block-level mean estimator differs from S_hat by a factor of N1*N2, so
    using `sub.mean()` here would compare against a quantity at the
    wrong scale and hide a missing N1 factor in the bound.

    Returns (mean(S_hat), var(S_hat)) across n_trials independent draws.
    """
    N1, N2 = J.shape
    sums: list[float] = []
    for _ in range(n_trials):
        mask1 = rng.random(N1) < theta1
        mask2 = rng.random(N2) < theta2
        if mask1.sum() < 1 or mask2.sum() < 1:
            sums.append(0.0)
            continue
        sampled = J[np.ix_(mask1, mask2)].sum()
        sums.append(float(sampled / (theta1 * theta2)))
    arr = np.asarray(sums, dtype=float)
    return float(arr.mean()), float(arr.var(ddof=1))


@pytest.fixture
def join_matrix_homogeneous():
    """A near-i.i.d. join matrix — easy case for variance bounds."""
    rng = np.random.default_rng(20250518)
    return rng.exponential(scale=10.0, size=(200, 100)), 200, 100


@pytest.fixture
def join_matrix_heterogeneous():
    """A heterogeneous matrix with per-row mean drift, exposing the scaling
    factor more clearly than the homogeneous case."""
    rng = np.random.default_rng(101)
    N1, N2 = 200, 100
    row_means = rng.gamma(shape=2.0, scale=5.0, size=N1)
    J = rng.exponential(scale=1.0, size=(N1, N2)) * row_means[:, None]
    return J, N1, N2


def test_uv_two_table_covers_empirical_variance_homogeneous(
    join_matrix_homogeneous,
):
    """U_V[Theta] should dominate the empirical Var[mu_hat] on i.i.d. data.

    If B1 (missing N1 scaling) is real, U_V will under-estimate the true
    variance by a factor proportional to N1/n_p, which is huge here
    (N1=200, n_p ≈ 20). Median UV across many pilot draws is compared
    against the empirical variance.
    """
    J, N1, N2 = join_matrix_homogeneous
    rng = np.random.default_rng(7)
    theta1, theta2 = 0.10, 0.10
    pilot_rate = 0.10

    _, empirical_var = _empirical_var_sum_hat(J, theta1, theta2, n_trials=2000, rng=rng)

    uv_values: list[float] = []
    for _ in range(200):
        pilot_mask = rng.random(N1) < pilot_rate
        if pilot_mask.sum() < 5:
            continue
        stats = _build_pilot_join_stats(J, pilot_mask, N2, pilot_rate)
        uv = compute_UV_two_table(theta1, theta2, stats, delta2=0.05)
        if np.isfinite(uv):
            uv_values.append(uv)

    assert uv_values, "no finite UV samples produced"
    median_uv = float(np.median(uv_values))
    # If scaling is missing, median_uv is hundreds of times smaller than
    # empirical_var. We require the bound to dominate the truth.
    assert median_uv >= empirical_var, (
        f"Median U_V[Theta]={median_uv:.4g} < empirical Var[mu_hat]={empirical_var:.4g} "
        f"(N1={N1}, N2={N2}, theta1={theta1}, theta2={theta2}). "
        f"Likely missing N1 scaling factor in Lemma 4.8 implementation."
    )


def test_uv_two_table_covers_empirical_variance_heterogeneous(
    join_matrix_heterogeneous,
):
    """Same coverage check on a heterogeneous matrix."""
    J, N1, N2 = join_matrix_heterogeneous
    rng = np.random.default_rng(13)
    theta1, theta2 = 0.10, 0.10
    pilot_rate = 0.10

    _, empirical_var = _empirical_var_sum_hat(J, theta1, theta2, n_trials=2000, rng=rng)

    uv_values: list[float] = []
    for _ in range(200):
        pilot_mask = rng.random(N1) < pilot_rate
        if pilot_mask.sum() < 5:
            continue
        stats = _build_pilot_join_stats(J, pilot_mask, N2, pilot_rate)
        uv = compute_UV_two_table(theta1, theta2, stats, delta2=0.05)
        if np.isfinite(uv):
            uv_values.append(uv)

    assert uv_values
    median_uv = float(np.median(uv_values))
    assert median_uv >= empirical_var, (
        f"Median U_V[Theta]={median_uv:.4g} < empirical Var[mu_hat]={empirical_var:.4g} "
        f"(N1={N1}, N2={N2}, theta1={theta1}, theta2={theta2}). "
        f"Likely missing N1 scaling factor in Lemma 4.8 implementation."
    )


def test_uv_scaling_sensitivity_to_N1():
    """If the bound is correct, U_V[Theta] should scale with N1 — everything
    else equal. We sub-slice **the same generative distribution** (a single
    pre-drawn large matrix) for the two N1 values, so any U_V ratio reflects
    the function's awareness of N1 rather than random-data variation.
    A flat ratio means term1/term3 are constructed without N1 awareness.
    """
    rng = np.random.default_rng(55)
    N2 = 100
    theta1, theta2 = 0.10, 0.10
    pilot_rate = 0.20

    # Single i.i.d. draw — slice prefixes for the two N1 settings so the
    # only difference between the two arms is the *population size*, not
    # the underlying distribution.
    N1_large = 500
    J_full = rng.exponential(scale=10.0, size=(N1_large, N2))
    J_small = J_full[:50]   # N1 = 50
    J_large = J_full        # N1 = 500

    def _collect_uv(J, n_pilots):
        N1 = J.shape[0]
        out: list[float] = []
        for _ in range(n_pilots):
            pilot_mask = rng.random(N1) < pilot_rate
            if pilot_mask.sum() < 5:
                continue
            stats = _build_pilot_join_stats(J, pilot_mask, N2, pilot_rate)
            uv = compute_UV_two_table(theta1, theta2, stats, delta2=0.05)
            if np.isfinite(uv):
                out.append(uv)
        return out

    uv_small = _collect_uv(J_small, n_pilots=150)
    uv_large = _collect_uv(J_large, n_pilots=150)
    med_small = float(np.median(uv_small))
    med_large = float(np.median(uv_large))
    ratio = med_large / med_small if med_small > 0 else float("inf")
    # Paper term1/term3 are sums over N1 T1 blocks → U_V should grow
    # roughly linearly in N1. With N1=500 vs N1=50 (10x), a *correct*
    # implementation gives ratio ≈ 10. A flat ratio (<2) would expose
    # the missing N1 factor flagged in B1.
    assert ratio > 2.0, (
        f"U_V[Theta] is insensitive to N1 on the same generative distribution "
        f"(ratio N1=500 vs N1=50 = {ratio:.2f}). Expected ratio ~10 if the "
        f"bound scales linearly with N1; a flat ratio indicates missing N1 "
        f"scaling in term1/term3."
    )


def test_uv_two_table_paper_coverage_probability():
    """Paper Lemma 4.8 claims P[Var[mu_hat] <= U_V[Theta]] >= 1 - delta_2.

    With delta_2 = 0.05 the bound must hold for at least ~95% of pilots
    drawn under the same generating process. We empirically check the
    coverage rate across many independent pilot samples and require it
    to be at least 80% (loose threshold — paper guarantees 95%, but we
    leave headroom for MC noise of the empirical variance reference).

    A coverage rate near 0% strongly suggests the bound systematically
    under-estimates the true variance — i.e. B1 is real.
    """
    rng = np.random.default_rng(2024)
    N1, N2 = 300, 150
    theta1, theta2 = 0.05, 0.05
    pilot_rate = 0.02
    delta_2 = 0.05

    # Heterogeneous: per-row drift exposes scaling more than i.i.d.
    row_means = rng.gamma(shape=2.0, scale=5.0, size=N1)
    J = rng.exponential(scale=1.0, size=(N1, N2)) * row_means[:, None]

    _, empirical_var = _empirical_var_sum_hat(
        J, theta1, theta2, n_trials=5000, rng=rng
    )

    n_pilots = 300
    covered = 0
    finite = 0
    uvs: list[float] = []
    for _ in range(n_pilots):
        pilot_mask = rng.random(N1) < pilot_rate
        if pilot_mask.sum() < 5:
            continue
        stats = _build_pilot_join_stats(J, pilot_mask, N2, pilot_rate)
        uv = compute_UV_two_table(theta1, theta2, stats, delta2=delta_2)
        if not np.isfinite(uv):
            continue
        finite += 1
        uvs.append(uv)
        if uv >= empirical_var:
            covered += 1

    assert finite > 0
    coverage = covered / finite
    # Paper guarantees Pr[U_V[Theta] >= Var[S_hat]] >= 1 - delta_2 = 0.95.
    # With the per-i2 term-2 bound (`y2_pivot` populated), the bound is
    # genuinely close to the paper asymptotic. We assert >= 93% to leave
    # ~2pp MC headroom on 300 pilots (standard deviation of the empirical
    # coverage rate at p=0.95, n=300 is sqrt(0.95*0.05/300) ≈ 1.3pp).
    assert coverage >= 0.93, (
        f"Coverage probability {coverage:.2%} below 93% threshold for SUM "
        f"estimator (empirical Var[S_hat]={empirical_var:.4g}, "
        f"median U_V={float(np.median(uvs)):.4g}, "
        f"min U_V={float(np.min(uvs)):.4g}). "
        f"Paper Lemma 4.8 promises >= {1 - delta_2:.0%}. A coverage near "
        f"zero would indicate U_V[Theta] under-estimates the SUM-estimator "
        f"variance — likely a missing N1 scaling in "
        f"`_student_t_sum_upper_bound`."
    )

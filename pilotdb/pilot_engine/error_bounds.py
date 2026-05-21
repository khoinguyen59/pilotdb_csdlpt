import logging
import math
from typing import Dict, List

import pandas as pd
from scipy.stats import chi2, norm, t

from pilotdb.pilot_engine.bounds_safety import validate_mean_bound_inputs
from pilotdb.pilot_engine.optimizer import optimize_sampling_plan


def get_mean_ub(
    sample_size: int, sample_mean: float, sample_std: float, failure_probability: float
):
    t_val = t.ppf(1 - failure_probability, sample_size - 1)
    return sample_mean + t_val * sample_std / (sample_size**0.5)


def get_mean_lb(
    sample_size: int, sample_mean: float, sample_std: float, failure_probability: float
):
    t_val = t.ppf(1 - failure_probability, sample_size - 1)
    return sample_mean - t_val * sample_std / (sample_size**0.5)


def get_mean_two_side(
    sample_size: int, sample_mean: float, sample_std: float, failure_probability: float
):
    failure_probability = failure_probability / 2
    lb = get_mean_lb(sample_size, sample_mean, sample_std, failure_probability)
    ub = get_mean_ub(sample_size, sample_mean, sample_std, failure_probability)
    return lb, ub


def get_std_lb(sample_size: int, sample_std: float, failure_probability: float):
    chi2_val = chi2.ppf(1 - failure_probability, sample_size - 1)
    return sample_std * ((sample_size - 1) / chi2_val) ** 0.5


def get_std_ub(sample_size: int, sample_std: float, failure_probability: float):
    chi2_val = chi2.ppf(failure_probability, sample_size - 1)
    return sample_std * ((sample_size - 1) / chi2_val) ** 0.5


def get_std_two_side(sample_size: int, sample_std: float, failure_probability: float):
    failure_probability = failure_probability / 2
    lb = get_std_lb(sample_size, sample_std, failure_probability)
    ub = get_std_ub(sample_size, sample_std, failure_probability)
    return lb, ub


def get_bernoulli_N_ub(
    sample_size: int, sample_rate: float, failure_probability: float
):
    z_val = norm.ppf(1 - failure_probability)
    return _solve_quadratic(
        a=sample_rate**2,
        b=-2 * sample_rate * sample_size - z_val * sample_rate * (1 - sample_rate),
        c=sample_size**2,
    )[1]


def get_bernoulli_N_lb(
    sample_size: int, sample_rate: float, failure_probability: float
):
    z_val = norm.ppf(1 - failure_probability)
    return _solve_quadratic(
        a=sample_rate**2,
        b=-2 * sample_rate * sample_size - z_val * sample_rate * (1 - sample_rate),
        c=sample_size**2,
    )[0]


def get_bernoulli_N_two_side(
    sample_size: int, sample_rate: float, failure_probability: float
):
    failure_probability = failure_probability / 2
    z_val = norm.ppf(1 - failure_probability)
    return _solve_quadratic(
        a=sample_rate**2,
        b=-2 * sample_rate * sample_size - z_val * sample_rate * (1 - sample_rate),
        c=sample_size**2,
    )


def get_mean_sample_size(
    error,
    fp: float,
    fp1: float,
    fp2: float,
    pilot_sample_mean,
    pilot_sample_std,
    pilot_sample_size,
):
    validate_mean_bound_inputs(
        error, pilot_sample_mean, pilot_sample_std, pilot_sample_size
    )
    std_ub = get_std_ub(pilot_sample_size, pilot_sample_std, failure_probability=fp1)
    mean_lb = get_mean_lb(
        pilot_sample_size, pilot_sample_mean, pilot_sample_std, failure_probability=fp2
    )
    if mean_lb <= 0 or not math.isfinite(mean_lb):
        raise ValueError("pilot sample lower bound for mean is non-positive or non-finite (degenerate bounds)")
    if std_ub > 3.0 * pilot_sample_std:
        std_ub = 3.0 * pilot_sample_std
    z_val = norm.ppf(1 - fp / 2)
    return (z_val / error * std_ub / mean_lb) ** 2


def _solve_quadratic(a, b, c):
    return (-b - math.sqrt(b**2 - 4 * a * c)) / (2 * a), (
        -b + math.sqrt(b**2 - 4 * a * c)
    ) / (2 * a)


def get_sample_rate(
    fp: float, sample_size: int, pilot_sample_rate: float, pilot_sample_size: int
):
    if pilot_sample_rate > 0.9:
        bernoulli_N_lb = pilot_sample_size
    else:
        bernoulli_N_lb = get_bernoulli_N_lb(pilot_sample_size, pilot_sample_rate, fp)
    if sample_size > bernoulli_N_lb:
        return 1.0
    z_val = norm.ppf(1 - fp)
    p = _solve_quadratic(
        a=bernoulli_N_lb**2 + z_val**2 * bernoulli_N_lb,
        b=-(2 * bernoulli_N_lb * sample_size + z_val**2 * bernoulli_N_lb),
        c=sample_size**2,
    )[1]
    return p


def get_bernoulli_N_sample_rate(
    error, fp: float, fp1: float, pilot_sample_rate: float, pilot_sample_size: int
):
    bernoulli_N_lb = get_bernoulli_N_lb(pilot_sample_size, pilot_sample_rate, fp1)
    z_val = norm.ppf(1 - fp / 2)
    return 1 / (1 + error**2 * bernoulli_N_lb / z_val**2)


_last_error = None


def estimate_final_rate(
    failure_prob: float,
    pilot_results: pd.DataFrame,
    page_errors: Dict,
    group_cols: List[str],
    pilot_rate: float = 0.0001,
    limit: int | None = None,
):
    global _last_error
    _last_error = None
    page_stats_cols = [col for col in page_errors.keys() if col != "n_page"]
    n_page_stats = len(page_stats_cols)
    page_size_stats = len(page_errors) - n_page_stats
    keep_columns = group_cols + page_stats_cols
    pilot_results = pilot_results[keep_columns]
    if len(group_cols) > 0:
        if limit is not None:
            df = (
                pilot_results.groupby(by=group_cols, sort=False)
                .agg(["mean", "std", "size"])
                .head(limit)
            )
        else:
            df = pilot_results.groupby(by=group_cols, sort=False).agg(
                ["mean", "std", "size"]
            )
    else:
        df = pilot_results.agg(["mean", "std", "size"])
    n_groups = df.shape[0] if len(group_cols) > 0 else 1
    n_est = (n_page_stats * 3 + page_size_stats * 2 + 1)
    candidate_sample_rate = []
    try:
        fp = failure_prob / n_est
        for col, error in page_errors.items():
            if len(group_cols) > 0:
                for group_i in range(n_groups):
                    try:
                        if col == "n_page":
                            sample_size = df[(page_stats_cols[0], "size")].iloc[group_i]
                            if sample_size < 2:
                                continue
                            final_sample_rate = get_bernoulli_N_sample_rate(
                                error, fp, fp, pilot_rate, sample_size
                            )
                            candidate_sample_rate.append(final_sample_rate)
                        else:
                            sample_mean = df[(col, "mean")].iloc[group_i]
                            sample_std = df[(col, "std")].iloc[group_i]
                            sample_size = df[(col, "size")].iloc[group_i]
                            if sample_size < 2:
                                continue
                            # [FIX B4] Paper Procedure 1: δ₁=δ₂=(1-p)/3
                            # Split failure budget into 3 equal parts:
                            # fp for z-value, fp1 for variance bound, fp2 for mean bound
                            delta = fp / 3
                            final_sample_size = get_mean_sample_size(
                                error, delta, delta, delta, sample_mean, sample_std, sample_size
                            )
                            final_sample_rate = get_sample_rate(
                                fp, final_sample_size, pilot_rate, sample_size
                            )
                            candidate_sample_rate.append(final_sample_rate)
                    except ValueError as e:
                        logging.debug(f"Skipping group {group_i} for column {col} due to ValueError: {e}")
            else:
                try:
                    if col == "n_page":
                        sample_size = df[page_stats_cols[0]].iloc[2]
                        if sample_size < 2:
                            continue
                        final_sample_rate = get_bernoulli_N_sample_rate(
                            error, fp, fp, pilot_rate, sample_size
                        )
                        candidate_sample_rate.append(final_sample_rate)
                    else:
                        sample_mean = df[col].iloc[0]
                        sample_std = df[col].iloc[1]
                        sample_size = df[col].iloc[2]
                        if sample_size < 2:
                            continue
                        # [FIX B4] Paper Procedure 1: δ₁=δ₂=(1-p)/3
                        delta = fp / 3
                        final_sample_size = get_mean_sample_size(
                            error, delta, delta, delta, sample_mean, sample_std, sample_size
                        )
                        final_sample_rate = get_sample_rate(
                            fp, final_sample_size, pilot_rate, sample_size
                        )
                        candidate_sample_rate.append(final_sample_rate)
                except ValueError as e:
                    logging.debug(f"Skipping column {col} due to ValueError: {e}")

    except Exception as e:
        _last_error = str(e)
        logging.info(f"fail to estimate final sample rate due to {e}")
        return -1
        
    if candidate_sample_rate:
        rate = optimize_sampling_plan(candidate_sample_rate)
        return min(max(rate, 0.0), 1.0)
    return -1


def estimate_final_rate_uniform(
    failure_prob: float,
    pilot_results: pd.DataFrame,
    page_errors: Dict,
    pilot_rate: float = 0.0001,
):
    try:
        candidate_sample_rates = []
        # [FIX F17b] Paper §3.1 Boole's inequality + Procedure 1 delta split
        n_groups = len(pilot_results)
        n_cols = len(page_errors)
        n_est = max(n_groups * (n_cols * 3 + 1), 1)
        fp_each = failure_prob / n_est
        for group_id, row in pilot_results.iterrows():
            for col, error in page_errors.items():
                try:
                    if col == "size":
                        sample_size = row[col]
                        if sample_size < 2:
                            continue
                        final_sample_rate = get_bernoulli_N_sample_rate(
                            error, fp_each, fp_each, pilot_rate, sample_size
                        )
                        candidate_sample_rates.append(final_sample_rate)
                    else:
                        sample_mean = row[col]
                        sample_std = row[col.replace("avg", "std")]
                        sample_size = row["sample_size"]
                        if sample_size < 2:
                            continue
                        # [FIX F17b] Procedure 1: δ₁=δ₂=fp_each/3
                        delta = fp_each / 3
                        final_sample_size = get_mean_sample_size(
                            error,
                            delta,
                            delta,
                            delta,
                            sample_mean,
                            sample_std,
                            sample_size,
                        )
                        final_sample_rate = get_sample_rate(
                            fp_each, final_sample_size, pilot_rate, sample_size
                        )
                        candidate_sample_rates.append(final_sample_rate)
                except ValueError as e:
                    logging.debug(f"Skipping row {group_id} for column {col} due to ValueError: {e}")
                    
        if candidate_sample_rates:
            rate = optimize_sampling_plan(candidate_sample_rates)
            return min(max(rate, 0.0), 1.0)
        return -1
    except Exception as e:
        logging.info(f"fail to estimate final sample rate due to {e}")
        return -1


def estimate_final_rate_oracle_tpch1(pilot_results: pd.DataFrame):
    """Ablation/oracle baseline for paper §5.5 PilotDB-O comparison only.

    This is NOT the paper-faithful main path. Differences vs. ``estimate_final_rate``:

      - Uses **multiplicative** Boole's inequality ``1 - (1-0.05)**(1/(k*m*4))``
        instead of the paper §3.1 **additive** ``fp / (k*m)``.
      - Hardcodes the relative error target to ``0.024`` and the column
        list to TPC-H Query 1's six AVG aggregates — ignores the
        user-supplied ``--error`` / ``--probability``.
      - Assumes the pilot query was full-scan (``execute_oracle_aqp``
        defaults ``pilot_sample_rate=100``), giving exact statistics.

    Only invoked from :func:`execute_oracle_aqp` when ``query.name ==
    "tpch-1"`` on PostgreSQL/SQL Server — never from the public
    :func:`execute_aqp` entry point.
    """
    columns = ["avg_1", "avg_2", "avg_3", "avg_4", "avg_5", "avg_6"]
    max_sample_rate = 0
    n_groups = len(pilot_results)
    fp = 1 - math.pow(1 - 0.05, 1 / n_groups / len(columns) / 4)
    for it, row in pilot_results.iterrows():
        for col in columns:
            sample_mean = row[col]
            sample_std = row[col.replace("avg", "std")]
            sample_size = row["n_page"]
            final_sample_size = get_mean_sample_size(
                0.024, fp, fp, fp, sample_mean, sample_std, sample_size
            )
            final_sample_rate = get_sample_rate(fp, final_sample_size, 1, sample_size)
            max_sample_rate = max(max_sample_rate, final_sample_rate)
        # sample rate for size column
        sample_size = row["n_page"]
        final_sample_rate = get_bernoulli_N_sample_rate(0.024, fp, fp, 1, sample_size)
        max_sample_rate = max(max_sample_rate, final_sample_rate)
    return max_sample_rate


if __name__ == "__main__":
    print(get_sample_rate(0.025, 300, 0.0001, 30))

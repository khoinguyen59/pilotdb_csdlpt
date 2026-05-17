"""Numerical safety checks for statistical bounds.

These helpers keep Procedure 1 failures explicit instead of silently producing
NaN/inf sampling rates when pilot samples are too small or degenerate.
"""

from __future__ import annotations

import math


def is_positive_finite(value: float) -> bool:
    return math.isfinite(float(value)) and float(value) > 0


def has_enough_pilot_units(sample_size: int, minimum: int = 2) -> bool:
    return int(sample_size) >= minimum


def validate_mean_bound_inputs(
    error: float,
    sample_mean: float,
    sample_std: float,
    sample_size: int,
) -> None:
    if not is_positive_finite(error):
        raise ValueError("relative error must be positive and finite")
    if not has_enough_pilot_units(sample_size):
        raise ValueError("pilot sample must contain at least two units")
    if not math.isfinite(float(sample_mean)):
        raise ValueError("pilot sample mean must be finite")
    if not math.isfinite(float(sample_std)) or float(sample_std) < 0:
        raise ValueError("pilot sample std must be non-negative and finite")

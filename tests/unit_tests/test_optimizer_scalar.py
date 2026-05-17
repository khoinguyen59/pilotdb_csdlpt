import pytest

from pilotdb.pilot_engine.optimizer import optimize_sampling_plan


def test_optimize_sampling_plan_matches_max_constraint_for_scalar_case():
    rate = optimize_sampling_plan([0.02, 0.05, 0.03])
    assert rate == pytest.approx(0.05, rel=5e-3, abs=5e-4)


def test_optimize_sampling_plan_filters_invalid_rates():
    rate = optimize_sampling_plan([-1, 0, 0.04])
    assert rate == pytest.approx(0.04, rel=3e-2, abs=1e-3)


def test_optimize_sampling_plan_returns_minus_one_for_no_valid_rate():
    assert optimize_sampling_plan([]) == -1
    assert optimize_sampling_plan([-1, 0]) == -1

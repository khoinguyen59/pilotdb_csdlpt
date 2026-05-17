import pytest

from pilotdb.pilot_engine.bounds_safety import (
    has_enough_pilot_units,
    is_positive_finite,
    validate_mean_bound_inputs,
)


def test_positive_finite():
    assert is_positive_finite(0.1)
    assert not is_positive_finite(0)
    assert not is_positive_finite(float("inf"))


def test_has_enough_pilot_units():
    assert has_enough_pilot_units(2)
    assert not has_enough_pilot_units(1)


def test_validate_mean_bound_inputs_accepts_valid_values():
    validate_mean_bound_inputs(0.05, 10.0, 1.5, 30)


def test_validate_mean_bound_inputs_rejects_bad_values():
    with pytest.raises(ValueError):
        validate_mean_bound_inputs(0.05, 10.0, 1.5, 1)
    with pytest.raises(ValueError):
        validate_mean_bound_inputs(-0.05, 10.0, 1.5, 30)
    with pytest.raises(ValueError):
        validate_mean_bound_inputs(0.05, 10.0, -1.5, 30)

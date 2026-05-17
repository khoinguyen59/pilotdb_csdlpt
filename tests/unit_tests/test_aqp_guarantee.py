import pytest

from pilotdb.pilot_engine.aqp_guarantee import (
    GuaranteeAssumptions,
    require_full_vector_guarantee,
)


def test_guarantee_assumptions_report_proxy_status():
    assumptions = GuaranteeAssumptions()
    assert assumptions.scientific_claim_status() == "core-taqa-bsap-with-vector-proxy"


def test_require_full_vector_guarantee_is_explicit():
    with pytest.raises(NotImplementedError, match="Phi"):
        require_full_vector_guarantee(GuaranteeAssumptions())


def test_require_full_vector_guarantee_accepts_full_flag():
    require_full_vector_guarantee(GuaranteeAssumptions(join_aware_vector_variance=True))

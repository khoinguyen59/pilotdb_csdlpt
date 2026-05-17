"""Runtime guardrails for PilotDB paper assumptions.

These checks enforce that the prototype does NOT silently use proxy mode
when the user expects full paper-100% guarantees. Per user requirement:
  'Proxy chỉ được dùng như fallback/dev mode, không phải paper-100% mode.'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class GuaranteeAssumptions:
    sub_gaussian_aggregates: bool = True
    independent_block_sampling: bool = True
    dbms_tablesample_system: bool = True
    join_aware_vector_variance: bool = False

    def scientific_claim_status(self) -> str:
        if self.join_aware_vector_variance:
            return "full-vector-guarantee-path"
        return "core-taqa-bsap-with-vector-proxy"


def require_full_vector_guarantee(assumptions: GuaranteeAssumptions) -> None:
    """Raise if the system is NOT in full paper-100% mode.

    Per user decision: proxy is only acceptable as explicit fallback.
    When calling this function, the caller asserts they need full guarantees.
    """
    if not assumptions.join_aware_vector_variance:
        raise NotImplementedError(
            "Full §3.2 Phi(Theta) join-aware vector variance constraints are not "
            "active. Current path uses scalar lower-bound proxy. "
            "To enable full guarantees, provide PhiConstraintSet to the optimizer. "
            "If data is insufficient for full constraints, the system MUST fall back "
            "to exact execution — not silent proxy."
        )


def check_guarantee_mode(
    has_phi_constraints: bool,
    n_sampled_tables: int,
    pilot_block_count: int,
) -> str:
    """Determine guarantee mode and log appropriately.

    Returns:
        'full-vector' | 'scalar-fallback' | 'exact-required'
    """
    if n_sampled_tables <= 1:
        # Single table: standard BSAP is sufficient (no join variance needed)
        return "full-vector"

    if not has_phi_constraints:
        logging.warning(
            "[GUARDRAIL] Multi-table query with %d sampled tables but NO "
            "Phi(Theta) constraints. Falling back to EXACT execution per "
            "paper requirement — not using silent proxy.",
            n_sampled_tables,
        )
        return "exact-required"

    if pilot_block_count < 2:
        logging.warning(
            "[GUARDRAIL] Pilot produced only %d blocks — insufficient for "
            "reliable variance estimation. Falling back to exact execution.",
            pilot_block_count,
        )
        return "exact-required"

    return "full-vector"

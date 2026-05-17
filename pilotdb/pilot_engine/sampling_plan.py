"""Sampling-plan helpers for PilotDB.

This module introduces an explicit SamplingPlan representation. The current
execution path still uses the legacy single-rate plan, but this abstraction is
the compatibility layer needed before implementing the full multi-table
optimizer described in PilotDB paper ?3.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Iterator, Mapping


@dataclass(frozen=True)
class SamplingPlan:
    """A sampling plan maps table names to sampling rates in [0, 1]."""

    rates: Mapping[str, float]
    estimated_cost: float | None = None
    reason: str = ""

    @property
    def max_rate(self) -> float:
        return max(self.rates.values(), default=1.0)

    def is_exact(self) -> bool:
        return all(rate >= 1.0 for rate in self.rates.values())

    def rate_for(self, table: str, default: float = 1.0) -> float:
        return self.rates.get(table, default)


def scalar_rate_plan(table: str | None, rate: float, reason: str = "legacy scalar") -> SamplingPlan:
    """Wrap the existing single-table sample-rate result as a SamplingPlan."""
    if table is None:
        return SamplingPlan(rates={}, reason=reason)
    return SamplingPlan(rates={table: rate}, reason=reason)


def enumerate_sample_sets(tables: Iterable[str]) -> Iterator[tuple[str, ...]]:
    """Enumerate non-empty sampled-table subsets for ?3.2 optimization."""
    table_list = list(dict.fromkeys(tables))
    for size in range(1, len(table_list) + 1):
        yield from combinations(table_list, size)


def choose_lowest_cost_plan(plans: Iterable[SamplingPlan]) -> SamplingPlan | None:
    """Return the lowest estimated-cost plan, ignoring plans without costs."""
    costed_plans = [plan for plan in plans if plan.estimated_cost is not None]
    if not costed_plans:
        return None
    return min(costed_plans, key=lambda plan: plan.estimated_cost)

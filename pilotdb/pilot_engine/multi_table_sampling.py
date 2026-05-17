"""Utilities for rendering per-table sampling clauses.

The original prototype uses one global {sampling_method} placeholder. Full
PilotDB ?3.2 needs a vector plan, so each sampled table must be renderable with
its own TABLESAMPLE clause.
"""

from __future__ import annotations

import re
from typing import Mapping

from pilotdb.pilot_engine.commons import DUCKDB, POSTGRES, SQLSERVER
from pilotdb.pilot_engine.sampling_plan import SamplingPlan




def get_sampling_clause(rate: float, dbms: str) -> str:
    if dbms == DUCKDB:
        return f"TABLESAMPLE SYSTEM({rate}%)"
    if dbms == POSTGRES:
        return f"TABLESAMPLE SYSTEM ({rate})"
    if dbms == SQLSERVER:
        return f"TABLESAMPLE ({rate} PERCENT)"
    raise ValueError(f"Unknown DBMS: {dbms}")


def sampling_key(table_name: str) -> str:
    safe_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in table_name)
    return f"sampling_method_{safe_name}"


def sampling_placeholder(table_name: str) -> str:
    return f"{{{sampling_key(table_name)}}}"


def sampling_format_args(plan: SamplingPlan, dbms: str) -> dict[str, str]:
    args = {"sampling_method": ""}
    for table, rate in plan.rates.items():
        args[sampling_key(table)] = get_sampling_clause(rate * 100, dbms)
    return args


def apply_sampling_plan_template(query_template: str, plan: SamplingPlan, dbms: str) -> str:
    """Format a query template containing per-table or legacy placeholders.

    Uses SafeDict to avoid KeyError on non-sampling placeholders like
    {sample_rate} which are resolved separately downstream.
    """
    class _SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    args = sampling_format_args(plan, dbms)
    for placeholder in re.findall(r"\{(sampling_method_[A-Za-z0-9_]+)\}", query_template):
        args.setdefault(placeholder, "")
    if "{sampling_method}" in query_template and plan.rates:
        # Backward-compatible path for the current single-table rewriter.
        args["sampling_method"] = get_sampling_clause(plan.max_rate * 100, dbms)
    return query_template.format_map(_SafeDict(args))


def build_empty_sampling_args(tables: Mapping[str, object] | list[str] | tuple[str, ...]) -> dict[str, str]:
    names = tables.keys() if hasattr(tables, "keys") else tables
    return {sampling_key(name): "" for name in names}


def sampled_rate_for_output(plan: SamplingPlan) -> float:
    """Return the inclusion-rate factor used to upscale SUM/COUNT outputs.

    For a single sampled table this is the table rate. For a vector plan this
    uses the product of table rates, which is the conservative independent
    inclusion-rate proxy until the full join-aware Phi(Theta) model is wired.
    """
    rate = 1.0
    for table_rate in plan.rates.values():
        rate *= table_rate
    return rate

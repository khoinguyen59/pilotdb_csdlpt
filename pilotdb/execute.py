import importlib.util
import json
import logging
import math
import re
import sys
import time
from typing import Dict
import warnings

import numpy as np
import pandas as pd
from sqlglot import transpile

from pilotdb.db_driver.block_size import DEFAULT_BLOCK_SIZE, lookup_block_sizes
from pilotdb.db_driver.driver import *
from pilotdb.pilot_engine.commons import *
from pilotdb.pilot_engine.error_bounds import (
    estimate_final_rate,
    estimate_final_rate_oracle_tpch1,
    estimate_final_rate_uniform,
)
from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
from pilotdb.pilot_engine.optimizer import (
    build_optimization_context,
    generate_candidate_plans,
)
from pilotdb.pilot_engine.sampling_plan import (
    SamplingPlan,
    choose_lowest_cost_plan,
    scalar_rate_plan,
)
from pilotdb.pilot_engine.multi_table_sampling import (
    apply_sampling_plan_template,
    sampled_rate_for_output,
)
from pilotdb.pilot_engine.rewriter.sampling import Sampling_Rewriter
from pilotdb.pilot_engine.utils import (
    aggregate_error_to_page_error,
    aggregate_error_uniform,
)
from pilotdb.pilot_engine.join_variance import (
    build_phi_constraints,
    JoinBlockStats,
    PhiConstraintSet,
)
from pilotdb.pilot_engine.aqp_guarantee import check_guarantee_mode
from pilotdb.query import *
from pilotdb.utils.path import *
from pilotdb.utils.timer import Timer
from pilotdb.utils.utils import dump_results, get_largest_sample_rate, setup_logging


warnings.simplefilter(action="ignore", category=UserWarning)


# ---- Phase 3: top-level wrap + residual subquery-placeholder guard ----
#
# Pilot / sampling SQL produced by `Pilot_Rewriter.rewrite` may carry
# `subquery_<N>` tokens until `process_subqueries` substitutes them with
# scalar results. If a token survives substitution (e.g. Q18 on SQL
# Server, where the rewriter encodes a multi-row IN-subquery as a single
# placeholder that the dialect parser then rejects), the rewritten SQL
# is unsafe to issue. We detect that explicitly and route to an exact
# fallback rather than letting the DBMS produce an opaque parse error.
_RESIDUAL_PLACEHOLDER_PAT = re.compile(r"\bsubquery_\d+\b")


class _UnrewritableError(Exception):
    """Internal control signal: a rewriter-unsafe construct was detected
    after rewriting completed. Caught by :func:`execute_aqp` and converted
    into a structured ``not_rewritable:<reason>`` exact-fallback.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _check_residual_subquery_placeholder(rewritten_sql: str, pq) -> None:
    """If a ``subquery_<N>`` token survived substitution, mark the
    rewriter unsafe and raise :class:`_UnrewritableError` so the wrap
    converts the call into an exact fallback. Also flips
    ``pq.is_rewritable`` and ``pq.unsupported_reason`` for diagnostics.
    """
    if _RESIDUAL_PLACEHOLDER_PAT.search(rewritten_sql):
        try:
            pq.is_rewritable = False
            pq.unsupported_reason = "subquery_placeholder"
        except Exception:
            pass
        logging.info(
            "[Phase 3 guard] residual subquery placeholder in rewritten SQL; "
            "marking unrewritable and exact-falling back."
        )
        raise _UnrewritableError("subquery_placeholder")


def _exact_fallback_for_query(
    query,
    db_config: dict,
    *,
    reason: str,
    cause: "Exception | None" = None,
    conn=None,
) -> "tuple[pd.DataFrame, dict]":
    """Run ``query.query`` exactly and return ``(df, timing)`` matching
    the :func:`execute_aqp` shape. The returned timing dict always
    satisfies Requirement 8.2 (``pilot_sample_rate``,
    ``final_sample_rate``, ``fallback_reason``). Used by the top-level
    wrap in :func:`execute_aqp` and by the explicit-fallback path for
    rewriter-unsafe constructs.
    """
    dbms = db_config["dbms"]
    timer = Timer()
    timer.start()
    own_conn = False
    try:
        if conn is None:
            conn = connect_to_db(dbms, db_config)
            own_conn = True
        try:
            df = execute_query(conn, query.query, dbms)
            timer.check("exact_query_execution")
        finally:
            timer.stop()
    finally:
        if own_conn and conn is not None:
            try:
                close_connection(conn, dbms)
            except Exception:  # noqa: BLE001
                pass
    timing = timer.get_records()
    timing["pilot_sample_rate"] = None
    timing["final_sample_rate"] = 1
    timing["fallback_reason"] = reason
    if cause is not None:
        timing["fallback_cause"] = f"{type(cause).__name__}: {cause}"
    return df, timing


def _extract_pilot_stats(
    pilot_results: pd.DataFrame,
    page_errors: dict,
    group_cols: list,
    limit: int | None = None,
    join_block_stats: JoinBlockStats | None = None,
) -> list[dict]:
    """Extract per-(aggregate, group) statistics from pilot results.

    Bridges the gap between the pilot DataFrame and build_phi_constraints().
    Returns a list of dicts with keys: sample_mean, sample_std, sample_size,
    aggregate_index, group_index, and optionally 'join_stats'.
    """
    page_stats_cols = [col for col in page_errors.keys() if col != "n_page"]
    keep_columns = group_cols + page_stats_cols
    pilot_df = pilot_results[keep_columns]

    if len(group_cols) > 0:
        if limit is not None:
            df = (
                pilot_df.groupby(by=group_cols, sort=False)
                .agg(["mean", "std", "size"])
                .head(limit)
            )
        else:
            df = pilot_df.groupby(by=group_cols, sort=False).agg(
                ["mean", "std", "size"]
            )
    else:
        df = pilot_df.agg(["mean", "std", "size"])

    n_groups = df.shape[0] if len(group_cols) > 0 else 1
    stats = []
    agg_idx = 0
    for col in page_stats_cols:
        for grp in range(n_groups):
            if len(group_cols) > 0:
                s_mean = float(df[(col, "mean")].iloc[grp])
                s_std = float(df[(col, "std")].iloc[grp])
                s_size = int(df[(col, "size")].iloc[grp])
            else:
                s_mean = float(df[col].iloc[0])
                s_std = float(df[col].iloc[1])
                s_size = int(df[col].iloc[2])
            # Replace NaN std with 0 (happens with single-block groups)
            if not math.isfinite(s_std):
                s_std = 0.0
            if join_block_stats is not None:
                n1 = join_block_stats.n_pilot_blocks
                n2 = getattr(join_block_stats, "n_pilot_blocks2", 1)
                n_pairs = max(n1 * n2, s_size, 1)
                sum_val = s_mean * s_size
                true_mean = sum_val / n_pairs
                mean_sq = s_size * (s_mean**2 + s_std**2) / n_pairs
                var_val = mean_sq - true_mean**2
                true_std = math.sqrt(max(var_val, 0.0))
                s_mean = true_mean
                s_std = true_std
                s_size = n_pairs

            stat_entry = {
                "sample_mean": s_mean,
                "sample_std": s_std,
                "sample_size": s_size,
                "aggregate_index": agg_idx,
                "group_index": grp,
            }
            # Attach real join block stats if available (Lemma 4.8)
            if join_block_stats is not None:
                stat_entry["join_stats"] = join_block_stats
            stats.append(stat_entry)
        agg_idx += 1
    return stats


def _extract_join_block_stats(
    pilot_results: pd.DataFrame,
    page_errors: dict,
    page_id_count: int,
    table_sizes: dict,
    block_size: int | None = None,
    block_sizes: dict[str, int] | None = None,
    sampled_tables: list | None = None,
    pilot_rates: dict[str, float] | None = None,
) -> JoinBlockStats | None:
    """Extract real JoinBlockStats (y1/y2/y3/N1/N2) from pilot query results.

    For two-table join queries, the pilot query produces:
      - page_id_0: block ID from the sampled table (T1)
      - page_id_1: block ID from the second table (T2)

    We group by these to reconstruct:
      y(1)_i = (Σ J(t_{1,i}, t_{2,*}))^2  — per T1-block
      y(2)_{i2,i} = J(t_{1,i}, t_{2,i2})   — per (T1-block, T2-block) pair
      y(3)_i = Σ J(t_{1,i}, t_{2,*})^2     — per T1-block

    ``block_sizes`` is a per-table rows-per-block dict so N1 and N2 can use
    each table's own granularity. The legacy ``block_size`` scalar is kept
    for backward compatibility and applied to BOTH tables when
    ``block_sizes`` is ``None``.

    Returns None if pilot results don't have multi-table page_id columns.
    """
    # Need at least 2 page_id columns for join stats
    if page_id_count < 2:
        return None

    pid_col_0 = "page_id_0"  # T1 block
    pid_col_1 = "page_id_1"  # T2 block
    if pid_col_0 not in pilot_results.columns or pid_col_1 not in pilot_results.columns:
        logging.info(
            "[JoinBlockStats] page_id_0/page_id_1 not found in pilot results; "
            "columns available: %s",
            list(pilot_results.columns),
        )
        return None

    # Pick the first aggregate column as the join value J(t1, t2)
    page_stats_cols = [col for col in page_errors.keys() if col != "n_page"]
    if not page_stats_cols:
        return None
    agg_col = page_stats_cols[0]
    if agg_col not in pilot_results.columns:
        return None

    try:
        # Parse block IDs from 'page_id_N:XXXX' format
        def parse_block_id(val):
            if isinstance(val, str) and ":" in val:
                return val.split(":", 1)[1].strip()
            return str(val)

        df = pilot_results.copy()
        df["_bid_t1"] = df[pid_col_0].apply(parse_block_id)
        df["_bid_t2"] = df[pid_col_1].apply(parse_block_id)

        # y(2): per (T1-block, T2-block) pair sums
        pair_sums = df.groupby(["_bid_t1", "_bid_t2"])[agg_col].sum()
        y2_values = pair_sums.values.astype(float)

        # y(2) pivot for per-i2 term-2 bound: shape
        # (n_pilot_blocks, n_t2_observed). Missing pairs (no join rows)
        # contribute 0 to the SUM aggregate, so fill with 0 is exact for
        # SUM-style queries.
        try:
            y2_pivot = (
                pair_sums.unstack(level="_bid_t2", fill_value=0.0)
                          .to_numpy(dtype=float)
            )
        except Exception:
            y2_pivot = None

        # y(1): per T1-block, sum across all T2 blocks, then square
        t1_sums = df.groupby("_bid_t1")[agg_col].sum()
        y1_per_block = (t1_sums.values ** 2).astype(float)

        # y(3): per T1-block, sum of squared per-T2-block join results
        pair_sq = pair_sums ** 2
        y3_per_block = pair_sq.groupby(level=0).sum().values.astype(float)

        # Determine per-table block sizes. `block_sizes` (dict) takes
        # precedence; otherwise fall back to the scalar `block_size`; if
        # both are missing, use the module default. T1 and T2 may have
        # different rows-per-block (analytic vs. transactional, wide vs.
        # narrow rows), so we must look up each independently.
        from pilotdb.db_driver.block_size import DEFAULT_BLOCK_SIZE
        if sampled_tables:
            t1_name = sampled_tables[0][0]
            t2_name = sampled_tables[1][0] if len(sampled_tables) >= 2 else None
        else:
            table_names = list(table_sizes.keys())
            t1_name = table_names[0] if table_names else None
            t2_name = table_names[1] if len(table_names) >= 2 else None

        if block_sizes is None:
            scalar = block_size if block_size is not None else DEFAULT_BLOCK_SIZE
            block_sizes = {name: scalar for name in ([t1_name, t2_name] if t2_name else [t1_name]) if name}

        t1_block_size = (
            block_sizes.get(t1_name, DEFAULT_BLOCK_SIZE)
            if t1_name else DEFAULT_BLOCK_SIZE
        )
        t2_block_size = (
            block_sizes.get(t2_name, DEFAULT_BLOCK_SIZE)
            if t2_name else DEFAULT_BLOCK_SIZE
        )

        # N1, N2: population block counts for each table.
        n_unique_t1 = df["_bid_t1"].nunique()
        n_unique_t2 = df["_bid_t2"].nunique()
        if t1_name:
            t1_size = table_sizes.get(t1_name, 0)
            N1 = max(math.ceil(t1_size / t1_block_size), n_unique_t1, 1)
        else:
            N1 = max(n_unique_t1, 1)
        if t2_name:
            t2_size = table_sizes.get(t2_name, 0)
            N2 = max(math.ceil(t2_size / t2_block_size), n_unique_t2, 1)
        else:
            N2 = max(n_unique_t2, 1)

        # Determine pilot rates
        if pilot_rates is not None:
            theta1 = pilot_rates.get(t1_name, 0.1) if t1_name else 0.1
            theta2 = pilot_rates.get(t2_name, 0.1) if t2_name else 0.1

            n_pilot_blocks = max(round(theta1 * N1), len(y1_per_block), 1)
            n_pilot_blocks2 = max(round(theta2 * N2), len(y2_values) // len(y1_per_block) if len(y1_per_block) > 0 else 1, 1)

            import numpy as np
            # Pad y1_per_block to shape (n_pilot_blocks,)
            if len(y1_per_block) < n_pilot_blocks:
                y1_padded = np.zeros(n_pilot_blocks)
                y1_padded[:len(y1_per_block)] = y1_per_block
                y1_per_block = y1_padded

            # Pad y3_per_block to shape (n_pilot_blocks,)
            if len(y3_per_block) < n_pilot_blocks:
                y3_padded = np.zeros(n_pilot_blocks)
                y3_padded[:len(y3_per_block)] = y3_per_block
                y3_per_block = y3_padded

            # Pad y2_pivot to shape (n_pilot_blocks, n_pilot_blocks2)
            if y2_pivot is not None:
                p_r, p_c = y2_pivot.shape
                if p_r < n_pilot_blocks or p_c < n_pilot_blocks2:
                    new_r = max(n_pilot_blocks, p_r)
                    new_c = max(n_pilot_blocks2, p_c)
                    y2_pivot_padded = np.zeros((new_r, new_c))
                    y2_pivot_padded[:p_r, :p_c] = y2_pivot
                    y2_pivot = y2_pivot_padded

            # Pad y2_values to shape (n_pilot_blocks * n_pilot_blocks2,)
            n_pairs = n_pilot_blocks * n_pilot_blocks2
            if len(y2_values) < n_pairs:
                y2_padded = np.zeros(n_pairs)
                y2_padded[:len(y2_values)] = y2_values
                y2_values = y2_padded
        else:
            theta1 = len(y1_per_block) / N1 if N1 > 0 else 0.05
            theta2 = 0.05
            n_pilot_blocks = len(y1_per_block)
            n_pilot_blocks2 = len(y2_values) // len(y1_per_block) if len(y1_per_block) > 0 else 1

        join_stats = JoinBlockStats(
            y1_per_block=y1_per_block,
            y2_values=y2_values,
            y3_per_block=y3_per_block,
            n_pilot_blocks=n_pilot_blocks,
            N1=N1,
            N2=N2,
            pilot_rate=theta1,
            y2_pivot=y2_pivot,
            pilot_rate2=theta2,
            n_pilot_blocks2=n_pilot_blocks2,
        )
        logging.info(
            "[JoinBlockStats] Extracted: n_pilot_blocks=%d, N1=%d, N2=%d, "
            "y2_pairs=%d, pilot_rate=%.4f (t1_block=%d, t2_block=%d)",
            n_pilot_blocks, N1, N2, len(y2_values), theta1,
            t1_block_size, t2_block_size,
        )
        return join_stats

    except Exception as e:
        logging.warning(
            "[JoinBlockStats] Failed to extract from pilot results: %s", e
        )
        return None


def _extract_user_aliases(original_sql: str) -> list[str]:
    """Parse SELECT projection aliases positionally from the user SQL.

    Returns a list of strings in the order projected by SELECT. Aliases
    that are explicit (`x AS y`) yield `y`; plain column refs yield the
    column name; everything else yields `col_<i>`. Empty list on parse
    failure or missing SELECT.
    """
    try:
        import sqlglot
        from sqlglot import exp
        parsed = sqlglot.parse_one(original_sql)
    except Exception:
        return []
    if parsed is None:
        return []
    select = parsed.find(exp.Select)
    if select is None:
        return []
    out: list[str] = []
    for i, proj in enumerate(select.args.get("expressions", []) or []):
        if isinstance(proj, exp.Alias):
            out.append(proj.alias)
        elif isinstance(proj, exp.Column):
            out.append(proj.this.this if hasattr(proj.this, "this") else str(proj.this))
        else:
            out.append(f"col_{i}")
    return out


def _translate_pilot_results(
    pilot_results: pd.DataFrame,
    pq,
    pilot_sample_rate: float,
    user_aliases: list[str] | None = None,
) -> pd.DataFrame:
    """Translate pilot block-level results into the final user-facing answer.

    Paper §3.3: when final_sample_rate <= pilot_sample_rate the pilot
    sample is already statistically sufficient. Instead of re-executing
    a sampling query we (a) drop block-id helper columns, (b) aggregate
    over blocks, (c) upscale SUM-like aggregates by 1/θ, and (d) map
    the rewriter's r{N} columns back to the user's SELECT aliases.

    Per-aggregate handling follows the operator kind in
    `pq.result_mapping_list[i][AGGREGATE]`:
      - SUM:   Σ(page_sum) / θ
      - COUNT: Σ(page_size) / θ
      - AVG:   Σ(page_sum) / Σ(page_size)        (ratio cancels θ)
      - MUL:   (Σ(first)/θ) × (Σ(second)/θ)
      - DIV:   (Σ(first)/θ) / (Σ(second)/θ)      (ratio cancels θ)
      - SUB:   (Σ(first) - Σ(second)) / θ
    """
    rate = pilot_sample_rate / 100.0
    if rate <= 0:
        raise ValueError(f"invalid pilot_sample_rate={pilot_sample_rate}")

    df = pilot_results.copy()
    # Drop block-id helper columns (page_id_0, page_id_1, ...)
    page_id_cols = [c for c in df.columns if str(c).startswith("page_id_")]
    if page_id_cols:
        df = df.drop(columns=page_id_cols, errors="ignore")

    n_group = len(pq.group_cols) if pq.group_cols else 0
    n_agg = len(pq.result_mapping_list) if pq.result_mapping_list else 0
    if user_aliases is None or len(user_aliases) != n_group + n_agg:
        user_aliases = (
            list(pq.group_cols or [])
            + [f"agg_{i}" for i in range(n_agg)]
        )

    if pq.group_cols:
        grouped = (
            df.groupby(list(pq.group_cols), sort=False, as_index=False)
              .sum(numeric_only=True)
        )
    else:
        agg_row = df.sum(numeric_only=True)
        grouped = pd.DataFrame([agg_row])

    out = pd.DataFrame()
    # Carry through grouping columns under user-facing names
    for i, gc in enumerate(pq.group_cols or []):
        if gc in grouped.columns:
            out[user_aliases[i]] = grouped[gc].values

    # Compute each aggregate value
    for j, mapping in enumerate(pq.result_mapping_list or []):
        alias = user_aliases[n_group + j]
        kind = mapping.get(AGGREGATE)
        if kind == SUM_OPERATOR:
            col = mapping[PAGE_SUM]
            out[alias] = grouped[col].astype(float).values / rate
        elif kind == COUNT_OPERATOR:
            col = mapping[PAGE_SIZE]
            out[alias] = grouped[col].astype(float).values / rate
        elif kind == AVG_OPERATOR:
            num = grouped[mapping[PAGE_SUM]].astype(float).values
            den = grouped[mapping[PAGE_SIZE]].astype(float).values
            out[alias] = np.where(den != 0, num / np.where(den == 0, 1, den), 0.0)
        elif kind == MUL_OPERATOR:
            a = grouped[mapping[FIRST_ELEMENT]].astype(float).values / rate
            b = grouped[mapping[SECOND_ELEMENT]].astype(float).values / rate
            out[alias] = a * b
        elif kind == DIV_OPERATOR:
            num = grouped[mapping[FIRST_ELEMENT]].astype(float).values / rate
            den = grouped[mapping[SECOND_ELEMENT]].astype(float).values / rate
            out[alias] = np.where(den != 0, num / np.where(den == 0, 1, den), 0.0)
        elif kind == SUB_OPERATOR:
            a = grouped[mapping[FIRST_ELEMENT]].astype(float).values
            b = grouped[mapping[SECOND_ELEMENT]].astype(float).values
            out[alias] = (a - b) / rate
        elif kind == COUNT_DISTINCT_OPERATOR:
            # Paper §2.3 explicitly excludes COUNT DISTINCT. The pilot
            # rewriter rejects the query in `validate_supported_query`,
            # so this branch should never be reached in the main AQP
            # path. Reaching it indicates either (a) the rewriter check
            # was bypassed or (b) a non-rewriter caller fed pilot
            # results in here directly. In both cases the safe move is
            # to refuse rather than return a biased proxy.
            raise NotImplementedError(
                "COUNT(DISTINCT) is not a supported AQP aggregate (paper §2.3); "
                "rewriter should have triggered exact-fallback before reaching "
                "_translate_pilot_results."
            )
        else:
            raise NotImplementedError(
                f"_translate_pilot_results: unsupported aggregate kind={kind!r}"
            )

    logging.info(
        "[direct-translate] pilot_rate=%.4f%% (θ=%.4f); out_shape=%s cols=%s",
        pilot_sample_rate, rate, out.shape, list(out.columns),
    )
    return out


def _min_pilot_rate_for_groups(
    table_size: int, block_size: int = 8192,
    min_group_size: int = 200, p_fail: float = 0.05
) -> float:
    """[FIX B6] Paper Lemma 3.2 (§3.1, Eq. 7)
    Compute minimum pilot sampling rate to ensure groups of size >= g
    are not missed with probability p_fail.
    θ >= 1 - (1 - (1-p_f)^(⌈g/b⌉/|T|))^(1/⌈g/b⌉)
    """
    blocks_per_group = math.ceil(min_group_size / block_size)
    total_blocks = max(math.ceil(table_size / block_size), 1)
    try:
        base = (1 - p_fail) ** (blocks_per_group / total_blocks)
        theta_min = 1 - base ** (1.0 / blocks_per_group)
        return max(theta_min * 100, 0.01)  # percent, at least 0.01%
    except (ValueError, ZeroDivisionError):
        return 0.05  # default fallback


def execute_aqp(query: Query, db_config: dict, pilot_sample_rate: float = 0.05):
    """Public AQP entry. Phase 3: wraps the inner implementation in a
    top-level try/except so any rewriter / driver / pandas exception
    is converted into a structured exact-fallback record instead of
    propagating up to the caller. The ``(results_df, timing)`` return
    contract from Requirement 8.1 is preserved on every input.

    Two fallback reasons are added by this wrap:

    - ``not_rewritable:<reason>`` — raised by
      :func:`_check_residual_subquery_placeholder` (and any future
      post-rewrite guard) when the rewritten SQL is unsafe to issue.
    - ``execute_aqp_recover`` — caught from any other uncaught
      :class:`Exception`, with the original exception type+message
      stored in ``timing["fallback_cause"]`` for diagnostics.
    """
    try:
        return _execute_aqp_internal(query, db_config, pilot_sample_rate)
    except _UnrewritableError as e:
        return _exact_fallback_for_query(
            query, db_config,
            reason=f"not_rewritable:{e.reason}",
        )
    except Exception as e:  # noqa: BLE001
        logging.warning(
            "[execute_aqp wrap] uncaught %s — exact-fallback recover. cause=%r",
            type(e).__name__, e,
        )
        return _exact_fallback_for_query(
            query, db_config,
            reason="execute_aqp_recover",
            cause=e,
        )


def query_table_sizes(dbms: str, db_config: dict, table_names: list[str]) -> dict[str, int]:
    # Fallback map for TPC-H SF=1
    fallback = {
        "lineitem": 6_001_215,
        "orders": 1_500_000,
        "partsupp": 800_000,
        "part": 200_000,
        "customer": 150_000,
        "supplier": 10_000,
        "nation": 25,
        "region": 5,
    }
    sizes = {}
    try:
        conn = connect_to_db(dbms, db_config)
        try:
            for name in table_names:
                sql = f"SELECT COUNT(*) FROM {name}"
                try:
                    df = execute_query(conn, sql, dbms)
                    sizes[name] = int(df.iloc[0, 0])
                except Exception:
                    if dbms == "sqlserver":
                        try:
                            df = execute_query(conn, f"SELECT COUNT(*) FROM dbo.{name}", dbms)
                            sizes[name] = int(df.iloc[0, 0])
                            continue
                        except Exception:
                            pass
                    # If query fails, use fallback or default
                    sizes[name] = fallback.get(name.lower(), 1_000_000)
        finally:
            close_connection(conn, dbms)
    except Exception as e:
        logging.warning(
            "Could not connect to database to query table sizes: %s. Using fallbacks.",
            e,
        )
        for name in table_names:
            sizes[name] = fallback.get(name.lower(), 1_000_000)
    return sizes


def _execute_aqp_internal(query: Query, db_config: dict, pilot_sample_rate: float = 0.05):
    # prepare the query and db
    dbms = db_config["dbms"]
    if isinstance(query.table_size, list):
        query.table_size = query_table_sizes(dbms, db_config, query.table_size)
    conn = connect_to_db(dbms, db_config)

    # Track which fallback path (if any) was taken; None means AQP succeeded.
    fallback_reason: str | None = None

    pq = Pilot_Rewriter(query.table_cols, query.table_size, dbms)
    sq = Sampling_Rewriter(query.table_cols, query.table_size, dbms)
    pilot_query = pq.rewrite(query.query) + ";"
    sampling_query = sq.rewrite(query.query) + ";"

    import sqlglot
    from sqlglot import exp
    try:
        parsed_query = sqlglot.parse_one(query.query, read=dbms)
        tables_in_query = {
            table.name.lower() for table in parsed_query.find_all(exp.Table)
        }
        query_tables = [
            t for t in query.table_size.keys() if t.lower() in tables_in_query
        ]
    except Exception:
        query_tables = list(query.table_size.keys())

    # Only optimize tables that are actually sampled in the pilot query
    if getattr(pq, 'sampled_tables', None):
        query_tables = [t[0] for t in pq.sampled_tables]

    optimizer_context = build_optimization_context(
        query_tables=query_tables,
        table_sizes=query.table_size,
    )

    # Detect sampled tables from rewriter (precise) and query metadata (fallback)
    n_sampled_tables = len(pq.sampled_tables) if getattr(pq, 'sampled_tables', None) else 1
    # pq.page_id_count tracks how many page_id columns were generated
    # page_id_count >= 2 means a join query with multi-table sampling
    is_join_query = getattr(pq, 'page_id_count', 0) >= 2

    # Resolve effective rows-per-block per table from the DBMS once, then
    # feed the dictionary into Lemma 3.2 / Lemma 4.8 helpers below so they
    # use real metadata instead of the 8192 fallback.
    block_sizes: dict[str, int] = lookup_block_sizes(
        conn, dbms, list(query.table_size.keys()) if query.table_size else []
    )

    # [FIX B6] Lemma 3.2: adjust pilot rate for GROUP BY queries
    has_group_by = len(pq.group_cols) > 0
    if has_group_by and query.table_size:
        largest_table_name = next(iter(query.table_size), None)
        if largest_table_name:
            largest_table_size = query.table_size[largest_table_name]
            largest_block_size = block_sizes.get(
                largest_table_name, DEFAULT_BLOCK_SIZE
            )
            min_rate = _min_pilot_rate_for_groups(
                table_size=largest_table_size,
                block_size=largest_block_size,
                min_group_size=200, p_fail=0.05
            )
            if min_rate > pilot_sample_rate:
                logging.info(
                    f"[Lemma 3.2] Adjusting pilot rate from {pilot_sample_rate}% "
                    f"to {min_rate:.4f}% for group coverage"
                )
                pilot_sample_rate = min_rate

    if getattr(pq, 'sampled_tables', None):
        K = len(pq.sampled_tables)
        effective_pilot_rate = (pilot_sample_rate / 100) ** (1.0 / K) if K > 1 else (pilot_sample_rate / 100)
        rates = {t_name: effective_pilot_rate for (t_name, _, _) in pq.sampled_tables}
        pilot_plan = SamplingPlan(rates=rates, reason="geometric multi-table pilot plan")
        logging.info(
            f"[PilotDB] Multi-table pilot plan: rates={rates}, target_rate={pilot_sample_rate}%"
        )
    else:
        pilot_plan = scalar_rate_plan(pq.largest_table, pilot_sample_rate / 100)

    pilot_query = apply_sampling_plan_template(
        pilot_query, pilot_plan, dbms
    )

    # start execution
    timer = Timer()
    job_id = str(int(timer.start() * 100))
    setup_logging(log_file=get_log_file_path("logs", query.name, job_id))
    log_query_info(query, dbms)
    pq.log_info()
    logging.info(f"optimizer context: {optimizer_context}")
    if not pq.is_rewritable:
        final_sample_rate = 1
        sampling_query = query.query
        subquery_results = {}
        fallback_reason = f"not_rewritable:{getattr(pq, 'unsupported_reason', None)}"
        logging.info(
            f"query is not rewritable by TAQA; running exact query. "
            f"reason={getattr(pq, 'unsupported_reason', None)}"
        )
    elif directly_run_exact(conn, query.query, pilot_query, dbms, pq.largest_table):
        final_sample_rate = 1
        fallback_reason = "directly_run_exact"
        # Replace the rewritten sampling_query with the original user SQL.
        # The rewriter may have emitted `subquery_<N>` placeholders that
        # would otherwise leak into the final query (the Q18-on-SQL-Server
        # bug). For exact execution we never need rewriter rewrites anyway.
        sampling_query = query.query
        logging.info(f"retrieving query plan time: {timer.check('query_plan_time')}")
        subquery_results = {}
    else:
        # execute subqueries
        subquery_results = process_subqueries(dbms, conn, pq)

        # execute pilot query
        for subquery_name, subquery_result in subquery_results.items():
            pilot_query = pilot_query.replace(subquery_name, subquery_result)

        # Phase 3 guard: any `subquery_<N>` token that survived
        # substitution means the rewriter generated an unsafe
        # placeholder the dialect parser will reject (Q18-on-SQL-Server).
        # Raises _UnrewritableError → wrap converts to
        # not_rewritable:subquery_placeholder exact-fallback.
        _check_residual_subquery_placeholder(pilot_query, pq)

        if dbms != SQLSERVER:
            logging.info(
                f"pilot query:\n{transpile(pilot_query, read=dbms, pretty=True)[0]}"
            )

        pilot_results = execute_query(conn, pilot_query, dbms)
        logging.info(
            f"pilot query executing time: {timer.check('pilot_query_execution')}"
        )

        # parse the results of pilot query
        page_errors = aggregate_error_to_page_error(
            pq.result_mapping_list, required_error=query.error
        )
        logging.info(f"converted page errors: {page_errors}")
        final_sample_rate = estimate_final_rate(
            failure_prob=query.failure_probability,
            pilot_results=pilot_results,
            page_errors=page_errors,
            group_cols=pq.group_cols,
            pilot_rate=pilot_sample_rate / 100,
            limit=pq.limit_value,
        )

        # ---- Wire Phi(Theta) constraints from pilot stats ----
        phi_constraints = None
        try:
            # Extract real JoinBlockStats for multi-table join queries (Lemma 4.8)
            join_block_stats = None
            if is_join_query:
                # Pass per-table block sizes so N1 and N2 are computed
                # against each table's own granularity, not T1's only.
                join_block_stats = _extract_join_block_stats(
                    pilot_results, page_errors,
                    page_id_count=getattr(pq, 'page_id_count', 0),
                    table_sizes=query.table_size,
                    block_sizes=block_sizes,
                    sampled_tables=getattr(pq, 'sampled_tables', None),
                    pilot_rates=rates,
                )
                if join_block_stats is not None:
                    logging.info(
                        "[Lemma 4.8] Real join block stats extracted — "
                        "full variance decomposition active."
                    )
                else:
                    logging.warning(
                        "[Lemma 4.8] Join query detected but failed to extract "
                        "block stats. Falling back to scalar variance proxy."
                    )

            pilot_stats = _extract_pilot_stats(
                pilot_results, page_errors, pq.group_cols, pq.limit_value,
                join_block_stats=join_block_stats,
            )
            n_page_stats = len([c for c in page_errors.keys() if c != "n_page"])
            n_groups = len(set(
                tuple(pilot_results[pq.group_cols].iloc[i])
                for i in range(len(pilot_results))
            )) if pq.group_cols else 1

            if pilot_stats:
                phi_constraints = build_phi_constraints(
                    failure_prob=query.failure_probability,
                    n_aggregates=n_page_stats,
                    n_groups=n_groups,
                    pilot_stats=pilot_stats,
                    required_error=query.error,
                    table_names=tuple(query_tables),
                )
                logging.info(
                    f"[Phi(Theta)] Built {len(phi_constraints.constraints)} constraints, "
                    f"mode={phi_constraints.mode}"
                )
                optimizer_context = build_optimization_context(
                    query_tables=query_tables,
                    table_sizes=query.table_size,
                    phi_constraints=phi_constraints,
                )
        except Exception as e:
            logging.warning(
                f"[Phi(Theta)] Failed to build constraints: {e}; "
                f"falling back to scalar proxy"
            )
            phi_constraints = None

        # ---- Guardrail: multi-table without Phi → exact ----
        guarantee_mode = check_guarantee_mode(
            has_phi_constraints=(phi_constraints is not None and phi_constraints.mode == "full"),
            n_sampled_tables=n_sampled_tables,
            pilot_block_count=len(pilot_results),
        )
        if guarantee_mode == "exact-required" and n_sampled_tables > 1:
            logging.info(
                "[GUARDRAIL] Multi-table query without sufficient Phi(Theta) "
                "constraints. Falling back to exact execution."
            )
            final_sample_rate = 1
            fallback_reason = "multi_table_no_phi"

        final_sampling_plan = scalar_rate_plan(
            pq.largest_table, final_sample_rate, reason="legacy scalar estimator"
        )
        output_sample_rate = final_sample_rate
        logging.info(f"candidate sampling plan: {final_sampling_plan}")
        has_phi = phi_constraints is not None and len(phi_constraints.constraints) > 0
        if has_phi or (final_sample_rate != -1 and final_sample_rate != 1):
            # --- Paper §3.2: cost-based plan enumeration for all DBMS ---
            # Plans are generated for every subset of large tables and
            # every per-table-weighted objective. The rewriter
            # (`Sampling_Rewriter.add_table_sample`) now emits one
            # TABLESAMPLE placeholder per large table, so multi-table
            # vector plans are realised in the final SQL — not just in
            # the chosen rate.
            base_min_rate = final_sample_rate if (final_sample_rate != -1 and final_sample_rate != 1) else (pilot_sample_rate / 100)
            candidate_plans = generate_candidate_plans(
                context=optimizer_context,
                table_sizes=query.table_size,
                min_rate=max(base_min_rate, pilot_sample_rate / 100),
                max_rate=min(get_largest_sample_rate(dbms) / 100, 0.1),
                phi_constraints=phi_constraints,
            )
            # [FIX P0 #1] choose_lowest_cost_plan() ranks by
            # `plan.estimated_cost`. The optimizer fills that field with
            # its *objective-function value* (a table-size-weighted theta
            # sum), which is NOT the DBMS cost. We must overwrite that
            # field with the real DBMS cost estimate before selection,
            # otherwise the chosen plan ignores EXPLAIN/SHOWPLAN entirely.
            import dataclasses
            costed_plans: list[SamplingPlan] = []
            for candidate_plan in candidate_plans:
                candidate_plan_query = apply_sampling_plan_template(
                    sampling_query, candidate_plan, dbms
                )
                try:
                    candidate_cost = estimate_cost(
                        conn, candidate_plan_query, dbms,
                        table_size=query.table_size, sampling_plan=candidate_plan
                    )
                except Exception as exc:
                    logging.warning(
                        f"[optimizer] cost estimation failed for plan={candidate_plan}: {exc}. "
                        f"Dropping plan from candidate set."
                    )
                    continue
                logging.info(
                    f"candidate plan cost: plan={candidate_plan}, cost={candidate_cost}"
                )
                costed_plans.append(
                    dataclasses.replace(candidate_plan, estimated_cost=float(candidate_cost))
                )
            best_plan = choose_lowest_cost_plan(costed_plans)
            if best_plan is not None:
                final_sampling_plan = best_plan
                final_sample_rate = best_plan.max_rate
                output_sample_rate = sampled_rate_for_output(best_plan)
                logging.info(f"selected best candidate plan: {best_plan}")
            else:
                final_sample_rate = 1
                fallback_reason = "optimizer_infeasible"

            # --- [FIX F14] Paper §3.2: Post-pilot cost rejection for ALL DBMSes ---
            # After the pilot, compare cost(approx_plan) vs cost(exact).
            # If approximate costs more, fall back to exact.
            if final_sample_rate != -1 and final_sample_rate != 1:
                exact_cost = estimate_cost(conn, query.query, dbms, table_size=query.table_size)
                approx_query = apply_sampling_plan_template(
                    sampling_query, final_sampling_plan, dbms
                )
                approx_cost = estimate_cost(
                    conn, approx_query, dbms,
                    table_size=query.table_size, sampling_plan=final_sampling_plan
                )
                logging.info(
                    f"cost model: exact={exact_cost}, approx={approx_cost}, "
                    f"plan={final_sampling_plan}"
                )
                if should_run_exact(exact_cost, approx_cost):
                    logging.info("cost model rejected approximate plan; running exact query")
                    final_sample_rate = 1
                    if fallback_reason is None:
                        fallback_reason = "exact_chosen_by_cost"
        logging.info(
            f"sample rate solving time: {timer.check('sampling_rate_solving')}"
        )

        if final_sample_rate == -1:
            final_sample_rate = 1
            if fallback_reason is None:
                from pilotdb.pilot_engine import error_bounds
                last_err = getattr(error_bounds, "_last_error", None)
                if last_err and "at least two units" in last_err:
                    fallback_reason = "pilot_sample_insufficient_units"
                elif last_err and "degenerate bounds" in last_err:
                    fallback_reason = "pilot_sample_degenerate_bounds"
                else:
                    fallback_reason = "solver_failed"
            logging.info(f"fail to solve sample rate, fall back to original queries")
        elif final_sample_rate * 100 > get_largest_sample_rate(dbms):
            logging.info(
                f"too big sample rate {final_sample_rate*100}, fall back to original queries"
            )
            final_sample_rate = 1
            if fallback_reason is None:
                fallback_reason = "sample_rate_too_high"
    if final_sample_rate == 1:
        logging.info(f"running exact query directly:\n{query.query}")
        results_df = execute_query(conn, query.query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )
    elif final_sample_rate * 100 > pilot_sample_rate:
        final_sample_rate = round(final_sample_rate * 100, 2)
        logging.info(f"final sample rate: {final_sample_rate}")
        sampling_query = apply_sampling_plan_template(
            sampling_query, final_sampling_plan, dbms
        ).format(sample_rate=output_sample_rate)
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        logging.info(f"sampling query:\n{sampling_query}")
        results_df = execute_query(conn, sampling_query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )
    else:
        logging.info(
            f"final sample rate: {final_sample_rate}, pilot sampling is large enough"
        )
        # [FIX B7] Direct pilot result translation — no re-sampling.
        # Reconstruct user-facing SELECT aliases so the returned DataFrame
        # matches the schema the exact query would produce.
        user_aliases = _extract_user_aliases(query.query)
        results_df = _translate_pilot_results(
            pilot_results, pq, pilot_sample_rate, user_aliases
        )
        logging.info(
            f"direct translate time: {timer.check('sampling_query_execution')}"
        )

    timer.stop()
    close_connection(conn, dbms)

    logging.info(f"aqp result:\n{results_df}")
    dump_results(
        result_file=get_result_file_path("./results", query.name, job_id, "aqp", dbms),
        results_df=results_df,
    )

    with open("all_results.jsonl", "a+") as f:
        result = {
            "query": query.name,
            "dbms": dbms,
            "pilot_sample_rate": pilot_sample_rate,
            "final_sample_rate": final_sample_rate,
            "fallback_reason": fallback_reason,
            "runtime": timer.get_records(),
            "error": query.error,
            "failure_probability": query.failure_probability,
            "results_file": get_result_file_path(
                "./results", query.name, job_id, "aqp", dbms
            ),
        }
        f.write(json.dumps(result) + "\n")

    timing_out = timer.get_records()
    timing_out["final_sample_rate"] = final_sample_rate
    timing_out["pilot_sample_rate"] = pilot_sample_rate
    timing_out["fallback_reason"] = fallback_reason
    return results_df, timing_out


def execute_block_wrong(query: Query, db_config: dict, pilot_sample_rate: float = 0.05):
    # prepare the query and db
    dbms = db_config["dbms"]
    conn = connect_to_db(dbms, db_config)

    # FIXME: query rewriter for uniform sampling

    if dbms == SQLSERVER:
        pq = uniform_rewriter(dbms, query.name)
        pilot_query = pq.pilot_query + ";"
        sampling_query = pq.sampling_query
        sampling_clause = get_sampling_clause(pilot_sample_rate, dbms)
        pilot_query = pilot_query.format(sampling_method=sampling_clause)
    else:
        pq = uniform_rewriter(dbms, query.name)
        pilot_query = pq.pilot_query + ";"
        sq = Sampling_Rewriter(query.table_cols, query.table_size, dbms)
        sampling_query = sq.rewrite(query.query) + ";"
        sampling_clause = get_sampling_clause(pilot_sample_rate, dbms)
        pilot_query = pilot_query.format(sampling_method=sampling_clause)

    # start execution
    timer = Timer()
    job_id = str(int(timer.start() * 100))
    setup_logging(log_file=get_log_file_path("logs", query.name, job_id))
    log_query_info(query, dbms)

    subquery_results = process_subqueries(dbms, conn, pq)

    # execute pilot query
    for subquery_name, subquery_result in subquery_results.items():
        pilot_query = pilot_query.replace(subquery_name, subquery_result)

    if dbms != "sqlserver":
        logging.info(
            f"pilot query:\n{transpile(pilot_query, read=dbms, pretty=True)[0]}"
        )
    else:
        logging.info(f"pilot query {pilot_query}")

    pilot_results = execute_query(conn, pilot_query, dbms)
    # dump_results(result_file=get_result_file_path("./results", query.name, job_id, "pilot", dbms),
    #              results_df=pilot_results)
    logging.info(f"pilot query executing time: {timer.check('pilot_query_execution')}")

    # parse the results of pilot query
    errors = aggregate_error_uniform(pq.results_mapping, required_error=query.error)
    logging.info(f"converted page errors: {errors}")
    final_sample_rate = estimate_final_rate_uniform(
        failure_prob=query.failure_probability,
        pilot_results=pilot_results,
        page_errors=errors,
        pilot_rate=pilot_sample_rate / 100,
    )
    logging.info(f"sample rate solving time: {timer.check('sampling_rate_solving')}")

    if final_sample_rate == -1:
        final_sample_rate = 1
        logging.info(f"fail to solve sample rate, fall back to original queries")
    elif final_sample_rate * 100 > get_largest_sample_rate(dbms):
        logging.info(
            f"too big sample rate {final_sample_rate*100}, fall back to original queries"
        )
        final_sample_rate = 1
    if final_sample_rate == 1:
        # sampling_query = sampling_query.format(sampling_method="", sample_rate="1")
        # for subquery_name, subquery_result in subquery_results.items():
        #     sampling_query = sampling_query.replace(subquery_name, subquery_result)
        sampling_query = query.query
        logging.info(f"sampling query:\n{sampling_query}")
        results_df = execute_query(conn, sampling_query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )
    elif final_sample_rate * 100 > pilot_sample_rate:
        final_sample_rate = round(final_sample_rate * 100, 2)
        logging.info(f"final sample rate: {final_sample_rate}")
        sampling_clause = get_sampling_clause(final_sample_rate, dbms)
        sampling_query = sampling_query.format(
            sampling_method=sampling_clause, sample_rate=final_sample_rate / 100
        )
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        logging.info(f"sampling query:\n{sampling_query}")
        results_df = execute_query(conn, sampling_query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )
    else:
        logging.info(
            f"final sample rate: {final_sample_rate}, pilot sampling is large enough"
        )
        # FIXME: directly translate pilot results instead of running sampling again
        sampling_clause = get_sampling_clause(final_sample_rate, dbms)
        sampling_query = sampling_query.format(
            sampling_method=sampling_clause, sample_rate=final_sample_rate / 100
        )
        print(sampling_query)
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        results_df = execute_query(conn, sampling_query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )

    timer.stop()
    close_connection(conn, dbms)

    logging.info(f"aqp result:\n{results_df}")
    dump_results(
        result_file=get_result_file_path(
            "./results", query.name, job_id, "uniform", dbms
        ),
        results_df=results_df,
    )

    with open("all_results.jsonl", "a+") as f:
        result = {
            "query": query.name,
            "dbms": dbms,
            "pilot_sample_rate": pilot_sample_rate,
            "final_sample_rate": final_sample_rate,
            "runtime": timer.get_records(),
            "error": query.error,
            "failure_probability": query.failure_probability,
            "results_file": get_result_file_path(
                "./results", query.name, job_id, "uniform", dbms
            ),
        }
        f.write(json.dumps(result) + "\n")

    return results_df, timer.get_records()


def execute_exact(query: Query, db_config: dict):
    dbms = db_config["dbms"]
    conn = connect_to_db(dbms, db_config)
    timer = Timer()
    job_id = str(int(timer.start() * 100))
    log_file = f"logs/{query.name}-{job_id}.log"
    setup_logging(log_file=log_file)
    results_df = execute_query(conn, query.query, dbms)
    logging.info(f"exact execution time: {timer.check('exact_execution')}")
    timer.stop()
    close_connection(conn, dbms)
    dump_results(
        result_file=get_result_file_path(
            "./results", query.name, job_id, "exact", dbms
        ),
        results_df=results_df,
    )
    logging.info(f"exact result:\n{results_df}")

    with open("all_results.jsonl", "a+") as f:
        result = {
            "query": query.name,
            "dbms": dbms,
            "runtime": timer.get_records(),
            "error": query.error,
            "failure_probability": query.failure_probability,
            "results_file": get_result_file_path(
                "./results", query.name, job_id, "exact", dbms
            ),
        }
        f.write(json.dumps(result) + "\n")

    return results_df, timer.get_records()


def execute_oracle_aqp(query: Query, db_config: dict, pilot_sample_rate: float = 100):
    # prepare the query and db
    dbms = db_config["dbms"]
    conn = connect_to_db(dbms, db_config)

    pq = Pilot_Rewriter(query.table_cols, query.table_size, dbms)
    pilot_query = pq.rewrite(query.query) + ";"
    sampling_clause = ""
    pilot_query = pilot_query.format(sampling_method=sampling_clause)

    # start execution
    timer = Timer()
    job_id = str(int(timer.start() * 100))
    setup_logging(log_file=get_log_file_path("logs", query.name, job_id))
    log_query_info(query, dbms)
    pq.log_info()
    if dbms == DUCKDB and not pq.is_rewritable:
        final_sample_rate = 100
        subquery_results = {}
    elif dbms == SQLSERVER and directly_run_exact(
        conn, query.query, pilot_query, dbms, pq.largest_table
    ):
        final_sample_rate = 100
        logging.info(f"retrieving query plan time: {timer.check('query_plan_time')}")
        subquery_results = {}
    else:
        if query.name == "tpch-1" and (dbms == POSTGRES or dbms == SQLSERVER):
            with open(f"benchmarks/{dbms}/tpch/query_1_oracle.sql", "r") as f:
                pilot_query = f.read()
            logging.info(f"pilot query:\n{pilot_query}")
            pilot_results = execute_query(conn, pilot_query, dbms)
            logging.info(
                f"pilot query executing time: {timer.check('pilot_query_execution')}"
            )
            final_sample_rate = estimate_final_rate_oracle_tpch1(pilot_results)

        else:
            # execute subqueries
            subquery_results = process_subqueries(dbms, conn, pq)

            # execute pilot query
            for subquery_name, subquery_result in subquery_results.items():
                pilot_query = pilot_query.replace(subquery_name, subquery_result)

            logging.info(f"pilot query:\n{pilot_query}")

            pilot_results = execute_query(conn, pilot_query, dbms)
            logging.info(
                f"pilot query executing time: {timer.check('pilot_query_execution')}"
            )

            # parse the results of pilot query
            page_errors = aggregate_error_to_page_error(pq.result_mapping_list)
            logging.info(f"converted page errors: {page_errors}")
            final_sample_rate = estimate_final_rate(
                failure_prob=0.05,
                pilot_results=pilot_results,
                page_errors=page_errors,
                group_cols=pq.group_cols,
                pilot_rate=pilot_sample_rate / 100,
                limit=pq.limit_value,
            )
        logging.info(
            f"sample rate solving time: {timer.check('sampling_rate_solving')}"
        )

        if final_sample_rate == -1:
            final_sample_rate = 100
            logging.info(f"fail to solve sample rate, fall back to original queries")
        elif final_sample_rate * 100 > get_largest_sample_rate(dbms):
            logging.info(
                f"too big sample rate {final_sample_rate*100}, fall back to original queries"
            )
            final_sample_rate = 100
    timer.stop()
    close_connection(conn, dbms)

    with open("all_results.jsonl", "a+") as f:
        result = {
            "query": query.name,
            "dbms": dbms,
            "pilot_sample_rate": pilot_sample_rate,
            "final_sample_rate": final_sample_rate,
            "runtime": timer.get_records(),
        }
        f.write(json.dumps(result) + "\n")

    for i in range(5):
        execute_sample(query, final_sample_rate, db_config, add_new_log=False)


def execute_sample(query: Query, sample_rate: float, db_config: dict, add_new_log=True):
    dbms = db_config["dbms"]
    conn = connect_to_db(dbms, db_config)
    sq = Sampling_Rewriter(query.table_cols, query.table_size, dbms)
    sampling_query = sq.rewrite(query.query) + ";"

    subquery_results = process_subqueries(dbms, conn, sq)
    if sample_rate == 100:
        sampling_query = apply_sampling_plan_template(
            sampling_query, scalar_rate_plan(sq.largest_table, 1), dbms
        ).format(sample_rate="1")
    else:
        sampling_query = apply_sampling_plan_template(
            sampling_query, scalar_rate_plan(sq.largest_table, sample_rate), dbms
        ).format(sample_rate=sample_rate)
    for subquery_name, subquery_result in subquery_results.items():
        sampling_query = sampling_query.replace(subquery_name, subquery_result)
    start = time.time()

    job_id = str(int(start * 100))
    if add_new_log:
        log_file = f"logs/{query.name}-{job_id}.log"
        setup_logging(log_file=log_file)

    logging.info(f"sampling query:\n{sampling_query}")
    results_df = execute_query(conn, sampling_query, dbms)
    runtime = time.time() - start

    close_connection(conn, dbms)

    result_file = f"results/{query.name}-sample_only-{dbms}-{job_id}.csv"
    dump_results(result_file=result_file, results_df=results_df)

    logging.info(f"sample only execution time: {runtime}")
    logging.info(f"sample only result:\n{results_df}")

    with open("all_results.jsonl", "a+") as f:
        result = {
            "query": query.name,
            "dbms": dbms,
            "final_sample_rate": sample_rate,
            "sample only execution time": runtime,
            "results_file": result_file,
        }
        f.write(json.dumps(result) + "\n")

    return results_df, {"sample_only_runtime": runtime}


def uniform_rewriter(dbms: str, query_name: str):
    spec = importlib.util.spec_from_file_location(
        "query_rewriter", f"benchmarks/{dbms}/uniform/{query_name}.py"
    )
    query_rewriter = importlib.util.module_from_spec(spec)
    sys.modules["query_rewriter"] = query_rewriter
    spec.loader.exec_module(query_rewriter)
    return query_rewriter


def execute_uniform(query: Query, db_config: dict, pilot_sample_rate: float = 0.05):
    # prepare the query and db
    dbms = db_config["dbms"]
    conn = connect_to_db(dbms, db_config)

    pq = uniform_rewriter(dbms, query.name)
    pilot_query = pq.pilot_query + ";"
    sampling_clause = get_uniform_sampling_clause(pilot_sample_rate, dbms)
    pilot_query = pilot_query.format(sampling_method=sampling_clause)

    if dbms == SQLSERVER:
        sampling_query = pq.sampling_query
    else:
        sq = Sampling_Rewriter(query.table_cols, query.table_size, dbms)
        sampling_query = sq.rewrite(query.query) + ";"

    # start execution
    timer = Timer()
    job_id = str(int(timer.start() * 100))
    setup_logging(log_file=get_log_file_path("logs", query.name, job_id))
    log_query_info(query, dbms)

    subquery_results = process_subqueries(dbms, conn, pq)

    # execute pilot query
    for subquery_name, subquery_result in subquery_results.items():
        pilot_query = pilot_query.replace(subquery_name, subquery_result)

    if dbms != SQLSERVER:
        logging.info(
            f"pilot query:\n{transpile(pilot_query, read=dbms, pretty=True)[0]}"
        )
    else:
        logging.info(f"pilot query {pilot_query}")

    pilot_results = execute_query(conn, pilot_query, dbms)

    logging.info(f"pilot query executing time: {timer.check('pilot_query_execution')}")

    # parse the results of pilot query
    errors = aggregate_error_uniform(pq.results_mapping, required_error=query.error)
    logging.info(f"converted page errors: {errors}")
    final_sample_rate = estimate_final_rate_uniform(
        failure_prob=query.failure_probability,
        pilot_results=pilot_results,
        page_errors=errors,
        pilot_rate=pilot_sample_rate / 100,
    )
    logging.info(f"sample rate solving time: {timer.check('sampling_rate_solving')}")

    if final_sample_rate == -1:
        final_sample_rate = 1
        logging.info(f"fail to solve sample rate, fall back to original queries")
    elif final_sample_rate * 100 > get_largest_sample_rate(dbms):
        logging.info(
            f"too big sample rate {final_sample_rate*100}, fall back to original queries"
        )
        final_sample_rate = 1

    if final_sample_rate == 1:
        sampling_query = query.query
        logging.info(f"sampling query:\n{sampling_query}")
        results_df = execute_query(conn, sampling_query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )
    elif final_sample_rate * 100 > pilot_sample_rate:
        final_sample_rate = round(final_sample_rate * 100, 2)
        logging.info(f"final sample rate: {final_sample_rate}")
        sampling_clause = get_uniform_sampling_clause(final_sample_rate, dbms)
        sampling_query = sampling_query.format(
            sampling_method=sampling_clause, sample_rate=final_sample_rate / 100
        )
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        logging.info(f"sampling query:\n{sampling_query}")
        results_df = execute_query(conn, sampling_query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )
    else:
        logging.info(
            f"final sample rate: {final_sample_rate}, pilot sampling is large enough"
        )
        # FIXME: directly translate pilot results instead of running sampling again
        sampling_clause = get_uniform_sampling_clause(pilot_sample_rate, dbms)
        sampling_query = sampling_query.format(
            sampling_method=sampling_clause, sample_rate=pilot_sample_rate / 100
        )
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        results_df = execute_query(conn, sampling_query, dbms)
        logging.info(
            f"sampling execution time: {timer.check('sampling_query_execution')}"
        )

    timer.stop()
    close_connection(conn, dbms)

    logging.info(f"aqp result:\n{results_df}")
    dump_results(
        result_file=get_result_file_path(
            "./results", query.name, job_id, "uniform", dbms
        ),
        results_df=results_df,
    )

    with open("all_results.jsonl", "a+") as f:
        result = {
            "query": query.name,
            "dbms": dbms,
            "pilot_sample_rate": pilot_sample_rate,
            "final_sample_rate": final_sample_rate,
            "runtime": timer.get_records(),
            "error": query.error,
            "failure_probability": query.failure_probability,
            "results_file": get_result_file_path(
                "./results", query.name, job_id, "uniform", dbms
            ),
        }
        f.write(json.dumps(result) + "\n")

    return results_df, timer.get_records()


def process_subqueries(dbms, conn, pq) -> Dict[str, str]:
    subquery_results = {}
    if len(pq.subquery_dict) != 0:
        for subquery_name, subquery in pq.subquery_dict.items():
            subquery_result = execute_query(conn, subquery, dbms)
            # subquery should only have one column
            assert len(subquery_result.columns) == 1
            column_name = subquery_result.columns[0]
            if len(subquery_result[column_name]) != 1:
                # convert the subquery results into a list
                subquery_result = subquery_result[column_name].tolist()
                # format the subquery results
                if isinstance(subquery_result[0], str) or isinstance(
                    subquery_result[0], pd.Timestamp
                ):
                    subquery_result = [f"'{r}'" for r in subquery_result]
                else:
                    subquery_result = [str(r) for r in subquery_result]
                subquery_result = "( " + ", ".join(subquery_result) + " )"
            else:
                subquery_result = subquery_result[column_name][0]
                if isinstance(subquery_result, str):
                    subquery_result = f"'{subquery_result}'"
                else:
                    subquery_result = str(subquery_result)
            subquery_results[subquery_name] = subquery_result
    return subquery_results


def connect(dbms: str, db_config: dict):
    conn = connect_to_db(dbms, db_config)
    return {"conn": conn, "dbms": dbms}


def run(conn: dict, query: str, error: float, probability: float):
    assert (
        error > 0 and error < 1
    ), f"Error rate should be between 0 and 1, but got {error}"
    assert (
        probability > 0 and probability < 1
    ), f"Failure probability should be between 0 and 1, but got {probability}"

    _conn = conn["conn"]
    _dbms = conn["dbms"]
    _query = Query(
        name="PilotDB-query",
        query=query,
        table_cols={},
        table_size={},
        error=error,
        failure_probability=probability,
    )

    pq = Pilot_Rewriter(_query.table_cols, _query.table_size, _dbms)
    sq = Sampling_Rewriter(_query.table_cols, _query.table_size, _dbms)
    pilot_query = pq.rewrite(_query.query)
    sampling_query = sq.rewrite(_query.query)
    sampling_clause = get_sampling_clause(0.05, _dbms)
    pilot_query = pilot_query.format(sampling_method=sampling_clause)

    # ======= start execution =========
    # execute subqueries
    subquery_results = process_subqueries(_dbms, _conn, pq)
    # execute pilot query
    for subquery_name, subquery_result in subquery_results.items():
        pilot_query = pilot_query.replace(subquery_name, subquery_result)
    pilot_results = execute_query(_conn, pilot_query, _dbms)
    # parse the results of pilot query
    page_errors = aggregate_error_to_page_error(
        pq.result_mapping_list, required_error=_query.error
    )
    final_sample_rate = estimate_final_rate(
        failure_prob=_query.failure_probability,
        pilot_results=pilot_results,
        page_errors=page_errors,
        group_cols=pq.group_cols,
        pilot_rate=0.05 / 100,
        limit=pq.limit_value,
    )
    if final_sample_rate == -1:
        final_sample_rate = 1
    elif final_sample_rate * 100 > get_largest_sample_rate(_dbms):
        final_sample_rate = 1
    if final_sample_rate == 1:
        sampling_query = apply_sampling_plan_template(
            sampling_query, scalar_rate_plan(sq.largest_table, 1), dbms
        ).format(sample_rate="1")
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        results_df = execute_query(_conn, sampling_query, _dbms)
    elif final_sample_rate * 100 > 0.05:
        final_sample_rate = round(final_sample_rate * 100, 2)
        logging.info(f"final sample rate: {final_sample_rate}")
        sampling_clause = get_sampling_clause(final_sample_rate, _dbms)
        sampling_query = sampling_query.format(
            sampling_method=sampling_clause, sample_rate=final_sample_rate / 100
        )
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        results_df = execute_query(_conn, sampling_query, _dbms)
    else:
        sampling_clause = get_sampling_clause(0.05, _dbms)
        sampling_query = sampling_query.format(
            sampling_method=sampling_clause, sample_rate=0.05 / 100
        )
        for subquery_name, subquery_result in subquery_results.items():
            sampling_query = sampling_query.replace(subquery_name, subquery_result)
        results_df = execute_query(_conn, sampling_query, _dbms)
    return results_df


def close(conn: dict):
    close_connection(conn["conn"], conn["dbms"])

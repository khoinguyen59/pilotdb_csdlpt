"""Multi-DBMS TPC-H AQP benchmark runner.

Runs each requested TPC-H query both exactly and via :func:`execute_aqp`
against DuckDB, PostgreSQL, or SQL Server. Captures (exact_runtime,
aqp_runtime, final_sample_rate, fallback_reason, relative_error) per query
and writes a JSON (and optional CSV) report.

The module name is preserved from the DuckDB-only version for backward
compatibility; pass ``--dbms`` to target a different DBMS::

    python -m pilotdb.benchmarks.run_duckdb_tpch \
        --queries q1,q6,q14 --pilot-rate 1.0 --sf 1 --output-dir bench_out

    python -m pilotdb.benchmarks.run_duckdb_tpch --dbms postgres \
        --db-config-yaml db_configs/postgres_local.yml \
        --queries q1,q6,q14 --pilot-rate 1.0 --sf 1 --output-dir bench_out_pg

The output schema is captured in :data:`_REQUIRED_KEYS`; every record
produced by :func:`measure` has exactly those keys.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import duckdb
import pandas as pd

from pilotdb.execute import execute_aqp
from pilotdb.benchmarks.tpch_shared import (
    ALL_QIDS,
    _transpile_for,
    available_qids,
    build_query_obj,
    exact_run,
    load_query_sql,
    scalar_summary,
    summarize_error,
)


def _extract_group_by_columns(qid: str) -> list[str]:
    """Return the GROUP BY column names for the given TPC-H query,
    extracted from the SQL template using sqlglot.

    Returns an empty list if the query has no GROUP BY or if parsing
    fails (callers should fall back to the dtype-based heuristic).
    """
    sql = load_query_sql(qid)
    if sql is None:
        return []
    try:
        import sqlglot
        from sqlglot import exp
        parsed = sqlglot.parse_one(sql)
        group = parsed.find(exp.Group)
        if group is None:
            return []
        return [e.alias_or_name for e in group.expressions]
    except Exception:
        return []


# Keys every record must have. Used by tests + CSV writer.
_REQUIRED_KEYS: frozenset[str] = frozenset({
    "query_id",
    "dbms",
    "sf",
    "pilot_sample_rate",
    "final_sample_rate",
    "exact_runtime_s",
    "aqp_runtime_s",
    "speedup",
    "fallback_reason",
    "fallback_triggered",
    "relative_error",
    "mean_row_relative_error",
    "max_row_relative_error",
    "missing_groups_count",
    "exact_value_sample",
    "aqp_value_sample",
    "n_rows_exact",
    "n_rows_aqp",
    "error",
    "skipped",
    "skip_reason",
    "timestamp_iso",
    "variance_bound_note",
})


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop unnamed/empty-header index columns that leak from CSV round-trips."""
    bad = [c for c in df.columns if not c or str(c).lower().startswith("unnamed")]
    if bad:
        return df.drop(columns=bad)
    return df


def compute_detailed_group_errors(
    exact_df: pd.DataFrame,
    aqp_df: pd.DataFrame,
    qid: str,
) -> tuple[float | None, float | None, int]:
    """Align matching group keys and compute mean/max relative error
    and missing group counts.

    Key columns are determined by parsing the SQL template's GROUP BY
    clause with sqlglot. This prevents numeric key columns (e.g.
    ``o_year`` int64, ``l_year`` int64) from being misclassified as
    metric columns by the ``is_numeric_dtype`` heuristic.
    """
    if exact_df is None or exact_df.empty:
        return None, None, 0
    if aqp_df is None or aqp_df.empty:
        return float("inf"), float("inf"), len(exact_df)

    exact_df = _clean_columns(exact_df)
    aqp_df = _clean_columns(aqp_df)

    # --- Determine key vs value columns ---
    # Preferred: use sqlglot to extract GROUP BY column names.
    group_by_keys = _extract_group_by_columns(qid)
    # Keep only keys that actually exist in both DataFrames.
    group_by_keys = [k for k in group_by_keys if k in exact_df.columns and k in aqp_df.columns]

    if group_by_keys:
        key_cols = group_by_keys
        value_cols = [
            c for c in exact_df.columns
            if c not in key_cols
            and c in aqp_df.columns
            and pd.api.types.is_numeric_dtype(exact_df[c])
        ]
    else:
        # Fallback: dtype-based heuristic (original behaviour).
        all_numeric = [c for c in exact_df.columns if pd.api.types.is_numeric_dtype(exact_df[c])]
        key_cols = [c for c in exact_df.columns if c not in all_numeric]
        value_cols = [c for c in all_numeric if c in aqp_df.columns]

    # --- Scalar aggregate (no GROUP BY) ---
    if not key_cols:
        errors = []
        for c in value_cols:
            e_val = float(exact_df[c].iloc[0])
            a_val = float(aqp_df[c].iloc[0])
            errors.append(
                abs(a_val - e_val) / abs(e_val)
                if e_val != 0
                else (0.0 if a_val == 0 else float("inf"))
            )
        if not errors:
            return None, None, 0
        return sum(errors) / len(errors), max(errors), 0

    # --- Group-level comparison ---
    key_cols = [c for c in key_cols if c in aqp_df.columns]
    if not key_cols:
        return None, None, 0

    aqp_lookup: dict[tuple, dict[str, float]] = {}
    for _, row in aqp_df.iterrows():
        k = tuple(row[col] for col in key_cols)
        aqp_lookup[k] = {col: row[col] for col in value_cols}

    row_errors: list[float] = []
    missing_groups = 0

    for _, row in exact_df.iterrows():
        k = tuple(row[col] for col in key_cols)
        if k not in aqp_lookup:
            missing_groups += 1
            continue
        aqp_row = aqp_lookup[k]
        for col in value_cols:
            if col in aqp_row:
                e_val = row[col]
                a_val = aqp_row[col]
                if pd.isna(e_val) or pd.isna(a_val):
                    continue
                e_val = float(e_val)
                a_val = float(a_val)
                rel = (
                    abs(a_val - e_val) / abs(e_val)
                    if e_val != 0
                    else (0.0 if a_val == 0 else float("inf"))
                )
                row_errors.append(rel)

    finite_errors = [r for r in row_errors if r != float("inf") and not pd.isna(r)]
    if not finite_errors:
        return (
            (float("inf") if missing_groups > 0 else 0.0),
            (float("inf") if missing_groups > 0 else 0.0),
            missing_groups,
        )

    return sum(finite_errors) / len(finite_errors), max(finite_errors), missing_groups



@dataclass
class RunOpts:
    pilot_rate: float = 1.0
    sf: int = 1
    error: float = 0.05
    failure_prob: float = 0.05


@dataclass
class DbTarget:
    """Where the benchmark runs.

    For DuckDB, `path` is the .duckdb file. For Postgres / SQL Server,
    `config` is the dict passed to `pilotdb.db_driver.driver.connect_to_db`.
    """
    dbms: str
    path: Optional[str] = None
    config: Optional[dict] = None

    def db_config(self) -> dict:
        """Build the dict expected by `execute_aqp`."""
        if self.dbms == "duckdb":
            return {"dbms": "duckdb", "path": self.path}
        # PG / MSSQL: copy the config dict and add the dbms key.
        cfg = dict(self.config or {})
        cfg.setdefault("dbms", self.dbms)
        return cfg


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_target(target_or_path: Union[DbTarget, str, os.PathLike]) -> DbTarget:
    """Accept a DbTarget OR a bare DuckDB path string (backward compat)."""
    if isinstance(target_or_path, DbTarget):
        return target_or_path
    return DbTarget(dbms="duckdb", path=str(target_or_path))


def _blank_record(qid: str, opts: RunOpts, dbms: str = "duckdb") -> dict[str, Any]:
    return {
        "query_id": qid,
        "dbms": dbms,
        "sf": opts.sf,
        "pilot_sample_rate": opts.pilot_rate,
        "final_sample_rate": None,
        "exact_runtime_s": None,
        "aqp_runtime_s": None,
        "speedup": None,
        "fallback_reason": None,
        "fallback_triggered": False,
        "relative_error": None,
        "mean_row_relative_error": None,
        "max_row_relative_error": None,
        "missing_groups_count": None,
        "exact_value_sample": None,
        "aqp_value_sample": None,
        "n_rows_exact": None,
        "n_rows_aqp": None,
        "error": None,
        "skipped": False,
        "skip_reason": None,
        "timestamp_iso": _utc_now_iso(),
        "variance_bound_note": None,
    }



def setup_tpch_db(sf: int, db_path: str) -> None:
    """Create a TPC-H SF=`sf` DuckDB at `db_path` if absent."""
    p = Path(db_path)
    if p.exists() and p.stat().st_size > 0:
        logging.info(f"[bench] reusing existing TPC-H db at {db_path}")
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    logging.info(f"[bench] generating TPC-H SF={sf} at {db_path}")
    conn = duckdb.connect(database=db_path, read_only=False)
    try:
        conn.execute("INSTALL tpch; LOAD tpch;")
        conn.execute(f"CALL dbgen(sf={sf});")
    finally:
        conn.close()


def setup_postgres_db(cfg: dict, duckdb_src: str, sf: int = 1) -> None:
    """Idempotent: load TPC-H into Postgres from the DuckDB source if needed."""
    from pilotdb.benchmarks.load_tpch_postgres import load as pg_load
    pg_load(cfg, duckdb_src, sf=sf, if_exists="skip")


def setup_sqlserver_db(cfg: dict, duckdb_src: str, sf: int = 1) -> None:
    """Idempotent: load TPC-H into SQL Server from the DuckDB source if needed."""
    from pilotdb.benchmarks.load_tpch_sqlserver import load as mssql_load
    mssql_load(cfg, duckdb_src, sf=sf, if_exists="skip")


def measure(
    qid: str,
    target_or_path: Union[DbTarget, str, os.PathLike],
    opts: RunOpts,
) -> dict[str, Any]:
    """Run one query both exactly and via AQP, return one report record."""
    target = _coerce_target(target_or_path)
    rec = _blank_record(qid, opts, dbms=target.dbms)
    sql = load_query_sql(qid)
    if sql is None:
        rec["skipped"] = True
        rec["skip_reason"] = "no_template"
        return rec
    # Canonical templates are in DuckDB / Postgres flavour. For SQL Server
    # we need T-SQL date arithmetic before either exact OR AQP can parse it.
    if target.dbms == "sqlserver":
        sql = _transpile_for(sql, "tsql")
    elif target.dbms == "postgres":
        sql = _transpile_for(sql, "postgres")

    # ---- exact -----------------------------------------------------------
    exact_df: pd.DataFrame | None = None
    try:
        t0 = time.perf_counter()
        exact_df = exact_run(sql, target.dbms,
                             path=target.path, config=target.config)
        rec["exact_runtime_s"] = time.perf_counter() - t0
        rec["n_rows_exact"] = len(exact_df)
        rec["exact_value_sample"] = scalar_summary(exact_df, qid)
    except Exception as e:
        rec["error"] = f"exact:{type(e).__name__}:{e}"
        rec["skipped"] = True
        rec["skip_reason"] = "exact_execution_failed"
        return rec

    # ---- AQP -------------------------------------------------------------
    aqp_df: pd.DataFrame | None = None
    try:
        q = build_query_obj(qid, sql,
                            error=opts.error,
                            failure_probability=opts.failure_prob)
        if opts.sf != 1:
            scaled_sizes = {}
            for name, size in q.table_size.items():
                if name.lower() in ("nation", "region"):
                    scaled_sizes[name] = size
                else:
                    scaled_sizes[name] = size * opts.sf
            q.table_size = scaled_sizes
        db_config = target.db_config()
        t0 = time.perf_counter()
        aqp_df, timing = execute_aqp(q, db_config, pilot_sample_rate=opts.pilot_rate)
        rec["aqp_runtime_s"] = time.perf_counter() - t0
        rec["n_rows_aqp"] = 0 if aqp_df is None else len(aqp_df)
        rec["aqp_value_sample"] = scalar_summary(aqp_df, qid) if aqp_df is not None else None
        rec["final_sample_rate"] = timing.get("final_sample_rate")
        rec["fallback_reason"] = timing.get("fallback_reason")
        rec["fallback_triggered"] = (rec["fallback_reason"] is not None)
    except Exception as e:
        rec["error"] = f"aqp:{type(e).__name__}:{e}"
        rec["fallback_reason"] = "execute_aqp_exception"
        rec["fallback_triggered"] = True
        rec["mean_row_relative_error"] = None
        rec["max_row_relative_error"] = None
        rec["missing_groups_count"] = None
        return rec

    # ---- summary ---------------------------------------------------------
    if rec["exact_runtime_s"] and rec["aqp_runtime_s"]:
        rec["speedup"] = rec["exact_runtime_s"] / max(rec["aqp_runtime_s"], 1e-9)
    rec["relative_error"] = summarize_error(exact_df, aqp_df, qid)
    mean_err, max_err, missing_cnt = compute_detailed_group_errors(exact_df, aqp_df, qid)
    rec["mean_row_relative_error"] = mean_err
    rec["max_row_relative_error"] = max_err
    rec["missing_groups_count"] = missing_cnt

    if (rec["fallback_reason"] is None
        and rec["mean_row_relative_error"] is not None
        and rec["mean_row_relative_error"] > opts.error):
        rec["variance_bound_note"] = "variance_bound_violated"

    return rec



def _resolve_qids(spec: str) -> list[str]:
    spec = spec.strip().lower()
    if spec in ("all", "*"):
        return list(ALL_QIDS)
    out = []
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if not tok.startswith("q"):
            tok = "q" + tok
        out.append(tok)
    return out


def _write_outputs(
    records: list[dict[str, Any]],
    output_dir: Path,
    write_csv: bool,
) -> tuple[Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"results_{stamp}.json"
    json_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    csv_path: Path | None = None
    if write_csv:
        csv_path = output_dir / f"results_{stamp}.csv"
        fields = sorted(_REQUIRED_KEYS)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for rec in records:
                w.writerow({k: rec.get(k) for k in fields})
    return json_path, csv_path


def _load_yaml_config(path: str) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"--db-config-yaml not found: {path}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="run_duckdb_tpch",
        description="Run exact + PilotDB AQP for TPC-H queries on DuckDB/Postgres/SQL Server.",
    )
    ap.add_argument("--dbms", choices=("duckdb", "postgres", "sqlserver"),
                    default="duckdb",
                    help="Target DBMS. DuckDB uses --db-path; others use --db-config-yaml.")
    ap.add_argument("--queries", default="all",
                    help="Comma-separated qids (e.g. q1,q6,q14) or 'all'.")
    ap.add_argument("--pilot-rate", type=float, default=1.0,
                    help="Pilot sample rate in percent (e.g. 1.0 = 1%%).")
    ap.add_argument("--sf", type=int, default=1, help="TPC-H scale factor.")
    ap.add_argument("--output-dir", default="bench_out", help="Where to write reports.")
    ap.add_argument("--db-path", default=None,
                    help="DuckDB only: path to .duckdb file; generated if missing.")
    ap.add_argument("--db-config-yaml", default=None,
                    help="Postgres / SQL Server: YAML config file.")
    ap.add_argument("--duckdb-src", default=None,
                    help="When --dbms is postgres/sqlserver, source DuckDB file to "
                         "auto-load if target DB is empty. Defaults to "
                         "<output-dir>/tpch_sf<sf>.duckdb.")
    ap.add_argument("--error", type=float, default=0.05,
                    help="Required relative error bound.")
    ap.add_argument("--failure-prob", type=float, default=0.05,
                    help="Acceptable failure probability.")
    ap.add_argument("--csv", dest="csv", action="store_true",
                    help="Also write a CSV report (default).")
    ap.add_argument("--no-csv", dest="csv", action="store_false")
    ap.set_defaults(csv=True)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build the DbTarget for whichever dbms.
    if args.dbms == "duckdb":
        db_path = args.db_path or str(output_dir / f"tpch_sf{args.sf}.duckdb")
        setup_tpch_db(args.sf, db_path)
        target = DbTarget(dbms="duckdb", path=db_path)
    else:
        if args.db_config_yaml is None:
            raise SystemExit(f"--db-config-yaml is required for --dbms {args.dbms}")
        cfg = _load_yaml_config(args.db_config_yaml)
        duckdb_src = args.duckdb_src or str(output_dir / f"tpch_sf{args.sf}.duckdb")
        if Path(duckdb_src).exists():
            if args.dbms == "postgres":
                setup_postgres_db(cfg, duckdb_src, sf=args.sf)
            else:
                setup_sqlserver_db(cfg, duckdb_src, sf=args.sf)
        else:
            logging.warning(
                "[bench] %s loader skipped: duckdb-src not found at %s. "
                "Assuming target DB is already populated.", args.dbms, duckdb_src,
            )
        target = DbTarget(dbms=args.dbms, config=cfg)

    opts = RunOpts(
        pilot_rate=args.pilot_rate,
        sf=args.sf,
        error=args.error,
        failure_prob=args.failure_prob,
    )
    qids = _resolve_qids(args.queries)
    records: list[dict[str, Any]] = []
    for qid in qids:
        logging.info(f"[bench] {qid} ...")
        try:
            rec = measure(qid, target, opts)
        except Exception as e:
            rec = _blank_record(qid, opts, dbms=target.dbms)
            rec["error"] = f"runner:{type(e).__name__}:{e}"
            rec["skipped"] = True
            rec["skip_reason"] = "runner_exception"
            logging.warning(traceback.format_exc())
        logging.info(
            f"[bench] {qid} -> exact={rec.get('exact_runtime_s')} "
            f"aqp={rec.get('aqp_runtime_s')} "
            f"rel_err={rec.get('relative_error')} "
            f"fsr={rec.get('final_sample_rate')} "
            f"reason={rec.get('fallback_reason')} "
            f"skipped={rec.get('skipped')}"
        )
        records.append(rec)

    json_path, csv_path = _write_outputs(records, output_dir, args.csv)
    logging.info(f"[bench] wrote {json_path}")
    if csv_path is not None:
        logging.info(f"[bench] wrote {csv_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

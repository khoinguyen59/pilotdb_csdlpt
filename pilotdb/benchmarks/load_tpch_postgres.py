"""Load TPC-H data into a PostgreSQL database from an existing DuckDB file.

Pipeline:

  DuckDB SF=N .duckdb  --COPY-->  CSV per table  --copy_expert-->  PostgreSQL

The CSV files are reused across DBMS loaders, so this module shares an output
directory with `load_tpch_sqlserver.py`. The loader is idempotent by default:
if the target Postgres table already has the expected row count, it is left
untouched (`--if-exists skip`). Pass `--if-exists drop` to force a fresh load.

CLI:

    python -m pilotdb.benchmarks.load_tpch_postgres \
        --pg-config db_configs/postgres_local.yml \
        --duckdb-src bench_out_full/tpch_sf1.duckdb \
        --sf 1
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import duckdb
import psycopg2
import yaml


TPCH_TABLES = [
    # In reference order: parents before children, so REFERENCES checks pass.
    "region",
    "nation",
    "supplier",
    "customer",
    "part",
    "partsupp",
    "orders",
    "lineitem",
]


# Expected row counts at each scale factor. Used to detect "already loaded".
SF_TABLE_SIZES: dict[int, dict[str, int]] = {
    1: {
        "lineitem": 6_001_215,
        "orders": 1_500_000,
        "partsupp": 800_000,
        "part": 200_000,
        "customer": 150_000,
        "supplier": 10_000,
        "nation": 25,
        "region": 5,
    },
    10: {
        "lineitem": 59_986_052,
        "orders": 15_000_000,
        "partsupp": 8_000_000,
        "part": 2_000_000,
        "customer": 1_500_000,
        "supplier": 100_000,
        "nation": 25,
        "region": 5,
    },
    100: {
        "lineitem": 600_037_902,
        "orders": 150_000_000,
        "partsupp": 80_000_000,
        "part": 20_000_000,
        "customer": 15_000_000,
        "supplier": 1_000_000,
        "nation": 25,
        "region": 5,
    },
}



def _load_pg_config(path: str | os.PathLike) -> dict:
    """Read a YAML PG config, with env var overrides."""
    cfg: dict = {}
    p = Path(path)
    if p.exists():
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg["dbname"] = os.environ.get("PILOTDB_PG_DBNAME", cfg.get("dbname", "tpch"))
    cfg["username"] = os.environ.get("PILOTDB_PG_USER", cfg.get("username", "pilotdb"))
    cfg["host"] = os.environ.get("PILOTDB_PG_HOST", cfg.get("host", "localhost"))
    cfg["port"] = int(os.environ.get("PILOTDB_PG_PORT", cfg.get("port", 5432)))
    cfg["password"] = os.environ.get("PILOTDB_PG_PASSWORD", cfg.get("password"))
    return cfg


def _pg_connect(cfg: dict, *, autocommit: bool = True, timeout: int = 5):
    kwargs = dict(
        dbname=cfg["dbname"],
        user=cfg["username"],
        host=cfg["host"],
        port=cfg["port"],
        connect_timeout=timeout,
    )
    if cfg.get("password"):
        kwargs["password"] = cfg["password"]
    conn = psycopg2.connect(**kwargs)
    conn.autocommit = autocommit
    return conn


def export_csvs_from_duckdb(
    duckdb_src: str,
    out_dir: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Path]:
    """Export each TPC-H table from DuckDB to a CSV file. Returns paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    conn = duckdb.connect(duckdb_src, read_only=True)
    try:
        for t in TPCH_TABLES:
            csv_path = out_dir / f"{t}.csv"
            if csv_path.exists() and not overwrite:
                logging.info(f"[export] reuse existing {csv_path}")
                paths[t] = csv_path
                continue
            logging.info(f"[export] {t} -> {csv_path}")
            conn.execute(
                f"COPY (SELECT * FROM {t}) TO '{csv_path.as_posix()}' "
                f"(FORMAT CSV, HEADER false, DELIMITER ',', "
                f"DATEFORMAT '%Y-%m-%d')"
            )
            paths[t] = csv_path
    finally:
        conn.close()
    return paths


def _ensure_database(cfg: dict) -> None:
    """Connect to the `postgres` admin DB and CREATE the target DB if absent."""
    admin_cfg = dict(cfg)
    admin_cfg["dbname"] = "postgres"
    try:
        conn = _pg_connect(admin_cfg, autocommit=True)
    except psycopg2.OperationalError as e:
        raise SystemExit(f"[pg] cannot reach Postgres admin DB: {e}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                        (cfg["dbname"],))
            if cur.fetchone() is None:
                logging.info(f"[pg] creating database {cfg['dbname']}")
                cur.execute(f'CREATE DATABASE "{cfg["dbname"]}"')
    finally:
        conn.close()


def _apply_ddl(conn, ddl_path: Path) -> None:
    sql = ddl_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)


def _table_row_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cur.fetchone()[0])


def _copy_csv(conn, table: str, csv_path: Path) -> int:
    with conn.cursor() as cur, csv_path.open("r", encoding="utf-8", newline="") as f:
        cur.copy_expert(
            f"COPY {table} FROM STDIN WITH (FORMAT CSV, DELIMITER ',', NULL '')",
            f,
        )
        return cur.rowcount


def load(
    pg_cfg: dict,
    duckdb_src: str,
    *,
    sf: int = 1,
    if_exists: str = "skip",
    csv_dir: Optional[Path] = None,
) -> dict[str, dict]:
    """Top-level loader. Returns per-table {action, rowcount, source}."""
    csv_dir = csv_dir or Path(duckdb_src).parent / "csv"
    paths = export_csvs_from_duckdb(duckdb_src, csv_dir, overwrite=False)

    _ensure_database(pg_cfg)
    conn = _pg_connect(pg_cfg, autocommit=False)
    try:
        target_sizes = SF_TABLE_SIZES.get(sf, {})
        # Decide whether to drop+recreate.
        do_recreate = True
        if if_exists == "skip":
            existing_sizes = {}
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
                existing = {r[0] for r in cur.fetchall()}
            if all(t in existing for t in TPCH_TABLES):
                for t in TPCH_TABLES:
                    try:
                        existing_sizes[t] = _table_row_count(conn, t)
                    except Exception:
                        existing_sizes[t] = -1
                if all(existing_sizes.get(t) == target_sizes.get(t, -2)
                       for t in TPCH_TABLES):
                    logging.info("[pg] all 8 tables already at SF=%d sizes; skipping load",
                                 sf)
                    return {t: {"action": "skip",
                                "rowcount": existing_sizes[t],
                                "source": str(paths[t])}
                            for t in TPCH_TABLES}
        if if_exists == "append":
            do_recreate = False

        ddl_path = Path(__file__).parent / "tpch_pg_ddl.sql"
        if do_recreate:
            logging.info(f"[pg] applying DDL from {ddl_path}")
            _apply_ddl(conn, ddl_path)
            conn.commit()

        report: dict[str, dict] = {}
        for t in TPCH_TABLES:
            csv_path = paths[t]
            n = _copy_csv(conn, t, csv_path)
            logging.info(f"[pg] {t}: loaded {n} rows from {csv_path}")
            report[t] = {"action": "load", "rowcount": n, "source": str(csv_path)}
        conn.commit()
        return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="load_tpch_postgres",
        description="Load TPC-H from a DuckDB source into PostgreSQL.",
    )
    ap.add_argument("--pg-config", default="db_configs/postgres_local.yml")
    ap.add_argument("--duckdb-src", required=True,
                    help="Path to an existing DuckDB .duckdb file with TPC-H data.")
    ap.add_argument("--sf", type=int, default=1,
                    help="Scale factor (used to verify expected row counts).")
    ap.add_argument("--if-exists", choices=("skip", "drop", "append"), default="skip",
                    help="skip: leave tables alone if sizes already match; "
                         "drop: drop+recreate; append: just COPY into existing tables.")
    ap.add_argument("--csv-dir", default=None,
                    help="Where to write intermediate CSV files. "
                         "Defaults to <duckdb-src-parent>/csv.")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = _load_pg_config(args.pg_config)
    csv_dir = Path(args.csv_dir).resolve() if args.csv_dir else None
    report = load(cfg, args.duckdb_src, sf=args.sf,
                  if_exists=args.if_exists, csv_dir=csv_dir)
    for t, info in report.items():
        print(f"  {t:<10} {info['action']:<6} rows={info['rowcount']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

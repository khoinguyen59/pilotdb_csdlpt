"""Load TPC-H data into a SQL Server database from an existing DuckDB file.

Strategy: reuse the CSV files produced by `load_tpch_postgres.export_csvs_from_duckdb`
and stream them into SQL Server via `pyodbc executemany`. We default to
`executemany` (works without bulk-copy privileges) and let the caller opt
into `BULK INSERT` when the CSV files are reachable from inside the server.

CLI:

    python -m pilotdb.benchmarks.load_tpch_sqlserver \
        --mssql-config db_configs/sqlserver_local.yml \
        --duckdb-src bench_out_full/tpch_sf1.duckdb \
        --sf 1
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import pyodbc
import yaml

from pilotdb.benchmarks.load_tpch_postgres import (
    SF_TABLE_SIZES,
    TPCH_TABLES,
    export_csvs_from_duckdb,
)


# Per-table column lists (header order in the exported CSV).
# Must match the CREATE TABLE column order in tpch_mssql_ddl.sql.
TABLE_COLUMNS: dict[str, list[str]] = {
    "region":   ["r_regionkey", "r_name", "r_comment"],
    "nation":   ["n_nationkey", "n_name", "n_regionkey", "n_comment"],
    "supplier": ["s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone",
                 "s_acctbal", "s_comment"],
    "customer": ["c_custkey", "c_name", "c_address", "c_nationkey", "c_phone",
                 "c_acctbal", "c_mktsegment", "c_comment"],
    "part":     ["p_partkey", "p_name", "p_mfgr", "p_brand", "p_type",
                 "p_size", "p_container", "p_retailprice", "p_comment"],
    "partsupp": ["ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost",
                 "ps_comment"],
    "orders":   ["o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice",
                 "o_orderdate", "o_orderpriority", "o_clerk", "o_shippriority",
                 "o_comment"],
    "lineitem": ["l_orderkey", "l_partkey", "l_suppkey", "l_linenumber",
                 "l_quantity", "l_extendedprice", "l_discount", "l_tax",
                 "l_returnflag", "l_linestatus", "l_shipdate", "l_commitdate",
                 "l_receiptdate", "l_shipinstruct", "l_shipmode", "l_comment"],
}


def _load_mssql_config(path: str | os.PathLike) -> dict:
    cfg: dict = {}
    p = Path(path)
    if p.exists():
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg["dbname"] = os.environ.get("PILOTDB_MSSQL_DBNAME", cfg.get("dbname", "pilotdb_tpch"))
    cfg["host"] = os.environ.get("PILOTDB_MSSQL_HOST", cfg.get("host", "localhost,1433"))
    cfg["driver"] = os.environ.get("PILOTDB_MSSQL_DRIVER", cfg.get("driver", "ODBC Driver 18 for SQL Server"))
    cfg["username"] = os.environ.get("PILOTDB_MSSQL_USER", cfg.get("username"))
    cfg["password"] = os.environ.get("PILOTDB_MSSQL_PASSWORD", cfg.get("password"))
    trusted_env = os.environ.get("PILOTDB_MSSQL_TRUSTED")
    if trusted_env is not None:
        cfg["trusted_connection"] = trusted_env.lower() in ("1", "true", "yes")
    else:
        cfg["trusted_connection"] = bool(cfg.get("trusted_connection", False))
    return cfg


def _build_conn_str(cfg: dict, *, dbname: str | None) -> str:
    parts = [f"DRIVER={{{cfg['driver']}}}", f"SERVER={cfg['host']}"]
    if dbname:
        parts.append(f"DATABASE={dbname}")
    if cfg.get("trusted_connection"):
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={cfg.get('username') or ''}")
        parts.append(f"PWD={cfg.get('password') or ''}")
    parts.append("TrustServerCertificate=yes")
    return ";".join(parts) + ";"


def _mssql_connect(cfg: dict, *, dbname: str | None = None, timeout: int = 5):
    conn = pyodbc.connect(_build_conn_str(cfg, dbname=dbname), timeout=timeout)
    return conn


def _ensure_database(cfg: dict) -> None:
    """Connect to master and CREATE the target DB if absent."""
    target = cfg["dbname"]
    try:
        conn = _mssql_connect(cfg, dbname="master")
    except pyodbc.Error as e:
        raise SystemExit(f"[mssql] cannot reach SQL Server master DB: {e}")
    conn.autocommit = True
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM sys.databases WHERE name = ?", target
        ).fetchone()
        if row is None:
            logging.info(f"[mssql] creating database {target}")
            cur.execute(f"CREATE DATABASE [{target}]")
    finally:
        conn.close()


def _apply_ddl(conn, ddl_path: Path) -> None:
    sql = ddl_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    # SQL Server batches: split on GO if needed; this DDL has none.
    cur.execute(sql)
    cur.commit()


def _table_row_count(conn, table: str) -> int:
    cur = conn.cursor()
    return int(cur.execute(f"SELECT COUNT(*) FROM dbo.{table}").fetchone()[0])


def _convert_value(col: str, raw: str):
    """Convert a CSV string cell to the appropriate Python type for pyodbc."""
    if raw == "":
        return None
    if col.endswith("key") or col in ("l_linenumber", "p_size", "ps_availqty",
                                       "o_shippriority", "n_regionkey"):
        return int(raw)
    if col in ("s_acctbal", "c_acctbal", "p_retailprice", "ps_supplycost",
               "o_totalprice", "l_quantity", "l_extendedprice", "l_discount",
               "l_tax"):
        return float(raw)
    # Dates and strings pass through as text (pyodbc auto-converts).
    return raw


def _load_table(conn, table: str, csv_path: Path, *, chunk_size: int = 5000) -> int:
    cols = TABLE_COLUMNS[table]
    placeholders = ",".join(["?"] * len(cols))
    insert_sql = (
        f"INSERT INTO dbo.{table} ({','.join(cols)}) VALUES ({placeholders})"
    )
    cur = conn.cursor()
    cur.fast_executemany = True
    n = 0
    batch: list[tuple] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) != len(cols):
                raise ValueError(
                    f"[mssql] {table}: row has {len(row)} fields, expected {len(cols)}"
                )
            batch.append(tuple(_convert_value(c, v) for c, v in zip(cols, row)))
            if len(batch) >= chunk_size:
                cur.executemany(insert_sql, batch)
                n += len(batch)
                batch = []
        if batch:
            cur.executemany(insert_sql, batch)
            n += len(batch)
    cur.commit()
    return n


def load(
    mssql_cfg: dict,
    duckdb_src: str,
    *,
    sf: int = 1,
    if_exists: str = "skip",
    csv_dir: Optional[Path] = None,
) -> dict[str, dict]:
    csv_dir = csv_dir or Path(duckdb_src).parent / "csv"
    paths = export_csvs_from_duckdb(duckdb_src, csv_dir, overwrite=False)

    _ensure_database(mssql_cfg)
    conn = _mssql_connect(mssql_cfg, dbname=mssql_cfg["dbname"])
    conn.autocommit = False
    try:
        target_sizes = SF_TABLE_SIZES.get(sf, {})
        do_recreate = True
        if if_exists == "skip":
            existing_sizes: dict[str, int] = {}
            cur = conn.cursor()
            existing = {r[0] for r in cur.execute(
                "SELECT name FROM sys.tables WHERE schema_id = SCHEMA_ID('dbo')"
            ).fetchall()}
            if all(t in existing for t in TPCH_TABLES):
                for t in TPCH_TABLES:
                    try:
                        existing_sizes[t] = _table_row_count(conn, t)
                    except Exception:
                        existing_sizes[t] = -1
                if all(existing_sizes.get(t) == target_sizes.get(t, -2)
                       for t in TPCH_TABLES):
                    logging.info(
                        "[mssql] all 8 tables already at SF=%d sizes; skipping load",
                        sf,
                    )
                    return {t: {"action": "skip",
                                "rowcount": existing_sizes[t],
                                "source": str(paths[t])}
                            for t in TPCH_TABLES}
        if if_exists == "append":
            do_recreate = False

        ddl_path = Path(__file__).parent / "tpch_mssql_ddl.sql"
        if do_recreate:
            logging.info(f"[mssql] applying DDL from {ddl_path}")
            _apply_ddl(conn, ddl_path)

        report: dict[str, dict] = {}
        for t in TPCH_TABLES:
            n = _load_table(conn, t, paths[t])
            logging.info(f"[mssql] {t}: loaded {n} rows from {paths[t]}")
            report[t] = {"action": "load", "rowcount": n,
                          "source": str(paths[t])}
        return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="load_tpch_sqlserver",
        description="Load TPC-H from a DuckDB source into SQL Server.",
    )
    ap.add_argument("--mssql-config", default="db_configs/sqlserver_local.yml")
    ap.add_argument("--duckdb-src", required=True)
    ap.add_argument("--sf", type=int, default=1)
    ap.add_argument("--if-exists", choices=("skip", "drop", "append"),
                    default="skip")
    ap.add_argument("--csv-dir", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = _load_mssql_config(args.mssql_config)
    csv_dir = Path(args.csv_dir).resolve() if args.csv_dir else None
    report = load(cfg, args.duckdb_src, sf=args.sf,
                  if_exists=args.if_exists, csv_dir=csv_dir)
    for t, info in report.items():
        print(f"  {t:<10} {info['action']:<6} rows={info['rowcount']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

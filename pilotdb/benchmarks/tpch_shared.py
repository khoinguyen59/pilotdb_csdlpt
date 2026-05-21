"""Shared TPC-H constants and helpers for the benchmark runner.

Centralises the TPC-H SF=1 row counts and column lists, plus the
template-loading and per-query error-summarisation logic. The runner
imports from here so the helper logic is unit-testable on its own.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd

from pilotdb.query import Query


# ---- TPC-H SF=1 row counts ----------------------------------------------
TPCH_SF1_SIZES: dict[str, int] = {
    "lineitem": 6_001_215,
    "orders": 1_500_000,
    "partsupp": 800_000,
    "part": 200_000,
    "customer": 150_000,
    "supplier": 10_000,
    "nation": 25,
    "region": 5,
}


TPCH_TABLE_COLS: dict[str, list[str]] = {
    "lineitem": [
        "l_orderkey", "l_partkey", "l_suppkey", "l_linenumber",
        "l_quantity", "l_extendedprice", "l_discount", "l_tax",
        "l_returnflag", "l_linestatus", "l_shipdate", "l_commitdate",
        "l_receiptdate", "l_shipinstruct", "l_shipmode", "l_comment",
    ],
    "orders": [
        "o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice",
        "o_orderdate", "o_orderpriority", "o_clerk", "o_shippriority",
        "o_comment",
    ],
    "partsupp": [
        "ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost",
        "ps_comment",
    ],
    "part": [
        "p_partkey", "p_name", "p_mfgr", "p_brand", "p_type",
        "p_size", "p_container", "p_retailprice", "p_comment",
    ],
    "customer": [
        "c_custkey", "c_name", "c_address", "c_nationkey", "c_phone",
        "c_acctbal", "c_mktsegment", "c_comment",
    ],
    "supplier": [
        "s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone",
        "s_acctbal", "s_comment",
    ],
    "nation": ["n_nationkey", "n_name", "n_regionkey", "n_comment"],
    "region": ["r_regionkey", "r_name", "r_comment"],
}


# ---- Layout ---------------------------------------------------------------
# pilotdb/benchmarks/tpch_shared.py
#   parents[0] = pilotdb/benchmarks/
#   parents[1] = pilotdb/
#   parents[2] = pilotdb_csdlpt/
QUERY_DIR: Path = Path(__file__).resolve().parents[2] / "benchmarks" / "duckdb" / "tpch"

ALL_QIDS: list[str] = [f"q{i}" for i in range(1, 23)]


def available_qids() -> list[str]:
    """List query ids whose SQL template exists on disk."""
    return sorted(
        f"q{p.stem.split('_')[-1]}"
        for p in QUERY_DIR.glob("query_*.sql")
    )


def load_query_sql(qid: str) -> Optional[str]:
    """Return the raw SQL text for the named query, or None if missing.

    `qid` may be lowercase like ``q6`` or ``Q6``.
    """
    qid = qid.strip().lower()
    if not qid.startswith("q"):
        return None
    num = qid[1:]
    if not num.isdigit():
        return None
    path = QUERY_DIR / f"query_{int(num)}.sql"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


_TABLE_PATTERN = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


def tables_in_sql(sql: str) -> list[str]:
    """Return canonical TPC-H table names referenced in `sql`, in order
    of first appearance. Duplicates removed.

    Uses sqlglot's AST when available (correctly handles comma-FROM and
    nested JOINs) and falls back to a regex on parse failure.
    """
    seen: list[str] = []
    try:
        import sqlglot
        from sqlglot import exp
        parsed = sqlglot.parse_one(sql)
        for table in parsed.find_all(exp.Table):
            name = (table.name or "").lower()
            if name in TPCH_SF1_SIZES and name not in seen:
                seen.append(name)
        if seen:
            return seen
    except Exception:
        pass
    for raw in _TABLE_PATTERN.findall(sql):
        name = raw.lower()
        if name in TPCH_SF1_SIZES and name not in seen:
            seen.append(name)
    return seen


def build_query_obj(
    qid: str,
    sql: str,
    *,
    error: float = 0.05,
    failure_probability: float = 0.05,
) -> Query:
    """Materialise a `Query` object for the runner, with `table_cols` and
    `table_size` pre-filled from TPC-H SF=1 metadata.
    """
    tables = tables_in_sql(sql)
    if not tables:
        # Fall back to lineitem so the query at least has metadata.
        tables = ["lineitem"]
    return Query(
        name=f"tpch-{qid}",
        query=sql,
        table_cols={t: TPCH_TABLE_COLS[t] for t in tables},
        table_size={t: TPCH_SF1_SIZES[t] for t in tables},
        error=error,
        failure_probability=failure_probability,
    )


# ---- Error summarisation --------------------------------------------------

def _common_numeric_columns(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    common = [c for c in a.columns if c in b.columns]
    return [c for c in common
            if pd.api.types.is_numeric_dtype(a[c])
            and pd.api.types.is_numeric_dtype(b[c])]


def _scalar_rel(a: float, b: float) -> float:
    if b == 0:
        return 0.0 if a == 0 else float("inf")
    return abs(a - b) / abs(b)


def summarize_error(
    exact_df: pd.DataFrame,
    aqp_df: pd.DataFrame,
    qid: str,
) -> Optional[float]:
    """Return a single relative-error number summarising AQP-vs-exact
    for the given query. The convention deliberately matches the
    existing E2E tests so the numbers line up.
    """
    qid = qid.lower()
    if exact_df is None or aqp_df is None or exact_df.empty or aqp_df.empty:
        return None

    if qid == "q6" and "revenue" in exact_df.columns and "revenue" in aqp_df.columns:
        return _scalar_rel(
            float(aqp_df["revenue"].iloc[0]),
            float(exact_df["revenue"].iloc[0]),
        )
    if qid == "q1" and "sum_qty" in exact_df.columns and "sum_qty" in aqp_df.columns:
        return _scalar_rel(
            float(aqp_df["sum_qty"].sum()),
            float(exact_df["sum_qty"].sum()),
        )
    if qid == "q14" and "promo_revenue" in exact_df.columns and "promo_revenue" in aqp_df.columns:
        return _scalar_rel(
            float(aqp_df["promo_revenue"].iloc[0]),
            float(exact_df["promo_revenue"].iloc[0]),
        )

    # Generic: mean of per-column relative errors on the row-aggregate.
    cols = _common_numeric_columns(exact_df, aqp_df)
    if not cols:
        return None
    rels = []
    for c in cols:
        e = float(exact_df[c].sum())
        a = float(aqp_df[c].sum())
        rels.append(_scalar_rel(a, e))
    finite = [r for r in rels if r != float("inf")]
    if not finite:
        return float("inf")
    return sum(finite) / len(finite)


# Two forms appear in TPC-H templates:
#   - `interval '1' year`   (num and unit separate)
#   - `interval '90 day'`   (num and unit inside the same quoted literal)
_INTERVAL_PAT = (
    r"interval\s+'(?P<num>\d+)(?:\s+(?P<unit_inside>year|month|day)s?)?'"
    r"(?:\s*(?P<unit_outside>year|month|day)s?)?"
)
_INTERVAL_PLUS = re.compile(
    r"date\s+'(?P<date>[\d\-]+)'\s*\+\s*" + _INTERVAL_PAT,
    re.IGNORECASE,
)
_INTERVAL_MINUS = re.compile(
    r"date\s+'(?P<date>[\d\-]+)'\s*\-\s*" + _INTERVAL_PAT,
    re.IGNORECASE,
)


def _interval_sub(sign: str):
    def repl(m: "re.Match[str]") -> str:
        unit = (m.group("unit_inside") or m.group("unit_outside") or "day").lower()
        n = m.group("num")
        return f"DATEADD({unit}, {sign}{n}, '{m.group('date')}')"
    return repl


def _rewrite_intervals_for_tsql(sql: str) -> str:
    """SQL Server doesn't understand `date '...' + interval 'N' UNIT`;
    rewrite to `DATEADD(UNIT, N, '...')` (or `-N` for subtraction).
    """
    sql = _INTERVAL_PLUS.sub(_interval_sub(""), sql)
    sql = _INTERVAL_MINUS.sub(_interval_sub("-"), sql)
    # Also strip stray `date '...'` literals — bare `'...'` works on SQL Server.
    sql = re.sub(r"\bdate\s+'(\d{4}-\d{2}-\d{2})'", r"'\1'", sql, flags=re.IGNORECASE)
    return sql


def _transpile_for(sql: str, dialect: str) -> str:
    """Best-effort transpile from generic TPC-H SQL into a DBMS-specific
    dialect. For SQL Server we first rewrite INTERVAL-arithmetic ourselves
    (sqlglot leaves `INTERVAL '1' YEAR` raw, which T-SQL rejects).
    """
    if dialect == "tsql":
        sql = _rewrite_intervals_for_tsql(sql)
    try:
        import sqlglot
        # No `read=` — passing read="postgres" makes sqlglot rewrite
        # `ORDER BY x` into NULLS-FIRST CASE WHEN, which SQL Server rejects
        # as a duplicate column.
        return sqlglot.transpile(sql, write=dialect)[0]
    except Exception:
        return sql


def exact_run(
    sql: str,
    dbms: str,
    *,
    path: Optional[str] = None,
    config: Optional[dict] = None,
) -> pd.DataFrame:
    """Execute `sql` exactly on the target DBMS and return a DataFrame.

    For DuckDB, opens a read-only `duckdb.connect(path)`.
    For Postgres / SQL Server, opens a fresh connection via the project's
    `connect_to_db` and closes it after the query. The canonical TPC-H
    templates are written in DuckDB / Postgres style; for SQL Server we
    transpile via sqlglot (e.g. ``date '1994-01-01'`` → ``CAST('1994-01-01' AS DATE)``).
    """
    if dbms == "duckdb":
        import os
        import duckdb
        conn = duckdb.connect(database=path, read_only=True)
        seed = os.environ.get("PILOTDB_SEED")
        if seed:
            conn.execute("SET threads = 1;")
            try:
                val = float(seed)
                double_seed = (abs(int(val)) % 1000000) / 1000000.0
                conn.execute(f"SELECT setseed({double_seed});")
            except ValueError:
                pass
        try:
            return conn.execute(sql).fetchdf()
        finally:
            conn.close()

    if dbms == "postgres":
        import pandas.io.sql as sqlio
        from pilotdb.db_driver.driver import connect_to_db
        cfg = dict(config or {})
        cfg["dbms"] = "postgres"
        conn = connect_to_db("postgres", cfg)
        try:
            return sqlio.read_sql_query(_transpile_for(sql, "postgres"), conn)
        finally:
            conn.close()
    if dbms == "sqlserver":
        import pandas.io.sql as sqlio
        from pilotdb.db_driver.driver import connect_to_db
        cfg = dict(config or {})
        cfg["dbms"] = "sqlserver"
        conn = connect_to_db("sqlserver", cfg)
        try:
            return sqlio.read_sql_query(_transpile_for(sql, "tsql"), conn)
        finally:
            conn.close()
    raise ValueError(f"unknown dbms: {dbms}")


def scalar_summary(df: pd.DataFrame, qid: str) -> Optional[float]:
    """Return a single representative numeric value from `df` for logging."""
    if df is None or df.empty:
        return None
    qid = qid.lower()
    if qid == "q6" and "revenue" in df.columns:
        return float(df["revenue"].iloc[0])
    if qid == "q1" and "sum_qty" in df.columns:
        return float(df["sum_qty"].sum())
    if qid == "q14" and "promo_revenue" in df.columns:
        return float(df["promo_revenue"].iloc[0])
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        return None
    return float(numeric.iloc[0, 0])

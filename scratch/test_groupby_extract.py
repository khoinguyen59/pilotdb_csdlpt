"""Quick test: extract GROUP BY columns for all TPC-H queries using sqlglot."""
import sqlglot
from sqlglot import exp
from pathlib import Path

qdir = Path("benchmarks/duckdb/tpch")
for p in sorted(qdir.glob("query_*.sql")):
    sql = p.read_text(encoding="utf-8")
    try:
        parsed = sqlglot.parse_one(sql)
        group = parsed.find(exp.Group)
        if group:
            keys = [e.alias_or_name for e in group.expressions]
            print(f"{p.stem:12s}  GROUP BY: {keys}")
        else:
            print(f"{p.stem:12s}  (no GROUP BY)")
    except Exception as exc:
        print(f"{p.stem:12s}  PARSE ERROR: {exc}")

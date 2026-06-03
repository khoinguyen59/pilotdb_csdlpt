"""Quick post-fix verification: run compute_detailed_group_errors on
identical Q9 and Q7 DataFrames. Should now return 0.0 error."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import duckdb
import pandas as pd
from pilotdb.benchmarks.run_duckdb_tpch import compute_detailed_group_errors
from pilotdb.benchmarks.tpch_shared import load_query_sql

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench_out", "tpch_sf1.duckdb")
conn = duckdb.connect(database=db_path, read_only=True)

PASS = True
for qid in ["q7", "q9", "q1", "q3", "q5", "q8", "q10", "q12", "q18"]:
    sql = load_query_sql(qid)
    if sql is None:
        print(f"  SKIP: {qid} (no SQL template)")
        continue
    df = conn.execute(sql).fetchdf()
    mean_err, max_err, missing = compute_detailed_group_errors(df, df, qid)
    status = "PASS" if (mean_err == 0.0 and max_err == 0.0 and missing == 0) else "FAIL"
    if status == "FAIL":
        PASS = False
    print(f"  {status}: {qid:4s}  mean={mean_err}, max={max_err}, missing={missing}")

# Also test scalar queries (no GROUP BY)
for qid in ["q6", "q14", "q19"]:
    sql = load_query_sql(qid)
    if sql is None:
        print(f"  SKIP: {qid} (no SQL template)")
        continue
    df = conn.execute(sql).fetchdf()
    mean_err, max_err, missing = compute_detailed_group_errors(df, df, qid)
    ok = (mean_err is None or mean_err == 0.0) and (max_err is None or max_err == 0.0)
    status = "PASS" if ok else "FAIL"
    if not ok:
        PASS = False
    print(f"  {status}: {qid:4s}  mean={mean_err}, max={max_err}, missing={missing}  (scalar)")

conn.close()

if PASS:
    print("\nALL PASSED - alignment fix verified")
else:
    print("\nFAILURES DETECTED")
    sys.exit(1)

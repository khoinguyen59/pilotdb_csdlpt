"""Phase 1 Verification: Prove that the Q9/Q7 non-zero error at sample_rate=1
is caused by misalignment in compute_detailed_group_errors, NOT by the estimator.

Hypothesis: `pd.api.types.is_numeric_dtype` classifies `o_year` (int64) as
numeric, so `compute_detailed_group_errors` treats it as a value column instead
of a group key. This causes rows to be matched on `nation` only (the sole
remaining non-numeric column), but since there are 7 years per nation, the
lookup dict overwrites entries and the comparison is scrambled.

Test:
  1. Load the SF=1 benchmark JSON results and check Q7/Q9 row-level errors.
  2. Manually re-run `compute_detailed_group_errors` on live data.
  3. Re-run with CORRECT key columns and compare.
"""

import json
import sys
import os
import time
import duckdb
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pilotdb.benchmarks.run_duckdb_tpch import compute_detailed_group_errors
from pilotdb.benchmarks.tpch_shared import load_query_sql

# ---- Step 1: Load a benchmark result file to see the error pattern ----
sf10_result = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\iter_0\results_20260520T204005Z.json"

with open(sf10_result, "r", encoding="utf-8") as f:
    records = json.load(f)

print("=" * 72)
print("STEP 1: Check existing benchmark results for alignment evidence")
print("=" * 72)
for rec in records:
    qid = rec["query_id"]
    fsr = rec["final_sample_rate"]
    fb = rec["fallback_reason"]
    rel = rec["relative_error"]
    mean_err = rec["mean_row_relative_error"]
    max_err = rec["max_row_relative_error"]
    if fsr == 1 and mean_err is not None and mean_err > 0:
        print(f"\n  *** ANOMALY: {qid} ***")
        print(f"      final_sample_rate = {fsr}  (exact execution)")
        print(f"      fallback_reason   = {fb}")
        print(f"      relative_error    = {rel}  (scalar summary)")
        print(f"      mean_row_rel_err  = {mean_err}")
        print(f"      max_row_rel_err   = {max_err}")
        print(f"      n_rows_exact      = {rec['n_rows_exact']}")
        print(f"      n_rows_aqp        = {rec['n_rows_aqp']}")
    elif fsr == 1 and (mean_err is None or mean_err == 0):
        print(f"  OK: {qid:4s} fsr=1, mean_row_err={mean_err}")

# ---- Step 2: Reproduce the bug on Q9 with live data ----
print("\n" + "=" * 72)
print("STEP 2: Reproduce alignment bug on Q9 using SF=1 DuckDB")
print("=" * 72)

db_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out\tpch_sf1.duckdb"
if not os.path.exists(db_path):
    print(f"  DB not found at {db_path}, creating...")
    conn = duckdb.connect(database=db_path, read_only=False)
    conn.execute("INSTALL tpch; LOAD tpch; CALL dbgen(sf=1);")
    conn.close()

conn = duckdb.connect(database=db_path, read_only=True)

q9_sql = load_query_sql("q9")
print(f"\n  Q9 SQL (first 200 chars): {q9_sql[:200]}...")

# Run exact query twice — both results should be identical
exact_df = conn.execute(q9_sql).fetchdf()
aqp_df = conn.execute(q9_sql).fetchdf()  # Same query = "AQP at 100%"

print(f"\n  exact_df shape: {exact_df.shape}")
print(f"  aqp_df   shape: {aqp_df.shape}")
print(f"  exact_df columns: {list(exact_df.columns)}")
print(f"  exact_df dtypes:\n{exact_df.dtypes}")

# ---- Step 3: Show the bug ----
print("\n" + "=" * 72)
print("STEP 3: Run compute_detailed_group_errors (CURRENT buggy logic)")
print("=" * 72)

mean_err, max_err, missing = compute_detailed_group_errors(exact_df, aqp_df, "q9")
print(f"  mean_row_relative_error = {mean_err}")
print(f"  max_row_relative_error  = {max_err}")
print(f"  missing_groups_count    = {missing}")

if mean_err is not None and mean_err > 0:
    print(f"\n  *** BUG CONFIRMED: Identical DataFrames show non-zero error! ***")
    print(f"  *** Root cause: o_year (int64) is classified as numeric metric ***")
else:
    print(f"\n  Hmm, error is 0 — hypothesis might not hold for SF=1.")

# ---- Step 4: Show WHY — demonstrate column classification ----
print("\n" + "=" * 72)
print("STEP 4: Diagnose column classification")
print("=" * 72)

numeric_cols = [c for c in exact_df.columns if pd.api.types.is_numeric_dtype(exact_df[c])]
key_cols = [c for c in exact_df.columns if c not in numeric_cols]
print(f"  Columns classified as NUMERIC (treated as values): {numeric_cols}")
print(f"  Columns classified as KEY    (used for matching):  {key_cols}")
print(f"\n  o_year dtype = {exact_df['o_year'].dtype}")
print(f"  o_year is_numeric = {pd.api.types.is_numeric_dtype(exact_df['o_year'])}")

if "o_year" in numeric_cols:
    print(f"\n  *** ROOT CAUSE PROVEN: o_year is treated as a value column! ***")
    print(f"  Matching uses only {key_cols} -> 7 rows per nation overwrites in dict")
    print(f"  -> comparisons are between mismatched years -> bogus error")

# ---- Step 5: Fix and re-measure ----
print("\n" + "=" * 72)
print("STEP 5: CORRECT alignment — use (nation, o_year) as key")
print("=" * 72)

correct_key_cols = ["nation", "o_year"]
correct_numeric_cols = ["sum_profit"]

aqp_lookup = {}
for _, row in aqp_df.iterrows():
    k = tuple(row[col] for col in correct_key_cols)
    aqp_lookup[k] = {col: row[col] for col in correct_numeric_cols}

row_errors = []
for _, row in exact_df.iterrows():
    k = tuple(row[col] for col in correct_key_cols)
    if k not in aqp_lookup:
        print(f"  MISSING: {k}")
        continue
    for col in correct_numeric_cols:
        e_val = float(row[col])
        a_val = float(aqp_lookup[k][col])
        rel = abs(a_val - e_val) / abs(e_val) if e_val != 0 else (0.0 if a_val == 0 else float("inf"))
        row_errors.append(rel)

if row_errors:
    correct_mean = sum(row_errors) / len(row_errors)
    correct_max = max(row_errors)
    print(f"  CORRECT mean_row_relative_error = {correct_mean}")
    print(f"  CORRECT max_row_relative_error  = {correct_max}")
    
    if correct_mean == 0.0:
        print(f"\n  ✅ HYPOTHESIS CONFIRMED: Alignment bug is the sole cause!")
        print(f"     Fixing column classification will resolve Q9 error.")
    else:
        print(f"\n  ⚠ Partial: Aligned error is {correct_mean}, not zero.")
        print(f"    May indicate a real estimator issue on top of alignment.")
else:
    print(f"  No errors computed (all rows missing?)")

# ---- Step 6: Also check Q7 ----
print("\n" + "=" * 72)
print("STEP 6: Same check for Q7")
print("=" * 72)

q7_sql = load_query_sql("q7")
exact_q7 = conn.execute(q7_sql).fetchdf()
aqp_q7 = conn.execute(q7_sql).fetchdf()

print(f"  Q7 columns: {list(exact_q7.columns)}")
print(f"  Q7 dtypes:\n{exact_q7.dtypes}")

numeric_q7 = [c for c in exact_q7.columns if pd.api.types.is_numeric_dtype(exact_q7[c])]
key_q7 = [c for c in exact_q7.columns if c not in numeric_q7]
print(f"\n  Q7 numeric (treated as values): {numeric_q7}")
print(f"  Q7 key     (used for matching): {key_q7}")

mean_err_q7, max_err_q7, _ = compute_detailed_group_errors(exact_q7, aqp_q7, "q7")
print(f"\n  BUGGY:   mean={mean_err_q7}, max={max_err_q7}")

# Check if l_year exists and is numeric
if "l_year" in exact_q7.columns:
    year_col = "l_year"
elif "l_year" in numeric_q7:
    year_col = "l_year"
else:
    # Q7 uses EXTRACT(year FROM ...) AS l_year
    year_col = None
    for c in exact_q7.columns:
        if "year" in c.lower():
            year_col = c
            break

if year_col and year_col in numeric_q7:
    print(f"\n  *** Q7 ROOT CAUSE: {year_col} (dtype={exact_q7[year_col].dtype}) treated as value ***")

conn.close()

print("\n" + "=" * 72)
print("CONCLUSION")
print("=" * 72)
print("""
If STEP 3 shows non-zero error on identical DataFrames,
the "scaling bug" is entirely a measurement/alignment issue
in compute_detailed_group_errors, NOT an estimator bug.

Fix: Override auto-detection for columns whose semantics are
"group key" even though their dtype is numeric (e.g. o_year, l_year).

Recommended approach: Parse SQL GROUP BY clause with sqlglot
to extract true key columns, rather than relying on dtype heuristics.
""")

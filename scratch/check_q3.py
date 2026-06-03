import pandas as pd
import glob
import os
from pilotdb.benchmarks.tpch_shared import load_query_sql, exact_run

db_path = "bench_out_sf10_v2/tpch_sf10.duckdb"
sql = load_query_sql("q3")
exact_df = exact_run(sql, "duckdb", path=db_path)

pattern = "results/tpch-q3-aqp-duckdb-*.csv"
aqp_files = sorted(glob.glob(pattern))
aqp_df = pd.read_csv(aqp_files[-1])

print("Exact columns:", exact_df.columns.tolist())
print(exact_df.dtypes)
print("\nAQP columns:", aqp_df.columns.tolist())
print(aqp_df.dtypes)

numeric_cols = [c for c in exact_df.columns if pd.api.types.is_numeric_dtype(exact_df[c])]
key_cols = [c for c in exact_df.columns if c not in numeric_cols]
print("\nIdentified key_cols:", key_cols)
print("Identified numeric_cols:", numeric_cols)

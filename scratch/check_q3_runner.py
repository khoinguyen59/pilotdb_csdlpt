import pandas as pd
from pilotdb.benchmarks.tpch_shared import load_query_sql, exact_run
from pilotdb.execute import execute_aqp
from pilotdb.benchmarks.run_duckdb_tpch import compute_detailed_group_errors, build_query_obj

db_path = "bench_out_sf10_v2/tpch_sf10.duckdb"
sql = load_query_sql("q3")
exact_df = exact_run(sql, "duckdb", path=db_path)

q = build_query_obj("q3", sql, error=0.05, failure_probability=0.05)
# Set table sizes for SF=10
scaled_sizes = {}
for name, size in q.table_size.items():
    if name.lower() in ("nation", "region"):
        scaled_sizes[name] = size
    else:
        scaled_sizes[name] = size * 10
q.table_size = scaled_sizes

db_config = {"dbms": "duckdb", "path": db_path}
aqp_df, timing = execute_aqp(q, db_config, pilot_sample_rate=1.0)

print("Exact columns & types:")
print(exact_df.dtypes)
print("AQP columns & types:")
print(aqp_df.dtypes)

mean_err, max_err, missing_cnt = compute_detailed_group_errors(exact_df, aqp_df, "q3")
print(f"Errors: mean={mean_err}, max={max_err}, missing={missing_cnt}")

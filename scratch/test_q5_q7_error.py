import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

from pilotdb.benchmarks.tpch_shared import build_query_obj, load_query_sql, exact_run
from pilotdb.execute import execute_aqp
from pilotdb.benchmarks.run_duckdb_tpch import summarize_error, compute_detailed_group_errors

db_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
db_config = {"dbms": "duckdb", "path": db_path}

for qid in ["q5", "q7"]:
    print(f"\n================ EVALUATING {qid.upper()} ================")
    sql = load_query_sql(qid)
    q = build_query_obj(qid, sql)
    
    # Scale table sizes to SF=10
    scaled_sizes = {}
    for name, size in q.table_size.items():
        if name.lower() in ("nation", "region"):
            scaled_sizes[name] = size
        else:
            scaled_sizes[name] = size * 10
    q.table_size = scaled_sizes
    
    exact_df = exact_run(sql, "duckdb", path=db_path)
    aqp_df, timing = execute_aqp(q, db_config, pilot_sample_rate=1.0)
    
    print(f"Timing details:")
    for k, v in timing.items():
        print(f"  {k}: {v}")
    
    rel_err = summarize_error(exact_df, aqp_df, qid)
    mean_err, max_err, missing_cnt = compute_detailed_group_errors(exact_df, aqp_df, qid)
    print(f"Overall error: {rel_err}")
    print(f"Row mean error: {mean_err}")
    print(f"Row max error: {max_err}")
    print(f"Missing groups: {missing_cnt}")

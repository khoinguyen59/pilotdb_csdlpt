import os
import sys
sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

import json
import logging
import traceback
import math
import pandas as pd
from pilotdb.query import Query
from pilotdb.execute import _execute_aqp_internal, connect_to_db, query_table_sizes, lookup_block_sizes, directly_run_exact, process_subqueries, execute_query, aggregate_error_to_page_error
from pilotdb.pilot_engine.multi_table_sampling import apply_sampling_plan_template
from pilotdb.pilot_engine.sampling_plan import scalar_rate_plan
from pilotdb.pilot_engine.error_bounds import get_bernoulli_N_sample_rate, get_mean_sample_size, get_sample_rate, optimize_sampling_plan

logging.basicConfig(level=logging.WARNING)

db_config = {
    "dbms": "duckdb",
    "path": r"C:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
}

meta_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\meta.json"
with open(meta_path, "r", encoding="utf-8") as f:
    meta = json.load(f)

queries = ["q1", "q3", "q5", "q6", "q7", "q8", "q9", "q10", "q12", "q14", "q18", "q19"]

for qname in queries:
    q_path = f"c:\\Users\\Nguyen Trong Khoi\\Downloads\\CSDLPT_DA\\pilotdb_csdlpt\\benchmarks\\tpch\\query_{qname[1:]}.sql"
    with open(q_path, "r", encoding="utf-8") as f:
        sql = f.read()

    q = Query(
        query=sql,
        table_cols=meta["table_cols"],
        table_size=meta["table_size"],
        error=0.10,
        failure_probability=0.05,
        name=qname
    )

    print(f"\n==================== DIAGNOSING {qname.upper()} ====================")
    try:
        dbms = db_config["dbms"]
        if isinstance(q.table_size, list):
            q.table_size = query_table_sizes(dbms, db_config, q.table_size)
        conn = connect_to_db(dbms, db_config)

        from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
        pq = Pilot_Rewriter(q.table_cols, q.table_size, dbms)
        pilot_query = pq.rewrite(q.query) + ";"

        is_direct = directly_run_exact(conn, q.query, pilot_query, dbms, pq.largest_table)
        if is_direct:
            print("Direct run exact: True")
            continue

        subquery_results = process_subqueries(dbms, conn, pq)
        for name, res in subquery_results.items():
            pilot_query = pilot_query.replace(name, res)

        pilot_query = apply_sampling_plan_template(
            pilot_query, scalar_rate_plan(pq.largest_table, 0.01), dbms
        )

        pilot_results = execute_query(conn, pilot_query, dbms)
        page_errors = aggregate_error_to_page_error(pq.result_mapping_list, required_error=q.error)

        # Run rate estimation manually to catch exception
        page_stats_cols = [col for col in page_errors.keys() if col != "n_page"]
        n_page_stats = len(page_stats_cols)
        page_size_stats = len(page_errors) - n_page_stats
        
        keep_columns = pq.group_cols + page_stats_cols
        pilot_df = pilot_results[keep_columns]
        
        if len(pq.group_cols) > 0:
            if pq.limit_value is not None:
                df = pilot_df.groupby(by=pq.group_cols, sort=False).agg(["mean", "std", "size"]).head(pq.limit_value)
            else:
                df = pilot_df.groupby(by=pq.group_cols, sort=False).agg(["mean", "std", "size"])
        else:
            df = pilot_df.agg(["mean", "std", "size"])
        
        n_groups = df.shape[0] if len(pq.group_cols) > 0 else 1
        n_est = n_groups * (n_page_stats * 3 + page_size_stats * 2 + 1)
        fp = q.failure_probability / n_est
        pilot_rate = 0.01

        from pilotdb.pilot_engine.error_bounds import estimate_final_rate
        rate = estimate_final_rate(
            failure_prob=q.failure_probability,
            pilot_results=pilot_results,
            group_cols=pq.group_cols,
            page_errors=page_errors,
            pilot_rate=0.01
        )
        print(f"Success! Estimated final rate: {rate}")

    except Exception as e:
        print(f"{qname.upper()} overall failed.")


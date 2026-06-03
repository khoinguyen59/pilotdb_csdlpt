import os
import sys
import json
import logging
import pandas as pd
import numpy as np

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

from pilotdb.query import Query
from pilotdb.execute import (
    _execute_aqp_internal,
    connect_to_db,
    query_table_sizes,
    lookup_block_sizes,
    directly_run_exact,
    process_subqueries,
    execute_query,
    aggregate_error_to_page_error,
    estimate_final_rate
)
from pilotdb.pilot_engine.multi_table_sampling import apply_sampling_plan_template
from pilotdb.pilot_engine.sampling_plan import scalar_rate_plan

db_config = {
    "dbms": "duckdb",
    "path": r"C:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
}

meta_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\meta.json"
with open(meta_path, "r", encoding="utf-8") as f:
    meta = json.load(f)

queries = ["q1", "q3", "q5", "q6", "q7", "q8", "q9", "q10", "q12", "q14", "q18", "q19"]
pilot_rates = [0.01, 0.02, 0.05, 0.10]

conn = connect_to_db(db_config["dbms"], db_config)

results = []

for qname in queries:
    q_path = f"c:\\Users\\Nguyen Trong Khoi\\Downloads\\CSDLPT_DA\\pilotdb_csdlpt\\benchmarks\\tpch\\query_{qname[1:]}.sql"
    with open(q_path, "r", encoding="utf-8") as f:
        sql = f.read()

    q = Query(
        query=sql,
        table_cols=meta["table_cols"],
        table_size=meta["table_size"],
        error=0.05,  # We can check with standard 0.05 error
        failure_probability=0.05,
        name=qname
    )
    if isinstance(q.table_size, list):
        q.table_size = query_table_sizes(db_config["dbms"], db_config, q.table_size)

    from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
    pq = Pilot_Rewriter(q.table_cols, q.table_size, db_config["dbms"])
    pilot_query_base = pq.rewrite(q.query) + ";"

    # Execute subqueries if any
    subquery_results = process_subqueries(db_config["dbms"], conn, pq)
    for name, res in subquery_results.items():
        pilot_query_base = pilot_query_base.replace(name, res)

    print(f"\n--- Testing Query {qname.upper()} ---")
    
    for pr in pilot_rates:
        try:
            # Apply sampling template for the pilot query
            pilot_plan = scalar_rate_plan(pq.largest_table, pr)
            pilot_query = apply_sampling_plan_template(pilot_query_base, pilot_plan, db_config["dbms"])
            
            pilot_results = execute_query(conn, pilot_query, db_config["dbms"])
            page_errors = aggregate_error_to_page_error(pq.result_mapping_list, required_error=q.error)
            
            final_rate = estimate_final_rate(
                failure_prob=q.failure_probability,
                pilot_results=pilot_results,
                page_errors=page_errors,
                group_cols=pq.group_cols,
                pilot_rate=pr,
                limit=pq.limit_value
            )
            print(f"  Pilot rate {pr:.2f} -> Final rate: {final_rate}")
            results.append({
                "query": qname,
                "pilot_rate": pr,
                "final_rate": final_rate,
                "status": "success" if final_rate != -1 else "failed"
            })
        except Exception as e:
            print(f"  Pilot rate {pr:.2f} -> Exception: {type(e).__name__}: {e}")
            results.append({
                "query": qname,
                "pilot_rate": pr,
                "final_rate": -1,
                "status": f"exception: {type(e).__name__}"
            })

conn.close()

# Print summary table
df = pd.DataFrame(results)
print("\n=== SUMMARY TABLE ===")
print(df.to_string(index=False))

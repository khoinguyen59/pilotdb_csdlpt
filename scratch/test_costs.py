import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb
from pilotdb.query import Query
from pilotdb.benchmarks.tpch_shared import load_query_sql, build_query_obj
from pilotdb.execute import execute_aqp
from pilotdb.db_driver.driver import connect_to_db, estimate_cost
from pilotdb.pilot_engine.sampling_plan import SamplingPlan

db_path = "bench_out_sf10/tpch_sf10.duckdb"
if not os.path.exists(db_path):
    print(f"Database {db_path} not found. Please run generate_tpch.py first.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)

db_config = {"dbms": "duckdb", "path": db_path}
conn = connect_to_db("duckdb", db_config)

for qid in ["q1", "q3", "q5", "q6", "q7", "q8", "q9", "q10", "q12", "q14", "q18", "q19"]:
    sql = load_query_sql(qid)
    if sql is None:
        continue
    print(f"\n--- Analyzing {qid} ---")
    q = build_query_obj(qid, sql)
    
    # We will simulate the cost estimation logic
    exact_cost = estimate_cost(conn, q.query, "duckdb", table_size=q.table_size)
    print(f"Exact Cost (scanned volume): {exact_cost}")
    
    # Run execute_aqp with a mock or trace to see what is evaluated
    try:
        df, timing = execute_aqp(q, db_config, pilot_sample_rate=1.0)
        print(f"AQP complete. final_sample_rate={timing.get('final_sample_rate')}, fallback_reason={timing.get('fallback_reason')}")
    except Exception as e:
        print(f"execute_aqp failed with: {e}")

import pandas as pd
import glob
import os
from pilotdb.benchmarks.tpch_shared import load_query_sql, exact_run

db_path = "bench_out_sf10_v2/tpch_sf10.duckdb"

def compare_query(qid):
    print(f"\n=================== COMPARISON FOR {qid.upper()} ===================")
    sql = load_query_sql(qid)
    
    # Run exact query
    exact_df = exact_run(sql, "duckdb", path=db_path)
    
    # Find latest AQP file in results/
    pattern = f"results/tpch-{qid}-aqp-duckdb-*.csv"
    aqp_files = sorted(glob.glob(pattern))
    if not aqp_files:
        print(f"No AQP file found for {qid}")
        return
        
    aqp_file = aqp_files[-1]
    print(f"Loading AQP results from: {aqp_file}")
    aqp_df = pd.read_csv(aqp_file)
    
    print("\n--- EXACT DF ---")
    print(exact_df)
    
    print("\n--- AQP DF ---")
    print(aqp_df)

compare_query("q7")
compare_query("q9")

import os
import sys
import json
import logging

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

from pilotdb.query import Query
from pilotdb.execute import _execute_aqp_internal, connect_to_db, query_table_sizes

logging.basicConfig(level=logging.INFO)

db_config = {
    "dbms": "duckdb",
    "path": r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
}

# Ensure database exists
import duckdb
db_dir = os.path.dirname(db_config["path"])
os.makedirs(db_dir, exist_ok=True)
if not os.path.exists(db_config["path"]):
    print("Generating TPC-H SF=10 DuckDB database...")
    conn = duckdb.connect(database=db_config["path"], read_only=False)
    conn.execute("INSTALL tpch; LOAD tpch; CALL dbgen(sf=10);")
    conn.close()
    print("TPC-H SF=10 database generated successfully.")

meta_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\meta.json"
with open(meta_path, "r", encoding="utf-8") as f:
    meta = json.load(f)

qname = "q3"
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

print(f"\n==================== RUNNING AQP ON SF=10 FOR {qname.upper()} ====================")
dbms = db_config["dbms"]
if isinstance(q.table_size, list):
    q.table_size = query_table_sizes(dbms, db_config, q.table_size)

results_df, timing_out = _execute_aqp_internal(q, db_config, pilot_sample_rate=1.0)
print(f"AQP Execution Finished!")
print(f"final_sample_rate={timing_out['final_sample_rate']}")
print(f"fallback_reason={timing_out['fallback_reason']}")
print(f"Result shape: {results_df.shape}")

import sys
import os
import json
import logging

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

from pilotdb.query import Query
from pilotdb.execute import execute_aqp
from pilotdb.benchmarks.tpch_shared import build_query_obj, load_query_sql

logging.basicConfig(level=logging.INFO)

db_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
db_config = {"dbms": "duckdb", "path": db_path}

for qid in ["q5", "q7"]:
    print(f"\n================ DIAGNOSING {qid.upper()} ================")
    sql = load_query_sql(qid)
    q = build_query_obj(qid, sql)
    df, timing = execute_aqp(q, db_config, pilot_sample_rate=1.0)
    print(f"Timing of {qid}:")
    print(json.dumps(timing, indent=2))

import os
import sys
sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

import json
import pandas as pd
from pilotdb.query import Query
from pilotdb.execute import connect_to_db, query_table_sizes, process_subqueries, execute_query, aggregate_error_to_page_error
from pilotdb.pilot_engine.multi_table_sampling import apply_sampling_plan_template
from pilotdb.pilot_engine.sampling_plan import scalar_rate_plan

db_config = {
    "dbms": "duckdb",
    "path": r"C:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out\tpch_sf1.duckdb"
}

meta_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\meta.json"
with open(meta_path, "r", encoding="utf-8") as f:
    meta = json.load(f)

qname = "q5"
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

dbms = db_config["dbms"]
if isinstance(q.table_size, list):
    q.table_size = query_table_sizes(dbms, db_config, q.table_size)
conn = connect_to_db(dbms, db_config)

from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
pq = Pilot_Rewriter(q.table_cols, q.table_size, dbms)
pilot_query = pq.rewrite(q.query) + ";"

subquery_results = process_subqueries(dbms, conn, pq)
for name, res in subquery_results.items():
    pilot_query = pilot_query.replace(name, res)

pilot_query = apply_sampling_plan_template(
    pilot_query, scalar_rate_plan(pq.largest_table, 0.01), dbms
)

pilot_results = execute_query(conn, pilot_query, dbms)
page_errors = aggregate_error_to_page_error(pq.result_mapping_list, required_error=q.error)

page_stats_cols = [col for col in page_errors.keys() if col != "n_page"]
keep_columns = pq.group_cols + page_stats_cols
pilot_df = pilot_results[keep_columns]

df = pilot_df.groupby(by=pq.group_cols, sort=False).agg(["mean", "std", "size"])
print("Grouped DF:")
print(df)

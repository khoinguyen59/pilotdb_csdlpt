from pilotdb.benchmarks.tpch_shared import load_query_sql, exact_run, available_qids

db_path = "bench_out_sf10_v2/tpch_sf10.duckdb"

for qid in available_qids():
    sql = load_query_sql(qid)
    if sql is None:
        continue
    try:
        df = exact_run(sql, "duckdb", path=db_path)
        print(f"{qid}: cols={df.columns.tolist()}")
    except Exception as e:
        print(f"{qid}: error={e}")

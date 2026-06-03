import os
import json
import argparse
import yaml
import time
from typing import List

from pilotdb.db_driver.driver import connect_to_db, close_connection, execute_query

def run(queries: List[str], config: dict, dbms: str):
    conn = connect_to_db(dbms, config)
    for q in queries:
        t0 = time.perf_counter()
        try:
            df = execute_query(conn, q, dbms)
            duration = time.perf_counter() - t0
            print(f"Executed query successfully. Rows returned: {len(df)}. Time: {duration:.3f}s")
        except Exception as e:
            print(f"Error executing query: {e}")
    close_connection(conn, dbms)

def get_actual_table_size(config: dict, dbms: str, query_id: str) -> int:
    table_name = "orders" if query_id == "7" else "lineitem"
    try:
        conn = connect_to_db(dbms, config)
        df = execute_query(conn, f"SELECT COUNT(*) FROM {table_name}", dbms)
        close_connection(conn, dbms)
        size = int(df.iloc[0, 0])
        print(f"Detected actual table size for '{table_name}': {size}")
        return size
    except Exception as e:
        print(f"Failed to query table size dynamically for {table_name}: {e}. Using fallback.")
        # Fallback to SF1 defaults if query fails
        return 1500000 if table_name == "orders" else 6001215

def run_order_by_random_rows(query_id: str, config: dict, dbms: str):
    with open(f"experiments/fixed_size/tpch_postgres_order_by_random_rows/query_{query_id}.sql") as f:
        query_template = f.read()
    with open("experiments/fixed_size/tpch_postgres_order_by_random_rows/meta.json") as f:
        meta = json.load(f)
        
    table_size = get_actual_table_size(config, dbms, query_id)
    sample_rate = meta["sample_rates"][query_id]
    sample_size = int(table_size * sample_rate / 100)
    pilot_sample_size = int(table_size * 0.05 / 100)
    
    pilot_query = query_template.format(sample_size=pilot_sample_size)
    final_query = query_template.format(sample_size=sample_size)
    queries = [pilot_query, final_query]
    run(queries, config, dbms)

def run_tsm_system_rows(query_id: str, config: dict, dbms: str):
    with open(f"experiments/fixed_size/tpch_postgres_tsm_system_rows/query_{query_id}.sql") as f:
        query_template = f.read()
    with open("experiments/fixed_size/tpch_postgres_tsm_system_rows/meta.json") as f:
        meta = json.load(f)
        
    table_size = get_actual_table_size(config, dbms, query_id)
    sample_rate = meta["sample_rates"][query_id]
    
    if dbms == "duckdb":
        # Format templates directly using TABLESAMPLE system(rate%) or bernoulli(rate%)
        pilot_query = query_template.replace("TABLESAMPLE SYSTEM_ROWS({sample_size})", "TABLESAMPLE system(0.05%)")
        final_query = query_template.replace("TABLESAMPLE SYSTEM_ROWS({sample_size})", f"TABLESAMPLE system({sample_rate}%)")
        queries = [pilot_query, final_query]
    else:
        sample_size = int(table_size * sample_rate / 100)
        pilot_sample_size = int(table_size * 0.05 / 100)
        pilot_query = query_template.format(sample_size=pilot_sample_size)
        final_query = query_template.format(sample_size=sample_size)
        init_query = "CREATE EXTENSION IF NOT EXISTS tsm_system_rows;"
        queries = [init_query, pilot_query, final_query]
        
    run(queries, config, dbms)
    
def run_exact(query_id: str, config: dict, dbms: str):
    with open(f"experiments/fixed_size/tpch_postgres/query_{query_id}.sql") as f:
        query = f.read()
    queries = [query]
    run(queries, config, dbms)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--qid", type=str, default="1")
    parser.add_argument("--process_mode", type=str, default="exact")
    parser.add_argument("--dbms", type=str, default="postgres", choices=("postgres", "duckdb"))
    parser.add_argument("--db_config_file", type=str, default="db_configs/postgres_tpch.yml")
    args = parser.parse_args()
    
    with open(args.db_config_file) as f:
        config = yaml.safe_load(f)
    
    start = time.time()
    if args.process_mode == "order_by_random_rows":
        run_order_by_random_rows(args.qid, config, args.dbms)
    elif args.process_mode == "tsm_system_rows":
        run_tsm_system_rows(args.qid, config, args.dbms)
    elif args.process_mode == "exact":
        run_exact(args.qid, config, args.dbms)
    else:
        raise ValueError(f"{args.process_mode} is not in [order_by_random_rows, tsm_system_rows, exact]")
    end = time.time()
    cost = end - start
    
    with open("all_results.jsonl", "a+") as f:
        result = {
            "qid": args.qid,
            "process_mode": args.process_mode,
            "dbms": args.dbms,
            "cost": cost
        }
        f.write(json.dumps(result) + "\n")
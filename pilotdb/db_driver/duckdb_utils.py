import os
import duckdb
import pandas as pd


def connect_to_db(path: str):
    conn = duckdb.connect(database=path, read_only=False)
    seed = os.environ.get("PILOTDB_SEED")
    if seed:
        conn.execute("SET threads = 1;")
        try:
            val = float(seed)
            double_seed = (abs(int(val)) % 1000000) / 1000000.0
            conn.execute(f"SELECT setseed({double_seed});")
        except ValueError:
            pass
    return conn


def close_connection(conn):
    conn.close()


def execute_query(conn, query: str) -> pd.DataFrame:
    return conn.execute(query).fetchdf()


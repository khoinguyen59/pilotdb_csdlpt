import pandas as pd
import pandas.io.sql as sqlio
import psycopg2


def connect_to_db(
    db: str,
    user: str,
    host: str = "localhost",
    port: int = 5432,
    password: str | None = None,
):
    conn = psycopg2.connect(
        dbname=db, user=user, host=host, port=port, password=password
    )
    return conn


def close_connection(conn):
    conn.close()


def execute_query(conn, query: str) -> pd.DataFrame:
    return sqlio.read_sql_query(query, conn)


def estimate_cost(conn, query: str) -> float:
    cur = conn.cursor()
    cur.execute(f"EXPLAIN (FORMAT JSON) {query}")
    return float(cur.fetchone()[0][0]["Plan"]["Total Cost"])


def is_high_estimated_cost(conn, query: str, pilot_query: str):
    estimated_exact_cost = estimate_cost(conn, query)
    estimated_aqp_cost = estimate_cost(conn, pilot_query)
    print(estimated_aqp_cost / estimated_exact_cost)
    if estimated_aqp_cost / estimated_exact_cost > 0.05:
        return True
    else:
        return False

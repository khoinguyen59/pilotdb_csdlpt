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


def _fix_postgres_syntax(query: str) -> str:
    import re
    pattern = r"\b(INTERVAL) '(\d+)' (DAYS)\b"
    query = re.sub(pattern, r"\1 '\2 \3'", query)
    query = re.sub(r'\bDOUBLE\b(?!\s+PRECISION)', 'DOUBLE PRECISION', query)
    return query


def execute_query(conn, query: str) -> pd.DataFrame:
    query = _fix_postgres_syntax(query)
    return sqlio.read_sql_query(query, conn)


def estimate_cost(conn, query: str) -> float:
    query = _fix_postgres_syntax(query)
    cur = conn.cursor()
    cur.execute(f"EXPLAIN (FORMAT JSON) {query}")
    return float(cur.fetchone()[0][0]["Plan"]["Total Cost"])



def is_high_estimated_cost(conn, query: str, pilot_query: str):
    import re
    import logging
    import os
    # Replace subquery placeholders like subquery_0 with a dummy value (1) for syntax-valid EXPLAIN
    cleaned_pilot_query = re.sub(r"\bsubquery_\d+\b", "(1)", pilot_query)
    
    try:
        estimated_exact_cost = estimate_cost(conn, query)
        estimated_aqp_cost = estimate_cost(conn, cleaned_pilot_query)
    except Exception as e:
        logging.warning("[is_high_estimated_cost] cost estimation failed: %r. Returning False to give AQP a chance.", e)
        return False
        
    if estimated_exact_cost == 0:
        return False
        
    cost_ratio = estimated_aqp_cost / estimated_exact_cost
    threshold = float(os.environ.get("PILOTDB_POSTGRES_COST_THRESHOLD", 0.05))
    if cost_ratio > threshold:
        return True
    else:
        return False


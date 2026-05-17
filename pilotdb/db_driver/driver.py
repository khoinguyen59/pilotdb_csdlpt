import os

import pandas as pd

import pilotdb.db_driver.duckdb_utils as duckdb_utils
import pilotdb.db_driver.postgres_utils as postgres_utils
import pilotdb.db_driver.sqlserver_utils as sqlserver_utils
from pilotdb.db_driver.cost import estimate_query_cost, should_run_exact


def connect_to_db(dbms: str, config: dict):
    if "flush_memory" not in config:
        config["flush_memory"] = False
    if dbms == "duckdb":
        return duckdb_utils.connect_to_db(config["path"])
    elif dbms == "postgres":
        return postgres_utils.connect_to_db(
            db=config["dbname"],
            user=config["username"],
            host=config.get("host", "localhost"),
            port=int(config.get("port", 5432)),
            password=config.get("password") or None,
        )
    elif dbms == "sqlserver":
        return sqlserver_utils.connect_to_db(
            db=config.get("dbname"),
            user=config.get("username"),
            host=config.get("host", "127.0.0.1"),
            password=config.get("password"),
            trusted_connection=bool(config.get("trusted_connection", False)),
            driver=config.get("driver", "ODBC Driver 18 for SQL Server"),
            flush_memory=bool(config.get("flush_memory", False)),
        )
    else:
        raise ValueError(f"Unknown DBMS: {dbms}")


def close_connection(conn, dbms: str):
    if dbms == "duckdb":
        return duckdb_utils.close_connection(conn)
    elif dbms == "postgres":
        return postgres_utils.close_connection(conn)
    elif dbms == "sqlserver":
        return sqlserver_utils.close_connection(conn)
    else:
        raise ValueError(f"Unknown DBMS: {dbms}")


def execute_query(conn, query: str, dbms: str) -> pd.DataFrame:
    if dbms == "duckdb":
        return duckdb_utils.execute_query(conn, query)
    elif dbms == "postgres":
        return postgres_utils.execute_query(conn, query)
    elif dbms == "sqlserver":
        return sqlserver_utils.execute_query(conn, query)
    else:
        raise ValueError(f"Unknown DBMS: {dbms}")


def get_sampling_clause(rate: float, dbms: str) -> str | None:
    if dbms == "duckdb":
        return f"TABLESAMPLE SYSTEM({rate}%)"
    elif dbms == "postgres":
        return f"TABLESAMPLE SYSTEM ({rate})"
    elif dbms == "sqlserver":
        return f"TABLESAMPLE ({rate} PERCENT)"
    else:
        ValueError(f"Unknown DBMS: {dbms}")


def get_uniform_sampling_clause(rate: float, dbms: str) -> str | None:
    if dbms == "duckdb":
        return f"TABLESAMPLE bernoulli({rate}%)"
    elif dbms == "postgres":
        return f"TABLESAMPLE BERNOULLI ({rate})"
    elif dbms == "sqlserver":
        return f"{rate / 100}"
    else:
        ValueError(f"Unknown DBMS: {dbms}")


def estimate_cost(conn, query: str, dbms: str, table_size=None, sampling_plan=None) -> float:
    return estimate_query_cost(
        conn, query, dbms, table_size=table_size, sampling_plan=sampling_plan
    )


def directly_run_exact(
    conn, query: str, pilot_query: str, dbms: str, largest_table: str
):
    if dbms == "sqlserver":
        return sqlserver_utils.is_index_seek(conn, query, largest_table)
    elif dbms == "postgres":
        return postgres_utils.is_high_estimated_cost(conn, query, pilot_query)

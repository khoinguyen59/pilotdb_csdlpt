import os
import xml.etree.ElementTree as ET
from xml.dom import minidom

import pandas as pd
import pandas.io.sql as sqlio
import pyodbc


def connect_to_db(
    db: str,
    user: str | None,
    host: str = "127.0.0.1",
    password: str | None = None,
    trusted_connection: bool = False,
    driver: str = "ODBC Driver 18 for SQL Server",
    flush_memory: bool = True,
):
    """Connect to SQL Server via pyodbc.

    Supports either SQL Authentication (UID/PWD) or Windows Trusted
    Connection. Passing `trusted_connection=True` ignores `user`/`password`.

    `flush_memory=False` skips the DBCC cache flushes — useful when the
    connecting principal lacks sysadmin (e.g. a constrained loader login).
    """
    parts = [f"DRIVER={{{driver}}}", f"SERVER={host}"]
    if db:
        parts.append(f"DATABASE={db}")
    if trusted_connection:
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={user}")
        parts.append(f"PWD={password if password is not None else ''}")
    parts.append("TrustServerCertificate=yes")
    conn_string = ";".join(parts) + ";"
    conn = pyodbc.connect(conn_string)
    if flush_memory:
        try:
            conn.execute("DBCC FREEPROCCACHE;")
            conn.execute("DBCC DROPCLEANBUFFERS;")
        except pyodbc.Error:
            # Insufficient privilege — proceed without flush.
            pass
    return conn


def close_connection(conn):
    conn.close()


def execute_query(conn, query: str) -> pd.DataFrame:
    return sqlio.read_sql_query(query, conn)


def is_index_seek(conn, query: str, largest_table: str):
    with conn.cursor() as cursor:
        # Enable showplan XML to get the query plan without executing the query
        cursor.execute("SET SHOWPLAN_XML ON;")
        cursor.commit()

        # Execute the query for which we want the plan
        cursor.execute(query)

        # Fetch the plan
        plan = cursor.fetchone()

        # Disable showplan XML to return to normal operations
        cursor.execute("SET SHOWPLAN_XML OFF;")
        cursor.commit()

        root = ET.fromstring(plan[0])
        namespaces = {"sql": "http://schemas.microsoft.com/sqlserver/2004/07/showplan"}

        # Find all elements where PhysicalOp is 'Clustered Index Seek'
        clustered_index_seeks = root.findall(
            ".//sql:RelOp[@PhysicalOp='Clustered Index Seek']", namespaces
        )
        for node in clustered_index_seeks:
            object_details = node.find(".//sql:Object", namespaces)
            if object_details is not None:
                if largest_table in object_details.get("Table"):
                    return True

        return False


def estimate_cost(conn, query: str) -> float:
    with conn.cursor() as cursor:
        cursor.execute("SET SHOWPLAN_XML ON;")
        cursor.commit()
        try:
            cursor.execute(query)
            plan = cursor.fetchone()
        finally:
            cursor.execute("SET SHOWPLAN_XML OFF;")
            cursor.commit()

    root = ET.fromstring(plan[0])
    namespaces = {"sql": "http://schemas.microsoft.com/sqlserver/2004/07/showplan"}
    costs = []
    for node in root.findall(".//sql:RelOp", namespaces):
        cost = node.get("EstimatedTotalSubtreeCost")
        if cost is not None:
            costs.append(float(cost))
    if not costs:
        raise ValueError("SQL Server showplan did not expose estimated costs")
    return max(costs)

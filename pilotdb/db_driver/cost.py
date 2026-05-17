"""DBMS cost-estimation helpers for sampling-plan optimization.

The PilotDB paper ?3.2 chooses the cheapest feasible sampling plan using the
underlying DBMS cost model. These helpers expose numeric cost estimates instead
of boolean heuristics so the optimizer can compare plans transparently.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Mapping

from pilotdb.pilot_engine.commons import DUCKDB, POSTGRES, SQLSERVER
from pilotdb.pilot_engine.sampling_plan import SamplingPlan


def estimate_postgres_cost(conn, query: str) -> float:
    cur = conn.cursor()
    cur.execute(f"EXPLAIN (FORMAT JSON) {query}")
    return float(cur.fetchone()[0][0]["Plan"]["Total Cost"])


def estimate_sqlserver_cost(conn, query: str) -> float:
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


def estimate_duckdb_scanned_volume(
    table_size: Mapping[str, int] | None, sampling_plan: SamplingPlan | None = None
) -> float:
    if not table_size:
        return 0.0
    if sampling_plan is None:
        return float(sum(table_size.values()))
    return float(
        sum(size * sampling_plan.rate_for(table, 1.0) for table, size in table_size.items())
    )


def estimate_query_cost(
    conn,
    query: str,
    dbms: str,
    table_size: Mapping[str, int] | None = None,
    sampling_plan: SamplingPlan | None = None,
) -> float:
    if dbms == POSTGRES:
        return estimate_postgres_cost(conn, query)
    if dbms == SQLSERVER:
        return estimate_sqlserver_cost(conn, query)
    if dbms == DUCKDB:
        return estimate_duckdb_scanned_volume(table_size, sampling_plan)
    raise ValueError(f"Unknown DBMS: {dbms}")


def should_run_exact(exact_cost: float, approximate_cost: float) -> bool:
    """Paper ?3.2 rejects approximate plans that cost more than exact plans."""
    return approximate_cost >= exact_cost

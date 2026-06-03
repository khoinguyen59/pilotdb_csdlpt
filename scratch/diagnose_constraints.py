import os
import sys
import json
import logging
import pandas as pd
import numpy as np

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

from pilotdb.query import Query
from pilotdb.execute import (
    _extract_pilot_stats,
    _extract_join_block_stats,
    lookup_block_sizes,
    connect_to_db,
    query_table_sizes,
    aggregate_error_to_page_error,
    Pilot_Rewriter,
    SamplingPlan,
    execute_query,
    apply_sampling_plan_template
)
from pilotdb.pilot_engine.join_variance import build_phi_constraints, phi_constraint_value

logging.basicConfig(level=logging.INFO)

db_config = {
    "dbms": "duckdb",
    "path": r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
}

meta_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\meta.json"
with open(meta_path, "r", encoding="utf-8") as f:
    meta = json.load(f)

qname = "q3"
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

# Run pilot and extract stats
conn = connect_to_db(dbms, db_config)
pq = Pilot_Rewriter(q.table_cols, q.table_size, dbms)
pilot_query = pq.rewrite(q.query) + ";"

is_join_query = getattr(pq, 'page_id_count', 0) >= 2
block_sizes = lookup_block_sizes(conn, dbms, list(q.table_size.keys()))

pilot_sample_rate = 1.0
K = len(pq.sampled_tables)
effective_pilot_rate = (pilot_sample_rate / 100) ** (1.0 / K) if K > 1 else (pilot_sample_rate / 100)
rates = {t_name: effective_pilot_rate for (t_name, _, _) in pq.sampled_tables}
pilot_plan = SamplingPlan(rates=rates)
pilot_query = apply_sampling_plan_template(pilot_query, pilot_plan, dbms)

pilot_results = execute_query(conn, pilot_query, dbms)
page_errors = aggregate_error_to_page_error(pq.result_mapping_list, required_error=q.error)

join_block_stats = _extract_join_block_stats(
    pilot_results, page_errors,
    page_id_count=getattr(pq, 'page_id_count', 0),
    table_sizes=q.table_size,
    block_sizes=block_sizes,
)

pilot_stats = _extract_pilot_stats(
    pilot_results, page_errors, pq.group_cols, pq.limit_value,
    join_block_stats=join_block_stats,
)

n_page_stats = len([c for c in page_errors.keys() if c != "n_page"])
n_groups = len(set(
    tuple(pilot_results[pq.group_cols].iloc[i])
    for i in range(len(pilot_results))
)) if pq.group_cols else 1

phi_constraints = build_phi_constraints(
    failure_prob=q.failure_probability,
    n_aggregates=n_page_stats,
    n_groups=n_groups,
    pilot_stats=pilot_stats,
    required_error=q.error,
    table_names=tuple(pq.sampled_tables),
)

print(f"\nAnalyzing {len(phi_constraints.constraints)} constraints:")
for idx, c in enumerate(phi_constraints.constraints):
    print(f"\nConstraint {idx}:")
    print(f"  Aggregate/Group Index: agg={c.aggregate_index}, grp={c.group_index}")
    print(f"  sample_mean: {pilot_stats[idx]['sample_mean']}")
    print(f"  sample_std: {pilot_stats[idx]['sample_std']}")
    print(f"  sample_size: {pilot_stats[idx]['sample_size']}")
    print(f"  L_mu: {c.L_mu}")
    print(f"  z_value: {c.z_value}")
    print(f"  pilot_sample_std (upper bound): {c.pilot_sample_std}")

conn.close()

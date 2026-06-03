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
    apply_sampling_plan_template,
    estimate_cost
)
from pilotdb.pilot_engine.join_variance import build_phi_constraints, phi_constraint_value, phi_constraint_residual
from pilotdb.pilot_engine.optimizer import solve_trust_region_plan

logging.basicConfig(level=logging.INFO)

db_config = {
    "dbms": "duckdb",
    "path": r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
}

meta_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\meta.json"
with open(meta_path, "r", encoding="utf-8") as f:
    meta = json.load(f)

qname = "q9"
q_path = f"c:\\Users\\Nguyen Trong Khoi\\Downloads\\CSDLPT_DA\\pilotdb_csdlpt\\benchmarks\\tpch\\query_{qname[1:]}.sql"
with open(q_path, "r", encoding="utf-8") as f:
    sql = f.read()

q = Query(
    query=sql,
    table_cols=meta["table_cols"],
    table_size=meta["table_size"],
    error=0.05,
    failure_probability=0.05,
    name=qname
)

dbms = db_config["dbms"]
if isinstance(q.table_size, list):
    q.table_size = query_table_sizes(dbms, db_config, q.table_size)

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

print("Running pilot query...")
pilot_results = execute_query(conn, pilot_query, dbms)
print(f"Pilot returned {len(pilot_results)} groups")

page_errors = aggregate_error_to_page_error(pq.result_mapping_list, required_error=q.error)
print(f"page_errors: {page_errors}")

join_block_stats = _extract_join_block_stats(
    pilot_results, page_errors,
    page_id_count=getattr(pq, 'page_id_count', 0),
    table_sizes=q.table_size,
    block_sizes=block_sizes,
    sampled_tables=getattr(pq, 'sampled_tables', None),
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
    table_names=tuple(q.table_size.keys()),
)

print(f"\nAnalyzing constraints:")
print(f"Total constraints: {len(phi_constraints.constraints)}")

table_names = ("lineitem", "orders")

# Evaluate residual at max_rates (0.1, 0.1)
max_rates = {"lineitem": 0.1, "orders": 0.1}
feasible_at_max = phi_constraints.is_feasible(max_rates)
print(f"Feasible at max rates (0.1, 0.1): {feasible_at_max}")

# Evaluate residual at min_rates (0.01, 0.01)
min_rates = {"lineitem": 0.01, "orders": 0.01}
feasible_at_min = phi_constraints.is_feasible(min_rates)
print(f"Feasible at min rates (0.01, 0.01): {feasible_at_min}")

# Let's count how many constraints are violated at min rates and print details for some
violated_at_min = []
for idx, c in enumerate(phi_constraints.constraints):
    res_min = phi_constraint_residual(c, min_rates)
    res_max = phi_constraint_residual(c, max_rates)
    if res_min < 0:
        violated_at_min.append((idx, res_min, res_max, c))

print(f"Number of violated constraints at min rates: {len(violated_at_min)} / {len(phi_constraints.constraints)}")
if violated_at_min:
    print("\nSome violated constraints details:")
    for idx, res_min, res_max, c in violated_at_min[:5]:
        print(f"Constraint {idx}:")
        print(f"  agg={c.aggregate_index}, grp={c.group_index}")
        print(f"  residual at min: {res_min:.4e}")
        print(f"  residual at max: {res_max:.4e}")
        print(f"  L_mu: {c.L_mu}")
        print(f"  z_value: {c.z_value}")
        if c.join_stats is not None:
            from pilotdb.pilot_engine.join_variance import _compute_UV_for_constraint, _safe_ratio
            uv_min = _compute_UV_for_constraint(c, min_rates)
            uv_max = _compute_UV_for_constraint(c, max_rates)
            print(f"  U_V at min: {uv_min:.4e}, U_V at max: {uv_max:.4e}")
            print(f"  _cached_U_y1: {c._cached_U_y1:.4e}")
            print(f"  _cached_U_y2_sum_sq: {c._cached_U_y2_sum_sq:.4e}")
            print(f"  _cached_U_y3: {c._cached_U_y3:.4e}")
            # Calculate term1, term2, term3 for min rates
            t1, t2 = c.sampled_tables[0], c.sampled_tables[1]
            theta1_min = min_rates.get(t1, 1.0)
            theta2_min = min_rates.get(t2, 1.0)
            term1 = _safe_ratio(1.0 - theta1_min, theta1_min) * c._cached_U_y1
            term2 = _safe_ratio(1.0 - theta2_min, theta2_min) * c._cached_U_y2_sum_sq
            term3 = _safe_ratio((1.0 - theta1_min) * (1.0 - theta2_min), theta1_min * theta2_min) * c._cached_U_y3
            print(f"  term1: {term1:.4e}")
            print(f"  term2: {term2:.4e}")
            print(f"  term3: {term3:.4e}")
            
            # Print statistics of the raw values in join_stats
            print(f"  join_stats.y1_per_block: len={len(c.join_stats.y1_per_block)}, mean={np.mean(c.join_stats.y1_per_block):.4f}, std={np.std(c.join_stats.y1_per_block):.4f}, sum={np.sum(c.join_stats.y1_per_block):.4f}")
            print(f"  join_stats.y2_values: len={len(c.join_stats.y2_values)}, mean={np.mean(c.join_stats.y2_values):.4f}, std={np.std(c.join_stats.y2_values):.4f}, sum={np.sum(c.join_stats.y2_values):.4f}")
            print(f"  join_stats.y3_per_block: len={len(c.join_stats.y3_per_block)}, mean={np.mean(c.join_stats.y3_per_block):.4f}, std={np.std(c.join_stats.y3_per_block):.4f}, sum={np.sum(c.join_stats.y3_per_block):.4f}")
            print(f"  join_stats.N1: {c.join_stats.N1}, join_stats.N2: {c.join_stats.N2}")


# Run optimization
print("\nRunning solve_trust_region_plan...")
plan = solve_trust_region_plan(
    subset=table_names,
    table_sizes=q.table_size,
    min_rate=0.01,
    max_rate=0.1,
    phi_constraints=phi_constraints,
)
print(f"Optimizer plan result: {plan}")

conn.close()

import sys
import os
import numpy as np

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")
sys.stdout.reconfigure(encoding='utf-8')

from pilotdb.benchmarks.tpch_shared import build_query_obj, load_query_sql, exact_run
from pilotdb.execute import (
    execute_aqp, _extract_join_block_stats, _extract_pilot_stats,
    execute_query, process_subqueries, lookup_block_sizes
)
from pilotdb.pilot_engine.join_variance import build_phi_constraints, phi_constraint_value, phi_constraint_residual, _compute_UV_for_constraint
from pilotdb.pilot_engine.utils import aggregate_error_to_page_error
from pilotdb.pilot_engine.optimizer import build_optimization_context, solve_trust_region_plan
from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
from pilotdb.pilot_engine.rewriter.sampling import Sampling_Rewriter
from pilotdb.pilot_engine.sampling_plan import SamplingPlan
from pilotdb.pilot_engine.multi_table_sampling import apply_sampling_plan_template

db_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
db_config = {"dbms": "duckdb", "path": db_path}

qid = "q7"
sql = load_query_sql(qid)
q = build_query_obj(qid, sql)

# Scale table sizes to SF=10
scaled_sizes = {}
for name, size in q.table_size.items():
    if name.lower() in ("nation", "region"):
        scaled_sizes[name] = size
    else:
        scaled_sizes[name] = size * 10
q.table_size = scaled_sizes

dbms = "duckdb"
import duckdb
conn = duckdb.connect(db_path)

pq = Pilot_Rewriter(q.table_cols, q.table_size, dbms)
sq = Sampling_Rewriter(q.table_cols, q.table_size, dbms)
pilot_query = pq.rewrite(q.query) + ";"
sampling_query = sq.rewrite(q.query) + ";"

subquery_results = process_subqueries(dbms, conn, pq)

pilot_sample_rate = 1.0
K = len(pq.sampled_tables)
effective_pilot_rate = (pilot_sample_rate / 100) ** (1.0 / K) if K > 1 else (pilot_sample_rate / 100)
rates = {t_name: effective_pilot_rate for (t_name, _, _) in pq.sampled_tables}
pilot_plan = SamplingPlan(rates=rates, reason="geometric multi-table pilot plan")

pilot_query_templated = apply_sampling_plan_template(
    pilot_query, pilot_plan, dbms
)
for subquery_name, subquery_result in subquery_results.items():
    pilot_query_templated = pilot_query_templated.replace(subquery_name, subquery_result)

pilot_results = execute_query(conn, pilot_query_templated, dbms)

page_errors = aggregate_error_to_page_error(
    pq.result_mapping_list, required_error=q.error
)

block_sizes = lookup_block_sizes(conn, dbms, list(q.table_size.keys()))

join_block_stats = _extract_join_block_stats(
    pilot_results, page_errors,
    page_id_count=getattr(pq, 'page_id_count', 0),
    table_sizes=q.table_size,
    block_sizes=block_sizes,
    sampled_tables=getattr(pq, 'sampled_tables', None),
    pilot_rates=rates,
)

pilot_stats = _extract_pilot_stats(
    pilot_results, page_errors, pq.group_cols, pq.limit_value,
    join_block_stats=join_block_stats,
)

query_tables = [t[0] for t in pq.sampled_tables]
phi_constraints = build_phi_constraints(
    failure_prob=q.failure_probability,
    n_aggregates=len([c for c in page_errors.keys() if c != "n_page"]),
    n_groups=len(pilot_stats),
    pilot_stats=pilot_stats,
    required_error=q.error,
    table_names=tuple(query_tables),
)

# Print detail of constraint 0 at theta = [0.01, 0.01]
c = phi_constraints.constraints[0]
theta = {t: 0.01 for t in query_tables}

UV = _compute_UV_for_constraint(c, theta)
print("Constraint 0 detail:")
print("  N1:", c.join_stats.N1)
print("  N2:", c.join_stats.N2)
print("  _cached_U_y1:", c._cached_U_y1)
print("  _cached_U_y2_sum_sq:", c._cached_U_y2_sum_sq)
print("  _cached_U_y3:", c._cached_U_y3)
t1, t2 = c.sampled_tables[0], c.sampled_tables[1]
theta1, theta2 = theta[t1], theta[t2]
term1 = ((1.0 - theta1)/theta1) * c._cached_U_y1
term2 = ((1.0 - theta2)/theta2) * c._cached_U_y2_sum_sq
term3 = (((1.0 - theta1) * (1.0 - theta2))/(theta1 * theta2)) * c._cached_U_y3
print("  term1:", term1)
print("  term2:", term2)
print("  term3:", term3)
print("  UV:", UV)
print("  sqrt(UV):", np.sqrt(UV))
print("  L_mu unscaled:", c.L_mu)
print("  L_mu scaled:", c.L_mu * c.join_stats.N1 * c.join_stats.N2)
print("  val:", c.z_value * np.sqrt(UV) / (c.L_mu * c.join_stats.N1 * c.join_stats.N2))

optimizer_context = build_optimization_context(
    query_tables=query_tables,
    table_sizes=q.table_size,
)
plan = solve_trust_region_plan(
    subset=query_tables,
    table_sizes=q.table_size,
    min_rate=0.01,
    max_rate=0.1,
    phi_constraints=phi_constraints,
)
print("Optimization plan:", plan)

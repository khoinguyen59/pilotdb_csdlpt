import sys
import os
import numpy as np
import math
from scipy.stats import t as t_dist, chi2 as chi2_dist, norm

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")
sys.stdout.reconfigure(encoding='utf-8')

from pilotdb.benchmarks.tpch_shared import build_query_obj, load_query_sql, exact_run
from pilotdb.execute import (
    execute_aqp, _extract_join_block_stats, _extract_pilot_stats,
    execute_query, process_subqueries, lookup_block_sizes
)
from pilotdb.pilot_engine.join_variance import build_phi_constraints, phi_constraint_value, phi_constraint_residual, _compute_UV_for_constraint, JoinBlockStats
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
)

pilot_stats = _extract_pilot_stats(
    pilot_results, page_errors, pq.group_cols, pq.limit_value,
    join_block_stats=join_block_stats,
)

query_tables = [t[0] for t in pq.sampled_tables]

# Let's perform padding:
N1 = join_block_stats.N1
N2 = join_block_stats.N2
theta1_pilot = effective_pilot_rate  # 0.1
theta2_pilot = effective_pilot_rate  # 0.1

n1 = max(round(theta1_pilot * N1), len(join_block_stats.y1_per_block), 1)
n2 = max(round(theta2_pilot * N2), 1)
n_pairs = n1 * n2

print(f"Padding stats: N1={N1}, N2={N2}, theta1={theta1_pilot}, theta2={theta2_pilot}")
print(f"Target sample size: n1={n1}, n2={n2}, n_pairs={n_pairs}")

# Pad y1_per_block
y1_padded = np.zeros(n1)
y1_padded[:len(join_block_stats.y1_per_block)] = join_block_stats.y1_per_block

# Pad y3_per_block
y3_padded = np.zeros(n1)
y3_padded[:len(join_block_stats.y3_per_block)] = join_block_stats.y3_per_block

# Pad y2_pivot
if join_block_stats.y2_pivot is not None:
    y2_pivot_padded = np.zeros((n1, n2))
    p_r, p_c = join_block_stats.y2_pivot.shape
    y2_pivot_padded[:p_r, :p_c] = join_block_stats.y2_pivot
else:
    y2_pivot_padded = None

# Pad y2_values
y2_values_padded = np.zeros(n_pairs)
y2_values_padded[:len(join_block_stats.y2_values)] = join_block_stats.y2_values

padded_join_stats = JoinBlockStats(
    y1_per_block=y1_padded,
    y2_values=y2_values_padded,
    y3_per_block=y3_padded,
    n_pilot_blocks=n1,  # Set n_pilot_blocks to n1!
    N1=N1,
    N2=N2,
    pilot_rate=theta1_pilot,  # Set pilot_rate to theta1_pilot!
    y2_pivot=y2_pivot_padded,
)

# Now pad pilot_stats for each group:
padded_pilot_stats = []
for stat in pilot_stats:
    # stat['sample_mean'] is the mean of non-empty block-pairs.
    # The total sum of the group is: sum_val = mean * size.
    # The true sample mean over all n_pairs block-pairs is: sum_val / n_pairs.
    sum_val = stat["sample_mean"] * stat["sample_size"]
    true_mean = sum_val / n_pairs
    
    # Standard deviation over all n_pairs:
    # Since we have stat["sample_size"] non-zero values with some mean and std,
    # and the remaining n_pairs - stat["sample_size"] values are 0.
    # Let's approximate the standard deviation of this combined array:
    # E[X^2] = (sum of squares) / n_pairs.
    # In the pilot results, the sum of squares is: size * (mean^2 + std^2).
    # So E[X^2] = size * (mean^2 + std^2) / n_pairs.
    # Var[X] = E[X^2] - E[X]^2.
    size_non_zero = stat["sample_size"]
    mean_non_zero = stat["sample_mean"]
    std_non_zero = stat["sample_std"]
    
    mean_sq = size_non_zero * (mean_non_zero**2 + std_non_zero**2) / n_pairs
    var_val = mean_sq - true_mean**2
    true_std = math.sqrt(max(var_val, 0.0))
    
    padded_stat = {
        "sample_mean": true_mean,
        "sample_std": true_std,
        "sample_size": n_pairs,
        "aggregate_index": stat["aggregate_index"],
        "group_index": stat["group_index"],
        "join_stats": padded_join_stats,
    }
    padded_pilot_stats.append(padded_stat)

phi_constraints = build_phi_constraints(
    failure_prob=q.failure_probability,
    n_aggregates=len([c for c in page_errors.keys() if c != "n_page"]),
    n_groups=len(padded_pilot_stats),
    pilot_stats=padded_pilot_stats,
    required_error=q.error,
    table_names=tuple(query_tables),
)

# Print detail of constraint 0 at theta = [0.01, 0.01]
c = phi_constraints.constraints[0]
for rate in [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 0.8, 1.0]:
    theta = {t: rate for t in query_tables}
    val = phi_constraint_value(c, theta)
    res = phi_constraint_residual(c, theta)
    print(f"At rate={rate}: val={val:.6f}, required={c.required_error}, residual={res:.6f} (feasible: {res >= 0})")

plan = solve_trust_region_plan(
    subset=tuple(query_tables),
    table_sizes=q.table_size,
    min_rate=0.01,
    max_rate=0.1,
    phi_constraints=phi_constraints,
)
print("\nOptimizer plan with padding:", plan)

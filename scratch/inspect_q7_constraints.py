import sys
import os

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")
sys.stdout.reconfigure(encoding='utf-8')

from pilotdb.benchmarks.tpch_shared import build_query_obj, load_query_sql, exact_run
from pilotdb.execute import (
    execute_aqp, _extract_join_block_stats, _extract_pilot_stats,
    execute_query, process_subqueries, lookup_block_sizes
)
from pilotdb.pilot_engine.join_variance import build_phi_constraints
from pilotdb.pilot_engine.utils import aggregate_error_to_page_error
from pilotdb.pilot_engine.optimizer import build_optimization_context
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
print("Pilot plan rates:", rates)

pilot_query_templated = apply_sampling_plan_template(
    pilot_query, pilot_plan, dbms
)
for subquery_name, subquery_result in subquery_results.items():
    pilot_query_templated = pilot_query_templated.replace(subquery_name, subquery_result)

pilot_results = execute_query(conn, pilot_query_templated, dbms)

page_errors = aggregate_error_to_page_error(
    pq.result_mapping_list, required_error=q.error
)
print("Page errors:", page_errors)

block_sizes = lookup_block_sizes(conn, dbms, list(q.table_size.keys()))
print("Block sizes:", block_sizes)

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

print("\nPilot Stats groups:", len(pilot_stats))
phi_constraints = build_phi_constraints(
    failure_prob=q.failure_probability,
    n_aggregates=len([c for c in page_errors.keys() if c != "n_page"]),
    n_groups=len(pilot_stats),
    pilot_stats=pilot_stats,
    required_error=q.error,
    table_names=tuple(q.table_size.keys()),
)

print("\nPhi Constraints mode:", phi_constraints.mode)
for idx, c in enumerate(phi_constraints.constraints):
    print(f"Constraint {idx}:")
    print("  sampled_tables:", c.sampled_tables)
    print("  z_value:", c.z_value)
    print("  L_mu:", c.L_mu)
    print("  required_error:", c.required_error)
    print("  pilot_sample_std:", c.pilot_sample_std)
    print("  pilot_sample_size:", c.pilot_sample_size)
    if c.join_stats:
        print("  join_stats details:")
        print("    n_pilot_blocks:", c.join_stats.n_pilot_blocks)
        print("    N1:", c.join_stats.N1)
        print("    N2:", c.join_stats.N2)
        print("    pilot_rate:", c.join_stats.pilot_rate)

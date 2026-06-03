import sys
import os
import duckdb
import json
import traceback

sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")
from pilotdb.query import Query
from pilotdb.execute import (
    _extract_join_block_stats,
    _extract_pilot_stats,
    build_phi_constraints,
    execute_query,
    aggregate_error_to_page_error,
)
from pilotdb.benchmarks.tpch_shared import build_query_obj, load_query_sql
from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
from pilotdb.pilot_engine.multi_table_sampling import SamplingPlan, apply_sampling_plan_template
from pilotdb.pilot_engine.join_variance import phi_constraint_value

try:
    db_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out_sf10\tpch_sf10.duckdb"
    sql = load_query_sql("q9")
    q = build_query_obj("q9", sql)
    sf = 10
    q.table_size = {
        t: size * sf if t.lower() not in ("nation", "region") else size
        for t, size in q.table_size.items()
    }
    conn = duckdb.connect(db_path)
    pq = Pilot_Rewriter(q.table_cols, q.table_size, "duckdb")
    pilot_query = pq.rewrite(q.query)
    rates = {t_name: 0.1099 for (t_name, _, _) in pq.sampled_tables}
    pilot_plan = SamplingPlan(rates=rates)
    pilot_query = apply_sampling_plan_template(pilot_query, pilot_plan, "duckdb")
    pilot_results = execute_query(conn, pilot_query, "duckdb")
    page_errors = aggregate_error_to_page_error(
        pq.result_mapping_list, required_error=q.error
    )
    join_block_stats = _extract_join_block_stats(
        pilot_results,
        page_errors,
        page_id_count=pq.page_id_count,
        table_sizes=q.table_size,
        block_sizes={"lineitem": 2048, "orders": 2048},
        sampled_tables=pq.sampled_tables,
    )
    pilot_stats = _extract_pilot_stats(
        pilot_results,
        page_errors,
        pq.group_cols,
        pq.limit_value,
        join_block_stats=join_block_stats,
    )
    phi_constraints = build_phi_constraints(
        failure_prob=q.failure_probability,
        n_aggregates=len([c for c in page_errors.keys() if c != "n_page"]),
        n_groups=len(pilot_results),
        pilot_stats=pilot_stats,
        required_error=q.error,
        table_names=tuple(["lineitem", "orders"]),
    )

    for theta in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]:
        infeasible = 0
        for c in phi_constraints.constraints:
            val = phi_constraint_value(c, {"lineitem": theta, "orders": theta})
            if val > c.required_error:
                infeasible += 1
        print(f"Theta={theta:.2f}: {infeasible}/{len(phi_constraints.constraints)} infeasible")
except Exception as e:
    traceback.print_exc()

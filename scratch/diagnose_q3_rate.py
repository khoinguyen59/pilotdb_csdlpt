import os
import sys
sys.path.insert(0, r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt")

import json
import logging
import math
import numpy as np
from pilotdb.query import Query
from pilotdb.execute import connect_to_db, execute_query, close_connection, query_table_sizes
from pilotdb.pilot_engine.rewriter.pilot import Pilot_Rewriter
from pilotdb.pilot_engine.utils import aggregate_error_to_page_error
from pilotdb.pilot_engine.join_variance import build_phi_constraints, phi_constraint_residual, phi_constraint_value
from pilotdb.pilot_engine.optimizer import build_optimization_context, generate_candidate_plans
from pilotdb.execute import _extract_join_block_stats, _extract_pilot_stats, apply_sampling_plan_template, SamplingPlan

logging.basicConfig(level=logging.INFO)

db_config = {
    "dbms": "duckdb",
    "path": r"C:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\bench_out\tpch_sf1.duckdb"
}

meta_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\benchmarks\tpch\meta.json"
# We know where meta.json and query_3.sql are from the environment.
# Let's search under workspace or the absolute paths
with open(r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\meta.json", "r", encoding="utf-8") as f:
    meta = json.load(f)

q3_path = r"c:\Users\Nguyen Trong Khoi\Downloads\CSDLPT_DA\pilotdb_csdlpt\benchmarks\tpch\query_3.sql"
with open(q3_path, "r", encoding="utf-8") as f:
    q3_sql = f.read()

# Setup Query (10% error target)
q = Query(
    query=q3_sql,
    table_cols=meta["table_cols"],
    table_size=meta["table_size"],
    error=0.10,
    failure_probability=0.05,
    name="q3"
)

# Step 1: Rewrite for pilot
dbms = "duckdb"
conn = connect_to_db(dbms, db_config)
try:
    # Resolve table sizes as a dict
    q.table_size = query_table_sizes(dbms, db_config, q.table_size)
    
    pq = Pilot_Rewriter(q.table_cols, q.table_size, dbms)
    pilot_sql = pq.rewrite(q.query) + ";"
    
    # We need to construct the pilot plan to get the actual template replacement
    pilot_sample_rate = 1.0
    K = len(pq.sampled_tables)
    effective_pilot_rate = (pilot_sample_rate / 100) ** (1.0 / K) if K > 1 else (pilot_sample_rate / 100)
    rates = {t_name: effective_pilot_rate for (t_name, _, _) in pq.sampled_tables}
    pilot_plan = SamplingPlan(rates=rates, reason="geometric multi-table pilot plan")
    pilot_sql_applied = apply_sampling_plan_template(pilot_sql, pilot_plan, dbms)
    
    # Run pilot query
    pilot_results = execute_query(conn, pilot_sql_applied, dbms)
    
    # Extract page errors
    page_errors = aggregate_error_to_page_error(
        pq.result_mapping_list, required_error=q.error
    )
    
    # Extract join stats
    join_stats = _extract_join_block_stats(
        pilot_results,
        page_errors,
        page_id_count=getattr(pq, 'page_id_count', 0),
        table_sizes=q.table_size,
        sampled_tables=pq.sampled_tables
    )
    
    # Extract pilot stats
    stats = _extract_pilot_stats(
        pilot_results,
        page_errors,
        group_cols=pq.group_cols,
        limit=pq.limit_value,
        join_block_stats=join_stats
    )
    
    n_page_stats = len([c for c in page_errors.keys() if c != "n_page"])
    n_groups = len(set(
        tuple(pilot_results[pq.group_cols].iloc[i])
        for i in range(len(pilot_results))
    )) if pq.group_cols else 1

    # Build phi constraints
    phi_constraints = build_phi_constraints(
        failure_prob=q.failure_probability,
        n_aggregates=n_page_stats,
        n_groups=n_groups,
        pilot_stats=stats,
        required_error=q.error,
        table_names=tuple(q.table_size.keys())
    )
    
    print("\n=== CONSTRAINTS DETAILS ===")
    for idx, c in enumerate(phi_constraints.constraints):
        print(f"\nConstraint {idx}:")
        print(f"  agg_idx={c.aggregate_index}, grp_idx={c.group_index}")
        print(f"  z_value={c.z_value:.4f}")
        print(f"  L_mu={c.L_mu}")
        print(f"  required_error={c.required_error:.4f}")
        print(f"  pilot_sample_std={c.pilot_sample_std}")
        print(f"  pilot_sample_size={c.pilot_sample_size}")
        if np.isnan(c.L_mu):
            print("  FAIL: L_mu is NaN (relative error undefined because lower bound is non-positive).")
        # Check value at max_rate = 1.0
        try:
            val = phi_constraint_residual(c, {"orders": 1.0, "lineitem": 1.0})
            print(f"  residual at rate 1.0: {val:.6f}")
            if val < 0:
                print(f"  FAIL: constraint is violated at rate 1.0 (residual = {val:.6f} < 0)")
        except Exception as e:
            print(f"  evaluation failed: {e}")

finally:
    close_connection(conn, dbms)

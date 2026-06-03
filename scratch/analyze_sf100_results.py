import json
import os
import sys
from collections import defaultdict
import numpy as np

def analyze_jsonl(filepath):
    if not os.path.exists(filepath):
        print(f"Error: File {filepath} does not exist.")
        return

    # Structure to hold metrics grouped by query
    # query -> list of dicts
    data = defaultdict(list)

    with open(filepath, 'r') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                query = record.get('query')
                # Only analyze duckdb at SF=100
                if record.get('dbms') == 'duckdb' and record.get('scale_factor', 100) == 100:
                    data[query].append(record)
            except Exception as e:
                pass

    if not data:
        # Fallback to general read if scale_factor field is not present or populated
        print("Warning: No records found with dbms='duckdb' and scale_factor=100. Analyzing all duckdb runs...")
        with open(filepath, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    query = record.get('query')
                    if record.get('dbms') == 'duckdb':
                        data[query].append(record)
                except Exception as e:
                    pass

    if not data:
        print("Error: No data parsed from the log file.")
        return

    print("# TPC-H SF=100 Benchmark Analysis Report")
    print("\n## Query-by-Query Performance Summary\n")
    print("| Query | Status | Avg Exact (s) | Avg AQP (s) | Speedup | Error | Sample Rate (%) | Fallback Reason |")
    print("|---|---|---|---|---|---|---|---|")

    for query in sorted(data.keys(), key=lambda q: int(q.split('q')[-1]) if 'q' in q else q):
        runs = data[query]
        
        exact_times = []
        aqp_times = []
        sample_rates = []
        errors = []
        fallbacks = []
        
        for r in runs:
            # We look for exact vs AQP runtimes
            # In the logger:
            # exact_time is stored in exact_runtime or computed
            # Let's extract exact and AQP runtime
            runtime_info = r.get('runtime', {})
            
            # DuckDB runner logs 'exact_runtime' at the root level or in runtime dict
            exact_t = r.get('exact_runtime') or runtime_info.get('exact_query_execution')
            aqp_t = r.get('aqp_runtime') or r.get('runtime', {}).get('runtime')
            
            if exact_t is not None:
                exact_times.append(exact_t)
            if aqp_t is not None:
                aqp_times.append(aqp_t)
                
            sr = r.get('final_sample_rate', 1.0)
            # convert from factor to percentage if it's stored as percentage or vice-versa
            # run_duckdb_tpch logs it as percentage or fraction. Let's handle both.
            if sr <= 1.0 and sr > 0.0:
                sample_rates.append(sr * 100)
            else:
                sample_rates.append(sr)
                
            err = r.get('rel_err') or r.get('error')
            if err is not None:
                errors.append(err)
                
            reason = r.get('fallback_reason') or r.get('reason')
            if reason:
                fallbacks.append(str(reason))

        avg_exact = np.mean(exact_times) if exact_times else 0.0
        avg_aqp = np.mean(aqp_times) if aqp_times else 0.0
        avg_sr = np.mean(sample_rates) if sample_rates else 100.0
        avg_err = np.mean(errors) if errors else 0.0
        
        # Unique fallbacks
        unique_fallbacks = set(fallbacks)
        fallback_str = ", ".join(unique_fallbacks) if unique_fallbacks else "None"
        
        status = "AQP Active" if not fallback_str or fallback_str == "None" else "Fallback"
        
        if avg_aqp > 0:
            speedup = avg_exact / avg_aqp
            speedup_str = f"{speedup:.2f}x"
        else:
            speedup_str = "N/A"
            
        err_str = f"{avg_err:.4%}" if avg_err else "0.0%"
        print(f"| {query} | {status} | {avg_exact:.3f} | {avg_aqp:.3f} | {speedup_str} | {err_str} | {avg_sr:.2f}% | {fallback_str} |")

    # Overall Summary
    total_exact = sum(np.mean([r.get('exact_runtime', 0.0) for r in runs]) for runs in data.values())
    total_aqp = sum(np.mean([r.get('aqp_runtime', 0.0) or r.get('runtime', {}).get('runtime', 0.0) for r in runs]) for runs in data.values())
    overall_speedup = total_exact / total_aqp if total_aqp > 0 else 0.0
    
    print("\n## Global Metrics Summary\n")
    print(f"- **Total Exact Execution Time (for all 12 queries)**: {total_exact:.2f} seconds")
    print(f"- **Total AQP Execution Time (including overhead)**: {total_aqp:.2f} seconds")
    print(f"- **Overall Speedup Factor**: {overall_speedup:.2f}x")

if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "all_results.jsonl"
    analyze_jsonl(filepath)

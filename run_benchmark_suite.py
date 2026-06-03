import os
import sys
import json
import time
import subprocess
import numpy as np
import pandas as pd
from pathlib import Path

import argparse

# Default Config
QUERIES = ["q1", "q3", "q5", "q6", "q7", "q8", "q9", "q10", "q12", "q14", "q18", "q19"]
NUM_ITERATIONS = 5
BASE_SEED = 42
PILOT_RATE = 1.0
SF = 10
OUTPUT_DIR = Path("bench_out_sf10")

def run_iteration(
    iter_idx: int,
    sf: int,
    output_dir: Path,
    queries: list[str],
    pilot_rate: float,
    num_iterations: int,
    dbms: str = "duckdb",
    db_config_yaml: str | None = None,
    error: float = 0.05,
    failure_prob: float = 0.05,
) -> Path:
    seed = BASE_SEED + iter_idx
    iter_dir = output_dir / f"iter_{iter_idx}"
    
    # If results already exist, skip execution
    json_files = list(iter_dir.glob("results_*.json"))
    if json_files:
        print(f"Skipping execution for Iteration {iter_idx + 1} (results already exist).")
        json_files.sort()
        return json_files[-1]

    print(f"\n==================================================")
    print(f"  RUNNING ITERATION {iter_idx + 1}/{num_iterations} (SEED={seed})")
    print(f"==================================================")

    # Set the environment variable for deterministic sample seed
    env = os.environ.copy()
    env["PILOTDB_SEED"] = str(seed)

    cmd = [
        sys.executable,
        "-m", "pilotdb.benchmarks.run_duckdb_tpch",
        "--dbms", dbms,
        "--queries", ",".join(queries),
        "--pilot-rate", str(pilot_rate),
        "--sf", str(sf),
        "--output-dir", str(iter_dir),
        "--error", str(error),
        "--failure-prob", str(failure_prob),
    ]
    if dbms == "duckdb":
        db_path = output_dir / f"tpch_sf{sf}.duckdb"
        cmd.extend(["--db-path", str(db_path)])
    else:
        if db_config_yaml:
            cmd.extend(["--db-config-yaml", db_config_yaml])

    t0 = time.perf_counter()
    # Run using subprocess.run
    result = subprocess.run(cmd, env=env, capture_output=False, text=True)
    duration = time.perf_counter() - t0

    if result.returncode != 0:
        print(f"ERROR: Iteration {iter_idx} exited with code {result.returncode}")
        sys.exit(result.returncode)

    print(f"Iteration {iter_idx + 1} completed in {duration:.2f} seconds.")

    # Find the JSON results file in the output directory
    json_files = list(iter_dir.glob("results_*.json"))
    if not json_files:
        raise FileNotFoundError(f"No results JSON file found in {iter_dir}")
    # Return the latest one
    json_files.sort()
    return json_files[-1]

def main():
    parser = argparse.ArgumentParser(description="Run PilotDB TPC-H Benchmark Suite")
    parser.add_argument("--sf", type=int, default=10, help="Scale factor (1 or 10)")
    parser.add_argument("--queries", type=str, default=",".join(QUERIES), help="Comma-separated list of queries")
    parser.add_argument("--iterations", type=int, default=5, help="Number of iterations")
    parser.add_argument("--pilot-rate", type=float, default=1.0, help="Pilot sampling rate in percent")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")
    parser.add_argument("--dbms", type=str, default="duckdb", choices=("duckdb", "postgres", "sqlserver"), help="Database system to benchmark")
    parser.add_argument("--db-config-yaml", type=str, default=None, help="Path to database config YAML (Postgres/SQL Server)")
    parser.add_argument("--error", type=float, default=0.05, help="Relative error bound")
    parser.add_argument("--failure-prob", type=float, default=0.05, help="Failure probability bound")
    args = parser.parse_args()

    queries_list = args.queries.split(",")
    sf = args.sf
    num_iterations = args.iterations
    pilot_rate = args.pilot_rate
    dbms = args.dbms
    db_config_yaml = args.db_config_yaml
    error = args.error
    failure_prob = args.failure_prob
    output_dir = Path(args.output_dir) if args.output_dir else Path(f"bench_out_sf{sf}" if sf != 1 else "bench_out")

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    output_dir.mkdir(parents=True, exist_ok=True)
    results_by_query = {q: [] for q in queries_list}

    # 1. Run all iterations
    for i in range(num_iterations):
        results_file = run_iteration(
            iter_idx=i,
            sf=sf,
            output_dir=output_dir,
            queries=queries_list,
            pilot_rate=pilot_rate,
            num_iterations=num_iterations,
            dbms=dbms,
            db_config_yaml=db_config_yaml,
            error=error,
            failure_prob=failure_prob,
        )

        with open(results_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            for rec in data:
                qid = rec["query_id"]
                if qid in results_by_query:
                    results_by_query[qid].append(rec)

    # 2. Aggregate metrics
    summary_data = []
    print("\n==================================================")
    print("  AGGREGATING EXPERIMENT RESULTS")
    print("==================================================")

    for qid in queries_list:
        recs = results_by_query[qid]
        if not recs:
            print(f"Warning: No records found for query {qid}")
            continue

        # Extract values across runs
        exact_runtimes = [r["exact_runtime_s"] for r in recs if r.get("exact_runtime_s") is not None]
        aqp_runtimes = [r["aqp_runtime_s"] for r in recs if r.get("aqp_runtime_s") is not None]
        speedups = [r["speedup"] for r in recs if r.get("speedup") is not None]
        
        # fallback info
        fallbacks = [bool(r.get("fallback_triggered")) for r in recs]
        fallback_reasons = [r.get("fallback_reason") for r in recs if r.get("fallback_reason") is not None]
        fallback_count = sum(fallbacks)
        fallback_rate = (fallback_count / len(recs)) * 100.0

        # final sample rate (excluding when it fell back, if any, or including?)
        # Let's compute mean final sample rate across non-fallback runs, or all runs
        fs_rates = [r["final_sample_rate"] for r in recs if r.get("final_sample_rate") is not None]
        # relative errors
        mean_rel_errs = [r["mean_row_relative_error"] for r in recs if r.get("mean_row_relative_error") is not None]
        max_rel_errs = [r["max_row_relative_error"] for r in recs if r.get("max_row_relative_error") is not None]
        missing_cnts = [r["missing_groups_count"] for r in recs if r.get("missing_groups_count") is not None]

        # Calculate statistics
        mean_exact = np.mean(exact_runtimes) if exact_runtimes else None
        std_exact = np.std(exact_runtimes) if exact_runtimes else None
        
        mean_aqp = np.mean(aqp_runtimes) if aqp_runtimes else None
        std_aqp = np.std(aqp_runtimes) if aqp_runtimes else None
        
        mean_speedup = np.mean(speedups) if speedups else None
        mean_fs_rate = np.mean(fs_rates) if fs_rates else None
        
        mean_row_err = np.mean(mean_rel_errs) if mean_rel_errs else None
        max_row_err = np.max(max_rel_errs) if max_rel_errs else None
        mean_missing_cnt = np.mean(missing_cnts) if missing_cnts else 0

        summary_data.append({
            "query_id": qid,
            "mean_exact_s": mean_exact,
            "std_exact_s": std_exact,
            "mean_aqp_s": mean_aqp,
            "std_aqp_s": std_aqp,
            "mean_speedup": mean_speedup,
            "mean_final_sample_rate_pct": mean_fs_rate,
            "fallback_count": fallback_count,
            "fallback_rate_pct": fallback_rate,
            "fallback_reasons": list(set(fallback_reasons)),
            "mean_row_relative_error": mean_row_err,
            "max_row_relative_error": max_row_err,
            "mean_missing_groups": mean_missing_cnt,
        })

    # Save Aggregated JSON
    aggregated_json_path = output_dir / "aggregated_report.json"
    with open(aggregated_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    # Save markdown report
    md_path = output_dir / "aggregated_report.md"
    
    if sf == 1:
        note_text = "> Threshold 10% được dùng cho assertion CI/CD vì SF=1 quá nhỏ để hit 5% một cách ổn định (sampling variance lớn trên dataset nhỏ). Trên SF=10, expect mean_row_relative_error sẽ về quanh 5%."
    else:
        note_text = f"> Ngưỡng sai số cấu hình rõ ràng là {error * 100.0:.0f}% (--error {error}). Trên SF={sf}, các mẫu thử có dung lượng lớn giúp kiểm chứng chính xác chất lượng ước lượng AQP dưới độ lệch mẫu thấp."

    md_lines = [
        "# Aggregated Benchmark Report",
        f"- **Scale Factor (SF)**: {sf}",
        f"- **Pilot Sample Rate**: {pilot_rate}%",
        f"- **Iterations**: {num_iterations} (deterministic seeds {BASE_SEED} to {BASE_SEED + num_iterations - 1})",
        "",
        "> [!NOTE]",
        note_text,
        "",
        "## Summary table",
        "",
        "| Query | Exact Time (s) | AQP Time (s) | Speedup | Final Sample Rate | Fallback Rate | Mean Row Error | Max Row Error | Missing Groups |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for s in summary_data:
        exact_str = f"{s['mean_exact_s']:.3f}s ±{s['std_exact_s']:.3f}s" if s['mean_exact_s'] is not None else "N/A"
        aqp_str = f"{s['mean_aqp_s']:.3f}s ±{s['std_aqp_s']:.3f}s" if s['mean_aqp_s'] is not None else "N/A"
        speedup_str = f"{s['mean_speedup']:.2f}x" if s['mean_speedup'] is not None else "N/A"
        fs_str = f"{s['mean_final_sample_rate_pct'] * 100.0:.2f}%" if s['mean_final_sample_rate_pct'] is not None else "N/A"
        fb_str = f"{s['fallback_rate_pct']:.1f}% ({s['fallback_count']}/{num_iterations})"
        
        mean_err_str = f"{s['mean_row_relative_error'] * 100.0:.3f}%" if s['mean_row_relative_error'] is not None else "N/A"
        max_err_str = f"{s['max_row_relative_error'] * 100.0:.3f}%" if s['max_row_relative_error'] is not None else "N/A"
        missing_str = f"{s['mean_missing_groups']:.1f}"

        md_lines.append(
            f"| **{s['query_id'].upper()}** | {exact_str} | {aqp_str} | {speedup_str} | {fs_str} | {fb_str} | {mean_err_str} | {max_err_str} | {missing_str} |"
        )

    md_lines.extend([
        "",
        "## Analysis and Assertions",
        ""
    ])

    # Assertions
    total_fallbacks = sum(s['fallback_count'] for s in summary_data)
    total_runs = len(summary_data) * num_iterations
    overall_fallback_rate = (total_fallbacks / total_runs) * 100.0
    
    md_lines.append(f"- **Overall Fallback Rate**: {overall_fallback_rate:.2f}% ({total_fallbacks}/{total_runs} runs)")
    
    if overall_fallback_rate >= 30.0:
        md_lines.append(
            "  > [!WARNING]\n"
            f"  > **High Fallback Rate Alert**: The overall fallback rate is {overall_fallback_rate:.2f}%, which exceeds the 30% quality threshold.\n"
            f"  > This indicates that AQP is frequently reverting to exact execution under SF={sf} due to query complexity or sampling variance.\n"
        )
    else:
        md_lines.append(
            "  > [!NOTE]\n"
            f"  > **Acceptable Fallback Rate**: The overall fallback rate of {overall_fallback_rate:.2f}% is within the 30% quality threshold.\n"
        )

    # Let's print out fallback details if any
    for s in summary_data:
        if s['fallback_count'] > 0:
            md_lines.append(f"- **{s['query_id'].upper()}** had {s['fallback_count']}/{num_iterations} fallbacks. Reasons: `{s['fallback_reasons']}`")

    md_content = "\n".join(md_lines)
    md_path.write_text(md_content, encoding="utf-8")

    print(f"\nAggregated report written to:")
    print(f"  - JSON: {aggregated_json_path}")
    print(f"  - Markdown: {md_path}")
    print("\nSummary table:")
    print(md_content.split("## Analysis and Assertions")[0])

if __name__ == "__main__":
    main()

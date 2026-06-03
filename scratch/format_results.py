import json
from pathlib import Path

result_file = Path("bench_out_sf10_results/results_20260520T211507Z.json")
with open(result_file, "r", encoding="utf-8") as f:
    results = json.load(f)

print("| Query | Status | Final Sample Rate | Fallback Reason | Exact Runtime (s) | AQP Runtime (s) | Speedup | Mean Relative Error | Max Relative Error |")
print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

for r in sorted(results, key=lambda x: int(x["query_id"][1:])):
    qid = r["query_id"].upper()
    fallback = r["fallback_triggered"]
    status = "Fallback" if fallback else "AQP Success"
    rate = f"{r['final_sample_rate']*100:.2f}%"
    reason = r["fallback_reason"] if r["fallback_reason"] else "N/A"
    exact_time = f"{r['exact_runtime_s']:.4f}s"
    aqp_time = f"{r['aqp_runtime_s']:.4f}s"
    speedup = f"{r['speedup']:.2f}x"
    
    mean_err = f"{r['mean_row_relative_error']*100:.2f}%" if r.get('mean_row_relative_error') is not None else "N/A"
    max_err = f"{r['max_row_relative_error']*100:.2f}%" if r.get('max_row_relative_error') is not None else "N/A"
    
    print(f"| {qid} | {status} | {rate} | {reason} | {exact_time} | {aqp_time} | {speedup} | {mean_err} | {max_err} |")

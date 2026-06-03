import json
from pathlib import Path

# Look in bench_out_sf10 or other output directories
for folder in ["bench_out_sf10", "bench_out_sf10_results"]:
    results_files = list(Path(folder).glob("**/results_*.json"))
    if results_files:
        results_files.sort()
        latest = results_files[-1]
        print(f"\nResults from {latest}:")
        with open(latest, "r", encoding="utf-8") as f:
            data = json.load(f)
        # If data is a list of results
        records = data if isinstance(data, list) else data.get("results", [])
        for r in records:
            print(f"  {r.get('query_id')}: final_rate={r.get('final_sample_rate', 0.0):.4f}, fallback={r.get('fallback_triggered', False)}, reason={r.get('fallback_reason')}")

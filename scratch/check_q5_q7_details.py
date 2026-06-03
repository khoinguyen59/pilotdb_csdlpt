import json
from pathlib import Path

latest = Path("bench_out_sf10_results/results_20260520T211507Z.json")
with open(latest, "r", encoding="utf-8") as f:
    data = json.load(f)

for r in data:
    if r.get("query_id") in ("q5", "q7"):
        print(json.dumps(r, indent=2))

import subprocess
import sys

queries = ["q1", "q3", "q5", "q6", "q7", "q8", "q9", "q10", "q12", "q14", "q18", "q19"]
db_config = "db_configs/duckdb_local.yml"

print("Starting smoke test for TPC-H queries on DuckDB...")
for q in queries:
    qid = q[1:]
    cmd = [
        "C:\\Python313\\python.exe", "evaluate.py",
        "--dbms", "duckdb",
        "--db_config_file", db_config,
        "--qid", qid,
        "--process_mode", "aqp",
        "--pilot_sample_rate", "1.0"
    ]
    print(f"\n================ Running {q} ================")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"{q}: SUCCESS")
        # print last 5 lines of stdout
        lines = res.stdout.strip().split('\n')
        for l in lines[-5:]:
            print(f"  {l}")
    else:
        print(f"{q}: FAILED with code {res.returncode}")
        print("STDOUT:")
        print(res.stdout)
        print("STDERR:")
        print(res.stderr)

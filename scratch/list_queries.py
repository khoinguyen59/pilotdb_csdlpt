import glob
import os
import re

query_dir = "benchmarks/duckdb/tpch"
sql_files = sorted(glob.glob(f"{query_dir}/query_*.sql"), key=lambda x: int(re.search(r"query_(\d+)\.sql", x).group(1)))

for f in sql_files:
    qid = os.path.basename(f).replace(".sql", "")
    with open(f, "r") as fh:
        sql = fh.read()
    # Find group by clause
    match = re.search(r"group\s+by\s+([\s\S]+?)(?:order\s+by|limit|$)", sql, re.IGNORECASE)
    group_by = match.group(1).strip() if match else "None"
    # Clean up whitespace/newlines
    group_by = re.sub(r"\s+", " ", group_by)
    print(f"{qid}: {group_by}")

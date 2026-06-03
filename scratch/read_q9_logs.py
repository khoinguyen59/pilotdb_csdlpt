import re
from pathlib import Path

log_path = Path(r"C:\Users\Nguyen Trong Khoi\.gemini\antigravity-ide\brain\3240827e-6fea-4c73-b31d-96ddd2220a6c\.system_generated\tasks\task-4188.log")
output_path = Path("scratch/q9_logs_extracted.txt")

if not log_path.exists():
    print(f"Log path does not exist: {log_path}")
    exit(1)

content = log_path.read_text(encoding="utf-8", errors="ignore")

# Find q9 section
# We can search for the start of q9 and grab lines until the start of q10
lines = content.splitlines()
q9_started = False
q9_lines = []

for line in lines:
    if "[bench] q9 ..." in line or "processing query tpch-q9" in line:
        q9_started = True
    elif q9_started and ("[bench] q10 ..." in line or "processing query tpch-q10" in line):
        q9_started = False
    
    if q9_started:
        q9_lines.append(line)

output_path.write_text("\n".join(q9_lines), encoding="utf-8")
print(f"Extracted {len(q9_lines)} lines to {output_path}")

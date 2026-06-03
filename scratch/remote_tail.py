import subprocess

out_text = ""

try:
    out_text += "=== Log Last 120 Lines ===\n"
    out_tail = subprocess.check_output(
        "gcloud compute ssh instance-20260530-163349 --zone=us-central1-a --command=\"tail -n 120 pilotdb_csdlpt/sf100_clean.log\"",
        stderr=subprocess.STDOUT, shell=True
    )
    out_text += out_tail.decode('utf-8', errors='ignore')
except subprocess.CalledProcessError as e:
    out_text += f"tail failed {e.returncode}:\n{e.output.decode('utf-8', errors='ignore') if e.output else 'None'}\n"

try:
    out_text += "\n=== Remote Output Directory Listing ===\n"
    out_ls = subprocess.check_output(
        "gcloud compute ssh instance-20260530-163349 --zone=us-central1-a --command=\"ls -la pilotdb_csdlpt/bench_out_pg_sf100_clean/\"",
        stderr=subprocess.STDOUT, shell=True
    )
    out_text += out_ls.decode('utf-8', errors='ignore')
except subprocess.CalledProcessError as e:
    output_str = e.output.decode('utf-8', errors='ignore') if e.output else 'None'
    out_text += f"ls failed {e.returncode}:\n{output_str}\n"

with open("scratch/remote_status.txt", "w", encoding="utf-8") as f:
    f.write(out_text)

print("Saved GCE status successfully to scratch/remote_status.txt")

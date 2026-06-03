import subprocess
try:
    print("Downloading clean results folder from GCE VM...")
    subprocess.check_call(
        "gcloud compute scp --recurse instance-20260530-163349:pilotdb_csdlpt/bench_out_pg_sf100_clean . --zone=us-central1-a --quiet",
        shell=True
    )
    print("Download successful!")
except Exception as e:
    print(f"Download failed: {e}")

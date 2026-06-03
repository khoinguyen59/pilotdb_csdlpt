import subprocess
try:
    print("Uploading updated run_benchmark_suite.py to GCE VM...")
    out = subprocess.check_output(
        "gcloud compute scp run_benchmark_suite.py instance-20260530-163349:pilotdb_csdlpt/run_benchmark_suite.py --zone=us-central1-a",
        text=True, stderr=subprocess.STDOUT, shell=True
    )
    print(out)
    print("Upload completed successfully!")
except subprocess.CalledProcessError as e:
    print(f"Upload failed with code {e.returncode}:\n{e.output}")

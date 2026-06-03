import subprocess
try:
    print("Stopping GCE VM to save credits...")
    subprocess.check_call(
        "gcloud compute instances stop instance-20260530-163349 --zone=us-central1-a --quiet",
        shell=True
    )
    print("VM stopped successfully!")
except Exception as e:
    print(f"Failed to stop VM: {e}")

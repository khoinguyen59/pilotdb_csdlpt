import subprocess
try:
    print("Pushing commits to GitHub...")
    subprocess.check_call(['git', 'push'])
    print("Git push completed successfully!")
except subprocess.CalledProcessError as e:
    print(f"Git push failed with code {e.returncode}")

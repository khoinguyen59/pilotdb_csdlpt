import subprocess
try:
    subprocess.check_call(['git', 'add', 'docs/report_sections_5_6.md'])
    subprocess.check_call([
        'git', 'commit', '-m',
        'docs/refactor: update Section 5.6.1 with clean, uncontaminated TPC-H SF100 PostgreSQL performance summary'
    ])
    print("Git commit completed successfully!")
except subprocess.CalledProcessError as e:
    print(f"Git commit failed with code {e.returncode}")

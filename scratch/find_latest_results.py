import glob
import os

files = glob.glob("results/*")
files_with_time = [(f, os.path.getmtime(f)) for f in files]
files_with_time.sort(key=lambda x: x[1])

print("Total files:", len(files))
print("Last 20 modified files:")
for f, t in files_with_time[-20:]:
    import datetime
    dt = datetime.datetime.fromtimestamp(t)
    print(f"{os.path.basename(f)} : {dt} : {os.path.getsize(f)} bytes")

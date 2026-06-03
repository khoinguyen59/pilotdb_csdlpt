import pandas as pd
import glob
import json
import os

# Find the results files
iter_dir = "bench_out_sf10_v2/iter_0"
exact_files = glob.glob(os.path.join(iter_dir, "*_exact.csv"))
aqp_files = glob.glob(os.path.join(iter_dir, "*_aqp.csv"))

print("Exact files:", [os.path.basename(f) for f in exact_files])
print("AQP files:", [os.path.basename(f) for f in aqp_files])

for q in ["q7", "q9"]:
    exact_f = [f for f in exact_files if f.endswith(f"_{q}_exact.csv")]
    aqp_f = [f for f in aqp_files if f.endswith(f"_{q}_aqp.csv")]
    if exact_f and aqp_f:
        print(f"\n--- Comparing {q} ---")
        df_exact = pd.read_csv(exact_f[0])
        df_aqp = pd.read_csv(aqp_f[0])
        print(f"Exact columns: {df_exact.columns.tolist()}, shape: {df_exact.shape}")
        print(f"AQP columns: {df_aqp.columns.tolist()}, shape: {df_aqp.shape}")
        print("Exact head:")
        print(df_exact.head(3))
        print("AQP head:")
        print(df_aqp.head(3))
        
        # Let's align and compute error ourselves
        numeric_cols = [c for c in df_exact.columns if pd.api.types.is_numeric_dtype(df_exact[c])]
        key_cols = [c for c in df_exact.columns if c not in numeric_cols]
        print(f"Key columns: {key_cols}, Numeric columns: {numeric_cols}")

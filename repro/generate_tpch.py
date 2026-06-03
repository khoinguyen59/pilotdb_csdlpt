import duckdb
import os
import sys
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Generate TPC-H data for DuckDB")
    parser.add_argument("--sf", type=int, default=10, help="Scale factor (1 or 10 or 100)")
    parser.add_argument("--output", type=str, default=None, help="Output path")
    args = parser.parse_args()
    
    sf = args.sf
    output_path = args.output or f"bench_out_sf{sf}/tpch_sf{sf}.duckdb"
    
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    
    if p.exists() and p.stat().st_size > 0:
        print(f"TPC-H database already exists at {p} ({p.stat().st_size / 1024 / 1024 / 1024:.2f} GB). Reusing it.")
        return
        
    print(f"Generating TPC-H SF={sf} DuckDB database at {p}...")
    conn = duckdb.connect(database=str(p), read_only=False)
    try:
        conn.execute("INSTALL tpch; LOAD tpch;")
        conn.execute(f"CALL dbgen(sf={sf});")
        print("Verification: table sizes")
        for table in ["lineitem", "orders", "customer", "part", "partsupp", "supplier", "nation", "region"]:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {cnt}")
    finally:
        conn.close()
    print("Generation complete!")

if __name__ == "__main__":
    main()

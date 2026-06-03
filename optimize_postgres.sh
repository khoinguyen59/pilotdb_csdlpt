#!/usr/bin/env bash
# =============================================================================
# optimize_postgres.sh
# MỤC ĐÍCH: Tối ưu hóa các cấu hình hiệu năng của PostgreSQL trên VM n2-standard-8
#           và khởi động lại tiến trình benchmark để chạy với tốc độ x10-x50.
# =============================================================================
set -eo pipefail

echo "[*] Optimizing PostgreSQL system parameters for n2-standard-8 (32GB RAM, 8 vCPUs)..."
sudo -u postgres psql -c "ALTER SYSTEM SET shared_buffers = '8GB';"
sudo -u postgres psql -c "ALTER SYSTEM SET work_mem = '512MB';"
sudo -u postgres psql -c "ALTER SYSTEM SET max_parallel_workers_per_gather = 6;"
sudo -u postgres psql -c "ALTER SYSTEM SET max_parallel_workers = 8;"
sudo -u postgres psql -c "ALTER SYSTEM SET max_worker_processes = 12;"

echo "[*] Restarting PostgreSQL cluster to apply changes..."
sudo systemctl restart postgresql@16-main

echo "[*] Verifying applied changes..."
sudo -u postgres psql -d tpch -c "SHOW shared_buffers; SHOW work_mem; SHOW max_parallel_workers_per_gather;"

echo "[*] Terminating currently running slow benchmark processes..."
sudo pkill -f run_benchmark_suite.py || true
sudo pkill -f run_duckdb_tpch || true

echo "[*] Launching PostgreSQL SF100 optimized benchmark suite in background..."
cd /home/khoitaikhoan2/pilotdb_csdlpt
export PYTHONPATH=.
export PILOTDB_POSTGRES_COST_THRESHOLD=999
export PILOTDB_POSTGRES_VOLUME_PROXY=1

nohup .venv/bin/python run_benchmark_suite.py \
    --dbms postgres \
    --db-config-yaml db_configs/postgres_local.yml \
    --sf 100 \
    --iterations 3 \
    --output-dir bench_out_pg_sf100 > recovery.run.log 2>&1 &

echo "[*] Optimization complete! Running in background."

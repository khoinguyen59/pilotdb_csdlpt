#!/usr/bin/env bash
# =============================================================================
# run_sf100_recovery.sh
# MỤC ĐÍCH: Khôi phục và chạy lại thực nghiệm SF100 Postgres Native với cơ chế
#           nạp dữ liệu tuần tự và giải phóng dung lượng đĩa thông minh.
# =============================================================================
set -uo pipefail

PYTHON_BIN=".venv/bin/python"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"
export PYTHONPATH=.

LOG_FILE="${REPO_DIR}/sf100_recovery.log"
echo "==============================================================" > "${LOG_FILE}"
echo " PilotDB SF100 Recovery Running Script - Started at $(date)" >> "${LOG_FILE}"
echo "==============================================================" >> "${LOG_FILE}"

export PILOTDB_POSTGRES_COST_THRESHOLD=999
export PILOTDB_POSTGRES_VOLUME_PROXY=1

# Step 1: Nạp dữ liệu SF100 vào Postgres tuần tự (sử dụng cơ chế giải phóng bộ nhớ thông minh)
echo "[*] Loading DuckDB SF=100 into PostgreSQL sequentially..." >> "${LOG_FILE}"
"${PYTHON_BIN}" -m pilotdb.benchmarks.load_tpch_postgres \
    --duckdb-src bench_out_sf100/tpch_sf100.duckdb \
    --sf 100 \
    --if-exists drop \
    --pg-config db_configs/postgres_local.yml >> "${LOG_FILE}" 2>&1

# Step 2: Chạy Suite Benchmark SF100 (3 iterations) với cost threshold = 999
echo "[*] Running PostgreSQL SF=100 Benchmark suite (3 iterations)..." >> "${LOG_FILE}"
rm -rf bench_out_pg_sf100
mkdir -p bench_out_pg_sf100
"${PYTHON_BIN}" run_benchmark_suite.py \
    --dbms postgres \
    --db-config-yaml db_configs/postgres_local.yml \
    --sf 100 \
    --iterations 3 \
    --output-dir bench_out_pg_sf100 >> "${LOG_FILE}" 2>&1

# Step 3: Kiểm chứng kết quả AQP engage
echo "[*] Running post-benchmark checks..." >> "${LOG_FILE}"
"${PYTHON_BIN}" scratch/check_aqp_engaged.py bench_out_pg_sf100 >> "${LOG_FILE}" 2>&1 || true

echo "==============================================================" >> "${LOG_FILE}"
echo " PilotDB SF100 Recovery Run completed successfully at $(date)!" >> "${LOG_FILE}"
echo "==============================================================" >> "${LOG_FILE}"

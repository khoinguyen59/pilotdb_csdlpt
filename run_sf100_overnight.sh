#!/usr/bin/env bash
# =============================================================================
# run_sf100_overnight.sh
# MỤC ĐÍCH: Chạy hoàn toàn tự động thực nghiệm SF100 trên PostgreSQL đơn (local),
#           tự động mở rộng phân vùng đĩa 240GB, nạp dữ liệu từ DuckDB SF100
#           đã sinh chéo, chạy benchmark suite (3 iterations) với ngưỡng cost mở (999),
#           lưu trữ kết quả an toàn và tự động shutdown VPS khi hoàn thành để tối ưu chi phí.
# =============================================================================
set -uo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"
export PYTHONPATH=.

LOG_FILE="${REPO_DIR}/sf100_overnight.log"
echo "==============================================================" > "${LOG_FILE}"
echo " PilotDB SF100 Overnight Running Script - Started at $(date)" >> "${LOG_FILE}"
echo "==============================================================" >> "${LOG_FILE}"

# Step 0: Đảm bảo ổ đĩa được mở rộng hoàn chỉnh (sử dụng 240GB)
echo "[*] Expanding VM disk partition to 240GB..." >> "${LOG_FILE}"
sudo growpart /dev/sda 1 >> "${LOG_FILE}" 2>&1 || true
sudo resize2fs /dev/sda1 >> "${LOG_FILE}" 2>&1 || true

# Step 1: Dọn dẹp hết mọi Docker container đang chạy để giải phóng RAM (32GB) + CPU
echo "[*] Cleaning up docker containers..." >> "${LOG_FILE}"
docker compose -f dockerfile/compose.yml down -v >> "${LOG_FILE}" 2>&1 || true
docker compose -f dockerfile/compose.citus.yml down -v >> "${LOG_FILE}" 2>&1 || true

# Step 2: Cài đặt và khởi chạy native PostgreSQL (cho hiệu năng tối ưu nhất trên SF100)
echo "[*] Configuring and starting native PostgreSQL..." >> "${LOG_FILE}"
sudo apt-get update >> "${LOG_FILE}" 2>&1 || true
sudo apt-get install -y postgresql postgresql-contrib >> "${LOG_FILE}" 2>&1 || true
sudo systemctl start postgresql >> "${LOG_FILE}" 2>&1 || true

# Step 3: Tạo Database & User
echo "[*] Creating database & credentials matching postgres_local.yml..." >> "${LOG_FILE}"
sudo -u postgres psql -c "DROP DATABASE IF EXISTS tpch;" >> "${LOG_FILE}" 2>&1 || true
sudo -u postgres psql -c "DROP USER IF EXISTS pilotdb;" >> "${LOG_FILE}" 2>&1 || true
sudo -u postgres psql -c "CREATE USER pilotdb WITH PASSWORD 'pilotdb' SUPERUSER;" >> "${LOG_FILE}" 2>&1 || true
sudo -u postgres psql -c "CREATE DATABASE tpch OWNER pilotdb;" >> "${LOG_FILE}" 2>&1 || true

# Step 4: Thiết lập các biến môi trường cho AQP để mở gate và kích hoạt volume proxy
export PILOTDB_POSTGRES_COST_THRESHOLD=999
export PILOTDB_POSTGRES_VOLUME_PROXY=1

# Step 5: Sinh dữ liệu DuckDB SF100
echo "[*] Generating DuckDB SF=100 database (this might take ~20-30 mins)..." >> "${LOG_FILE}"
mkdir -p bench_out_sf100
"${PYTHON_BIN}" scratch/generate_tpch.py \
    --sf 100 \
    --output bench_out_sf100/tpch_sf100.duckdb >> "${LOG_FILE}" 2>&1

# Step 6: Nạp dữ liệu SF100 vào Postgres
echo "[*] Loading DuckDB SF=100 into PostgreSQL (this might take ~1 hour)..." >> "${LOG_FILE}"
"${PYTHON_BIN}" -m pilotdb.benchmarks.load_tpch_postgres \
    --duckdb-src bench_out_sf100/tpch_sf100.duckdb \
    --sf 100 \
    --if-exists skip \
    --pg-config db_configs/postgres_local.yml >> "${LOG_FILE}" 2>&1

# Step 7: Chạy Suite Benchmark SF100
echo "[*] Running PostgreSQL SF=100 Benchmark suite (3 iterations)..." >> "${LOG_FILE}"
mkdir -p bench_out_pg_sf100
"${PYTHON_BIN}" run_benchmark_suite.py \
    --dbms postgres \
    --db-config-yaml db_configs/postgres_local.yml \
    --sf 100 \
    --iterations 3 \
    --output-dir bench_out_pg_sf100 >> "${LOG_FILE}" 2>&1

# Step 8: Kiểm chứng và xuất kết quả
echo "[*] Running post-benchmark checks..." >> "${LOG_FILE}"
"${PYTHON_BIN}" scratch/check_aqp_engaged.py bench_out_pg_sf100 >> "${LOG_FILE}" 2>&1 || true

echo "==============================================================" >> "${LOG_FILE}"
echo " PilotDB SF100 Overnight Run completed successfully at $(date)!" >> "${LOG_FILE}"
echo " Shutting down VPS in 5 minutes to save GCP billing..." >> "${LOG_FILE}"
echo "==============================================================" >> "${LOG_FILE}"

sleep 300
sudo shutdown -h now

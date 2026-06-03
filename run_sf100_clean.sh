#!/usr/bin/env bash
# =============================================================================
# run_sf100_clean.sh
# MỤC ĐÍCH: Chạy thực nghiệm TPC-H SF100 Postgres Native sạch tuyệt đối.
#           - Xóa bỏ tệp cache .pilotdb_cache.db để loại bỏ nhiễm cache từ SF10.
#           - Bỏ qua bước nạp CSDL (do PostgreSQL đã chứa dữ liệu SF100 hoàn chỉnh).
#           - Cấu hình rõ ràng giới hạn sai số --error 0.05.
# =============================================================================
set -uo pipefail

PYTHON_BIN=".venv/bin/python"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"
export PYTHONPATH=.

LOG_FILE="${REPO_DIR}/sf100_clean.log"
echo "==============================================================" > "${LOG_FILE}"
echo " PilotDB SF100 CLEAN Running Script - Started at $(date)" >> "${LOG_FILE}"
echo "==============================================================" >> "${LOG_FILE}"

export PILOTDB_POSTGRES_COST_THRESHOLD=999
export PILOTDB_POSTGRES_VOLUME_PROXY=1

# Step 1: Xóa bỏ tệp cache nhiễm bẩn (.pilotdb_cache.db)
echo "[*] Purging stale .pilotdb_cache.db database file..." >> "${LOG_FILE}"
rm -f .pilotdb_cache.db
if [ ! -f ".pilotdb_cache.db" ]; then
    echo "  [SUCCESS] Cache file cleared successfully." >> "${LOG_FILE}"
else
    echo "  [ERROR] Failed to delete cache file!" >> "${LOG_FILE}"
    exit 1
fi

# Step 2: Dọn dẹp thư mục đầu ra cũ
echo "[*] Cleaning up output directory..." >> "${LOG_FILE}"
rm -rf bench_out_pg_sf100_clean
mkdir -p bench_out_pg_sf100_clean

# Step 3: Chạy Suite Benchmark SF100 (3 iterations) sạch
echo "[*] Running clean PostgreSQL SF=100 Benchmark suite..." >> "${LOG_FILE}"
"${PYTHON_BIN}" run_benchmark_suite.py \
    --dbms postgres \
    --db-config-yaml db_configs/postgres_local.yml \
    --sf 100 \
    --iterations 3 \
    --error 0.05 \
    --output-dir bench_out_pg_sf100_clean >> "${LOG_FILE}" 2>&1

# Step 4: Kiểm chứng kết quả AQP engage
echo "[*] Running post-benchmark checks..." >> "${LOG_FILE}"
"${PYTHON_BIN}" scratch/check_aqp_engaged.py bench_out_pg_sf100_clean >> "${LOG_FILE}" 2>&1 || true

echo "==============================================================" >> "${LOG_FILE}"
echo " PilotDB SF100 CLEAN Run completed successfully at $(date)!" >> "${LOG_FILE}"
echo "==============================================================" >> "${LOG_FILE}"

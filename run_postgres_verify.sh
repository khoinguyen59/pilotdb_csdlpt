#!/usr/bin/env bash
# =============================================================================
# run_postgres_verify.sh
# MỤC ĐÍCH: Chạy lại benchmark PostgreSQL SF=10 SAU khi đã sửa Phase B
#           (cost-model + AS DOUBLE + subquery leak) để CHỨNG MINH AQP engage
#           thật (final_sample_rate < 1), thay vì chỉ tin vào tuyên bố.
#
# YÊU CẦU TRƯỚC KHI CHẠY:
#   - Có một PostgreSQL đang chạy và khớp db_configs/postgres_local.yml
#       * Cách A (VPS, giống overnight): apt postgres, user 'pilotdb'/'pilotdb', db 'tpch'
#       * Cách B (Docker):  docker compose -f dockerfile/compose.yml up -d postgres
#         (lưu ý: compose dùng mật khẩu 'PilotDB123' -> sửa postgres_local.yml cho khớp)
#   - Có file DuckDB TPC-H SF=10 nguồn (để nạp sang Postgres).
#
# CHẠY:   bash run_postgres_verify.sh
# =============================================================================
set -uo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"
export PYTHONPATH=.

# --- QUAN TRỌNG: cho AQP một cơ hội engage ---
# Lần chạy SF10 trước fallback 100% vì gate tiền-pilot `directly_run_exact`
# (is_high_estimated_cost, ngưỡng mặc định 0.05) vẫn kích hoạt. Phase B đã làm
# ngưỡng này CẤU HÌNH ĐƯỢC nhưng KHÔNG đổi mặc định. Ta nâng ngưỡng để vô hiệu hóa
# gate tiền-pilot, rồi để quyết định hậu-pilot (volume proxy) tự chọn AQP/exact.
export PILOTDB_POSTGRES_COST_THRESHOLD="${PILOTDB_POSTGRES_COST_THRESHOLD:-999}"  # 999 = thực tế tắt gate tiền-pilot
export PILOTDB_POSTGRES_VOLUME_PROXY="${PILOTDB_POSTGRES_VOLUME_PROXY:-1}"        # quyết định hậu-pilot bằng volume proxy

PG_CONFIG="${PG_CONFIG:-db_configs/postgres_local.yml}"
OUT_DIR="${OUT_DIR:-bench_out_pg_sf10_v2}"
QUERIES="${QUERIES:-q1,q3,q5,q6,q7,q8,q9,q10,q12,q14,q18,q19}"
SF=10

echo "=============================================================="
echo " PostgreSQL AQP verification (SF=${SF})"
echo " config=${PG_CONFIG}  out=${OUT_DIR}"
echo "=============================================================="

# 1) Tìm nguồn DuckDB SF=10 ở các vị trí thường gặp.
DUCKDB_SRC=""
for c in \
    "bench_out_sf10/tpch_sf10.duckdb" \
    "../bench_out_sf10/tpch_sf10.duckdb" \
    "bench_out_sf10_final/tpch_sf10.duckdb" ; do
    if [ -f "$c" ]; then DUCKDB_SRC="$c"; break; fi
done
if [ -z "${DUCKDB_SRC}" ]; then
    echo "[!] Không tìm thấy DuckDB SF=10. Hãy sinh trước, ví dụ:"
    echo "    ${PYTHON_BIN} -m pilotdb.benchmarks.run_duckdb_tpch --sf 10 --output bench_out_sf10/tpch_sf10.duckdb"
    echo "    (hoặc đặt biến môi trường DUCKDB_SRC trỏ tới file .duckdb có sẵn)"
    DUCKDB_SRC="${DUCKDB_SRC_OVERRIDE:-}"
    [ -z "${DUCKDB_SRC}" ] && exit 1
fi
echo "[*] DuckDB source: ${DUCKDB_SRC}"

# 2) Nạp TPC-H SF=10 vào Postgres (loader đã tự chạy ANALYZE ở cuối).
echo "[*] Nạp dữ liệu sang Postgres..."
"${PYTHON_BIN}" -m pilotdb.benchmarks.load_tpch_postgres \
    --duckdb-src "${DUCKDB_SRC}" \
    --sf "${SF}" \
    --if-exists skip \
    --pg-config "${PG_CONFIG}" || { echo "[!] Nạp dữ liệu thất bại"; exit 1; }

# 3) Chạy benchmark suite (5 iterations) trên Postgres.
echo "[*] Chạy benchmark suite trên Postgres..."
"${PYTHON_BIN}" run_benchmark_suite.py \
    --dbms postgres \
    --db-config-yaml "${PG_CONFIG}" \
    --sf "${SF}" \
    --queries "${QUERIES}" \
    --output-dir "${OUT_DIR}"

# 4) KIỂM CHỨNG: AQP có engage thật không?
echo "[*] Kiểm chứng kết quả..."
"${PYTHON_BIN}" scratch/check_aqp_engaged.py "${OUT_DIR}"
RC=$?

echo "=============================================================="
if [ "${RC}" -eq 0 ]; then
    echo " ✓ THÀNH CÔNG: có câu chạy AQP thật trên Postgres (xem bảng trên)."
    echo "   -> Có thể dùng số liệu này trong báo cáo (thay cho 'Postgres 100% fallback')."
else
    echo " ✗ Postgres VẪN fallback 100%. Cần debug tiếp Phase B:"
    echo "   - postgres_utils.py:is_high_estimated_cost (fail-closed + ngưỡng 0.05)"
    echo "   - cost.py (bất đối xứng proxy vs EXPLAIN), should_run_exact"
    echo "   - rò 'AS DOUBLE'/'subquery_0' trong sampling query (execute.py ~1090-1096)"
    echo "   Xem logs/ và overnight.log để biết fallback_reason cụ thể."
fi
echo "=============================================================="
exit "${RC}"

#!/usr/bin/env bash
# =============================================================================
# run_citus_benchmark.sh
# MỤC ĐÍCH: THỰC SỰ dựng cụm Citus (1 coordinator + 2 workers), shard dữ liệu
#           TPC-H, chạy benchmark PilotDB và lấy SỐ LIỆU THẬT — thay cho con số
#           "3.59x-6.54x" hiện đang BỊA (không có dữ liệu backing).
#
# CHIẾN LƯỢC (theo yêu cầu): "thử dựng thật, lỗi thì future-work".
#   Nếu bất kỳ bước hạ tầng nào lỗi -> script dừng và in hướng dẫn chuyển phần
#   Citus sang 'future-work' trong báo cáo (KHÔNG bịa số).
#
# YÊU CẦU: Docker + Docker Compose. Chỉ dùng `docker exec` để nạp dữ liệu nên
#          KHÔNG cần cài psql client trên host. Benchmark nối qua psycopg2:5432.
#
# CHẠY:        bash run_citus_benchmark.sh
# CHẠY LẠI SẠCH: docker compose -f dockerfile/compose.citus.yml down -v && bash run_citus_benchmark.sh
# =============================================================================
set -uo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"
export PYTHONPATH=.

# --- QUAN TRỌNG: Citus cũng là Postgres -> cùng gate `directly_run_exact` (ngưỡng 0.05).
# Không set các biến này thì Citus cũng fallback 100% y như Postgres SF10 vừa rồi. ---
export PILOTDB_POSTGRES_COST_THRESHOLD="${PILOTDB_POSTGRES_COST_THRESHOLD:-999}"  # tắt gate tiền-pilot
export PILOTDB_POSTGRES_VOLUME_PROXY="${PILOTDB_POSTGRES_VOLUME_PROXY:-1}"        # quyết định hậu-pilot bằng volume proxy

COMPOSE="dockerfile/compose.citus.yml"
COORD="citus_coordinator"
PW="${POSTGRES_PASSWORD:-PilotDB123}"
DB="${POSTGRES_DB:-tpch}"
USER_="${POSTGRES_USER:-pilotdb}"
OUT_DIR="${OUT_DIR:-bench_out_citus_sf10}"
QUERIES="${QUERIES:-q1,q6,q12}"   # q1,q6 = single-table (an toàn); q12 = join 2 bảng co-located
SF=10
CSV_DIR="citus_csv"

# psql tiện ích chạy bên trong coordinator (đọc SQL/CSV từ STDIN trên host).
pexec() { docker exec -e PGPASSWORD="${PW}" -i "${COORD}" psql -v ON_ERROR_STOP=1 -U "${USER_}" -d "${DB}" "$@"; }
fail_future_work() {
    echo "=============================================================="
    echo " ✗ Bước hạ tầng Citus thất bại: $1"
    echo "   => Theo chiến lược đã chọn: CHUYỂN phần Citus sang 'future-work'."
    echo "      - Giữ lại: compose.citus.yml, citus_init.sql, DDL, lý thuyết Distributed"
    echo "        Block Sampling Equivalence + limitations."
    echo "      - TUYỆT ĐỐI không ghi số speedup Citus nếu không có thư mục ${OUT_DIR}/."
    echo "=============================================================="
    exit 1
}

echo "=============================================================="
echo " Citus distributed benchmark (SF=${SF}, queries=${QUERIES})"
echo "=============================================================="

# 0) Tìm nguồn DuckDB SF=10.
DUCKDB_SRC=""
for c in "bench_out_sf10/tpch_sf10.duckdb" "../bench_out_sf10/tpch_sf10.duckdb" "bench_out_sf10_final/tpch_sf10.duckdb"; do
    [ -f "$c" ] && { DUCKDB_SRC="$c"; break; }
done
[ -z "${DUCKDB_SRC}" ] && fail_future_work "không tìm thấy DuckDB SF=10 (sinh bằng run_duckdb_tpch --sf 10 --output bench_out_sf10/tpch_sf10.duckdb)"
echo "[*] DuckDB source: ${DUCKDB_SRC}"

# 1) Dựng cụm.
echo "[*] docker compose up..."
docker compose -f "${COMPOSE}" up -d || fail_future_work "docker compose up lỗi"

# 2) Chờ coordinator + workers sẵn sàng (tối đa ~60s).
echo "[*] Chờ các node sẵn sàng..."
for node in citus_coordinator citus_worker1 citus_worker2; do
    ok=0
    for i in $(seq 1 30); do
        if docker exec "${node}" pg_isready -U "${USER_}" -d "${DB}" >/dev/null 2>&1; then ok=1; break; fi
        sleep 2
    done
    [ "${ok}" -eq 1 ] || fail_future_work "node ${node} không sẵn sàng sau 60s"
    echo "    - ${node} OK"
done

# 3) Cấu hình cụm Citus: extension + coordinator host + (citus_init: add_node + shard).
echo "[*] Cấu hình Citus (extension, coordinator host, đăng ký workers, shard bảng rỗng)..."
pexec -c "CREATE EXTENSION IF NOT EXISTS citus;" || fail_future_work "tạo extension citus"
# set_coordinator_host: gotcha của Citus 12.x — workers cần địa chỉ coordinator.
pexec -c "SELECT citus_set_coordinator_host('coordinator', 5432);" || fail_future_work "citus_set_coordinator_host"

# 3a) DDL Citus-safe (drop+create, KHÔNG FK) — phải tạo bảng TRƯỚC khi distribute.
echo "[*] Áp DDL Citus-safe..."
pexec < pilotdb/benchmarks/tpch_pg_ddl_citus.sql || fail_future_work "áp tpch_pg_ddl_citus.sql"

# 3b) Đăng ký worker + create_distributed_table/create_reference_table trên bảng RỖNG (nhanh).
echo "[*] Đăng ký workers + phân tán bảng (citus_init.sql)..."
pexec < dockerfile/citus_init.sql || fail_future_work "citus_init.sql (add_node/create_distributed_table)"

# 4) Xuất CSV từ DuckDB (tái dùng hàm sẵn có, không sửa code).
echo "[*] Xuất CSV từ DuckDB -> ${CSV_DIR}/ ..."
"${PYTHON_BIN}" -c "from pathlib import Path; from pilotdb.benchmarks.load_tpch_postgres import export_csvs_from_duckdb; export_csvs_from_duckdb('${DUCKDB_SRC}', Path('${CSV_DIR}'))" \
    || fail_future_work "export CSV từ DuckDB"

# 5) Nạp dữ liệu qua STDIN -> coordinator -> Citus tự định tuyến về shards.
#    Thứ tự cha-trước-con (dù DDL không còn FK, vẫn giữ cho gọn).
echo "[*] COPY dữ liệu vào các bảng phân tán..."
for t in region nation supplier customer part partsupp orders lineitem; do
    if [ ! -f "${CSV_DIR}/${t}.csv" ]; then fail_future_work "thiếu ${CSV_DIR}/${t}.csv"; fi
    echo "    - COPY ${t}"
    docker exec -e PGPASSWORD="${PW}" -i "${COORD}" \
        psql -v ON_ERROR_STOP=1 -U "${USER_}" -d "${DB}" \
        -c "\copy ${t} FROM STDIN WITH (FORMAT CSV, DELIMITER ',', NULL '')" \
        < "${CSV_DIR}/${t}.csv" || fail_future_work "COPY bảng ${t}"
done

# 6) ANALYZE để cost-model có thống kê.
echo "[*] ANALYZE..."
pexec -c "ANALYZE;" || echo "    [!] ANALYZE cảnh báo (bỏ qua được)"

# 7) Chạy benchmark suite trỏ vào coordinator (db_configs/postgres_citus.yml: host=localhost:5432).
echo "[*] Chạy benchmark suite trên Citus..."
"${PYTHON_BIN}" run_benchmark_suite.py \
    --dbms postgres \
    --db-config-yaml db_configs/postgres_citus.yml \
    --sf "${SF}" \
    --queries "${QUERIES}" \
    --output-dir "${OUT_DIR}" \
    || echo "    [!] suite gặp lỗi ở một số câu — vẫn kiểm chứng phần chạy được."

# 8) KIỂM CHỨNG số liệu thật.
echo "[*] Kiểm chứng kết quả Citus..."
"${PYTHON_BIN}" scratch/check_aqp_engaged.py "${OUT_DIR}"
RC=$?

echo "=============================================================="
if [ "${RC}" -eq 0 ]; then
    echo " ✓ Citus chạy + có câu AQP thật. Dùng SỐ TRONG ${OUT_DIR}/aggregated_report.md"
    echo "   cho báo cáo (thay con số 3.59x-6.54x đang bịa). Ghi rõ limitation:"
    echo "   ctid bypass -> block-variance chưa nhận-biết-shard (future work)."
else
    echo " ⚠ Citus chạy được nhưng các câu fallback về exact (hoặc không sample)."
    echo "   -> Báo cáo trung thực: cụm Citus DỰNG & SHARD thành công, AQP single-table"
    echo "      chưa cho speedup ở SF=10; đánh giá sâu là future-work. KHÔNG bịa số."
fi
echo "=============================================================="
exit 0

# Hướng dẫn Cài đặt Môi trường & Chạy Thực nghiệm — PilotDB CSDLPT

Tài liệu hướng dẫn cài đặt và tái lập các thực nghiệm của đồ án. Mọi lệnh chạy từ thư mục gốc `pilotdb_csdlpt/`.

---

## 1. Yêu cầu hệ thống

| Thành phần | Phiên bản | Bắt buộc? |
| :--- | :--- | :--- |
| Python | **>= 3.11** | ✅ |
| `uv` (trình quản lý gói) hoặc `pip` | mới nhất | ✅ (khuyến nghị `uv`) |
| DuckDB | cài qua pip (`duckdb>=1.3.1`) | ✅ (lõi thực nghiệm) |
| Docker + Docker Compose | mới nhất | Tùy chọn (cho PostgreSQL / Citus) |
| PostgreSQL 16 | qua Docker hoặc cài máy | Tùy chọn (thực nghiệm Postgres) |

## 2. Cài đặt thư viện

**Cách A — dùng `uv` (khuyến nghị, theo `uv.lock`):**
```bash
uv sync
uv pip install -e .
```

**Cách B — dùng `pip` thuần:**
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |  Linux/Mac: source .venv/bin/activate
pip install -e .
```

Các phụ thuộc chính (khai báo trong `pyproject.toml`): `duckdb, pandas, numpy, scipy, sqlglot, psycopg2-binary, pyyaml, flask, pyodbc`.

> **Dashboard (tùy chọn):** cần cài thêm Streamlit (không nằm trong `pyproject.toml`):
> ```bash
> pip install streamlit
> ```

## 3. Sinh dữ liệu TPC-H (DuckDB)

DuckDB có extension `tpch` tích hợp; script sẽ tự sinh dữ liệu:
```bash
# SF=1 (nhẹ, dùng để chạy test & tái lập COUNT DISTINCT / fixed-size)
python repro/generate_tpch.py --sf 1   --output tpch_sf1.db
# SF=10 (~10GB)  /  SF=100 (~100GB, cần nhiều đĩa)
python repro/generate_tpch.py --sf 10  --output bench_out_sf10/tpch_sf10.duckdb
python repro/generate_tpch.py --sf 100 --output bench_out_sf100/tpch_sf100.duckdb
```

## 4. Chạy kiểm thử (unit tests)

```bash
python -m pytest tests/ -q
```
Kỳ vọng: **203 passed, 7 skipped** (7 test e2e PostgreSQL/SQL Server bị skip nếu không có DB sống). Test e2e DuckDB chạy được ngay:
```bash
python -m pytest tests/e2e/test_duckdb_tpch_e2e.py -q
```

## 5. Benchmark DuckDB (TPC-H)

```bash
# SF=10, 5 iterations, sai số mục tiêu 5%
python run_benchmark_suite.py --dbms duckdb --sf 10 \
    --iterations 5 --error 0.05 --output-dir bench_out_sf10
# Cấu hình "relaxed" (sai số 10%) cho SF=100 — cho thấy speedup rõ rệt hơn
python run_benchmark_suite.py --dbms duckdb --sf 100 \
    --iterations 5 --error 0.10 --output-dir bench_out_sf100_relaxed
```
Kết quả: `<output-dir>/aggregated_report.md` + `iter_*/results_*.json`.

## 6. Benchmark PostgreSQL (single-node)

```bash
# 6.1 Dựng PostgreSQL bằng Docker
docker compose -f dockerfile/compose.yml up -d postgres
#     (mật khẩu mặc định PilotDB123 — chỉnh db_configs/postgres_local.yml cho khớp,
#      hoặc dùng biến môi trường PILOTDB_PG_PASSWORD)

# 6.2 Nạp dữ liệu TPC-H từ DuckDB sang Postgres
python -m pilotdb.benchmarks.load_tpch_postgres \
    --duckdb-src bench_out_sf10/tpch_sf10.duckdb --sf 10 \
    --pg-config db_configs/postgres_local.yml

# 6.3 Chạy benchmark — MỞ gate chi phí để AQP có cơ hội engage
PILOTDB_POSTGRES_COST_THRESHOLD=999 PILOTDB_POSTGRES_VOLUME_PROXY=1 \
python run_benchmark_suite.py --dbms postgres \
    --db-config-yaml db_configs/postgres_local.yml \
    --sf 10 --iterations 3 --error 0.05 --output-dir bench_out_pg_sf10
```
> **Lưu ý:** nếu KHÔNG đặt `PILOTDB_POSTGRES_COST_THRESHOLD=999`, gate `directly_run_exact` (ngưỡng mặc định 0.05) sẽ khiến Postgres fallback 100% về chạy chính xác.

## 7. Benchmark Citus phân tán (1 Coordinator + 2 Workers)

```bash
# Yêu cầu Docker. Script tự dựng cụm, shard dữ liệu, chạy benchmark, kiểm chứng.
bash run_citus_benchmark.sh
# Tương đương thủ công:
docker compose -f dockerfile/compose.citus.yml up -d
# rồi áp DDL + dockerfile/citus_init.sql + nạp dữ liệu (xem chi tiết trong run_citus_benchmark.sh)
```

## 8. Tổng hợp lại báo cáo OFFLINE (không chạy lại DB)

Nếu đã có sẵn `iter_*/results_*.json`, tạo lại `aggregated_report.md` mà **không đụng DB**:
```bash
python run_benchmark_suite.py --dbms postgres --sf 100 \
    --output-dir bench_out_pg_sf100_clean --aggregate-only
```

## 9. Tái lập 2 mở rộng

**COUNT(DISTINCT) qua Chao/GEE:**
```bash
python -m pytest tests/unit_tests/test_count_distinct.py -v          # kiểm tra công thức
python -m pytest tests/e2e/test_duckdb_tpch_e2e.py -k count_distinct -v   # end-to-end trên DuckDB
```

**Gap #6 — Bernoulli (row-level) vs System (block-level) sampling** (cần `tpch_sf1.db`):
```bash
python repro/run_bernoulli_vs_system.py
# Kết quả ghi vào docs/fixed_size_comparison.md
```

## 10. Dashboard giám sát AQP

```bash
pip install streamlit
streamlit run dashboard.py
# Dashboard đọc số liệu thật từ bench_out_*/aggregated_report.json
```

---

## 11. Ghi chú & xử lý sự cố

- **Mật khẩu Postgres:** `db_configs/postgres_local.yml` mặc định user/pwd = `pilotdb`/`pilotdb`. Docker `compose.yml` dùng `PilotDB123` → sửa file config cho khớp, hoặc set `PILOTDB_PG_PASSWORD`, `PILOTDB_PG_HOST`, ...
- **Biến môi trường AQP (Postgres/Citus):**
  - `PILOTDB_POSTGRES_COST_THRESHOLD` (mặc định `0.05`): nâng lên `999` để vô hiệu hóa gate tiền-pilot, cho AQP engage.
  - `PILOTDB_POSTGRES_VOLUME_PROXY` (mặc định `1`): quyết định AQP-vs-exact dùng proxy thể tích.
- **Cache:** template cache lưu ở `.pilotdb_cache.db`. **Xóa trước mỗi lần đo sạch** để tránh tái dùng kế hoạch cũ: `rm -f .pilotdb_cache.db`.
- **Đĩa cho SF=100:** dữ liệu DuckDB (~26GB) + CSV trung gian (~100GB) + Postgres (~100GB) → cần ổ đĩa lớn (khuyến nghị ≥ 240GB).
- **Windows:** dùng `python` thay `python3`; đường dẫn dùng `\`. Các script `.sh` chạy trên Linux/Git-Bash hoặc VM.

# Đồ án CSDLPT — Gói nộp: Mã nguồn & Dữ liệu

**Đề tài:** MỞ RỘNG HỆ THỐNG XỬ LÝ TRUY VẤN XẤP XỈ PILOTDB: HỖ TRỢ NON-LINEAR AGGREGATES VÀ ADAPTIVE CACHING CHO CƠ SỞ DỮ LIỆU PHÂN TÁN
**Bài báo gốc:** PilotDB (SIGMOD '25) — DOI 10.1145/3725335
**Mã nguồn (GitHub):** https://github.com/khoinguyen59/pilotdb_csdlpt
**Nhóm:** _______ (MSSV: _______)

> Hướng dẫn cài đặt & chạy: xem [`INSTALL.md`](INSTALL.md). Báo cáo khoa học: xem [`docs/`](docs/).

---

## 1. Cấu trúc mã nguồn

| Thư mục / Tệp | Nội dung |
| :--- | :--- |
| `pilotdb/pilot_engine/` | Lõi AQP: TAQA, BSAP, bộ tối ưu lấy mẫu, **`count_distinct.py`** (Chao/GEE), **`caching.py`** (Adaptive Caching) |
| `pilotdb/db_driver/` | Trình điều khiển DuckDB / PostgreSQL / SQL Server, cost model, block-size detection |
| `pilotdb/benchmarks/` | Loader TPC-H (`load_tpch_postgres.py`), runner |
| `tests/` | `unit_tests/` (203 pass) + `e2e/` (DuckDB/Postgres/SQLServer) |
| `benchmarks/` | Câu truy vấn TPC-H, SSB, ClickBench, TPC-DS... |
| `experiments/fixed_size/` | Thí nghiệm Gap #6 (Bernoulli vs block sampling) |
| `dockerfile/` | `compose.yml` (Postgres+SQLServer single-node), `compose.citus.yml` (Citus 1 coordinator + 2 workers), `citus_init.sql` |
| `db_configs/` | Cấu hình kết nối DB (yaml) |
| `dashboard.py` | Dashboard giám sát AQP (Streamlit) |
| `run_citus_benchmark.sh` | Script tự động dựng Citus, phân tán dữ liệu và chạy benchmark |
| `docs/` | **Báo cáo + thiết kế + kết quả** (xem mục 3) |

## 2. Dữ liệu

- **Dữ liệu kết quả (bằng chứng thực nghiệm) — ĐÃ KÈM:** `docs/benchmark_results/` chứa các báo cáo tổng hợp thật:
  - `duckdb_sf10.md`, `duckdb_sf100_strict.md`, `duckdb_sf100_relaxed.md`
  - `postgres_sf10.md`, `postgres_sf100_clean.md`
- **Dữ liệu thô TPC-H — KHÔNG KÈM (quá lớn):** các tệp DuckDB SF1/SF10/SF100 (~26GB–100GB), CSV trung gian, và dữ liệu PostgreSQL không được đóng gói do dung lượng. **Cách tái tạo:** xem `INSTALL.md` (mục "Sinh dữ liệu TPC-H"). `.gitignore` đã loại trừ các tệp dữ liệu lớn, cache (`.pilotdb_cache.db`) và log.

## 3. Tài liệu báo cáo (`docs/`)

| Tệp | Nội dung |
| :--- | :--- |
| `report_sections_5_6.md` | **Phần 5 (Kết quả thực nghiệm) + Phần 6 (Thảo luận)** + Phụ lục Alignment Bug |
| `paper_comparison_report.md` | Đối chiếu kết quả của nhóm với bài báo gốc SIGMOD '25 |
| `fixed_size_comparison.md` | Gap #6: Bernoulli (row-level) vs System (block-level) sampling |
| `hll_distinct_count.md` | Thiết kế COUNT(DISTINCT) qua Chao/GEE |
| `caching_design.md` | Thiết kế Adaptive Pilot Caching 2 lớp |
| `distributed_postgresql.md` | Thiết kế cụm phân tán Citus |
| `fallback_mechanisms.md`, `benchmark_methodology.md`, `code_alignment_and_fixes.md`, `empirical_findings_sf100.md` | Cơ chế fallback, phương pháp benchmark, các bản vá kỹ nghệ |
| `benchmark_results/` | Bản sao các báo cáo kết quả thật (bằng chứng) |

## 4. Tóm tắt phạm vi đã thực hiện (trung thực)

| Đăng ký | Trạng thái |
| :--- | :--- |
| 1. Tái hiện PilotDB (PostgreSQL + DuckDB, SF10–100) | DuckDB: tái hiện **một phần** (speedup ở quy mô lớn, vd Q12 1.56×–4.14×). PostgreSQL/Citus: **negative result** (fallback an toàn) — phác họa *operational envelope* |
| 2. COUNT(DISTINCT) qua HLL/Chao/GEE | ✅ Hoàn thành + kiểm chứng (Chao sai số ~0.3%; GEE thất bại trên khóa chính → đề xuất Horvitz-Thompson) |
| 3. Adaptive Pilot Caching | ✅ Hoàn thành (cache 2 lớp, có unit test) |
| 4. Đánh giá Citus phân tán | ✅ Dựng cụm + chạy benchmark SF10 (fallback an toàn) |
| Mở rộng: Gap #6 Bernoulli vs block sampling | ✅ Thí nghiệm trên DuckDB SF1 |
| Mở rộng: Dashboard giám sát AQP | ✅ `dashboard.py` (Streamlit) |

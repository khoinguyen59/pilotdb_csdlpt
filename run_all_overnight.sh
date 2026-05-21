#!/bin/bash
# -----------------------------------------------------------------------------
# TPC-H Overnight Benchmark Automation Script
# Preserves VPS state but auto-shutdowns at completion to optimize costs.
# -----------------------------------------------------------------------------

# Print all commands to stdout/stderr for log visibility
set -x

# Define variables
REPO_DIR="/home/khoitaikhoan2/pilotdb_csdlpt"
PYTHON_BIN="${REPO_DIR}/venv/bin/python"
LOG_FILE="/home/khoitaikhoan2/overnight.log"

cd "${REPO_DIR}"

echo "============================================================" >> "${LOG_FILE}"
echo "===== OVERNIGHT BENCHMARK RUN STARTED AT $(date) =====" >> "${LOG_FILE}"
echo "============================================================" >> "${LOG_FILE}"

# -----------------------------------------------------------------------------
# STEP 0: System updates & PostgreSQL Installation
# -----------------------------------------------------------------------------
echo "[$(date)] Installing PostgreSQL..." >> "${LOG_FILE}"
sudo apt-get update >> "${LOG_FILE}" 2>&1
sudo apt-get install -y postgresql postgresql-contrib >> "${LOG_FILE}" 2>&1
sudo systemctl start postgresql >> "${LOG_FILE}" 2>&1

# -----------------------------------------------------------------------------
# STEP 1: Configure PostgreSQL User and Database
# -----------------------------------------------------------------------------
echo "[$(date)] Configuring PostgreSQL credentials..." >> "${LOG_FILE}"
# Re-create database and user to guarantee fresh, clean state matching postgres_local.yml
sudo -u postgres psql -c "DROP DATABASE IF EXISTS tpch;" >> "${LOG_FILE}" 2>&1
sudo -u postgres psql -c "DROP USER IF EXISTS pilotdb;" >> "${LOG_FILE}" 2>&1
sudo -u postgres psql -c "CREATE USER pilotdb WITH PASSWORD 'pilotdb' SUPERUSER;" >> "${LOG_FILE}" 2>&1
sudo -u postgres psql -c "CREATE DATABASE tpch OWNER pilotdb;" >> "${LOG_FILE}" 2>&1

# -----------------------------------------------------------------------------
# RUN 1: PostgreSQL SF=10 Benchmark
# -----------------------------------------------------------------------------
echo "[$(date)] Run 1: PostgreSQL SF=10 Benchmark" >> "${LOG_FILE}"

# 1a. Generate SF=10 DuckDB file (source for Postgres loader)
echo "Generating TPC-H SF=10 DuckDB..." >> "${LOG_FILE}"
PYTHONPATH=. "${PYTHON_BIN}" scratch/generate_tpch.py \
    --sf 10 \
    --output bench_out_sf10/tpch_sf10.duckdb >> "${LOG_FILE}" 2>&1

# 1b. Load PostgreSQL SF=10
echo "Loading TPC-H SF=10 into PostgreSQL..." >> "${LOG_FILE}"
PYTHONPATH=. "${PYTHON_BIN}" -m pilotdb.benchmarks.load_tpch_postgres \
    --duckdb-src bench_out_sf10/tpch_sf10.duckdb \
    --sf 10 \
    --if-exists skip \
    --pg-config db_configs/postgres_local.yml >> "${LOG_FILE}" 2>&1

# 1c. Run Benchmark Suite on PostgreSQL
echo "Running PostgreSQL SF=10 suite (5 iterations)..." >> "${LOG_FILE}"
PYTHONPATH=. "${PYTHON_BIN}" run_benchmark_suite.py \
    --dbms postgres \
    --db-config-yaml db_configs/postgres_local.yml \
    --sf 10 \
    --iterations 5 \
    --output-dir bench_out_pg_sf10 >> "${LOG_FILE}" 2>&1

echo "[$(date)] Run 1 (Postgres SF=10) completed." >> "${LOG_FILE}"

# -----------------------------------------------------------------------------
# RUN 2: DuckDB SF=100 Relaxed Config Benchmark
# -----------------------------------------------------------------------------
echo "[$(date)] Run 2: DuckDB SF=100 Relaxed Configuration Benchmark" >> "${LOG_FILE}"

# Ensure relaxed output directory exists and symbolic link to SF=100 DB is created
mkdir -p bench_out_sf100_relaxed
if [ -f bench_out_sf100_final/tpch_sf100.duckdb ]; then
    echo "Creating symlink to existing SF=100 DuckDB database..." >> "${LOG_FILE}"
    ln -sf "${REPO_DIR}/bench_out_sf100_final/tpch_sf100.duckdb" "${REPO_DIR}/bench_out_sf100_relaxed/tpch_sf100.duckdb"
else
    echo "WARNING: bench_out_sf100_final/tpch_sf100.duckdb not found. Re-generating SF=100 DuckDB..." >> "${LOG_FILE}"
    PYTHONPATH=. "${PYTHON_BIN}" scratch/generate_tpch.py \
        --sf 100 \
        --output bench_out_sf100_relaxed/tpch_sf100.duckdb >> "${LOG_FILE}" 2>&1
fi

# Run Benchmark Suite on DuckDB SF=100 with error=10%, failure_prob=5%
echo "Running DuckDB SF=100 Relaxed suite (5 iterations)..." >> "${LOG_FILE}"
PYTHONPATH=. "${PYTHON_BIN}" run_benchmark_suite.py \
    --dbms duckdb \
    --sf 100 \
    --iterations 5 \
    --error 0.10 \
    --failure-prob 0.05 \
    --output-dir bench_out_sf100_relaxed >> "${LOG_FILE}" 2>&1

echo "[$(date)] Run 2 (DuckDB SF=100 relaxed) completed." >> "${LOG_FILE}"

# -----------------------------------------------------------------------------
# RUN 3 (OPTIONAL): DuckDB SF=300 Benchmark
# -----------------------------------------------------------------------------
echo "[$(date)] Run 3 (OPTIONAL): DuckDB SF=300 Benchmark" >> "${LOG_FILE}"
# To enable this run, uncomment the lines below.
# Note: TPC-H SF=300 requires ~75GB disk space and 16GB+ RAM.
#
# echo "Generating TPC-H SF=300 DuckDB..." >> "${LOG_FILE}"
# PYTHONPATH=. "${PYTHON_BIN}" scratch/generate_tpch.py \
#     --sf 300 \
#     --output bench_out_sf300/tpch_sf300.duckdb >> "${LOG_FILE}" 2>&1
#
# echo "Running DuckDB SF=300 suite (3 iterations)..." >> "${LOG_FILE}"
# PYTHONPATH=. "${PYTHON_BIN}" run_benchmark_suite.py \
#     --dbms duckdb \
#     --sf 300 \
#     --iterations 3 \
#     --output-dir bench_out_sf300 >> "${LOG_FILE}" 2>&1
#
echo "Run 3 (DuckDB SF=300) skipped." >> "${LOG_FILE}"

# -----------------------------------------------------------------------------
# RUN 4 (OPTIONAL): PostgreSQL SF=100 Benchmark
# -----------------------------------------------------------------------------
echo "[$(date)] Run 4 (OPTIONAL): PostgreSQL SF=100 Benchmark" >> "${LOG_FILE}"
# To enable this run, uncomment the lines below.
# Note: Loading TPC-H SF=100 into PostgreSQL takes significant time (~1-2 hours) and space.
#
# if [ -f bench_out_sf100_final/tpch_sf100.duckdb ]; then
#     echo "Loading TPC-H SF=100 into PostgreSQL..." >> "${LOG_FILE}"
#     PYTHONPATH=. "${PYTHON_BIN}" -m pilotdb.benchmarks.load_tpch_postgres \
#         --duckdb-src bench_out_sf100_final/tpch_sf100.duckdb \
#         --sf 100 \
#         --if-exists skip \
#         --pg-config db_configs/postgres_local.yml >> "${LOG_FILE}" 2>&1
#
#     echo "Running PostgreSQL SF=100 suite (5 iterations)..." >> "${LOG_FILE}"
#     PYTHONPATH=. "${PYTHON_BIN}" run_benchmark_suite.py \
#         --dbms postgres \
#         --db-config-yaml db_configs/postgres_local.yml \
#         --sf 100 \
#         --iterations 5 \
#         --output-dir bench_out_pg_sf100 >> "${LOG_FILE}" 2>&1
# fi
#
echo "Run 4 (Postgres SF=100) skipped." >> "${LOG_FILE}"

# -----------------------------------------------------------------------------
# COMPLETION & AUTO-SHUTDOWN
# -----------------------------------------------------------------------------
echo "============================================================" >> "${LOG_FILE}"
echo "===== ALL BENCHMARK RUNS COMPLETED AT $(date) =====" >> "${LOG_FILE}"
echo "Shutting down the VM in 5 minutes to prevent idle billing..." >> "${LOG_FILE}"
echo "============================================================" >> "${LOG_FILE}"

sleep 300
sudo shutdown -h now

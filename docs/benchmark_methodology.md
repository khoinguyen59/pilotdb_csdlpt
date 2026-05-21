# PilotDB Benchmark Methodology & Experimental Setup

This document describes the benchmarking methodology, query coverage, performance metrics, and system configuration used to evaluate the PilotDB approximate query processing (AQP) engine.

---

## 1. Experimental Setup

The benchmarks are executed on a dedicated Google Compute Engine (GCE) Virtual Machine instance with the following specifications:

- **Compute**: 4 vCPUs (Intel Haswell architecture or newer)
- **Memory**: 16 GB RAM
- **Storage**: Balanced Persistent Disk (NVMe SSD storage backed)
- **Operating System**: Linux Ubuntu 22.04 LTS
- **Database Management System**: DuckDB v1.5.3 (embedded in-process engine)
- **Environment**: Python 3.11 with Virtualenv configuration

### TPC-H Scale Factors
1. **SF=10**: Evaluated locally and on VPS to establish safety baseline and check for fallback boundary behaviors. (Total dataset size ~10GB raw, database size ~3.5GB).
2. **SF=100**: Evaluated on VPS to measure publishable speedup and performance metrics under large-scale workloads. (Total database size ~25GB compressed DuckDB storage, representing ~100GB raw data).

---

## 2. Evaluation Metrics

For each query, the following metrics are captured across **5 iterations**:

1. **Exact Query Time ($T_{\text{exact}}$)**: The execution time (in seconds) of the query run directly on the database without sampling.
2. **AQP Query Time ($T_{\text{aqp}}$)**: The total execution time of the AQP flow, which includes:
   - SQL analysis and rewriting overhead.
   - The pilot query execution (run at `pilot_sample_rate` = 1%).
   - Statistics aggregation and rate optimization solver computation.
   - The final sampling query execution (run at the optimized sampling rate `final_sample_rate`).
3. **Speedup Factor**: Computed as:
   $$\text{Speedup} = \frac{T_{\text{exact}}}{T_{\text{aqp}}}$$
   - A speedup $> 1.0$ indicates that AQP was faster than the exact execution.
   - A speedup $< 1.0$ indicates that the pilot query overhead or fallback mechanism made AQP slower than exact execution.
4. **Fallback Rate**: The percentage of iterations that resorted to running the exact query because the AQP plan was deemed unsafe or too expensive.
5. **Mean Group Relative Error**: The average relative error across all matching rows between the AQP approximate result and the Exact result. Under safe AQP execution, this must be $\le \text{target\_error\_limit}$ (default: 5%).

---

## 3. Query Suite Coverage

The suite targets **12 representative TPC-H queries** covering various structural patterns (joins, groupings, filtering):

| Query | Category | Grouping Columns | AQP Feasibility Notes |
| :--- | :--- | :--- | :--- |
| **Q1** | Single-table aggregation | `l_returnflag`, `l_linestatus` | High speedup potential via sampling. |
| **Q3** | Multi-table join | `o_orderkey`, `o_orderdate`, `o_shippriority` | Joins lineitem, orders, customer. |
| **Q5** | Multi-table join | `n_name` | Join across 6 tables; highly sensitive to sampling variance. |
| **Q6** | Single-table scan | None (single group) | Simple aggregate filter scan. |
| **Q7** | Multi-table join | `supp_nation`, `cust_nation`, `l_year` | Join across 6 tables; extracts year from date. |
| **Q8** | Multi-table join | `o_year` | Join across 8 tables; extracts year. |
| **Q9** | Multi-table join | `nation`, `o_year` | Multi-column group-by; complex join of 6 tables. |
| **Q10** | Multi-table join | `c_custkey`, `c_name`, `c_acctbal`, `c_phone`, `n_name`, `c_address`, `c_comment` | Multi-column grouping; joins 4 tables. |
| **Q12** | Multi-table join | `l_shipmode` | Join lineitem and orders. |
| **Q14** | Multi-table join | None (single group) | Computes promotional revenue ratio. |
| **Q18** | Multi-table join | `c_name`, `c_custkey`, `o_orderkey`, `o_orderdate`, `o_totalprice` | Multi-column group-by with subquery. |
| **Q19** | Multi-table join | None (single group) | Join with complex OR predicates. |

---

## 4. Run Parameters

The system optimizer runs with the following configurations:
- **`target_error_limit` ($\epsilon$)**: `0.05` (representing a maximum allowable relative error of 5%).
- **`confidence_level` ($1 - \delta$)**: `0.95` (95% statistical confidence).
- **`pilot_sample_rate`**: `0.01` (1% sample rate for the pilot pass).
- **`max_sample_rate`**: `0.10` (maximum allowable sample rate for AQP is capped at 10% to prevent high-cost sampling plans).
- **`block_size`**: Dynamic auto-detection (typically 262,144 bytes / 256KB for DuckDB).

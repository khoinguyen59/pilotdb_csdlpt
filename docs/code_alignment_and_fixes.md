# PilotDB Code-to-Paper Alignment & Bug Resolution

This document records the mathematical alignment between the PilotDB research paper and the source code implementation, along with details of the bugs identified and fixed during code audits.

---

## 1. Mathematical Mapping

The core logic of PilotDB is derived from the academic paper. Below is the mapping of the key formulas and bounds to specific functions and variables in the Python codebase:

| Equation / Formula | Academic Context | Code Symbol & File Location |
| :--- | :--- | :--- |
| **Lemma 4.8 (Variance Bound)** | Multi-table join variance upper bound calculation. | [join_variance.py](file:///C:/Users/Nguyen%20Trong%20Khoi/Downloads/CSDLPT_DA/pilotdb_csdlpt/pilotdb/pilot_engine/join_variance.py#L12) - `compute_join_variance_bounds()` |
| **Equation (3) (Pilot Statistics)** | Computing pilot stats (sums, squared sums, block sizes). | [multi_table_sampling.py](file:///C:/Users/Nguyen%20Trong%20Khoi/Downloads/CSDLPT_DA/pilotdb_csdlpt/pilotdb/pilot_engine/multi_table_sampling.py#L32) - `estimate_pilot_statistics()` |
| **Optimizer Objective** | Minimize aggregate sampling cost subject to error limits. | [optimizer.py](file:///C:/Users/Nguyen%20Trong%20Khoi/Downloads/CSDLPT_DA/pilotdb_csdlpt/pilotdb/pilot_engine/optimizer.py#L82) - `optimize_sample_rates()` |
| **Standard Error Guarantee** | $\text{Error} \le \Theta \cdot \sigma$ (standard deviation constraint). | [error_bounds.py](file:///C:/Users/Nguyen%20Trong%20Khoi/Downloads/CSDLPT_DA/pilotdb_csdlpt/pilotdb/pilot_engine/error_bounds.py#L40) - `verify_error_bounds()` |
| **Aggregate Variance** | $\sigma^2$ for simple aggregation queries. | [join_variance.py](file:///C:/Users/Nguyen%20Trong%20Khoi/Downloads/CSDLPT_DA/pilotdb_csdlpt/pilotdb/pilot_engine/join_variance.py#L80) - `compute_aggregate_variance()` |

---

## 2. Bug Discoveries & Resolutions

### 2.1. The GROUP BY Alignment Bug (Critical Logic Bug)

#### Root Cause Analysis
In the original implementation of `compute_detailed_group_errors` in [run_duckdb_tpch.py](file:///C:/Users/Nguyen%20Trong%20Khoi/Downloads/CSDLPT_DA/pilotdb_csdlpt/pilotdb/benchmarks/run_duckdb_tpch.py), the relative error between AQP and Exact results was computed using Python dictionary lookups. However:
- The keys used for dictionary lookups were strings like `"nation"`.
- For queries involving multi-column groupings or multi-year evaluations (e.g., Q9 grouping by `nation` and `year`, resulting in 175 rows: 25 nations $\times$ 7 years), a simple dictionary lookup mapped a 25-entry dictionary against a 175-row dataframe.
- This mismatch caused lookup failures, resulting in the code incorrectly computing a high relative error (e.g. 5.9% to 46.9%) for *identical* datasets. This falsely triggered AQP fallback to exact queries even when AQP was 100% correct.

#### Resolution
We refactored the group error calculation to utilize AST parsing via `sqlglot`.
1. **Query-Specific GROUP BY Key Extraction**:
   We added a helper function `_extract_group_by_columns(sql)` to parse the SQL statement and identify the precise grouping columns:
   ```python
   def _extract_group_by_columns(sql_str: str) -> list[str]:
       try:
           import sqlglot
           parsed = sqlglot.parse_one(sql_str)
           group_exprs = parsed.find(sqlglot.exp.Group)
           if group_exprs:
               cols = []
               for expr in group_exprs.expressions:
                   if isinstance(expr, sqlglot.exp.Column):
                       cols.append(expr.name)
                   elif isinstance(expr, sqlglot.exp.Alias):
                       cols.append(expr.alias)
               return cols
       except Exception:
           pass
       return []
   ```
2. **Defensive Cleanup**:
   We added `_clean_columns()` to strip index, unnamed, or metadata columns from both dataframes prior to comparison.
3. **Precise DataFrame Alignment**:
   Instead of using dictionary lookups, we merge the AQP and Exact dataframes on the extracted GROUP BY columns and calculate the row-by-row relative difference. This guarantees mathematical alignment.

---

### 2.2. The Aggregation Report Display Bug (Off-by-100 Factor)

#### Root Cause Analysis
In [run_benchmark_suite.py](file:///C:/Users/Nguyen%20Trong%20Khoi/Downloads/CSDLPT_DA/pilotdb_csdlpt/run_benchmark_suite.py), the output display and markdown reporter printed the `mean_final_sample_rate_pct` as a raw fraction (e.g., `0.01` or `1.00`), while the text header and column name implied it was a percentage (`%`). This created inconsistencies where a sample rate of `100.00%` was displayed as `1.00%`.

#### Resolution
We corrected the reporter output formatting to multiply the fractional sample rate by `100.0` inside `run_benchmark_suite.py` before formatting, ensuring that `0.01` is printed as `1.00%` and `1.0` is printed as `100.00%`.

---

## 3. Regression Test Coverage

To ensure that future changes do not reintroduce these alignment and group-error bugs, we added a comprehensive suite of unit tests:

1. **`test_identical_dfs_produce_zero_group_error`**:
   - Parameterized across 16 TPC-H queries.
   - Verifies that comparing identical datasets yields exactly `0.0` error under all query grouping configurations.
2. **`test_block_size_detection`**:
   - Verifies the auto-detection of database page size and block sizes under various system and driver settings.
3. **`test_lemma_48_montecarlo`**:
   - Validates that the estimated variance bounds consistently bound the empirical variance generated by Monte Carlo simulations.

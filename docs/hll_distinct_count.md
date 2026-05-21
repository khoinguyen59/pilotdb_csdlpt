# COUNT DISTINCT (Non-linear Aggregate) Support via HLL & Block Sampling

This document details the design and mathematical formulation for extending PilotDB to support **non-linear aggregates**, specifically `COUNT(DISTINCT column)`, which is currently unsupported (as described in Paper §2.3).

---

## 1. The Challenge of COUNT(DISTINCT) under Sampling

Unlike linear aggregates (`SUM`, `COUNT`) where the global value scales linearly with the sampling rate ($Y \approx y / p$), the number of distinct values ($D$) does not scale linearly.
- If $D$ is small and values are uniform, a small sample will contain almost all distinct values (linear scaling by $1/p$ would catastrophically overestimate).
- If $D$ is large (e.g., primary keys), the sample distinct count will scale almost linearly with the sample size (linear scaling by $1/p$ would be correct).

Therefore, simple linear division by the sample rate yields high errors. PilotDB requires a sophisticated estimator that combines **HyperLogLog (HLL) sketches** with **Block Frequency Estimators**.

---

## 2. Mathematical Estimator Design

To estimate the global distinct count $D$ from a sample with rate $p$, we implement a hybrid estimator using:
1. **Chao's Estimator** (for low-duplicity datasets)
2. **Generalized Jackknife (GEE) / Horvitz-Thompson Estimator** (for high-duplicity datasets)
3. **HyperLogLog (HLL) Sketch Cardinality** $d$ computed directly on the sample.

### Chao's Estimator Formula
Let:
- $d$ = number of distinct values observed in the sample (computed using HLL `approx_count_distinct` on the sample).
- $f_1$ = number of values that appear exactly once in the sample (singletons).
- $f_2$ = number of values that appear exactly twice in the sample (doubletons).

The estimated total distinct count $D_{\text{Chao}}$ is:
$$D_{\text{Chao}} = d + \frac{f_1^2}{2 f_2}$$

### Horvitz-Thompson & GEE Estimator
When $f_2 = 0$ or the duplicity is very high, we fall back to Charikar's GEE estimator:
$$D_{\text{GEE}} = d + f_1 \sqrt{\frac{1}{p}} \left( 1 - p \right)$$

---

## 3. Query Rewriting and Execution Integration

The implementation spans both the SQL Rewriter and the execution engine:

```
      [Input SQL: COUNT(DISTINCT col)]
                     │
                     ▼
          [validate_supported_query]
           Allow COUNT(DISTINCT)
                     │
                     ▼
             [Query Rewriter]
     Rewrite to approx_count_distinct(col)
                     │
                     ▼
             [Execute AQP Path]
  1. Run Pilot Query to get pilot d, f1, f2
  2. Compute optimal sample rate p
  3. Execute final sampling query
  4. Apply Chao/GEE scale-up formula on AQP result
```

### 3.1 Rewriter Changes (`pilot.py` and `sampling.py`)
We modify `validate_supported_query` to allow `COUNT(DISTINCT)` and mark it as a non-linear target.
In `Sampling_Rewriter.add_sample_rate`, we rewrite:
- `COUNT(DISTINCT col)` $\to$ `approx_count_distinct(col)` (if DuckDB) or `count(distinct col)` (if Postgres/SQLServer).
- Crucially, we **do not** divide the aggregate by `{sample_rate}` directly in the SQL for COUNT DISTINCT. Instead, we keep the raw aggregate and scale it in python post-processing.

### 3.2 Execution Changes (`execute.py`)
In the post-processing phase of `execute_aqp`, if a `COUNT(DISTINCT)` aggregate is present:
1. We run a helper query on the sample to estimate the frequency of frequencies ($f_1$ and $f_2$).
   - Helper query template:
     ```sql
     SELECT cnt, count(*) as freq 
     FROM (SELECT col, count(*) as cnt FROM sample_table GROUP BY col) 
     WHERE cnt <= 2 GROUP BY cnt;
     ```
2. We compute $D_{\text{est}}$ using the Chao/GEE formulas.
3. We return $D_{\text{est}}$ as the approximate query result.

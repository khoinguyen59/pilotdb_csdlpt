# Fixed-size vs Bernoulli Sampling Performance Comparison (DuckDB SF=1)

This report compares the average execution times for the exact query vs two sampling techniques on DuckDB:
- **Exact**: Direct query execution.
- **Fixed-size (ORDER BY RANDOM)**: Fixed-size random rows sampling using `ORDER BY RANDOM() LIMIT {sample_size}`.
- **Bernoulli (TABLESAMPLE system)**: Block/system sampling using `TABLESAMPLE system({sample_rate}%)`.

| Query | Exact Time (s) | Fixed-size Time (s) | Bernoulli Time (s) | Speedup (Fixed-size) | Speedup (Bernoulli) |
|---|---|---|---|---|---|
| **Q1** | 0.6859s | 0.7081s | 0.6460s | 0.97x | 1.06x |
| **Q5** | 0.6990s | 0.8719s | 0.8047s | 0.80x | 0.87x |
| **Q6** | 0.6935s | 0.7581s | 0.7032s | 0.91x | 0.99x |
| **Q7** | 0.7628s | 0.8770s | 0.8739s | 0.87x | 0.87x |
| **Q8** | 1.1866s | 1.8183s | 1.4176s | 0.65x | 0.84x |
| **Q9** | 0.8271s | 1.0328s | 4.6188s | 0.80x | 0.18x |
| **Q12** | 0.7229s | 0.7544s | 0.7046s | 0.96x | 1.03x |
| **Q14** | 0.7130s | 0.6837s | 0.6966s | 1.04x | 1.02x |
| **Q19** | 0.7343s | 1.7492s | 0.7482s | 0.42x | 0.98x |
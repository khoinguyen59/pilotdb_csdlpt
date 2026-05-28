# Aggregated Benchmark Report
- **Scale Factor (SF)**: 10
- **Pilot Sample Rate**: 1.0%
- **Iterations**: 5 (deterministic seeds 42 to 46)

> [!NOTE]
> Threshold 10% được dùng cho assertion CI/CD vì SF=1 quá nhỏ để hit 5% một cách ổn định (sampling variance lớn trên dataset nhỏ). Trên SF=10, expect mean_row_relative_error sẽ về quanh 5%.

## Summary table

| Query | Exact Time (s) | AQP Time (s) | Speedup | Final Sample Rate | Fallback Rate | Mean Row Error | Max Row Error | Missing Groups |
|---|---|---|---|---|---|---|---|---|
| **Q1** | 21.751s ±1.258s | 24.335s ±6.555s | 0.93x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q3** | 8.650s ±1.757s | 8.471s ±1.436s | 1.02x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q5** | 10.730s ±8.022s | 9.321s ±7.723s | 1.22x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q6** | 2.752s ±0.087s | 2.768s ±0.163s | 1.00x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q7** | 6.484s ±1.499s | 6.399s ±1.245s | 1.01x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q8** | 11.005s ±11.775s | 10.260s ±10.126s | 1.01x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q9** | 24.061s ±0.921s | 23.609s ±0.191s | 1.02x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q10** | 8.913s ±3.546s | 8.915s ±3.467s | 1.00x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q12** | 5.971s ±0.554s | 6.011s ±0.618s | 0.99x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q14** | 2.950s ±0.030s | 3.011s ±0.045s | 0.98x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q18** | 49.186s ±2.788s | 49.253s ±2.564s | 1.00x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q19** | 4.918s ±1.271s | 4.932s ±1.275s | 1.00x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |

## Analysis and Assertions

- **Overall Fallback Rate**: 100.00% (60/60 runs)
  > [!WARNING]
  > **High Fallback Rate Alert**: The overall fallback rate is 100.00%, which exceeds the 30% quality threshold.
  > This indicates that AQP is frequently reverting to exact execution under SF=1 small-scale variance.

- **Q1** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'optimizer_infeasible']`
- **Q3** had 5/5 fallbacks. Reasons: `['directly_run_exact']`
- **Q5** had 5/5 fallbacks. Reasons: `['directly_run_exact']`
- **Q6** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'optimizer_infeasible']`
- **Q7** had 5/5 fallbacks. Reasons: `['directly_run_exact']`
- **Q8** had 5/5 fallbacks. Reasons: `['execute_aqp_recover']`
- **Q9** had 5/5 fallbacks. Reasons: `['directly_run_exact']`
- **Q10** had 5/5 fallbacks. Reasons: `['directly_run_exact']`
- **Q12** had 5/5 fallbacks. Reasons: `['directly_run_exact']`
- **Q14** had 5/5 fallbacks. Reasons: `['execute_aqp_recover']`
- **Q18** had 5/5 fallbacks. Reasons: `['execute_aqp_recover']`
- **Q19** had 5/5 fallbacks. Reasons: `['directly_run_exact']`
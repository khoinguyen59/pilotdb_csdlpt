# Aggregated Benchmark Report
- **Scale Factor (SF)**: 10
- **Pilot Sample Rate**: 1.0%
- **Iterations**: 5 (deterministic seeds 42 to 46)

> [!NOTE]
> Threshold 10% được dùng cho assertion CI/CD vì SF=1 quá nhỏ để hit 5% một cách ổn định (sampling variance lớn trên dataset nhỏ). Trên SF=10, expect mean_row_relative_error sẽ về quanh 5%.

## Summary table

| Query | Exact Time (s) | AQP Time (s) | Speedup | Final Sample Rate | Fallback Rate | Mean Row Error | Max Row Error | Missing Groups |
|---|---|---|---|---|---|---|---|---|
| **Q1** | 2.365s ±0.285s | 2.982s ±0.504s | 0.80x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q3** | 1.490s ±0.273s | 1.973s ±0.239s | 0.76x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q5** | 1.487s ±0.154s | 1.905s ±0.311s | 0.79x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q6** | 0.768s ±0.021s | 0.895s ±0.107s | 0.87x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q7** | 1.474s ±0.254s | 1.746s ±0.287s | 0.85x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q8** | 1.392s ±0.201s | 1.705s ±0.236s | 0.82x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q9** | 4.679s ±0.655s | 22.357s ±3.055s | 0.21x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q10** | 2.995s ±0.303s | 6.899s ±2.004s | 0.46x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q12** | 1.865s ±0.468s | 2.124s ±0.300s | 0.87x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q14** | 1.475s ±0.233s | 1.591s ±0.222s | 0.93x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q18** | 3.673s ±0.469s | 6.855s ±0.972s | 0.54x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q19** | 2.222s ±0.263s | 2.590s ±0.538s | 0.88x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |

## Analysis and Assertions

- **Overall Fallback Rate**: 100.00% (60/60 runs)
  > [!WARNING]
  > **High Fallback Rate Alert**: The overall fallback rate is 100.00%, which exceeds the 30% quality threshold.
  > This indicates that AQP is frequently reverting to exact execution under SF=1 small-scale variance.

- **Q1** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q3** had 5/5 fallbacks. Reasons: `['multi_table_no_phi']`
- **Q5** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q6** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q7** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q8** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q9** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q10** had 5/5 fallbacks. Reasons: `['multi_table_no_phi']`
- **Q12** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q14** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q18** had 5/5 fallbacks. Reasons: `['multi_table_no_phi']`
- **Q19** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
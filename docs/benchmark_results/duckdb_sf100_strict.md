# Aggregated Benchmark Report
- **Scale Factor (SF)**: 100
- **Pilot Sample Rate**: 1.0%
- **Iterations**: 5 (deterministic seeds 42 to 46)

> [!NOTE]
> Threshold 10% được dùng cho assertion CI/CD vì SF=1 quá nhỏ để hit 5% một cách ổn định (sampling variance lớn trên dataset nhỏ). Trên SF=10, expect mean_row_relative_error sẽ về quanh 5%.

## Summary table

| Query | Exact Time (s) | AQP Time (s) | Speedup | Final Sample Rate | Fallback Rate | Mean Row Error | Max Row Error | Missing Groups |
|---|---|---|---|---|---|---|---|---|
| **Q1** | 30.312s ±0.611s | 35.566s ±0.571s | 0.85x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q3** | 23.091s ±4.571s | 27.701s ±0.978s | 0.84x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q5** | 26.305s ±3.841s | 23.993s ±2.705s | 1.12x | 629.60% | 40.0% (2/5) | 0.495% | 1.668% | 0.0 |
| **Q6** | 9.649s ±0.250s | 9.632s ±0.432s | 1.00x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q7** | 21.782s ±0.942s | 22.315s ±2.572s | 0.99x | 627.80% | 40.0% (2/5) | 0.526% | 2.327% | 0.0 |
| **Q8** | 22.670s ±3.706s | 23.279s ±0.925s | 0.98x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q9** | 54.666s ±1.617s | 195.139s ±8.031s | 0.28x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q10** | 38.449s ±3.225s | 61.549s ±2.065s | 0.62x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q12** | 28.315s ±1.296s | 18.205s ±0.445s | 1.56x | 376.80% | 0.0% (0/5) | 0.890% | 2.527% | 0.0 |
| **Q14** | 19.212s ±0.854s | 20.105s ±0.632s | 0.96x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q18** | 52.587s ±2.392s | 92.379s ±4.210s | 0.57x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q19** | 28.865s ±0.671s | 29.858s ±0.569s | 0.97x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |

## Analysis and Assertions

- **Overall Fallback Rate**: 81.67% (49/60 runs)
  > [!WARNING]
  > **High Fallback Rate Alert**: The overall fallback rate is 81.67%, which exceeds the 30% quality threshold.
  > This indicates that AQP is frequently reverting to exact execution under SF=1 small-scale variance.

- **Q1** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q3** had 5/5 fallbacks. Reasons: `['multi_table_no_phi']`
- **Q5** had 2/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q6** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q7** had 2/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q8** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q9** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q10** had 5/5 fallbacks. Reasons: `['multi_table_no_phi']`
- **Q14** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
- **Q18** had 5/5 fallbacks. Reasons: `['multi_table_no_phi']`
- **Q19** had 5/5 fallbacks. Reasons: `['optimizer_infeasible']`
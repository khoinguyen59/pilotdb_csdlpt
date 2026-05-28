# Aggregated Benchmark Report
- **Scale Factor (SF)**: 100
- **Pilot Sample Rate**: 1.0%
- **Iterations**: 5 (deterministic seeds 42 to 46)

> [!NOTE]
> Threshold 10% được dùng cho assertion CI/CD vì SF=1 quá nhỏ để hit 5% một cách ổn định (sampling variance lớn trên dataset nhỏ). Trên SF=10, expect mean_row_relative_error sẽ về quanh 5%.

## Summary table

| Query | Exact Time (s) | AQP Time (s) | Speedup | Final Sample Rate | Fallback Rate | Mean Row Error | Max Row Error | Missing Groups |
|---|---|---|---|---|---|---|---|---|
| **Q1** | 32.352s ±5.245s | 30.425s ±3.308s | 1.06x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q3** | 21.781s ±3.446s | 22.649s ±4.989s | 0.97x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q5** | 24.605s ±3.605s | 11.444s ±6.147s | 2.45x | 56.58% | 80.0% (4/5) | 1.957% | 6.334% | 0.0 |
| **Q6** | 9.878s ±0.862s | 9.522s ±0.244s | 1.04x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q7** | 21.885s ±1.609s | 11.237s ±4.928s | 2.16x | 57.41% | 80.0% (4/5) | 1.770% | 4.988% | 0.0 |
| **Q8** | 21.732s ±3.549s | 21.751s ±3.266s | 1.00x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q9** | 63.697s ±6.520s | 98.690s ±55.612s | 0.76x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q10** | 40.377s ±2.566s | 39.104s ±12.119s | 1.10x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q12** | 27.362s ±2.679s | 7.560s ±3.801s | 4.14x | 1.00% | 80.0% (4/5) | 1.669% | 3.801% | 0.0 |
| **Q14** | 17.807s ±0.656s | 5.293s ±1.537s | 3.55x | 59.90% | 80.0% (4/5) | 0.335% | 0.862% | 0.0 |
| **Q18** | 50.231s ±3.773s | 59.025s ±20.042s | 0.91x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |
| **Q19** | 28.105s ±0.620s | 28.183s ±0.907s | 1.00x | 100.00% | 100.0% (5/5) | 0.000% | 0.000% | 0.0 |

## Analysis and Assertions

- **Overall Fallback Rate**: 93.33% (56/60 runs)
  > [!WARNING]
  > **High Fallback Rate Alert**: The overall fallback rate is 93.33%, which exceeds the 30% quality threshold.
  > This indicates that AQP is frequently reverting to exact execution under SF=1 small-scale variance.

- **Q1** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'optimizer_infeasible']`
- **Q3** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'multi_table_no_phi']`
- **Q5** had 4/5 fallbacks. Reasons: `['cache_hit_template']`
- **Q6** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'optimizer_infeasible']`
- **Q7** had 4/5 fallbacks. Reasons: `['cache_hit_template']`
- **Q8** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'optimizer_infeasible']`
- **Q9** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'optimizer_infeasible']`
- **Q10** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'multi_table_no_phi']`
- **Q12** had 4/5 fallbacks. Reasons: `['cache_hit_template']`
- **Q14** had 4/5 fallbacks. Reasons: `['cache_hit_template']`
- **Q18** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'multi_table_no_phi']`
- **Q19** had 5/5 fallbacks. Reasons: `['cache_hit_template', 'optimizer_infeasible']`
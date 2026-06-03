# Aggregated Benchmark Report
- **Scale Factor (SF)**: 100
- **Pilot Sample Rate**: 1.0%
- **Iterations**: 3 (deterministic seeds 42 to 44)

> [!NOTE]
> Ngưỡng sai số cấu hình rõ ràng là 5% (--error 0.05). Trên SF=100, các mẫu thử có dung lượng lớn giúp kiểm chứng chính xác chất lượng ước lượng AQP dưới độ lệch mẫu thấp.

## Summary table

| Query | Exact Time (s) | AQP Time (s) | Speedup | Final Sample Rate | Fallback Rate | Mean Row Error | Max Row Error | Missing Groups |
|---|---|---|---|---|---|---|---|---|
| **Q1** | 410.065s ±1.874s | 475.000s ±93.682s | 0.89x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q3** | 480.236s ±1.964s | 683.360s ±288.814s | 0.81x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q5** | 478.743s ±1.772s | 716.779s ±338.123s | 0.80x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q6** | 405.297s ±3.438s | 420.578s ±25.022s | 0.97x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q7** | 478.636s ±2.063s | 679.945s ±286.252s | 0.81x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q8** | 494.323s ±2.205s | 647.171s ±285.331s | 0.90x | 5.00% | 66.7% (2/3) | 7.433% | 12.486% | 0.0 |
| **Q9** | 569.859s ±2.326s | 859.030s ±406.720s | 0.80x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q10** | 455.427s ±2.061s | 966.796s ±679.188s | 0.70x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q12** | 505.990s ±20.105s | 700.810s ±276.944s | 0.81x | 5.00% | 66.7% (2/3) | 0.441% | 0.956% | 0.0 |
| **Q14** | 420.635s ±3.512s | 613.639s ±276.505s | 0.81x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q18** | 1206.751s ±25.779s | 1683.080s ±648.785s | 0.82x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |
| **Q19** | 418.646s ±0.189s | 586.212s ±237.188s | 0.82x | 100.00% | 100.0% (3/3) | 0.000% | 0.000% | 0.0 |

## Analysis and Assertions

- **Overall Fallback Rate**: 94.44% (34/36 runs)
  > [!WARNING]
  > **High Fallback Rate Alert**: The overall fallback rate is 94.44%, which exceeds the 30% quality threshold.
  > This indicates that AQP is frequently reverting to exact execution under SF=100 due to query complexity or sampling variance.

- **Q1** had 3/3 fallbacks. Reasons: `['optimizer_infeasible', 'cache_hit_template']`
- **Q3** had 3/3 fallbacks. Reasons: `['cache_hit_template', 'multi_table_no_phi']`
- **Q5** had 3/3 fallbacks. Reasons: `['optimizer_infeasible', 'cache_hit_template']`
- **Q6** had 3/3 fallbacks. Reasons: `['optimizer_infeasible', 'cache_hit_template']`
- **Q7** had 3/3 fallbacks. Reasons: `['optimizer_infeasible', 'cache_hit_template']`
- **Q8** had 2/3 fallbacks. Reasons: `['cache_hit_template']`
- **Q9** had 3/3 fallbacks. Reasons: `['optimizer_infeasible', 'cache_hit_template']`
- **Q10** had 3/3 fallbacks. Reasons: `['cache_hit_template', 'multi_table_no_phi']`
- **Q12** had 2/3 fallbacks. Reasons: `['cache_hit_template']`
- **Q14** had 3/3 fallbacks. Reasons: `['optimizer_infeasible', 'cache_hit_template']`
- **Q18** had 3/3 fallbacks. Reasons: `['cache_hit_template', 'multi_table_no_phi']`
- **Q19** had 3/3 fallbacks. Reasons: `['optimizer_infeasible', 'cache_hit_template']`
# CHANGELOG — Sửa Code Theo Bài Báo PilotDB (SIGMOD 2025)

> **Ngày sửa**: 2026-05-14  
> **Tham chiếu**: `PAPER_CODE_AUDIT.md`  
> **Files đã sửa**: `utils.py`, `error_bounds.py`, `execute.py`

---

## FIX B1 — MUL_OPERATOR crash → thêm handler

**File**: `pilotdb/pilot_engine/utils.py`  
**Vị trí**: 2 hàm `aggregate_error_to_page_error()` và `aggregate_error_uniform()`

### Vấn đề
`pilot.py` (dòng 280–283) tạo `result_mapping` với `aggregate = MUL_OPERATOR` khi gặp query dạng `SUM(a) * SUM(b)`. Nhưng `utils.py` không có handler → **crash** với `raise NotImplemented`.

### Cơ sở lý thuyết (Paper Table 2, §3.1, dòng 476–532)
- Multiplication: `|ê_product| ≤ e₁·e₂ + e₁ + e₂`
- Phân bổ đều: `e' = √(e+1) - 1`

### Code đã thêm
```python
# [FIX B1] MUL_OPERATOR: Paper Table 2, Multiplication row
# e_product <= e1 + e2 + e1*e2, equal allocation => e' = sqrt(e+1) - 1
elif aggregate["aggregate"] == MUL_OPERATOR:
    page_required_error = min(
        1 - math.sqrt(1 - required_error),
        math.sqrt(required_error + 1) - 1,
    )
    # ... assign to FIRST_ELEMENT and SECOND_ELEMENT
```

---

## FIX B2 — SUB_OPERATOR crash → thêm handler

**File**: `pilotdb/pilot_engine/utils.py`  
**Vị trí**: Cùng 2 hàm như B1

### Vấn đề
`pilot.py` (dòng 342–346) tạo `SUB_OPERATOR` khi gặp `SUM(a) - SUM(b)`. Tương tự B1 → **crash**.

### Cơ sở lý thuyết (Paper Table 2, Addition row, dòng 480–481)
- Addition/Subtraction: `|ê_sum| ≤ max(e₁, e₂)`
- Phân bổ đều: `e' = required_error` (mỗi thành phần dùng cùng error)

### Code đã thêm
```python
# [FIX B2] SUB_OPERATOR: Paper Table 2, Addition row
# e_sum <= max(e1, e2), equal allocation => e' = required_error
elif aggregate["aggregate"] == SUB_OPERATOR:
    page_required_error = required_error
    # ... assign to FIRST_ELEMENT and SECOND_ELEMENT
```

---

## FIX B3 — Boole's inequality: dạng nhân → dạng cộng

**File**: `pilotdb/pilot_engine/error_bounds.py`  
**Vị trí**: Hàm `estimate_final_rate()`, dòng 161

### Vấn đề
| | Bài báo | Code cũ |
|---|---|---|
| Công thức | `fp_each = (1-p) / (k·m)` | `fp_each = 1 - (1-fp)^(1/n)` |
| Loại | Boole's inequality (cộng) | Multiplicative independence |

### Cơ sở lý thuyết (Paper §3.1, dòng 572–574)
> "we allocate the confidence evenly. Namely, if we have k·m aggregates, each aggregate μ_{i,j} needs to satisfy confidence of p_{i,j} = 1 − (1−p)/(k·m)."

### Thay đổi
```diff
- fp = 1 - math.pow(1 - failure_prob, 1 / n_est)
+ # [FIX B3] Paper §3.1: Boole's inequality additive form
+ fp = failure_prob / n_est
```

### So sánh bằng số (fp=0.05, n_est=10)
- **Cũ**: `1 - 0.95^0.1 = 0.00512`
- **Mới**: `0.05/10 = 0.005`
- Code cũ nghiêm ngặt hơn 2.4% → sampling rate cao hơn không cần thiết

---

## FIX B4 — δ₁, δ₂ không chia 3 phần → chia đúng

**File**: `pilotdb/pilot_engine/error_bounds.py`  
**Vị trí**: Hàm `estimate_final_rate()`, 2 chỗ gọi `get_mean_sample_size()`

### Vấn đề
Code cũ truyền cùng `fp` cho cả 3 tham số:
```python
get_mean_sample_size(error, fp, fp, fp, ...)
#                           ^   ^   ^
#                           |   |   └─ fp2 (mean bound δ₂)
#                           |   └───── fp1 (variance bound δ₁)
#                           └───────── fp  (z-value)
```

### Cơ sở lý thuyết (Paper §3.1, Procedure 1, dòng 458–462)
> "p' = p + δ₁ + δ₂" và "By default, δ₁ = δ₂ = (1-p')/1 = (1-p)/3"

Mỗi tham số nên dùng **1/3 failure budget**:

### Thay đổi
```diff
+ delta = fp / 3
  final_sample_size = get_mean_sample_size(
-     error, fp, fp, fp, sample_mean, sample_std, sample_size
+     error, delta, delta, delta, sample_mean, sample_std, sample_size
  )
```

---

## FIX B6 — Thêm Lemma 3.2 group coverage check

**File**: `pilotdb/execute.py`  
**Vị trí**: Hàm mới `_min_pilot_rate_for_groups()` + logic trong `execute_aqp()`

### Vấn đề
Pilot sampling rate cố định `0.05%` cho mọi query. Với GROUP BY queries trên bảng nhỏ, pilot có thể bỏ sót nhóm → error guarantees không đảm bảo.

### Cơ sở lý thuyết (Paper Lemma 3.2, §3.1, dòng 587–593)
> "block sampling with rate θ satisfying: θ ≥ 1 − (1 − (1−p_f)^(⌈g/b⌉/|T|))^(1/⌈g/b⌉) ensures that the probability of missing a group of size > g is less than p_f"

Mặc định: g=200 (min group size), p_f=0.05 (miss probability).

### Code đã thêm
```python
def _min_pilot_rate_for_groups(
    table_size, block_size=8192, min_group_size=200, p_fail=0.05
):
    """Paper Lemma 3.2 (§3.1, Eq. 7)"""
    blocks_per_group = math.ceil(min_group_size / block_size)
    total_blocks = max(math.ceil(table_size / block_size), 1)
    base = (1 - p_fail) ** (blocks_per_group / total_blocks)
    theta_min = 1 - base ** (1.0 / blocks_per_group)
    return max(theta_min * 100, 0.01)
```

Logic trong `execute_aqp()`:
```python
if has_group_by and query.table_size:
    min_rate = _min_pilot_rate_for_groups(table_size=largest_table_size)
    if min_rate > pilot_sample_rate:
        pilot_sample_rate = min_rate  # Nâng lên đảm bảo group coverage
```

---

## Tổng kết các file đã thay đổi

| File | Fix | Dòng thay đổi | Mô tả |
|------|-----|----------------|-------|
| `utils.py` | B1 | +40 dòng (sau dòng 80) | Thêm MUL_OPERATOR handler |
| `utils.py` | B2 | +20 dòng (sau MUL) | Thêm SUB_OPERATOR handler |
| `utils.py` | B1 | +22 dòng (sau dòng 150) | Thêm MUL_OPERATOR cho uniform |
| `utils.py` | B2 | +18 dòng (sau MUL uniform) | Thêm SUB_OPERATOR cho uniform |
| `error_bounds.py` | B3 | Sửa dòng 161 | Boole's additive |
| `error_bounds.py` | B4 | Sửa 2 chỗ gọi get_mean_sample_size | delta = fp/3 |
| `execute.py` | B6 | +38 dòng (hàm mới + logic) | Lemma 3.2 |

### Chưa sửa (phức tạp, cần thảo luận)
- **B5**: Sampling Plan Optimization (§3.2) — cần implement scipy.optimize trust region solver
- **B7**: FIXME pilot result reuse — performance optimization

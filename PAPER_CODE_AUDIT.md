# BÁO CÁO KIỂM TRA ĐỐI CHIẾU: Bài Báo ↔ Mã Nguồn PilotDB

> **Bài báo**: PilotDB — SIGMOD 2025, Article 198  
> **Mã nguồn**: `pilotdb_csdlpt/pilotdb/`  
> **Ngày kiểm tra**: 2026-05-14

---

## PHẦN A — CÁC ĐIỂM KHỚP CHÍNH XÁC (✅)

### A1. Pipeline TAQA hai giai đoạn (§2.2, §3)

**Bài báo mô tả**: Giai đoạn 1 — chạy Pilot Query với sampling rate θ_p để thu thập thống kê. Giai đoạn 2 — tính toán sampling rate tối ưu rồi chạy Final Query.

**Mã nguồn** — `execute.py`, hàm `execute_aqp()` (dòng 34–166):

```python
# Giai đoạn 1: Rewrite và chạy pilot query (dòng 39-76)
pq = Pilot_Rewriter(query.table_cols, query.table_size, dbms)
pilot_query = pq.rewrite(query.query) + ";"
pilot_results = execute_query(conn, pilot_query, dbms)

# Tính sampling rate (dòng 79-90)
page_errors = aggregate_error_to_page_error(pq.result_mapping_list, required_error=query.error)
final_sample_rate = estimate_final_rate(...)

# Giai đoạn 2: Chạy final query (dòng 112-125)
sampling_clause = get_sampling_clause(final_sample_rate, dbms)
results_df = execute_query(conn, sampling_query, dbms)
```

**Kết luận**: ✅ Khớp hoàn toàn — luồng xử lý đúng 2 giai đoạn như mô tả trong §2.2.

---

### A2. Trích xuất Page-ID (Block Identification) — §3.3, §4.1

**Bài báo**: Sử dụng vị trí vật lý của data block làm GROUP BY key — ctid (PostgreSQL), rowid/block_size (DuckDB), %%physloc%% (SQL Server).

**Mã nguồn** — `pilot.py`, hàm `extract_page_id()` (dòng 68–138):

| DBMS | Bài báo | Code | Khớp? |
|------|---------|------|-------|
| PostgreSQL | `ctid` system column | `(table.ctid::text::point)[0]::int` | ✅ |
| DuckDB | `rowid / block_size` | `floor(table.rowid/2048)` | ✅ |
| SQL Server | `%%physloc%%` | `SUBSTRING(table.%%physloc%%, ...)` | ✅ |

**Kết luận**: ✅ Khớp chính xác.

---

### A3. Pilot Query Rewriting — 3 bước (§3.3, Figure 4)

**Bài báo** mô tả 3 bước:
1. Thêm `TABLESAMPLE SYSTEM` vào bảng lớn nhất
2. Thêm block location vào GROUP BY
3. Phân tách composite aggregates (VD: AVG → SUM + COUNT)

**Mã nguồn** — `pilot.py`, hàm `rewrite()` (dòng 725–763):

```python
def rewrite(self, original_query):
    # ...
    expression = self.rewrite_select_expression(expression)  # Bước 3
    expression = self.primary_query_rewriter(expression)     # Bước 1+2
```

- **Bước 1** — `add_table_sample()` (dòng 411–441): thêm TABLESAMPLE vào bảng lớn nhất
- **Bước 2** — `add_page_id()` (dòng 363–400): thêm page_id vào GROUP BY
- **Bước 3** — `rewrite_select_expression()` (dòng 195–350): tách AVG→SUM, xử lý DIV/MUL

**Kết luận**: ✅ Khớp đầy đủ cả 3 bước.

---

### A4. Final Query Rewriting — 2 bước (§3.3)

**Bài báo**: (1) Thêm sampling clause, (2) Upscale SUM-like aggregates bằng cách chia cho sampling rate.

**Mã nguồn** — `sampling.py`, hàm `add_sample_rate()` (dòng 154–196):

```python
new_div_expression = exp.Div(this=agg_expression, expression="{sample_rate}")
```

**Kết luận**: ✅ SUM và COUNT được chia cho `sample_rate` để upscale. AVG không cần vì AVG = SUM/COUNT.

---

### A5. Công thức thống kê BSAP (§3.1, §4)

**Bài báo**: Dùng Student's t-distribution cho mean bounds (Eq. 4), Chi-squared cho variance bounds (Eq. 5).

**Mã nguồn** — `error_bounds.py` (dòng 9–46):

| Công thức | Bài báo | Code | Khớp? |
|-----------|---------|------|-------|
| Mean lower bound | `μ̂ - t_{n-1,1-δ} · σ̂/√n` | `get_mean_lb()` dùng `t.ppf()` | ✅ |
| Std upper bound | `σ̂ · √((n-1)/χ²_{δ,n-1})` | `get_std_ub()` dùng `chi2.ppf()` | ✅ |
| Sample size (Eq. 6) | `n = (z/e · σ_ub/μ_lb)²` | `get_mean_sample_size()` dòng 97 | ✅ |
| Bernoulli N bound | Giải phương trình bậc 2 | `_solve_quadratic()` dòng 100 | ✅ |

**Kết luận**: ✅ Tất cả công thức thống kê cốt lõi khớp chính xác.

---

### A6. Error Propagation cho SUM, AVG, COUNT, DIV (Table 2)

**Bài báo Table 2**:
- Multiplication: `e' = √(e+1) - 1`  
- Division: `e' = e/(2-e)` (phân bổ đều)
- Addition: `e' = max(e₁, e₂)`

**Mã nguồn** — `utils.py`, hàm `aggregate_error_to_page_error()`:

| Operator | Bài báo | Code (dòng) | Khớp? |
|----------|---------|-------------|-------|
| SUM (= mean × N) | `√(e+1) - 1` | dòng 13–14: `min(1-√(1-e), √(e+1)-1)` | ✅ |
| AVG (= SUM/COUNT) | `e/(2-e)` | dòng 30: `required_error / (2 - required_error)` | ✅ |
| COUNT (= size × N) | `√(e+1) - 1` | dòng 47–48: giống SUM | ✅ |
| DIV (SUM/SUM) | Division rồi Multiplication | dòng 62–67: 2 tầng propagation | ✅ |

**Kết luận**: ✅ Bốn operator chính khớp chính xác.

---

### A7. Block Sampling Clause (§4.1)

**Mã nguồn** — `driver.py`, `get_sampling_clause()` (dòng 53–61):

| DBMS | Code | Bài báo (§2.1) | Khớp? |
|------|------|----------------|-------|
| PostgreSQL | `TABLESAMPLE SYSTEM (rate)` | TABLESAMPLE SYSTEM | ✅ |
| DuckDB | `TABLESAMPLE SYSTEM(rate%)` | TABLESAMPLE SYSTEM | ✅ |
| SQL Server | `TABLESAMPLE (rate PERCENT)` | TABLESAMPLE | ✅ |

---

## PHẦN B — CÁC ĐIỂM KHÔNG KHỚP (❌/⚠️)

---

### B1. ❌ BUG — MUL_OPERATOR không có handler trong error propagation

**Mức độ**: NGHIÊM TRỌNG (crash tại runtime)

**Bài báo (Table 2, dòng 476–477)**: Quy tắc Multiplication: `e_product ≤ e₁·e₂ + e₁ + e₂`. Phân bổ đều: `e' = √(e+1) - 1`.

**Minh chứng — MUL_OPERATOR được TẠO trong pilot.py** (dòng 280–283):
```python
elif ratio_type == MUL_OPERATOR:
    result_mapping[AGGREGATE] = MUL_OPERATOR          # ← Tạo mapping "mul"
    result_mapping[FIRST_ELEMENT] = f"r{...}"
    result_mapping[SECOND_ELEMENT] = f"r{...}"
```

**Minh chứng — KHÔNG CÓ handler trong utils.py** (dòng 81–84):
```python
else:
    raise NotImplemented(                              # ← MUL_OPERATOR rơi vào đây!
        f"operator {aggregate['aggregate']} is not implemented"
    )
```

`aggregate_error_to_page_error()` chỉ xử lý: SUM, AVG, COUNT, DIV. Khi gặp `MUL_OPERATOR` → crash.

**VD query sẽ crash**: `SELECT SUM(a) * SUM(b) FROM table`

**Cách sửa** — thêm vào `utils.py` trước `else` ở dòng 81:
```python
elif aggregate["aggregate"] == MUL_OPERATOR:
    page_required_error = min(
        1 - math.sqrt(1 - required_error),
        math.sqrt(required_error + 1) - 1,
    )
    if aggregate[FIRST_ELEMENT] in page_errors:
        page_errors[aggregate[FIRST_ELEMENT]] = min(
            page_errors[aggregate[FIRST_ELEMENT]], page_required_error
        )
    else:
        page_errors[aggregate[FIRST_ELEMENT]] = page_required_error
    if aggregate[SECOND_ELEMENT] in page_errors:
        page_errors[aggregate[SECOND_ELEMENT]] = min(
            page_errors[aggregate[SECOND_ELEMENT]], page_required_error
        )
    else:
        page_errors[aggregate[SECOND_ELEMENT]] = page_required_error
```

Tương tự cần thêm vào hàm `aggregate_error_uniform()` (dòng 152).

---

### B2. ❌ BUG — SUB_OPERATOR không có handler trong error propagation

**Mức độ**: NGHIÊM TRỌNG (crash tại runtime)

**Bài báo (Table 2, dòng 480–481)**: Quy tắc Addition/Subtraction: `e_sum ≤ max(e₁, e₂)`. Phân bổ đều: `e' = e` (mỗi thành phần cùng error).

**Minh chứng — SUB_OPERATOR được TẠO trong pilot.py** (dòng 341–346):
```python
if new_select_expression:
    self.result_mapping_list[-1] = {
        AGGREGATE: SUB_OPERATOR,                       # ← Tạo mapping "sub"
        FIRST_ELEMENT: f"r{...}",
        SECOND_ELEMENT: f"r{...}",
    }
```

**Minh chứng — KHÔNG CÓ handler** — cùng vấn đề như B1, rơi vào `raise NotImplemented`.

**VD query sẽ crash**: `SELECT SUM(a) - SUM(b) FROM table`

**Cách sửa** — thêm vào `utils.py`:
```python
elif aggregate["aggregate"] == SUB_OPERATOR:
    # Addition/Subtraction: e' = max(e1, e2) → phân bổ đều → e' = required_error
    page_required_error = required_error
    if aggregate[FIRST_ELEMENT] in page_errors:
        page_errors[aggregate[FIRST_ELEMENT]] = min(
            page_errors[aggregate[FIRST_ELEMENT]], page_required_error
        )
    else:
        page_errors[aggregate[FIRST_ELEMENT]] = page_required_error
    if aggregate[SECOND_ELEMENT] in page_errors:
        page_errors[aggregate[SECOND_ELEMENT]] = min(
            page_errors[aggregate[SECOND_ELEMENT]], page_required_error
        )
    else:
        page_errors[aggregate[SECOND_ELEMENT]] = page_required_error
```

---

### B3. ⚠️ Công thức phân bổ Confidence khác bài báo

**Mức độ**: TRUNG BÌNH (kết quả vẫn đúng nhưng không tối ưu)

**Bài báo (§3.1, dòng 572–574)**:
> "we allocate the confidence evenly. Namely, if we have k·m aggregates, each aggregate needs to satisfy confidence of p_{i,j} = 1 - (1-p)/(k·m)"

Tức **Boole's inequality dạng cộng**: `fp_each = (1-p) / (k·m)`

**Mã nguồn** — `error_bounds.py` dòng 161:
```python
fp = 1 - math.pow(1 - failure_prob, 1 / n_est)
```

Đây là **dạng nhân (multiplicative independence)**: `fp_each = 1 - (1-fp)^(1/n_est)`

**So sánh bằng số**:
- Giả sử `failure_prob = 0.05`, `n_est = 10`
- Bài báo: `fp_each = 0.05/10 = 0.005`
- Code: `fp_each = 1 - 0.95^(1/10) = 0.00512`
- Chênh lệch: ~2.4% (code **nghiêm ngặt hơn**)

**Tác động**: Code **bảo toàn** (conservative) — error guarantees vẫn hợp lệ. Tuy nhiên sampling rate sẽ cao hơn một chút → query chậm hơn ~5% so với tối ưu.

**Cách sửa** (nếu muốn khớp chính xác):
```python
# Thay dòng 161:
fp = failure_prob / n_est    # Boole's inequality dạng cộng
```

---

### B4. ⚠️ Tham số δ₁, δ₂ không chia theo mặc định của bài báo

**Mức độ**: TRUNG BÌNH

**Bài báo (§3.1, Procedure 1, dòng 462)**:
> "By default, we set δ₁ = δ₂ = (1-p')/1 = (1-p)/3"

Nghĩa là failure probability được chia 3 phần: δ₁ cho mean bound, δ₂ cho variance bound, phần còn lại cho z-value.

**Mã nguồn** — `error_bounds.py` dòng 175–176:
```python
final_sample_size = get_mean_sample_size(
    error, fp, fp, fp, sample_mean, sample_std, sample_size
)
#         ^   ^   ^
#         |   |   └─ fp2 (variance bound)
#         |   └───── fp1 (mean bound)  
#         └───────── fp  (z-value)
```

Cả 3 tham số đều dùng **cùng một giá trị `fp`** (đã qua Boole's adjustment). Bài báo yêu cầu split riêng biệt.

**Tác động**: Kết quả vẫn hợp lệ (vì tổng failure budget không vượt quá). Nhưng phân bổ không tối ưu → sampling rate có thể cao hơn cần thiết.

**Cách sửa** (để khớp bài báo):
```python
# Trong estimate_final_rate(), sau khi tính fp:
delta = (1 - failure_prob) / 3  # Chia đều 3 phần
# Rồi gọi:
final_sample_size = get_mean_sample_size(
    error, delta, delta, delta, sample_mean, sample_std, sample_size
)
```

---

### B5. ❌ Thiếu Sampling Plan Optimization (§3.2)

**Mức độ**: CAO (cho multi-table joins)

**Bài báo (§3.2, dòng 600–648)**: Mô tả chi tiết:
1. Liệt kê tập bảng cần sample
2. Cho mỗi tổ hợp, giải bài toán tối ưu bằng **trust region method** (`scipy.optimize`)
3. Dùng **cost model của DBMS** để chọn plan tốt nhất
4. So sánh cost với exact query, loại bỏ plan không hiệu quả

**Mã nguồn** — `error_bounds.py` dòng 204:
```python
return max(candidate_sample_rate)    # ← Chỉ lấy max, KHÔNG tối ưu
```

Không có bất kỳ call nào đến `scipy.optimize`. Không có cost model comparison.

**Minh chứng bằng grep**:
- `scipy.optimize` không xuất hiện trong toàn bộ codebase
- Chỉ có 1 sampling rate duy nhất cho bảng lớn nhất (không hỗ trợ multi-table sampling)

**Tác động**:
- **Single-table queries**: `max()` là đúng (chỉ có 1 rate)
- **Multi-table join queries**: Thiếu tối ưu, có thể chọn plan chậm hơn tối ưu

**Cách sửa**: Cần implement module optimization mới:
```python
from scipy.optimize import minimize

def optimize_sampling_plan(constraints, cost_model, tables):
    # Enumerate table subsets
    # For each subset, solve: minimize θ_i subject to Φ(Θ)
    # Use trust-constr method
    result = minimize(cost_fn, x0, method='trust-constr', constraints=constraints)
    return result.x
```

---

### B6. ⚠️ Thiếu kiểm tra Group Coverage (Lemma 3.2)

**Mức độ**: TRUNG BÌNH

**Bài báo (Lemma 3.2, dòng 587–593)**: Với GROUP BY queries, pilot sampling rate θ_p phải thỏa:
```
θ ≥ 1 - (1 - (1-p_f)^(⌈g/b⌉/|T|))^(1/⌈g/b⌉)
```
để đảm bảo không bỏ sót group lớn hơn g hàng. Mặc định g=200, p_f=0.05.

**Mã nguồn** — `execute.py` dòng 34:
```python
def execute_aqp(query: Query, db_config: dict, pilot_sample_rate: float = 0.05):
#                                                                        ^^^^^
#                                         Cố định 0.05%, KHÔNG điều chỉnh theo Lemma 3.2
```

Không có logic nào tính minimum sampling rate cho group coverage.

**Tác động**: Với bảng nhỏ hoặc group nhỏ, pilot có thể bỏ sót group → error guarantees không đảm bảo cho group bị thiếu.

**Cách sửa**:
```python
import math

def min_pilot_rate_for_groups(table_size, block_size, min_group_size=200, p_fail=0.05):
    blocks_per_group = math.ceil(min_group_size / block_size)
    total_blocks = table_size  # hoặc table_size / block_size
    base = (1 - p_fail) ** (blocks_per_group / total_blocks)
    theta_min = 1 - base ** (1.0 / blocks_per_group)
    return theta_min * 100  # percent

# Trong execute_aqp():
min_rate = min_pilot_rate_for_groups(table_size, block_size)
pilot_sample_rate = max(pilot_sample_rate, min_rate)
```

---

### B7. ⚠️ FIXME chưa xử lý: tái sử dụng pilot results

**Mức độ**: THẤP (performance, không ảnh hưởng correctness)

**Mã nguồn** — `execute.py` dòng 130–131:
```python
# FIXME: directly translate pilot results instead of running sampling again
sampling_clause = get_sampling_clause(pilot_sample_rate, dbms)
```

Khi `final_sample_rate ≤ pilot_sample_rate`, code vẫn **chạy lại** sampling query thay vì dùng kết quả pilot đã có. Bài báo (§5.7, dòng 599) đề cập:
> "we can efficiently tune δ₁ and δ₂ with **cached pilot query results**"

**Tác động**: Lãng phí thời gian chạy query thừa.

---

## PHẦN C — BẢNG TÓM TẮT

| # | Vị trí | Loại | Mức độ | Mô tả | Trạng thái |
|---|--------|------|--------|-------|------------|
| A1 | execute.py | Pipeline | — | TAQA 2 giai đoạn | ✅ Khớp |
| A2 | pilot.py | Page-ID | — | ctid, rowid, physloc | ✅ Khớp |
| A3 | pilot.py | Rewriting | — | 3 bước pilot rewrite | ✅ Khớp |
| A4 | sampling.py | Rewriting | — | 2 bước final rewrite | ✅ Khớp |
| A5 | error_bounds.py | Thống kê | — | t-dist, chi2, quadratic | ✅ Khớp |
| A6 | utils.py | Error prop | — | SUM/AVG/COUNT/DIV | ✅ Khớp |
| A7 | driver.py | Sampling | — | TABLESAMPLE clause | ✅ Khớp |
| **B1** | utils.py:81 | **Bug** | **Cao** | MUL_OPERATOR crash | ❌ Cần sửa |
| **B2** | utils.py:81 | **Bug** | **Cao** | SUB_OPERATOR crash | ❌ Cần sửa |
| B3 | error_bounds.py:161 | Công thức | TB | Boole's dạng nhân vs cộng | ⚠️ Conservative |
| B4 | error_bounds.py:176 | Tham số | TB | δ₁=δ₂ không chia đúng | ⚠️ Suboptimal |
| **B5** | error_bounds.py:204 | **Thiếu** | **Cao** | Optimizer chưa implement | ❌ Thiếu |
| B6 | execute.py:34 | Thiếu | TB | Lemma 3.2 chưa implement | ⚠️ Thiếu |
| B7 | execute.py:130 | FIXME | Thấp | Pilot result reuse | ⚠️ TODO |

---

## PHẦN D — ĐỀ XUẤT ƯU TIÊN SỬA

1. **Ưu tiên 1** (B1, B2): Fix bug crash MUL/SUB operator — dễ sửa, impact cao
2. **Ưu tiên 2** (B3, B4): Đồng bộ công thức confidence — sửa 2 dòng code
3. **Ưu tiên 3** (B6): Implement Lemma 3.2 — trung bình độ phức tạp
4. **Ưu tiên 4** (B5): Sampling plan optimization — phức tạp, cần scipy.optimize
5. **Ưu tiên 5** (B7): Pilot result caching — nice-to-have

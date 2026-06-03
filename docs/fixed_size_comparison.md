# Bernoulli (row-level) vs System (block-level) Sampling — DuckDB SF=1

So sánh hiệu năng thực thi giữa quét chính xác và hai **phương pháp lấy mẫu** ở **cùng một tỷ lệ mẫu cố định p = 10%** (để cô lập ảnh hưởng của phương pháp, trung bình 5 lần chạy). Đây là cách tiếp cận đúng cho Research Gap #6 (*Table 5 bài gốc: lấy mẫu mức khối hiệu quả hơn Bernoulli ~3.8%*).

- **Exact**: quét toàn bảng (`from lineitem`).
- **Bernoulli (row-level)**: `TABLESAMPLE BERNOULLI(p%)` — đánh giá xác suất từng dòng.
- **System (block-level)**: `TABLESAMPLE SYSTEM(p%)` — lấy mẫu theo khối/trang lưu trữ.

| Query | Exact (s) | Bernoulli (s) | System (s) | Speedup Bernoulli | Speedup System | System/Bernoulli |
|---|---|---|---|---|---|---|
| **Q1** | 0.0371 | 0.1552 | 0.0065 | 0.24x | 5.72x | 23.975x |
| **Q5** | 0.0284 | 0.1286 | 0.0235 | 0.22x | 1.21x | 5.474x |
| **Q6** | 0.0076 | 0.1080 | 0.0023 | 0.07x | 3.27x | 46.706x |
| **Q7** | 0.0258 | 0.1490 | 0.0175 | 0.17x | 1.47x | 8.511x |
| **Q8** | 0.0194 | 0.4460 | 0.3372 | 0.04x | 0.06x | 1.323x |
| **Q9** | 0.0633 | 2.7481 | 3.0251 | 0.02x | 0.02x | 0.908x |
| **Q12** | 0.0213 | 0.1312 | 0.0075 | 0.16x | 2.82x | 17.395x |
| **Q14** | 0.0172 | 0.1119 | 0.0108 | 0.15x | 1.59x | 10.342x |
| **Q19** | 0.0277 | 0.1491 | 0.0154 | 0.19x | 1.79x | 9.676x |

**Tổng hợp**: trung bình nhân (geometric mean) tỷ số **System/Bernoulli = 7.818x** trên 9 truy vấn — tức lấy mẫu mức khối (System) nhanh hơn Bernoulli mức dòng trung bình **681.8%**.

> [!NOTE]
> **Diễn giải trung thực:**
> 1. Thí nghiệm chạy ở **SF=1** (lineitem ~6M dòng); thời gian dưới giây nên chênh lệch tuyệt đối nhỏ, nhưng tỷ số System/Bernoulli phản ánh đúng *xu hướng* hiệu năng giữa hai phương pháp.
> 2. **System (block) nhanh hơn Bernoulli (row)** phù hợp với nhận định của bài gốc (Table 5): lấy mẫu mức khối đọc theo trang/row-group nên ít chi phí I/O ngẫu nhiên hơn so với đánh giá xác suất từng dòng của Bernoulli.
> 3. **Lưu ý phạm vi:** biến thể *block-level fixed-size* chính xác của bài gốc dùng extension PostgreSQL `tsm_system_rows` (chỉ có trên Postgres). Ở đây trên DuckDB, chúng tôi dùng `TABLESAMPLE SYSTEM(p%)` (block-level, theo tỷ lệ) làm đại diện cho lấy mẫu mức khối, so với `TABLESAMPLE BERNOULLI(p%)` (row-level). Đây là so sánh *Bernoulli vs block sampling* đúng nghĩa, không phải tái lập y hệt con số 3.8% fixed-size của Postgres.

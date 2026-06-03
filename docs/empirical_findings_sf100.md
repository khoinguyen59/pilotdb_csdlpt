# Báo Cáo Thực Nghiệm Bổ Sung: Các Phát Hiện Định Lượng & Giới Hạn Kỹ Thuật Ở Quy Quy Mô SF100 (100GB)

Tài liệu này ghi nhận toàn bộ các phát hiện khoa học, hiện tượng nghẽn vật lý, lỗi logic giải thuật và cơ chế tự phục hồi tự động của hệ thống **PilotDB AQP** thu hoạch được từ loạt chạy thực nghiệm sạch (Clean Re-run) trên máy chủ đám mây GCP ở quy mô dữ liệu lớn **Scale Factor = 100 (100GB)**. Các số liệu và chẩn đoán này là nguồn tài liệu đắt giá để bổ sung trực tiếp vào **Chương 5 (Thực nghiệm)** và **Chương 6 (Thảo luận)** trong báo cáo chính thức môn học **Cơ sở Dữ liệu Phân tán**.

---

## 1. Hiện Tượng 1: Nhiễm Bẩn Cache Layer-2 & Lỗi Tổng Hợp Chỉ Số Q8

### 1.1. Bản Chất Vấn Đề (Nhiễm Cache từ SF10)
Trong các lượt chạy thực nghiệm PostgreSQL SF100 đầu tiên, nhật trình hệ thống ghi nhận **100% câu truy vấn đều báo lý do Fallback là `cache_hit_template`**. 
* **Nguyên nhân**: Tệp cơ sở dữ liệu cache template `.pilotdb_cache.db` (dung lượng 20KB) sinh ra từ loạt chạy cục bộ **SF10** trước đó không được dọn dẹp trước khi chạy suite SF100. 
* **Cơ chế lỗi**: Bộ tối ưu hóa Pilot đánh giá khớp mẫu truy vấn (Layer-2 Template Cache) ngay ở đầu chuỗi logic thực thi (trước khi đánh giá chốt chặn chi phí `PILOTDB_POSTGRES_COST_THRESHOLD`). Hệ thống đã nhận diện mẫu truy vấn tương tự và tái áp dụng ngay quyết định lấy mẫu cũ của quy mô SF10, hoàn toàn bỏ qua các tính toán tối ưu hóa thực tế cho tập dữ liệu lớn 100GB.

### 1.2. Lỗi Tỷ Lệ Lấy Mẫu Không Tưởng `252.50%` ở Q8
* **Hiện tượng**: Báo cáo tổng hợp in ra tỷ lệ lấy mẫu cuối cùng của truy vấn Q8 đạt mức vô lý là `252.50%`.
* **Phân tích lỗi**: 
  1. Trong tệp cache template cũ `.pilotdb_cache.db`, tỷ lệ lấy mẫu được lưu dưới dạng phân số/hệ số thập phân (`0.05` đại diện cho 5%).
  2. Ở các phiên chạy live AQP thực tế, hệ thống trả về tỷ lệ lấy mẫu dưới dạng phần trăm thực (`5.0` đại diện cho 5%).
  3. Bộ tổng hợp kết quả (`run_benchmark_suite.py`) thực hiện tính trung bình cộng tập hợp chứa cả số thập phân và phần trăm: $\text{Average}(5.0, 0.05) = 2.525$. Sau đó, bộ tổng hợp tiếp tục nhân giá trị trung bình này với $100.0$ một lần nữa ở đầu ra báo cáo, dẫn đến con số sai lệch nghiêm trọng `252.50%`.
* **Giải pháp khắc phục**: Đã thực hiện xóa sạch hoàn toàn `.pilotdb_cache.db` trên VM trước khi kích hoạt chạy sạch, đồng thời điều chỉnh lại bộ tổng hợp dữ liệu để đồng bộ hóa định dạng biểu diễn tỷ lệ lấy mẫu.

---

## 2. Hiện Tượng 2: Nghẽn Cấp Phát Bộ Nhớ TiB & Cơ Chế Tự Phục Hồi Trên Q18

Đây là phát hiện khoa học đắt giá nhất của đợt chạy sạch SF100, minh chứng cho các thách thức vật lý thực tế của giải thuật AQP khi đối mặt với các phép Join đa bảng phức tạp trên dữ liệu lớn.

### 2.1. Phân Tích Hiện Tượng Trên Q18 (Iteration 0)
Truy vấn Q18 thực hiện gom nhóm (`GROUP BY`) và liên kết (`JOIN`) 3 bảng lớn (`customer`, `orders`, `lineitem`) kèm một subquery lọc phức tạp. 
* **Quá trình quét Pilot**: Hệ thống mất tới **1391.88 giây (~23.2 phút)** chỉ để quét lấy mẫu Pilot 1.0% trên đĩa lạnh PostgreSQL Heap.
* **Lỗi vật lý phát sinh**: Khi PilotDB kích hoạt hàm `JoinBlockStats` để ước lượng ma trận hiệp phương sai sai số khối liên kết, hệ thống cần khởi tạo một mảng dữ liệu 2 chiều có kích thước cực lớn:
  $$\text{Shape} = (1,132,305 \text{ dòng} \times 263,158 \text{ cột})$$
  Với kiểu dữ liệu số thực dấu phẩy động 64-bit (`float64`), mảng này yêu cầu cấp phát liên tục:
  $$\text{Dung lượng yêu cầu} = 1,132,305 \times 263,158 \times 8 \text{ bytes} \approx 2.38 \times 10^{12} \text{ bytes} \approx \mathbf{2.17\text{ TiB}}$$
* **Trạng thái phần cứng**: Máy ảo GCE `n2-standard-8` chỉ được trang bị **32 GB RAM**, dẫn đến việc hệ điều hành từ chối cấp phát bộ nhớ ảo và ném ngoại lệ:
  > `[JoinBlockStats] Failed to extract from pilot results: Unable to allocate 2.17 TiB for an array with shape (1132305, 263158) and data type float64`

### 2.2. Cơ Chế Tự Phục Hồi An Toàn (Graceful Safety Degradation)
Thay vì làm sập tiến trình Python đang chạy, PilotDB đã kích hoạt chuỗi chốt chặn tự phục hồi cực kỳ chuẩn mực theo thiết kế học thuật:
1. **Bắt ngoại lệ cấp phát**: Hệ thống ghi nhận lỗi OOM của mảng và tự động chuyển cấp dự phòng về `scalar variance proxy` (bộ ước lượng phương sai vô hướng đơn giản).
2. **Vô hiệu hóa ma trận $\Phi$**: Hệ thống báo cáo không thể xây dựng ma trận liên kết nhóm:
   `[Phi] No constraints built; falling back to scalar mode`
3. **Kích hoạt chốt chặn an toàn học thuật (Academic Guardrail)**: Nhận thấy đây là truy vấn Join đa bảng phức tạp nhưng không thể tính toán đầy đủ ma trận $\Phi(\Theta)$ để kiểm soát sai số, hệ thống tự động ngăn chặn việc lấy mẫu AQP để tránh sai số vô hạn (vượt quá mục tiêu 5%):
   `[GUARDRAIL] Multi-table query with 2 sampled tables but NO Phi(Theta) constraints. Falling back to EXACT execution...`
4. **Thực thi Exact dự phòng**: Hệ thống tự động nâng tỷ lệ lấy mẫu lên $100\%$, kích hoạt fallback chạy câu lệnh chính xác trực tiếp trên PostgreSQL, hoàn thành trong **1208.51s (~20.1 phút)**.
5. **Ý nghĩa học thuật**: Phát hiện này chứng minh cơ chế bảo vệ tính chính xác của PilotDB hoạt động vô cùng nhạy bén và hiệu quả trên quy mô dữ liệu thực tế, ưu tiên độ tin cậy của dữ liệu lên trên hết.

---

## 3. Hiện Tượng 3: Điểm Nghẽn Quét Đĩa Lạnh Vật Lý (Cold Disk I/O Bottleneck)

Thực nghiệm SF100 trên PostgreSQL Native đã phơi bày một đặc tính vật lý quan trọng của CSDL dạng dòng (Row-store) truyền thống:
* **Thời gian thực thi Exact**: Tất cả các câu truy vấn cơ bản quét tuần tự (Sequential Scan) trên PostgreSQL đều dao động ổn định ở mức cực cao, từ **400s đến 550s (gần 7 - 9 phút)** cho mỗi lượt chạy, và lên đến **20 phút** ở Q18.
* **Nguyên nhân vật lý**: Quy mô SF100 sở hữu lượng dữ liệu thô khổng lồ (~93GB riêng cho 2 bảng `lineitem` và `orders`), vượt xa dung lượng RAM đệm (32GB) của máy chủ. Bộ đệm trang của PostgreSQL liên tục xảy ra hiện tượng ghi đè (cache thrashing), buộc hệ quản trị phải đọc dữ liệu tuần tự trực tiếp từ đĩa cứng vật lý lạnh với băng thông SSD bị giới hạn (~240 MB/s).
* **Ảnh hưởng đến AQP**: Điểm nghẽn đọc đĩa vật lý lạnh ảnh hưởng trực tiếp đến tốc độ cải thiện (Speedup) của AQP. Vì pha quét Pilot 1.0% vẫn phải gánh chịu chi phí quét đĩa vật lý ban đầu (khoảng 72 giây cho Q1), nên đối với các câu truy vấn đơn giản chạy nhanh, chi phí quét Pilot và chi phí giải toán tối đôi khi vượt quá lợi ích lấy mẫu mang lại. AQP chỉ thực sự phát huy speedup vượt trội ($4\times - 10\times$) khi cấu hình sai số được nới lỏng hoặc khi bộ đệm RAM đủ lớn để giữ các trang Pilot.

---

## 4. Xác Định Đúng Cấu Hình Máy Chủ Thực Nghiệm (Scientific Honesty)

Để đảm bảo báo cáo khoa học không có bất kỳ điểm mâu thuẫn nội bộ nào, cấu hình phần cứng của hai đợt chạy đã được ghi nhận riêng biệt và cam kết trung thực:

1. **Thực nghiệm hệ DuckDB SF100 (Chạy ngày 21/05/2026)**:
   * **Máy ảo**: GCP Compute Engine, dòng máy **`e2-standard-4`**.
   * **Cấu hình**: 4 vCPUs, 16 GB RAM, SSD 150GB.
   * **Hệ điều hành / CSDL**: Debian Linux, DuckDB v1.5.3.
2. **Thực nghiệm hệ PostgreSQL SF100 (Chạy ngày 30/05/2026 - 01/06/2026)**:
   * **Máy ảo**: GCP Compute Engine, dòng máy **`n2-standard-8`** (để đáp ứng bộ nhớ chạy PostgreSQL quy mô lớn).
   * **Cấu hình**: 8 vCPUs, 32 GB RAM, SSD 150GB.
   * **Hệ điều hành / CSDL**: Debian Linux, PostgreSQL 16.3 (Cấu hình mặc định ngoài hộp - Out-of-the-box configuration).

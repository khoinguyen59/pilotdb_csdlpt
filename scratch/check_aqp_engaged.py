#!/usr/bin/env python3
"""Kiểm chứng AQP có THỰC SỰ engage hay không (không bị fallback 100%).

Đọc <output-dir>/aggregated_report.json (do run_benchmark_suite.py sinh ra) và
in bảng tóm tắt: câu nào chạy AQP thật (fallback < 100%), speedup, sai số.

Exit code 0 nếu CÓ ít nhất 1 câu engage AQP (bằng chứng thật);
exit code 1 nếu tất cả đều fallback (chưa chứng minh được) — để script gọi biết mà rẽ nhánh.

Usage:
    python scratch/check_aqp_engaged.py <output-dir> [output-dir2 ...]
"""
import json
import sys
from pathlib import Path

# Tránh crash UnicodeEncodeError trên console Windows (cp1252) khi in tiếng Việt / ký hiệu.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def load_report(out_dir: Path):
    p = out_dir / "aggregated_report.json"
    if not p.exists():
        print(f"  [!] KHÔNG tìm thấy {p} — benchmark chưa chạy hoặc lỗi.")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  [!] Lỗi đọc {p}: {e}")
        return None


def summarize(out_dir: Path) -> bool:
    rows = load_report(out_dir)
    if not rows:
        return False

    print(f"\n=== {out_dir} ===")
    hdr = f"{'Query':<7}{'Exact(s)':>10}{'AQP(s)':>10}{'Speedup':>9}{'Rate%':>8}{'Fallback%':>11}{'MeanErr%':>10}"
    print(hdr)
    print("-" * len(hdr))

    engaged = []
    for r in rows:
        qid = r.get("query_id", "?")
        ex = r.get("mean_exact_s", 0.0)
        aqp = r.get("mean_aqp_s", 0.0)
        spd = r.get("mean_speedup", 0.0)
        rate = r.get("mean_final_sample_rate_pct", 100.0)
        fb = r.get("fallback_rate_pct", 100.0)
        err = (r.get("mean_row_relative_error", 0.0) or 0.0) * 100.0
        # "Engaged" = ít nhất 1 lần chạy không fallback VÀ có lấy mẫu thật (<100%).
        is_engaged = fb < 100.0 and rate < 100.0
        flag = "  <== AQP" if is_engaged else ""
        if is_engaged:
            engaged.append((qid, spd, rate, err))
        print(f"{qid:<7}{ex:>10.2f}{aqp:>10.2f}{spd:>9.2f}{rate:>8.2f}{fb:>11.1f}{err:>10.3f}{flag}")

    print("-" * len(hdr))
    if engaged:
        print(f"  ✓ AQP ENGAGE THẬT trên {len(engaged)} câu: "
              + ", ".join(f"{q}({s:.2f}x@{r:.1f}%,err={e:.2f}%)" for q, s, r, e in engaged))
        return True
    print("  ✗ TẤT CẢ đều fallback về exact — CHƯA có bằng chứng AQP engage.")
    return False


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    any_engaged = False
    for d in argv[1:]:
        if summarize(Path(d)):
            any_engaged = True
    print()
    return 0 if any_engaged else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

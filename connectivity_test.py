"""
Task 0: 실행 환경에서 바이낸스 API 접근성 테스트.

GitHub Actions 러너(주로 미국 리전)에서 바이낸스 fapi가 지역 차단(HTTP 451)되는지
확인한다. 이 결과가 전체 아키텍처를 결정한다:
  A. 전부 정상          → fapi 직접 호출 (계획대로)
  B. fapi 차단 + vision 정상 → data.binance.vision 일 덤프로 전환, 폴링 일 1회
  C. 둘 다 차단          → Actions 포기, 아시아 리전 VM 검토

로컬/Actions 어디서든 실행 가능:  python connectivity_test.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TARGETS = [
    ("ping",       "https://fapi.binance.com/fapi/v1/ping"),
    ("klines",     "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=5"),
    ("oi_hist",    "https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=5"),
    ("vision_root", "https://data.binance.vision/"),
]

# plan B의 실제 수집원: index가 아니라 zip 오브젝트를 러너가 받을 수 있어야 한다.
# (CDN geo 정책이 index와 다를 수 있으므로 실제 다운로드로 확인) 최근 완료일 후보를 순회.
VISION_ZIP_CANDIDATES = [
    "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/5m/BTCUSDT-5m-{d}.zip",
    "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{d}.zip",
]
_UA = "netls-connectivity-test/2.0"


def probe(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(400).decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read(400).decode("utf-8", "replace") if e.fp else ""
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def probe_zip() -> tuple[bool, str]:
    """실제 vision zip 다운로드 성공 여부(zip 매직 'PK' 확인). 최근 5일 중 하나라도 성공하면 OK."""
    import datetime as _dt
    # 러너에 명시적 날짜 계산 필요(오늘 덤프는 미발행 → 1~6일 전 순회)
    try:
        base = _dt.datetime.now(_dt.timezone.utc).date()
    except Exception:  # noqa: BLE001
        base = None
    days = []
    if base is not None:
        days = [(base - _dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 7)]
    for d in days:
        for tmpl in VISION_ZIP_CANDIDATES:
            url = tmpl.format(d=d)
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    head = r.read(4)
                    if r.status == 200 and head[:2] == b"PK":
                        return True, f"OK {url} ({d}, zip magic PK 확인)"
            except urllib.error.HTTPError as e:
                if e.code == 451:
                    return False, f"HTTP 451 (지역차단) {url}"
                continue
            except Exception:  # noqa: BLE001
                continue
    return False, "최근 6일 zip 다운로드 실패(파일 미발행이거나 차단)"


def main() -> int:
    results: dict[str, int] = {}
    print("=" * 70)
    print("Task 0: Binance API 접근성 테스트")
    print("=" * 70)
    for name, url in TARGETS:
        status, body = probe(url)
        results[name] = status
        summary = body.replace("\n", " ")[:120]
        print(f"[{name:9s}] HTTP {status:<4}  {url}")
        print(f"            → {summary}")

    # plan B 실효성은 zip 실제 다운로드로 판정(index 200만으로는 불충분).
    zip_ok, zip_msg = probe_zip()
    print(f"[vision_zip] {'OK' if zip_ok else 'FAIL'}  {zip_msg}")
    results["vision_zip"] = 200 if zip_ok else -1

    fapi_ok = all(results.get(k) == 200 for k in ("ping", "klines", "oi_hist"))

    print("-" * 70)
    if fapi_ok:
        verdict = "A"
        msg = "fapi 전부 정상 → fapi 직접 호출 경로(source=fapi) 사용 가능"
    elif zip_ok:
        verdict = "B"
        msg = ("fapi 차단 + vision zip 다운로드 정상 → data.binance.vision 일 덤프 전환"
               " (source=vision, collect.yml 일 1회). 현재 리포 기본 구성이 이 경로임.")
    else:
        verdict = "C"
        msg = "fapi·vision 모두 차단 → Actions 포기, 아시아 리전 VM 검토 필요"

    print(f"판정: {verdict}\n{msg}")
    print("결과 JSON:", json.dumps({"results": results, "verdict": verdict}))

    # Actions Step Summary 로 노출 (있을 때만)
    import os
    spath = os.environ.get("GITHUB_STEP_SUMMARY")
    if spath:
        with open(spath, "a", encoding="utf-8") as f:
            f.write(f"## Task 0 접근성 판정: **{verdict}**\n\n{msg}\n\n")
            f.write("| target | status |\n|---|---|\n")
            for k, v in results.items():
                f.write(f"| {k} | {v} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

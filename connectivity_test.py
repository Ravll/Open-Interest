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
    ("vision",     "https://data.binance.vision/"),
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

    fapi_ok = all(results.get(k) == 200 for k in ("ping", "klines", "oi_hist"))
    vision_ok = results.get("vision", -1) in (200, 301, 302, 403)  # 디렉터리 리스팅 정책 편차 허용

    print("-" * 70)
    if fapi_ok:
        verdict = "A"
        msg = "fapi 전부 정상 → 계획대로 fapi 직접 호출 (collect.yml 30분 주기)"
    elif vision_ok:
        verdict = "B"
        msg = "fapi 차단 + vision 정상 → data.binance.vision 일 덤프 전환, 폴링 일 1회"
    else:
        verdict = "C"
        msg = "fapi·vision 모두 차단 → Actions 포기, 아시아 리전 VM 검토"

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

"""
Binance USDT-M Futures 공개 API 접근 모듈 (무인증).

verify_netls.py / collector.py 공용. stdlib(urllib)만 사용해 GitHub Actions에서
추가 의존성 없이 동작한다. 지역 차단(HTTP 451) 및 레이트리밋(418/429)을 명시적으로
탐지·처리한다.

엔드포인트별 가용 한도 (2026 기준):
- /fapi/v1/klines            : limit<=1500, startTime/endTime 페이지네이션, 장기 소급 가능
- /futures/data/openInterestHist : limit<=500,  최근 30일만 제공 (핵심 제약)
- /fapi/v1/fundingRate       : limit<=1000, startTime/endTime 페이지네이션
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

FAPI_BASE = "https://fapi.binance.com"
DATA_VISION = "https://data.binance.vision"

# 바이낸스 futures/data 계열 period 파라미터로 허용되는 값
VALID_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}

_UA = "netls-collector/2.0 (+https://github.com; public-data)"


class RegionBlockedError(RuntimeError):
    """HTTP 451 등 지역 차단으로 판단되는 응답."""


class BinanceAPIError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        self.status = status
        self.url = url
        self.body = body[:500]
        super().__init__(f"HTTP {status} for {url}: {self.body}")


def _request(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, body


def get_json(path: str, params: dict[str, Any] | None = None, *,
             base: str = FAPI_BASE, max_retries: int = 5) -> Any:
    """
    지수 백오프 재시도가 포함된 JSON GET.

    - 451: 지역 차단 → RegionBlockedError (재시도 무의미, 즉시 전파)
    - 418/429: 레이트리밋 → 백오프 후 재시도
    - 5xx: 일시 오류 → 백오프 후 재시도
    - 그 외 4xx: BinanceAPIError 즉시 전파
    """
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = f"{base}{path}{query}"
    delay = 1.0
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            status, body = _request(url)
        except (urllib.error.URLError, TimeoutError) as e:
            last_exc = e
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue

        if status == 200:
            return json.loads(body)
        if status == 451:
            raise RegionBlockedError(f"HTTP 451 (region blocked) for {url}: {body[:200]}")
        if status in (418, 429) or 500 <= status < 600:
            # 레이트리밋/서버오류: Retry-After 힌트가 없으므로 지수 백오프
            time.sleep(delay)
            delay = min(delay * 2, 60)
            last_exc = BinanceAPIError(status, url, body)
            continue
        raise BinanceAPIError(status, url, body)

    raise BinanceAPIError(getattr(last_exc, "status", -1), url, str(last_exc))


# ----------------------------------------------------------------------------
# 엔드포인트 래퍼 (원시 응답 그대로 반환; 파싱은 호출측 책임)
# ----------------------------------------------------------------------------

def ping() -> None:
    get_json("/fapi/v1/ping")


def klines(symbol: str, interval: str, *, start_ms: int | None = None,
           end_ms: int | None = None, limit: int = 1500) -> list[list]:
    """
    선물 캔들. 각 원소는 12필드 리스트:
      [0]open_time [1]open [2]high [3]low [4]close [5]volume(base)
      [6]close_time [7]quote_volume [8]trades
      [9]taker_buy_base [10]taker_buy_quote [11]ignore
    """
    params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    return get_json("/fapi/v1/klines", params)


def open_interest_hist(symbol: str, period: str, *, start_ms: int | None = None,
                       end_ms: int | None = None, limit: int = 500) -> list[dict]:
    """
    미결제약정 히스토리 (최근 30일 한도).
    각 원소: {symbol, sumOpenInterest(기초자산), sumOpenInterestValue(USD),
              CMCCirculatingSupply, timestamp}
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"invalid period {period!r}")
    params: dict[str, Any] = {"symbol": symbol, "period": period, "limit": limit}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    return get_json("/futures/data/openInterestHist", params)


def funding_rate(symbol: str, *, start_ms: int | None = None,
                 end_ms: int | None = None, limit: int = 1000) -> list[dict]:
    """
    펀딩비 히스토리 (8시간 주기). 각 원소: {symbol, fundingTime, fundingRate, markPrice}.
    """
    params: dict[str, Any] = {"symbol": symbol, "limit": limit}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    return get_json("/fapi/v1/fundingRate", params)

"""
data.binance.vision 일/월 덤프 접근 모듈 (Task 0 판정 B 경로).

GitHub Actions 러너(미국 리전)에서 fapi가 지역 차단될 때, 바이낸스가 공개하는
일 단위 데이터 덤프에서 동일 원천을 받아온다. fapi의 OI 30일 제한이 없어 과거
소급이 훨씬 길다. 단점은 전일 데이터라 하루 지연(백테스트 용도엔 무영향).

경로 규격 (USD-M 선물):
  klines : {V}/data/futures/um/daily/klines/{SYM}/5m/{SYM}-5m-{YYYY-MM-DD}.zip
  metrics: {V}/data/futures/um/daily/metrics/{SYM}/{SYM}-metrics-{YYYY-MM-DD}.zip  (OI 포함)
  funding: {V}/data/futures/um/monthly/fundingRate/{SYM}/{SYM}-fundingRate-{YYYY-MM}.zip

시간 기준: 모든 타임스탬프는 UTC. metrics의 create_time은 'YYYY-MM-DD HH:MM:SS'
문자열, klines의 open_time과 funding의 calc_time은 epoch(ms).
"""
from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

from binance_api import RegionBlockedError

VISION = "https://data.binance.vision"
UM = "/data/futures/um"
_UA = "netls-collector/2.0 (+https://github.com; public-data)"


def _download(url: str, *, timeout: int = 60) -> bytes | None:
    """zip 바이트 반환. 없는 날짜(404)는 None. 451은 지역차단으로 전파."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 451:
            raise RegionBlockedError(f"HTTP 451 (region blocked) for {url}")
        raise


def _csv_rows(data: bytes) -> list[list[str]]:
    z = zipfile.ZipFile(io.BytesIO(data))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    return list(csv.reader(text.splitlines()))


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def dt_utc_to_ms(s: str) -> int:
    dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ----------------------------------------------------------------------------
def klines_day(symbol: str, date_str: str) -> dict[int, list]:
    """open_time(ms) -> kline 행(문자열 리스트). 없는 날은 빈 dict."""
    url = f"{VISION}{UM}/daily/klines/{symbol}/5m/{symbol}-5m-{date_str}.zip"
    data = _download(url)
    if data is None:
        return {}
    out: dict[int, list] = {}
    for row in _csv_rows(data):
        if not row or not _is_number(row[0]):  # 헤더 스킵(파일별 유무 상이)
            continue
        out[int(row[0])] = row
    return out


def metrics_day(symbol: str, date_str: str) -> dict[int, dict]:
    """
    create_time(ms) -> {sum_oi, sum_oi_value, top_acc_ls_ratio, top_pos_ls_ratio,
                        global_acc_ls_ratio, taker_ls_vol_ratio}. 없는 날은 빈 dict.

    포지셔닝 비율 4종(상위트레이더 계정수/포지션, 전체 계정수, 테이커 매수/매도 볼륨)은
    원본 metrics 파일에 함께 제공되므로 같이 저장한다. 파일에 없으면 None.
    """
    url = f"{VISION}{UM}/daily/metrics/{symbol}/{symbol}-metrics-{date_str}.zip"
    data = _download(url)
    if data is None:
        return {}
    rows = _csv_rows(data)
    if not rows:
        return {}
    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}
    ct = idx.get("create_time", 0)
    cols = {
        "sum_oi": idx.get("sum_open_interest"),
        "sum_oi_value": idx.get("sum_open_interest_value"),
        "top_acc_ls_ratio": idx.get("count_toptrader_long_short_ratio"),
        "top_pos_ls_ratio": idx.get("sum_toptrader_long_short_ratio"),
        "global_acc_ls_ratio": idx.get("count_long_short_ratio"),
        "taker_ls_vol_ratio": idx.get("sum_taker_long_short_vol_ratio"),
    }
    out: dict[int, dict] = {}
    for row in rows[1:]:
        try:
            ts = dt_utc_to_ms(row[ct])
        except (ValueError, IndexError):
            continue
        rec = {}
        for key, i in cols.items():
            try:
                rec[key] = float(row[i]) if i is not None and row[i] != "" else None
            except (ValueError, IndexError):
                rec[key] = None
        if rec.get("sum_oi") is not None:
            out[ts] = rec
    return out


def funding_month(symbol: str, ym: str) -> dict[int, float]:
    """calc_time(ms) -> last_funding_rate. 없는 달은 빈 dict."""
    url = f"{VISION}{UM}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
    data = _download(url)
    if data is None:
        return {}
    rows = _csv_rows(data)
    out: dict[int, float] = {}
    start = 1 if rows and not _is_number(rows[0][0]) else 0  # 헤더 유무 감지
    for row in rows[start:]:
        if not row or not _is_number(row[0]):
            continue
        # calc_time, funding_interval_hours, last_funding_rate
        out[int(row[0])] = float(row[-1])
    return out

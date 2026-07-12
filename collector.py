"""
Task 2: 백필형 수집기.

매 실행마다 저장소의 마지막 타임스탬프를 조회해 그 이후부터 현재까지 수집한다
(idempotent). 실행 주기·cron 지연과 무관하게 데이터 연속성을 보장한다.

핵심 원칙:
1. 백필 우선: 마지막 저장 지점 → 현재. 최초 실행은 설정한 최대 일수까지 소급.
2. raw 변화량 저장: 봉 자체 완결값(cvd_delta, 스냅샷 sum_oi)만 저장. 누적 러닝값 금지.
3. 완결봉만 저장: 아직 마감 안 된 현재 진행봉은 제외.
4. 무결성: 5분 그리드 결측 탐지→1회 재시도, PK 중복 차단, 실행별 로그 기록.

사용:
    python collector.py                 # config.json 기반 전 심볼 수집
    python collector.py --symbol BTCUSDT
    python collector.py --config config.json
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import binance_api as api
import netls
from netls import BAR_MS
from store import CSVStore

FUNDING_MS = 8 * 3600 * 1000  # 펀딩 주기(참고용)


def fnum(x) -> str:
    """부동소수를 결정적 문자열로(diff 안정화). None→''."""
    if x is None:
        return ""
    if isinstance(x, int):
        return str(x)
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# ----------------------------------------------------------------------------
# klines 페이지네이션 (완결봉만)
# ----------------------------------------------------------------------------
def fetch_klines_range(symbol: str, start_ms: int, end_ms: int) -> dict[int, list]:
    """open_time -> raw kline. [start_ms, end_ms) 의 완결봉만."""
    out: dict[int, list] = {}
    cursor = start_ms
    while cursor < end_ms:
        batch = api.klines(symbol, "5m", start_ms=cursor, end_ms=end_ms, limit=1500)
        if not batch:
            break
        for k in batch:
            ot = int(k[0])
            # 완결봉만: 마감시각(open+5m)이 end_ms 이하
            if ot + BAR_MS <= end_ms:
                out[ot] = k
        last_open = int(batch[-1][0])
        if last_open + BAR_MS >= end_ms or len(batch) < 2:
            break
        cursor = last_open + BAR_MS
        time.sleep(0.25)
    return out


def fetch_oi_range(symbol: str, start_ms: int, end_ms: int) -> dict[int, dict]:
    """timestamp -> OI 스냅샷. openInterestHist는 최근 30일만 가능."""
    out: dict[int, dict] = {}
    cursor = start_ms
    while cursor < end_ms:
        batch = api.open_interest_hist(symbol, "5m", start_ms=cursor, end_ms=end_ms, limit=500)
        if not batch:
            break
        for o in batch:
            out[int(o["timestamp"])] = o
        last_ts = int(batch[-1]["timestamp"])
        if last_ts + BAR_MS >= end_ms or len(batch) < 2:
            break
        cursor = last_ts + BAR_MS
        time.sleep(0.25)
    return out


def fetch_funding_range(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        batch = api.funding_rate(symbol, start_ms=cursor, end_ms=end_ms, limit=1000)
        if not batch:
            break
        out.extend(batch)
        last_ts = int(batch[-1]["fundingTime"])
        if len(batch) < 1000:
            break
        cursor = last_ts + 1
        time.sleep(0.25)
    # dedup
    dedup = {int(f["fundingTime"]): f for f in out}
    return [dedup[t] for t in sorted(dedup)]


# ----------------------------------------------------------------------------
# 봉 행 조립 (klines + OI + 파생지표)
# ----------------------------------------------------------------------------
def build_bar_rows(symbol: str, kl_map: dict[int, list],
                   oi_map: dict[int, dict]) -> list[dict]:
    rows: list[dict] = []
    for ot in sorted(kl_map):
        k = kl_map[ot]
        volume = float(k[5])
        tbb = float(k[9])
        cvd = netls.cvd_delta(tbb, volume)

        sum_oi = sum_oi_val = oi_delta = nl = ns = None
        oi = oi_map.get(ot)
        if oi is not None:
            sum_oi = float(oi["sumOpenInterest"])
            sum_oi_val = float(oi["sumOpenInterestValue"])
            prev = oi_map.get(ot - BAR_MS)
            if prev is not None:
                oi_delta = sum_oi - float(prev["sumOpenInterest"])
                nl = netls.net_long_delta(oi_delta, cvd)
                ns = netls.net_short_delta(oi_delta, cvd)

        rows.append({
            "symbol": symbol,
            "open_time": ot,
            "datetime_utc": netls.ms_to_utc(ot),
            "datetime_kst": netls.ms_to_kst(ot),
            "open": fnum(float(k[1])), "high": fnum(float(k[2])),
            "low": fnum(float(k[3])), "close": fnum(float(k[4])),
            "volume": fnum(volume), "quote_volume": fnum(float(k[7])),
            "trades": int(k[8]),
            "taker_buy_base": fnum(tbb), "taker_buy_quote": fnum(float(k[10])),
            "sum_oi": fnum(sum_oi), "sum_oi_value": fnum(sum_oi_val),
            "cvd_delta": fnum(cvd), "oi_delta": fnum(oi_delta),
            "net_long_delta": fnum(nl), "net_short_delta": fnum(ns),
        })
    return rows


def build_funding_rows(symbol: str, raw: list[dict]) -> list[dict]:
    rows = []
    for f in raw:
        ft = int(f["fundingTime"])
        rows.append({
            "symbol": symbol,
            "funding_time": ft,
            "datetime_utc": netls.ms_to_utc(ft),
            "datetime_kst": netls.ms_to_kst(ft),
            "funding_rate": fnum(float(f["fundingRate"])),
            "mark_price": fnum(float(f["markPrice"])) if f.get("markPrice") else "",
        })
    return rows


def count_missing(kl_map: dict[int, list], start_ms: int, end_ms: int) -> tuple[int, int, int]:
    """[start,end) 그리드 대비 결측 봉 수. (기대, 실측, 결측)."""
    grid_start = netls.align_floor(start_ms)
    expected = [t for t in range(grid_start, end_ms, BAR_MS) if t + BAR_MS <= end_ms and t >= start_ms]
    got = sum(1 for t in expected if t in kl_map)
    return len(expected), got, len(expected) - got


# ----------------------------------------------------------------------------
# 심볼 단위 수집
# ----------------------------------------------------------------------------
def collect_symbol(store: CSVStore, symbol: str, cfg: dict, run_started: str) -> list[dict]:
    logs: list[dict] = []
    now = netls.now_ms()
    # 완결봉 경계: 현재 진행봉 제외
    end_ms = netls.align_floor(now)

    bf = cfg["backfill"]

    # --- bars (klines + OI) ------------------------------------------------
    t0 = time.time()
    last = store.last_bar_time(symbol)
    if last is None:
        start_ms = int(end_ms - bf["klines_max_days"] * 86400_000)
        store.set_meta_once(symbol, "klines_backfill_start_ms", start_ms)
        store.set_meta_once(symbol, "klines_backfill_start_kst", netls.ms_to_kst(start_ms))
    else:
        start_ms = last + BAR_MS

    written = 0
    missing = 0
    note = ""
    if start_ms < end_ms:
        kl_map = fetch_klines_range(symbol, start_ms, end_ms)
        # 결측 1회 재시도
        exp, got, missing = count_missing(kl_map, start_ms, end_ms)
        if missing > 0:
            retry = fetch_klines_range(symbol, start_ms, end_ms)
            kl_map.update(retry)
            _, _, missing = count_missing(kl_map, start_ms, end_ms)

        # OI: 최근 30일 한도. 직전봉 하나 포함해 oi_delta 계산 가능하게.
        oi_floor = int(end_ms - bf["oi_max_days"] * 86400_000)
        oi_start = max(start_ms - BAR_MS, oi_floor)
        oi_map = fetch_oi_range(symbol, oi_start, end_ms) if oi_start < end_ms else {}
        if oi_map:
            earliest_oi = min(oi_map)
            store.set_meta_once(symbol, "oi_backfill_start_ms", earliest_oi)
            store.set_meta_once(symbol, "oi_backfill_start_kst", netls.ms_to_kst(earliest_oi))

        rows = build_bar_rows(symbol, kl_map, oi_map)
        written = store.upsert_bars(symbol, rows)
        if start_ms < oi_floor:
            note = "OI 30일 한도 초과 구간은 OI/파생지표 NULL"
    else:
        note = "신규 완결봉 없음"

    logs.append({
        "run_started_utc": run_started, "symbol": symbol, "domain": "bars",
        "rows_written": written, "range_start_ms": start_ms, "range_end_ms": end_ms,
        "missing_bars": missing, "elapsed_sec": round(time.time() - t0, 2), "note": note,
    })

    # --- funding -----------------------------------------------------------
    t1 = time.time()
    last_f = store.last_funding_time(symbol)
    if last_f is None:
        f_start = int(end_ms - bf["funding_max_days"] * 86400_000)
        store.set_meta_once(symbol, "funding_backfill_start_ms", f_start)
    else:
        f_start = last_f + 1
    f_written = 0
    if f_start < now:
        raw_f = fetch_funding_range(symbol, f_start, now)
        f_written = store.upsert_funding(symbol, build_funding_rows(symbol, raw_f))
    logs.append({
        "run_started_utc": run_started, "symbol": symbol, "domain": "funding",
        "rows_written": f_written, "range_start_ms": f_start, "range_end_ms": now,
        "missing_bars": 0, "elapsed_sec": round(time.time() - t1, 2), "note": "",
    })
    return logs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NetLS 백필형 수집기")
    p.add_argument("--config", default="config.json")
    p.add_argument("--symbol", help="단일 심볼만 수집(설정 override)")
    args = p.parse_args(argv)

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    symbols = [args.symbol] if args.symbol else cfg["symbols"]
    store = CSVStore(cfg.get("data_dir", "data"))
    run_started = netls.ms_to_utc(netls.now_ms())

    all_logs: list[dict] = []
    try:
        for sym in symbols:
            print(f"[{sym}] 수집 시작...")
            logs = collect_symbol(store, sym, cfg, run_started)
            all_logs.extend(logs)
            for lg in logs:
                print(f"  {lg['domain']:8s} rows={lg['rows_written']:5d} "
                      f"missing={lg['missing_bars']} {lg['elapsed_sec']}s {lg['note']}")
    except api.RegionBlockedError as e:
        print(f"[지역차단] {e}")
        store.append_runlog(all_logs)
        return 2

    store.append_runlog(all_logs)

    total = sum(lg["rows_written"] for lg in all_logs)
    parts = [f"{lg['symbol']}/{lg['domain']}+{lg['rows_written']}"
             for lg in all_logs if lg["rows_written"] > 0]
    summary = (f"collect {run_started}Z | rows={total} | "
               + (", ".join(parts) if parts else "no new data"))
    store.write_summary(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

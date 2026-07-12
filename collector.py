"""
Task 2: 백필형 수집기 (Task 0 판정 B → data.binance.vision 일 덤프 경로).

매 실행마다 저장소의 마지막 타임스탬프를 조회해 그 이후 날짜부터 어제(UTC)까지의
일 덤프를 받아 수집한다 (idempotent). cron 지연·중복 실행과 무관하게 연속성 보장.

전환 배경: Actions 러너(미국)에서 바이낸스 fapi가 지역 차단(HTTP 451)되어,
바이낸스 공개 일 덤프(data.binance.vision)로 수집원을 바꿨다. 덤프는 전일 데이터라
하루 지연되지만 백테스트 용도에는 무영향이고, fapi의 OI 30일 제한이 없어 과거
소급이 오히려 길다. (source='fapi' 경로는 로컬/비차단 환경 검증용으로 아래 유지)

핵심 원칙:
1. 백필 우선: 마지막 저장일 → 어제. 최초 실행은 config의 backfill_start_date부터.
2. raw 변화량 저장: 봉 완결값(cvd_delta)·절대 스냅샷(sum_oi)만. 누적 러닝값 금지.
3. 완결일만: 아직 발행 안 된 오늘 덤프는 제외(어제까지).
4. 무결성: 결측일 탐지, PK 중복 차단, 실행별 로그 기록.

사용:
    python collector.py                 # config.json 기반 전 심볼
    python collector.py --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone

import netls
import vision_source as vs
from binance_api import RegionBlockedError
from netls import BAR_MS
from store import CSVStore


def fnum(x) -> str:
    """부동소수를 결정적 문자열로(diff 안정화). None→''."""
    if x is None:
        return ""
    if isinstance(x, int):
        return str(x)
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _dstr(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _months(start: date, end: date) -> list[str]:
    out, seen = [], set()
    for d in _daterange(start, end):
        ym = d.strftime("%Y-%m")
        if ym not in seen:
            seen.add(ym)
            out.append(ym)
    return out


# ----------------------------------------------------------------------------
# 봉 행 조립 (klines + vision OI(metrics) + 파생지표)
# ----------------------------------------------------------------------------
def build_bar_rows(symbol: str, kl_map: dict[int, list],
                   oi_map: dict[int, tuple[float, float]]) -> list[dict]:
    rows: list[dict] = []
    for ot in sorted(kl_map):
        k = kl_map[ot]
        volume = float(k[5])
        tbb = float(k[9])              # taker_buy_volume (기초자산)
        cvd = netls.cvd_delta(tbb, volume)

        sum_oi = sum_oi_val = oi_delta = nl = ns = None
        cur = oi_map.get(ot)
        if cur is not None:
            sum_oi, sum_oi_val = cur
            # ΔOI는 "해당 캔들 구간 [T, T+5m) 동안의 OI 변화" = sum_oi[T+1] − sum_oi[T].
            # CVD_Δ[T]도 같은 구간의 테이커 체결이라 시간축이 일치한다(Coinglass 정의와 동일).
            nxt = oi_map.get(ot + BAR_MS)
            if nxt is not None:
                oi_delta = nxt[0] - sum_oi
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


# ----------------------------------------------------------------------------
# 심볼 단위 수집 (vision)
# ----------------------------------------------------------------------------
def collect_symbol(store: CSVStore, symbol: str, cfg: dict, run_started: str) -> list[dict]:
    logs: list[dict] = []
    today = datetime.now(timezone.utc).date()
    end_date = today - timedelta(days=1)   # 오늘 덤프는 아직 미발행

    # --- bars (klines + metrics OI) ---------------------------------------
    t0 = time.time()
    last = store.last_bar_time(symbol)
    if last is None:
        start_date = datetime.strptime(cfg["vision"]["backfill_start_date"], "%Y-%m-%d").date()
        store.set_meta_once(symbol, "vision_backfill_start_date", _dstr(start_date))
    else:
        # 마지막 저장 봉이 속한 날부터 재처리(그날 나머지 봉 채우기, 중복은 dedup).
        start_date = datetime.fromtimestamp(last / 1000, timezone.utc).date()

    written = 0
    missing_days: list[str] = []
    got_bars = 0
    note = ""

    if start_date > end_date:
        note = "신규 완결일 없음"
    else:
        # OI 프리로드: 첫 봉(00:00)의 sum_oi는 전날 metrics 파일 끝행에 있고, 마지막 봉
        # (23:55)의 다음 OI(익일 00:00)는 당일 파일 끝행에 있다. 하루 앞선 날짜까지 포함.
        oi_map: dict[int, tuple[float, float]] = {}
        for d in _daterange(start_date - timedelta(days=1), end_date):
            oi_map.update(vs.metrics_day(symbol, _dstr(d)))
        if oi_map:
            earliest = min(oi_map)
            store.set_meta_once(symbol, "oi_backfill_start_ms", earliest)
            store.set_meta_once(symbol, "oi_backfill_start_kst", netls.ms_to_kst(earliest))

        all_rows: list[dict] = []
        for d in _daterange(start_date, end_date):
            kl = vs.klines_day(symbol, _dstr(d))
            if not kl:
                missing_days.append(_dstr(d))
                continue
            got_bars += len(kl)
            all_rows.extend(build_bar_rows(symbol, kl, oi_map))
        written = store.upsert_bars(symbol, all_rows)
        if missing_days:
            note = f"결측일 {len(missing_days)}개: {', '.join(missing_days[:5])}" \
                   + (" ..." if len(missing_days) > 5 else "")

    logs.append({
        "run_started_utc": run_started, "symbol": symbol, "domain": "bars",
        "rows_written": written,
        "range_start_ms": int(datetime.combine(start_date, datetime.min.time(),
                                               timezone.utc).timestamp() * 1000),
        "range_end_ms": int(datetime.combine(end_date, datetime.min.time(),
                                             timezone.utc).timestamp() * 1000),
        "missing_bars": len(missing_days), "elapsed_sec": round(time.time() - t0, 2),
        "note": note,
    })

    # --- funding (월 덤프) -------------------------------------------------
    # 주의: 펀딩은 '월' 덤프만 있고(일 덤프 없음) 그 달이 끝나야 발행된다. 따라서
    # 진행 중인 달의 펀딩은 다음 달에야 채워진다. 이를 놓치지 않기 위해 펀딩 범위를
    # 봉 날짜와 분리하고, 마지막 저장분에서 ~40일 소급해 뒤늦게 올라온 월 덤프를 재수집한다.
    t1 = time.time()
    last_f = store.last_funding_time(symbol)
    if last_f is None:
        f_start = datetime.strptime(cfg["vision"]["backfill_start_date"], "%Y-%m-%d").date()
    else:
        f_start = datetime.fromtimestamp(last_f / 1000, timezone.utc).date() - timedelta(days=40)

    frows: list[dict] = []
    for ym in _months(f_start, end_date):
        fm = vs.funding_month(symbol, ym)
        for ts, rate in fm.items():
            frows.append({
                "symbol": symbol, "funding_time": ts,
                "datetime_utc": netls.ms_to_utc(ts), "datetime_kst": netls.ms_to_kst(ts),
                "funding_rate": fnum(rate), "mark_price": "",  # 월 덤프엔 mark price 없음
            })
    f_written = store.upsert_funding(symbol, frows)
    logs.append({
        "run_started_utc": run_started, "symbol": symbol, "domain": "funding",
        "rows_written": f_written, "range_start_ms": "", "range_end_ms": "",
        "missing_bars": 0, "elapsed_sec": round(time.time() - t1, 2), "note": "",
    })
    return logs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NetLS 백필형 수집기 (vision)")
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
                print(f"  {lg['domain']:8s} rows={lg['rows_written']:6d} "
                      f"missing_days={lg['missing_bars']} {lg['elapsed_sec']}s {lg['note']}")
    except RegionBlockedError as e:
        # vision까지 차단 → 사실상 판정 C. 명확히 실패 처리.
        print(f"[지역차단] vision 접근 차단됨(판정 C 가능성): {e}")
        store.append_runlog(all_logs)
        return 2

    store.append_runlog(all_logs)

    total = sum(lg["rows_written"] for lg in all_logs)
    parts = [f"{lg['symbol']}/{lg['domain']}+{lg['rows_written']}"
             for lg in all_logs if lg["rows_written"] > 0]
    summary = (f"collect(vision) {run_started}Z | rows={total} | "
               + (", ".join(parts) if parts else "no new data"))
    store.write_summary(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Task 1: Net Long/Short 지표 재현 검증 스크립트.

바이낸스 원천 데이터(klines + openInterestHist)로 Coinglass Net L/S(New) 지표를
자체 복원하고, 5분봉별 테이블을 출력한다. Coinglass 웹 차트와 수동 대조하기 위한
값 테이블과 markdown 리포트를 산출한다.

사용:
    python verify_netls.py --symbol BTCUSDT --hours 6
    python verify_netls.py --symbol ETHUSDT --start "2026-07-11 18:00" --end "2026-07-11 20:00"
    python verify_netls.py --symbol BTCUSDT --hours 3 --report report_btc.md

타임스탬프 정렬 방식 (채택):
    klines의 open_time(캔들 시가)과 openInterestHist의 timestamp(스냅샷 시각)는
    둘 다 5분 그리드 경계(예: 00, 05, 10분...)에 정렬되어 발행된다. 실측에서 동일
    epoch(ms) 값을 확인했다. 따라서 open_time == oi_timestamp 로 직접 조인한다.

    ΔOI[T] = OI[T+1] − OI[T] : "해당 캔들 구간 [T, T+5m) 동안의 OI 변화"로 정의한다.
    CVD_Δ[T]도 같은 구간의 테이커 체결이므로 두 값의 시간축이 정확히 일치하며, 이렇게
    맞춰야 Coinglass Net L/S(New) 표시값과 일치한다(2026-07 BTC 실측 대조로 확인).
    (초기 버전은 OI[T] − OI[T−1]로 한 봉 밀려 계산해 Coinglass와 어긋났음 → 수정됨)
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime

import binance_api as api
import netls
from netls import BAR_MS, KST, UTC


@dataclass
class Bar:
    open_time: int
    close: float
    volume: float
    taker_buy_base: float
    cvd_delta: float
    sum_oi: float | None = None
    oi_delta: float | None = None
    net_long_delta: float | None = None
    net_short_delta: float | None = None
    residual: float | None = None


def parse_dt(s: str) -> int:
    """'YYYY-MM-DD HH:MM' (KST로 간주) → epoch ms."""
    dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    return int(dt.timestamp() * 1000)


def fetch_bars(symbol: str, start_ms: int, end_ms: int) -> list[Bar]:
    # klines: open_time -> Bar
    raw_kl = api.klines(symbol, "5m", start_ms=start_ms, end_ms=end_ms, limit=1500)
    bars: dict[int, Bar] = {}
    for k in raw_kl:
        ot = int(k[0])
        vol = float(k[5])
        tbb = float(k[9])
        bars[ot] = Bar(
            open_time=ot,
            close=float(k[4]),
            volume=vol,
            taker_buy_base=tbb,
            cvd_delta=netls.cvd_delta(tbb, vol),
        )

    # OI: openInterestHist는 30일 한도. 요청 구간에 한 칸 앞선 스냅샷도 필요하므로
    # start를 한 봉 당겨 ΔOI[first] 계산이 가능하도록 한다.
    # ΔOI[T] = OI[T+1] − OI[T] (캔들 구간 동안 OI 변화)이므로 마지막 봉의 다음 OI까지 확보.
    raw_oi = api.open_interest_hist(
        symbol, "5m", start_ms=start_ms, end_ms=end_ms + BAR_MS, limit=500
    )
    oi_map: dict[int, float] = {int(o["timestamp"]): float(o["sumOpenInterest"]) for o in raw_oi}

    for ot, bar in bars.items():
        if ot in oi_map:
            bar.sum_oi = oi_map[ot]
            nxt = oi_map.get(ot + BAR_MS)   # 해당 캔들 구간 [T, T+5m) 동안의 OI 변화
            if nxt is not None:
                bar.oi_delta = nxt - bar.sum_oi
                bar.net_long_delta = netls.net_long_delta(bar.oi_delta, bar.cvd_delta)
                bar.net_short_delta = netls.net_short_delta(bar.oi_delta, bar.cvd_delta)
                bar.residual = netls.identity_residual(
                    bar.net_long_delta, bar.net_short_delta, bar.oi_delta
                )
    return [bars[t] for t in sorted(bars)]


def format_table(symbol: str, bars: list[Bar]) -> str:
    lines = []
    header = (
        f"{'time (UTC)':<20}{'time (KST)':<20}"
        f"{'close':>12}{'ΔOI':>12}{'CVD_Δ':>12}"
        f"{'netLong_Δ':>12}{'netShort_Δ':>12}{'항등식':>10}"
    )
    lines.append(f"# {symbol}  (단위: 기초자산)")
    lines.append(header)
    lines.append("-" * len(header))
    for b in bars:
        if b.oi_delta is None:
            oi = cvd = nl = ns = chk = "  (OI 없음)"
            lines.append(
                f"{netls.ms_to_utc(b.open_time):<20}{netls.ms_to_kst(b.open_time):<20}"
                f"{b.close:>12.2f}{'-':>12}{b.cvd_delta:>12.2f}{'-':>12}{'-':>12}{'-':>10}"
            )
            continue
        # 항등식: residual이 허용오차 내면 OK
        ok = abs(b.residual) < 1e-6
        lines.append(
            f"{netls.ms_to_utc(b.open_time):<20}{netls.ms_to_kst(b.open_time):<20}"
            f"{b.close:>12.2f}{b.oi_delta:>12.2f}{b.cvd_delta:>12.2f}"
            f"{b.net_long_delta:>12.2f}{b.net_short_delta:>12.2f}"
            f"{'OK' if ok else 'FAIL':>10}"
        )
    return "\n".join(lines)


def build_report(symbol: str, bars: list[Bar], start_ms: int, end_ms: int) -> str:
    valid = [b for b in bars if b.oi_delta is not None]
    max_resid = max((abs(b.residual) for b in valid), default=0.0)
    # 변동 큰 캔들: |CVD_Δ| 상위 및 |ΔOI| 상위
    top_cvd = sorted(valid, key=lambda b: abs(b.cvd_delta), reverse=True)[:5]
    top_oi = sorted(valid, key=lambda b: abs(b.oi_delta), reverse=True)[:5]

    md = []
    md.append(f"# Net L/S 지표 재현 검증 리포트 — {symbol}\n")
    md.append(f"- 구간: {netls.ms_to_kst(start_ms)} ~ {netls.ms_to_kst(end_ms)} (KST)")
    md.append(f"- 캔들 수: {len(bars)} (OI 매칭: {len(valid)})")
    md.append(f"- 항등식 최대 잔차: {max_resid:.2e} (0에 수렴 시 공식 정합)\n")

    md.append("## 복원 공식\n")
    md.append("```")
    md.append("CVD Δ       = 2·takerBuyBase − totalVolume")
    md.append("Net Long Δ  = ΔOI + CVD Δ/2")
    md.append("Net Short Δ = ΔOI − CVD Δ/2")
    md.append("```\n")

    md.append("## Coinglass 대조용 값 (변동 큰 캔들 우선)\n")
    md.append("| time (KST) | close | ΔOI | CVD_Δ | netLong_Δ | netShort_Δ |")
    md.append("|---|---:|---:|---:|---:|---:|")
    picked = {b.open_time: b for b in (top_cvd + top_oi)}
    for b in sorted(picked.values(), key=lambda x: x.open_time):
        md.append(
            f"| {netls.ms_to_kst(b.open_time)} | {b.close:.2f} | {b.oi_delta:.2f} "
            f"| {b.cvd_delta:.2f} | {b.net_long_delta:.2f} | {b.net_short_delta:.2f} |"
        )

    md.append("\n## 검증 프로토콜\n")
    md.append("1. 위 표의 캔들을 Coinglass Net Long/Short(New) 5분봉 차트와 수동 대조.")
    md.append("2. 허용오차 ±5% 이내 일치 여부 판정.")
    md.append("   - **일치** → Coinglass는 바이낸스 단일 기준. 완전 대체 가능.")
    md.append("   - **체계적 배율 차이** → Coinglass는 복수 거래소 집계로 추정. "
              "'바이낸스판 Net L/S'를 자체 표준으로 채택(전략 유효성 무영향).")
    md.append("\n## 알려진 한계\n")
    md.append("- OI 스냅샷 시각과 캔들 구간 경계의 미세 불일치 → 대조 허용오차의 주요 원인.")
    md.append("- ΔOI는 인접 OI 스냅샷 차분, CVD_Δ는 봉 구간 테이커 체결 기반으로 구간 정의가 미세하게 다름.")
    md.append("- 본 지표는 테이커 방향 + OI 분해 기반 추정치이며 거래소 공식 포지션 데이터가 아님.")
    return "\n".join(md)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Net L/S 지표 재현 검증")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--hours", type=float, default=6.0, help="현재 기준 소급 시간(--start 미지정 시)")
    p.add_argument("--start", help="시작 시각 'YYYY-MM-DD HH:MM' (KST)")
    p.add_argument("--end", help="종료 시각 'YYYY-MM-DD HH:MM' (KST)")
    p.add_argument("--report", help="markdown 리포트 저장 경로")
    args = p.parse_args(argv)

    if args.start:
        start_ms = parse_dt(args.start)
        end_ms = parse_dt(args.end) if args.end else netls.now_ms()
    else:
        end_ms = netls.now_ms()
        start_ms = end_ms - int(args.hours * 3600 * 1000)

    try:
        bars = fetch_bars(args.symbol, start_ms, end_ms)
    except api.RegionBlockedError as e:
        print(f"[지역차단] {e}", file=sys.stderr)
        return 2

    if not bars:
        print("데이터 없음(구간/심볼 확인).", file=sys.stderr)
        return 1

    print(format_table(args.symbol, bars))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(build_report(args.symbol, bars, start_ms, end_ms))
        print(f"\n리포트 저장: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

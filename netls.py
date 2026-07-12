"""
Net Long/Short 지표 복원 공식과 시간 유틸리티 (verify/collector 공용).

복원 공식 (기초자산 단위):
    CVD Δ        = 테이커 매수량 − 테이커 매도량 = 2·takerBuyBase − totalVolume
    Net Long Δ   = ΔOI + CVD Δ / 2
    Net Short Δ  = ΔOI − CVD Δ / 2

항등식:
    Long Δ + Short Δ = 2·ΔOI
    Long Δ − Short Δ = CVD Δ
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BAR_MS = 5 * 60 * 1000  # 5분봉 그리드 간격(ms)
KST = timezone(timedelta(hours=9))
UTC = timezone.utc


def cvd_delta(taker_buy_base: float, total_volume: float) -> float:
    """CVD Δ = 매수 − 매도 = 2·takerBuyBase − totalVolume."""
    return 2.0 * taker_buy_base - total_volume


def net_long_delta(oi_delta: float, cvd: float) -> float:
    return oi_delta + cvd / 2.0


def net_short_delta(oi_delta: float, cvd: float) -> float:
    return oi_delta - cvd / 2.0


def identity_residual(long_d: float, short_d: float, oi_delta: float) -> float:
    """(Long Δ + Short Δ) − 2·ΔOI. 0에 가까울수록 정합."""
    return (long_d + short_d) - 2.0 * oi_delta


def ms_to_iso(ms: int, tz: timezone = UTC) -> str:
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%Y-%m-%d %H:%M:%S")


def ms_to_utc(ms: int) -> str:
    return ms_to_iso(ms, UTC)


def ms_to_kst(ms: int) -> str:
    return ms_to_iso(ms, KST)


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def month_key(ms: int) -> str:
    """파티션 키 (UTC 기준 YYYY-MM). 커밋 파일 분할에 사용."""
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


def align_floor(ms: int, grid_ms: int = BAR_MS) -> int:
    return (ms // grid_ms) * grid_ms

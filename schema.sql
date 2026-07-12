-- ============================================================================
-- NetLS 수집 데이터 논리 스키마 (schema.sql)
--
-- 채택 저장 포맷: 월별 CSV 파티션 (data/<symbol>/bars_<YYYY-MM>.csv,
--                data/<symbol>/funding_<YYYY-MM>.csv). 근거는 README 참조.
-- 이 파일은 (1) CSV 컬럼/타입/제약의 규범적 정의, (2) CSV를 SQLite로 적재해
-- 분석할 때 그대로 사용할 수 있는 DDL 두 역할을 겸한다. CSV 헤더는 아래
-- 컬럼명과 일치한다.
--
-- 단위 주의: sum_oi / cvd_delta / oi_delta / net_*_delta 는 모두 "기초자산"
-- 단위(BTC, ETH ...). *_value / quote_* 만 USD(T).
-- ============================================================================

-- 5분봉 병합 테이블: klines + OI + 파생지표
CREATE TABLE IF NOT EXISTS bars (
    symbol           TEXT    NOT NULL,           -- 예: BTCUSDT
    open_time        INTEGER NOT NULL,           -- 캔들 시가 epoch(ms), 5분 그리드 정렬
    datetime_utc     TEXT    NOT NULL,           -- open_time의 UTC 문자열(가독용)
    datetime_kst     TEXT    NOT NULL,           -- open_time의 KST 문자열(가독용)
    open             REAL    NOT NULL,
    high             REAL    NOT NULL,
    low              REAL    NOT NULL,
    close            REAL    NOT NULL,
    volume           REAL    NOT NULL,           -- 기초자산 거래량
    quote_volume     REAL    NOT NULL,           -- USD 거래대금
    trades           INTEGER NOT NULL,
    taker_buy_base   REAL    NOT NULL,           -- 테이커 매수량(기초자산)
    taker_buy_quote  REAL    NOT NULL,           -- 테이커 매수 대금(USD)
    sum_oi           REAL,                       -- 미결제약정(기초자산) 스냅샷; OI 결측 시 NULL
    sum_oi_value     REAL,                       -- 미결제약정(USD) 스냅샷
    cvd_delta        REAL    NOT NULL,           -- 2*taker_buy_base - volume (봉 자체 완결)
    oi_delta         REAL,                       -- sum_oi[t+1] - sum_oi[t] (캔들 구간 OI변화); 다음봉/OI 결측 시 NULL
    net_long_delta   REAL,                       -- oi_delta + cvd_delta/2
    net_short_delta  REAL,                       -- oi_delta - cvd_delta/2
    PRIMARY KEY (symbol, open_time)              -- 중복 차단(idempotent 재수집 안전)
);

-- 펀딩비 테이블 (8시간 주기, 별도 cadence이므로 분리)
CREATE TABLE IF NOT EXISTS funding (
    symbol        TEXT    NOT NULL,
    funding_time  INTEGER NOT NULL,              -- 정산 시각 epoch(ms)
    datetime_utc  TEXT    NOT NULL,
    datetime_kst  TEXT    NOT NULL,
    funding_rate  REAL    NOT NULL,
    mark_price    REAL,
    PRIMARY KEY (symbol, funding_time)
);

-- 수집 메타 (데이터셋 생일 등). CSV 저장 시 data/_meta.json 으로 대응.
CREATE TABLE IF NOT EXISTS collection_meta (
    symbol            TEXT    NOT NULL,
    key               TEXT    NOT NULL,          -- 예: 'oi_backfill_start_ms', 'klines_backfill_start_ms'
    value             TEXT    NOT NULL,
    PRIMARY KEY (symbol, key)
);

-- 실행별 수집 로그 (수집 봉 수, 결측, 소요시간). CSV 저장 시 data/_runlog.csv 로 대응.
CREATE TABLE IF NOT EXISTS run_log (
    run_started_utc   TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    domain            TEXT    NOT NULL,          -- 'bars' | 'funding'
    rows_written      INTEGER NOT NULL,
    range_start_ms    INTEGER,
    range_end_ms      INTEGER,
    missing_bars      INTEGER NOT NULL DEFAULT 0,
    elapsed_sec       REAL    NOT NULL,
    note              TEXT
);

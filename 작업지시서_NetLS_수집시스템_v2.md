# 작업지시서 v2: Net Long/Short 지표 자체 구현·검증 및 GitHub 상주 수집 시스템

## 프로젝트 배경

Coinglass의 Net Long/Short(New) 지표 기반 스퀴즈 페이드 전략을 구축한다.
Coinglass API 유료 제약(5분봉은 Standard 이상)을 우회하기 위해, 바이낸스 무료 API의
원천 데이터로 동일 지표를 자체 생산한다. 복원 공식은 실측 검증 완료:

```
CVD Δ        = 테이커 매수량 − 테이커 매도량        (기초자산 단위)
Net Long Δ   = ΔOI + CVD Δ / 2
Net Short Δ  = ΔOI − CVD Δ / 2

항등식: Long Δ + Short Δ = 2·ΔOI  /  Long Δ − Short Δ = CVD Δ
```

검산 근거 (2026-07-11 18:20 KST, 5분봉): ΔOI −1,497, CVD +6,350
→ Long Δ +1,678, Short Δ −4,672 (Coinglass 표시값과 일치)

**운영 환경 제약**: 로컬 상시 구동 불가. 수집기는 GitHub Actions 스케줄 실행으로
상주시킨다. 수집 대상이 모두 과거 조회형 API(백필 가능)이므로 cron 지연에 강건한
"매 실행 시 마지막 저장 지점부터 백필" 구조로 설계한다.

## 본 지시서의 범위

- Task 0: 실행 환경 검증 (지역 차단 테스트) ← 최우선, 아키텍처 분기점
- Task 1: 지표 재현 검증 스크립트
- Task 2: 백필형 수집기
- Task 3: GitHub Actions 배포
- 범위 외: 이벤트 태깅·백테스트(Phase 2), 실시간 시그널 봇(Phase 4)

---

## Task 0: GitHub Actions 환경에서 바이낸스 API 접근성 테스트

**목적**: Actions 러너는 대부분 미국 리전이며, 바이낸스가 미국 IP를 차단(HTTP 451 등)할
가능성이 있다. 본 테스트 결과가 이후 전체 아키텍처를 결정한다.

**구현**: 최소 워크플로(.github/workflows/connectivity-test.yml) 작성, 수동 트리거
(workflow_dispatch)로 아래 엔드포인트를 호출하고 상태코드·응답 요약을 로그 출력:

1. `https://fapi.binance.com/fapi/v1/ping`
2. `https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=5`
3. `https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=5`
4. `https://data.binance.vision/` 접근성 (대안 경로 사전 확인)

**판정 및 분기**:
- A. 전부 정상 → 계획대로 진행 (fapi 직접 호출)
- B. fapi 차단 + data.binance.vision 정상 → 수집원을 일 단위 공개 덤프로 전환
  (klines·OI·펀딩비 등 제공됨, 전일 데이터라 1일 지연 — 백테스트 용도에는 무영향).
  이 경우 Task 2의 폴링 주기를 일 1회로 변경
- C. 둘 다 차단 → Actions 포기, 무료 VM(아시아 리전) 검토. 본 지시서는 Task 1–2의
  로컬 실행분까지만 진행하고 중단 후 보고

---

## Task 1: 지표 재현 검증 스크립트 (`verify_netls.py`)

### 데이터 소스 (Binance USDT-M Futures, 무인증)
1. klines: `GET /fapi/v1/klines`, interval=5m
   - `taker buy base asset volume` 필드 사용
   - `CVD Δ = 2 × takerBuyBase − totalBaseVolume`
2. OI 히스토리: `GET /futures/data/openInterestHist`, period=5m (과거 30일 한도)
   - `sumOpenInterest`(기초자산 단위) 사용. USD 단위 필드와 혼동 금지
   - `ΔOI = OI[t] − OI[t−1]`

### 요구사항
- 심볼·기간 인자로 받아 5분봉별 테이블 출력:
  `timestamp(KST 병기), ΔOI, CVD_Δ, netLong_Δ, netShort_Δ, 항등식검증`
- 타임스탬프 정렬: klines는 캔들 시가 기준, OI hist는 스냅샷 시각 기준.
  정렬 로직을 명시적으로 구현하고 채택한 방식을 주석으로 기록
- 항등식 자가검증 컬럼(Long+Short ≈ 2·ΔOI, 허용오차 내 여부)

### 검증 프로토콜
- BTCUSDT 포함 2개 이상 심볼에서 변동 큰 캔들 포함 5–10개 캔들 선정
- 사용자가 Coinglass 웹 차트 표시값과 수동 대조 (허용오차 ±5%)
- 판정:
  - 일치 → Coinglass는 바이낸스 단일 기준으로 확정, 완전 대체 가능
  - 체계적 배율 차이 → Coinglass는 복수 거래소 집계로 추정.
    "바이낸스판 Net L/S"를 자체 표준으로 채택 (전략 유효성 무영향, README에 명기)
- 산출물: 대조용 값 테이블 + 검증 리포트(markdown)

---

## Task 2: 백필형 수집기 (`collector.py`)

### 수집 대상
- 심볼: BTCUSDT, ETHUSDT (설정 파일로 확장 가능한 구조)
- 항목 (모두 5분봉 기준):
  - klines: OHLCV, takerBuyBase
  - OI: sumOpenInterest
  - 펀딩비: `/fapi/v1/fundingRate` (8시간 주기 데이터)
  - 파생 계산: CVD_Δ, ΔOI, netLong_Δ, netShort_Δ

### 핵심 설계 원칙
1. **백필 우선**: 매 실행 시 DB의 마지막 타임스탬프 조회 → 그 이후부터 현재까지 수집.
   실행 주기·지연과 무관하게 데이터 연속성 보장 (idempotent)
2. **raw 변화량 저장**: 누적값 저장 금지 (앵커 문제 방지)
3. **최초 실행 백필**: klines·펀딩비는 가능한 최대(1년 이상), OI는 30일 한도까지 소급.
   OI 수집 시작일을 메타 테이블에 기록 (데이터셋 생일)
4. **무결성**: 5분 그리드 기준 결측 봉 탐지 → 재시도, UNIQUE 제약으로 중복 차단,
   실행별 수집 로그(수집 봉 수, 결측, 소요시간)

### 저장소
- SQLite 단일 파일. 단, git 커밋 diff 효율을 위해 월별 parquet 분할 저장을 대안으로
  검토하고 채택안을 README에 근거와 함께 기록
- 스키마 파일(`schema.sql` 또는 동등물) 분리 제공

---

## Task 3: GitHub Actions 배포

### 리포 구성
- 수집기 리포는 public (Actions 무료 무제한 활용, 시장 공개 데이터라 문제없음)
- 분석·전략 코드는 본 리포에 포함하지 않는다 (추후 별도 private/로컬)

### 워크플로 요구사항 (`.github/workflows/collect.yml`)
- 스케줄: cron 30분 주기 (Task 0 결과가 B안이면 일 1회로 변경)
  + workflow_dispatch (수동 실행) 병행
- 단계: checkout → Python 셋업 → collector 실행 → 데이터 파일 변경 시에만
  커밋·푸시 (커밋 메시지에 수집 범위 요약)
- 동시 실행 방지: concurrency 그룹 설정 (이전 실행 미종료 시 중복 방지)
- 실패 시 로그로 원인 식별 가능하도록 에러 핸들링
- 60일 비활성화 규칙: 데이터 커밋이 지속되는 한 자동 회피됨을 README에 명기

### 산출물 정리
1. `connectivity-test.yml` + 테스트 결과 로그
2. `verify_netls.py` + 검증 리포트
3. `collector.py`, 스키마 파일, `collect.yml`
4. `README.md`: 공식 유도 요약, 실행 방법, 아키텍처 결정 기록(Task 0 판정 포함),
   알려진 한계

---

## 알려진 한계 (README·코드 주석에 명기)
- OI 스냅샷 타이밍과 캔들 경계의 미세 불일치 가능 → 대조 허용오차의 주요 원인
- OI 과거 30일 제한 → forward 수집으로 해소, 본 분석 가능 시점은 수집 시작 +90일 전후
- 본 지표는 테이커 방향 + OI 분해 기반 추정치이며 거래소 공식 포지션 데이터가 아님
- Actions cron은 지연이 흔하나 백필 구조로 무해화됨. 단 실시간 알림 용도로는 부적합
  (Phase 4에서 별도 환경 검토)

## 다음 단계 (범위 외, 참고)
- Phase 2: 스퀴즈 이벤트 태깅(숏커버 비중, 델타 z-score, OI 방향) → triple-barrier
  라벨링 → 조건 조합별 승률·기대값 테이블 (거래비용 왕복 0.1% 이상 반영)
- 게이트: 기대값 양수 조합 부재 시 피처 재설계. ML 선행 금지

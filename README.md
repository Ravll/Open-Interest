# NetLS 수집 시스템

바이낸스 USDT-M 선물의 **무료 공개 API** 원천 데이터로 Coinglass의
Net Long/Short(New) 지표를 자체 복원·수집하는 GitHub Actions 상주 파이프라인.

Coinglass 유료 제약(5분봉은 Standard 이상)을 우회하기 위해, 테이커 체결 방향과
미결제약정(OI) 변화로부터 동일 지표를 재생산한다.

---

## 1. 공식 유도 요약

기초자산(BTC, ETH…) 단위 기준:

```
CVD Δ        = 테이커 매수량 − 테이커 매도량 = 2·takerBuyBase − totalVolume
Net Long Δ   = ΔOI + CVD Δ / 2
Net Short Δ  = ΔOI − CVD Δ / 2

항등식:  Long Δ + Short Δ = 2·ΔOI      Long Δ − Short Δ = CVD Δ
```

- `takerBuyBase` = kline의 taker buy base asset volume (필드 인덱스 9)
- `totalVolume`  = kline의 base asset volume (필드 인덱스 5)
- `ΔOI`          = `sumOpenInterest[t] − sumOpenInterest[t−1]` (기초자산 단위)

**검산 근거** (2026-07-11 18:20 KST, 5분봉): ΔOI −1,497, CVD +6,350
→ Long Δ +1,678, Short Δ −4,672 (Coinglass 표시값과 일치).
본 리포의 `verify_netls.py` 실행 시 모든 봉에서 항등식 잔차 ≈ 0 으로 자가검증된다.

---

## 2. 구성 파일

| 파일 | 역할 |
|---|---|
| `binance_api.py` | 무인증 fapi 접근 모듈 (451/429 처리, 재시도) |
| `netls.py` | 지표 공식 + 시간 유틸 |
| `connectivity_test.py` | **Task 0** 접근성 테스트 (A/B/C 판정) |
| `verify_netls.py` | **Task 1** 지표 재현 검증 + 대조 리포트 생성 |
| `collector.py` | **Task 2** 백필형 수집기 |
| `store.py` | 월별 CSV 파티션 저장소 |
| `schema.sql` | 논리 스키마(컬럼/타입/제약) — CSV 헤더와 일치, SQLite 적재 겸용 |
| `config.json` | 수집 대상 심볼·백필 한도 설정 |
| `.github/workflows/connectivity-test.yml` | Task 0 워크플로(수동) |
| `.github/workflows/collect.yml` | **Task 3** 30분 주기 수집·커밋 워크플로 |

의존성: **없음**(Python 표준 라이브러리만). Actions에서 `pip install` 불필요.

---

## 3. 실행 방법

```bash
# Task 0: 접근성 판정 (로컬/Actions 동일)
python connectivity_test.py

# Task 1: 지표 재현 검증
python verify_netls.py --symbol BTCUSDT --hours 6 --report report_btc.md
python verify_netls.py --symbol ETHUSDT --start "2026-07-11 18:00" --end "2026-07-11 20:00"

# Task 2: 수집 (config.json 기반 전 심볼)
python collector.py
python collector.py --symbol BTCUSDT       # 단일 심볼
```

수집 산출물은 `data/` 아래에 커밋된다:

```
data/<symbol>/bars_<YYYY-MM>.csv      5분봉 병합(klines+OI+파생지표)
data/<symbol>/funding_<YYYY-MM>.csv   펀딩비(8h)
data/_meta.json                       심볼별 백필 시작점(데이터셋 생일)
data/_runlog.csv                      실행별 수집 로그
data/_last_run_summary.txt            커밋 메시지용 요약
```

---

## 4. 아키텍처 결정 기록 (ADR)

### ADR-1. 실행 환경 — GitHub Actions (Task 0 판정에 종속)

`connectivity_test.py`의 판정에 따라 분기한다:

- **A. fapi 전부 정상** → 계획대로 fapi 직접 호출, `collect.yml` 30분 주기. ✅ 기본안
- **B. fapi 차단 + data.binance.vision 정상** → 수집원을 일 단위 공개 덤프로 전환,
  폴링을 일 1회로 변경(백테스트 용도엔 1일 지연 무영향).
- **C. 둘 다 차단** → Actions 포기, 아시아 리전 무료 VM 검토.

> **판정 기록**: 로컬(한국 IP)에서는 A. **Actions 러너(미국 리전) 판정은
> `Task0 Connectivity Test` 워크플로를 수동 실행해 확정할 것.** 워크플로 로그의
> `판정: X` 및 Step Summary를 확인한 뒤, B/C면 위 지침대로 `collect.yml`을 조정한다.

### ADR-2. 저장 포맷 — 월별 CSV 파티션 (SQLite/Parquet 대신)

| 후보 | git diff | 리포 증가 | 의존성 | 분석 편의 |
|---|---|---|---|---|
| SQLite 단일파일 | ✗ 바이너리 전체 재저장 | ✗ 커밋마다 전체 스냅샷 | 낮음 | 높음 |
| Parquet 월분할 | △ 바이너리, 월파일 재작성 | △ | pyarrow 필요 | 높음 |
| **CSV 월분할** ✅ | ✅ 텍스트 라인 diff | ✅ 현재 월만 churn | **없음** | 중(파싱 필요) |

**채택: 월별 CSV 파티션.** 30분마다 커밋되는 공개 데이터 리포에서 지배적 비용은
바이너리 전체 재저장에 따른 리포 비대화다. CSV는 (1) 텍스트라 git이 델타 압축에
유리하고 라인 단위 diff가 남으며, (2) 과거 월 파티션은 불변이라 churn이 현재 월
파일로 한정되고, (3) 표준 라이브러리만으로 처리돼 Actions 셋업이 가벼우며,
(4) `pd.read_csv`로 즉시 분석 가능하다. `schema.sql`은 CSV 컬럼의 규범적 정의이자,
필요 시 CSV를 SQLite로 적재하는 DDL로 그대로 쓸 수 있다.

### ADR-3. raw 변화량 저장 (누적값 앵커 문제 방지)

봉 자체로 완결되는 값만 저장한다: `cvd_delta`(2·takerBuyBase−volume)는 해당 봉의
테이커 체결만으로 결정된다. `sum_oi`는 임의 시점부터 누적한 러닝값이 아니라 각
시각의 **절대 스냅샷 관측치**이므로 앵커 문제가 없다. `oi_delta`는 인접 스냅샷 차분으로
파생 저장하되 원천 `sum_oi`도 함께 보관해 언제든 재계산 가능하게 한다. 임의 시작점에
의존하는 러닝 누적 CVD 같은 값은 저장하지 않는다.

---

## 5. 수집 설계 핵심

- **백필 우선(idempotent)**: 매 실행 시 마지막 저장 타임스탬프 조회 → 그 이후만 수집.
  cron 지연·중복 실행과 무관하게 연속성 보장. 재실행해도 PK 중복은 0.
- **완결봉만**: 진행 중인 현재 봉은 마감 후 다음 실행에서 수집.
- **최초 백필**: klines·펀딩비는 최대 `~400일`(설정), OI는 **최근 30일 한도**까지 소급.
  각 도메인 백필 시작점을 `data/_meta.json`에 "데이터셋 생일"로 1회 고정 기록.
- **무결성**: 5분 그리드 결측 탐지 → 1회 재시도, PK로 중복 차단, 실행별 로그(`_runlog.csv`)
  에 수집 봉 수·결측·소요시간 기록.

---

## 6. 알려진 한계

- **OI 스냅샷 vs 캔들 경계**: OI `timestamp`(순간 스냅샷)와 kline 구간
  `[open, open+5m)` 의 정의가 미세하게 달라 Coinglass 대조 시 소폭 오차가 날 수 있다
  (허용오차의 주요 원인).
- **OI 30일 제한**: 과거 소급이 30일로 막혀 있어 forward 수집으로만 축적된다.
  본 분석 가능 시점은 대략 **수집 시작 +90일** 전후.
- **추정치**: 본 지표는 테이커 방향 + OI 분해 기반 추정치이며 거래소 공식 포지션
  데이터가 아니다. Coinglass가 복수 거래소 집계라면 배율 차이가 날 수 있고, 그 경우
  "바이낸스판 Net L/S"를 자체 표준으로 채택한다(전략 유효성엔 무영향).
- **Actions cron 지연**: 지연은 흔하지만 백필 구조로 무해화된다. 단 실시간 알림
  용도로는 부적합(Phase 4에서 별도 환경 검토).
- **60일 비활성화 규칙**: 데이터 커밋이 지속되는 한 자동 회피된다.

---

## 7. 리포 운영 메모

- 본 리포는 **public** 권장(Actions 무료 무제한, 시장 공개 데이터라 문제없음).
- 분석·전략 코드는 본 리포에 포함하지 않는다(추후 별도 private/로컬).
- 범위 외: 이벤트 태깅·백테스트(Phase 2), 실시간 시그널 봇(Phase 4).

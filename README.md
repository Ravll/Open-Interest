# NetLS 수집 시스템

바이낸스 USDT-M 선물의 **무료 공개 API** 원천 데이터로 Coinglass의
Net Long/Short(New) 지표를 자체 복원·수집하는 GitHub Actions 상주 파이프라인.

Coinglass 유료 제약(5분봉은 Standard 이상)을 우회하기 위해, 테이커 체결 방향과
미결제약정(OI) 변화로부터 동일 지표를 재생산한다.

> 📌 **코딩을 처음 하시는 분은 아래 [§0 처음 시작하는 분을 위한 안내](#0-처음-시작하는-분을-위한-안내)만 따라 하시면 됩니다.**
> 나머지 섹션(1~7)은 기술 참고용이라 안 읽어도 됩니다.

---

## 0. 처음 시작하는 분을 위한 안내

### 0-0. 이게 뭐 하는 물건인가요? (1분 이해)

바이낸스 거래소가 **누구나 무료로 볼 수 있게 공개한 거래 데이터**를, 5분마다 자동으로
받아와 파일로 차곡차곡 쌓아두는 **자동 기록 로봇**입니다. 이 로봇을 내 컴퓨터가 아니라
**GitHub이라는 회사의 컴퓨터에서 24시간 공짜로 돌립니다**(내 컴퓨터는 꺼도 됨).
쌓인 데이터는 나중에 "이럴 때 사면 이겼나?"를 검증하는 전략 연구의 재료가 됩니다.

비유하자면: 편의점 CCTV를 하나 달아두는 일입니다. 한 번 설치해두면 알아서 계속
녹화하고, 나는 가끔 잘 녹화되는지 확인만 하면 됩니다.

### 0-1. 당신이 할 일은 딱 3가지입니다

| 단계 | 무엇을 | 언제 | 소요 |
|---|---|---|---|
| **STEP 1** | 코드를 GitHub에 올리기 | 최초 1회 | ~15분 |
| **STEP 2** | "접근성 테스트" 버튼 누르고 결과 확인 | 최초 1회 | ~5분 |
| **STEP 3** | 30분 뒤 데이터가 쌓이는지 구경 | 가끔 | ~2분 |

그 다음부터는 **아무것도 안 해도** 로봇이 알아서 데이터를 모읍니다.

---

### STEP 1. 코드를 GitHub에 올리기 (최초 1회)

> 코딩 몰라도 됩니다. **GitHub Desktop**이라는 무료 프로그램의 버튼만 누르면 됩니다.

**1-1. GitHub 계정 만들기**
1. https://github.com 접속 → `Sign up` → 이메일·비밀번호로 가입 (무료).

**1-2. GitHub Desktop 설치**
1. https://desktop.github.com 접속 → `Download` → 설치.
2. 실행 후 위에서 만든 GitHub 계정으로 로그인(`Sign in`).

**1-3. 이 프로젝트 폴더를 GitHub Desktop에 등록**
1. GitHub Desktop 상단 메뉴 `File` → `Add local repository`.
2. `Choose…` 눌러 이 폴더 선택: `D:\Dev\Open Interest` → `Add repository`.

**1-4. 인터넷에 올리기(Publish)**
1. 가운데에 `Publish repository` 파란 버튼 클릭.
2. 창이 뜨면:
   - `Name`: 아무거나 (예: `netls-collector`)
   - **`Keep this code private` 체크박스는 반드시 ☐ 체크 해제** (공개로 둬야 자동 실행이 무료·무제한)
3. `Publish repository` 클릭 → 끝. 이제 코드가 GitHub에 올라갔습니다.

---

### STEP 2. "접근성 테스트" 실행하고 결과 확인 (최초 1회)

> 이 로봇이 도는 GitHub 컴퓨터는 대부분 **미국**에 있는데, 바이낸스가 미국 접속을
> 막는 경우가 있습니다. 그래서 **먼저 접속이 되는지 딱 한 번 확인**합니다.

**2-1. 웹브라우저에서 내 저장소 열기**
1. https://github.com 로그인 → 오른쪽 위 내 프로필 → `Your repositories` → 방금 만든 저장소 클릭.

**2-2. 테스트 실행**
1. 상단 탭 중 **`Actions`** 클릭.
2. (처음이면 "I understand my workflows, go ahead and enable them" 초록 버튼이 보일 수 있음 → 클릭)
3. 왼쪽 목록에서 **`Task0 Connectivity Test`** 클릭.
4. 오른쪽 `Run workflow` 회색 버튼 클릭 → 다시 초록 `Run workflow` 클릭.
5. 30초쯤 기다리면 목록에 노란 점 → 초록 체크(✓)로 바뀝니다.

**2-3. 결과(A/B/C) 확인**
1. 방금 실행된 항목(초록 체크) 클릭 → `probe` 클릭 → `Run connectivity probe` 줄 펼치기.
2. 맨 아래 **`판정: A` 또는 `판정: B` / `판정: C`** 글자를 확인.

**2-4. 판정에 따라 — 그냥 나(Claude)에게 복사해서 알려주세요**

| 판정 | 뜻 | 당신이 할 일 |
|---|---|---|
| **A** | 접속 정상 👍 | fapi 직접 경로 사용 가능. |
| **B** | fapi만 차단 | **일 덤프(vision) 경로로 자동 대응.** 하루 1회 수집. |
| **C** | 완전 차단 | 나에게 **"Task 0 판정 C 나왔어"** 라고 말하면, 대안(다른 서버)을 같이 정합니다. |

> 즉 A가 아니면 결과 화면을 캡처하거나 `판정: X` 글자만 나에게 알려주시면 나머지는 내가 처리합니다.

> 📍 **현재 이 저장소 상태**: 실제 Actions 러너에서 **판정 B**(미국 IP라 fapi 차단, 하지만
> `data.binance.vision` 일 덤프는 정상)로 확인되어, **이미 vision 일 덤프 경로로 구성 완료**되었습니다.
> 그래서 당신이 지금 할 일은 **① 코드 Push → ② `Collect NetLS Data` 워크플로를 한 번 수동 실행**뿐입니다:
>
> 1. GitHub Desktop에서 **`Push origin`** (제가 방금 바꾼 코드 반영)
> 2. 웹 저장소 `Actions` 탭 → 왼쪽 **`Collect NetLS Data`** → `Run workflow` → 초록 `Run workflow`
> 3. 첫 실행은 **과거 1년치를 한 번에 받느라 5~10분** 걸립니다(초록 체크 뜰 때까지 기다리기).
>    이후부터는 매일 자동으로 전일치만 짧게 받습니다.

---

### STEP 3. 데이터가 쌓이는지 구경 (가끔)

- 첫 `Collect` 실행이 끝나면(초록 체크 ✓) 저장소 메인에 **`data`** 폴더가 생깁니다.
- `data` → `BTCUSDT` → `bars_...csv` 를 클릭하면 5분봉 데이터가 줄줄이 보입니다.
- 이후 **매일 1회**(한국시간 오후 5시경) 전일 데이터가 자동 추가되고, 커밋 메시지
  (`data: collect(vision) ...`)가 갱신되면 정상 작동 중입니다.
- vision은 **전일 데이터**라 항상 하루 늦게 채워집니다(정상 — 백테스트 용도엔 무관).
- **`Actions` 탭에서 초록 체크가 매일 찍히는지**만 가끔 확인하면 됩니다.

---

### 자주 나오는 질문 / 문제 해결

- **"내 컴퓨터를 켜놔야 하나요?"** → 아니요. GitHub 컴퓨터가 대신 돕니다. 꺼도 됩니다.
- **"돈이 드나요?"** → 공개(public) 저장소면 무료입니다. STEP 1-4에서 private 체크를 꼭 해제하세요.
- **`Actions` 탭에 빨간 X가 떴어요** → 그 항목을 클릭해 화면을 캡처하고 나에게 보여주세요. 원인 찾아 고쳐드립니다.
- **한동안 안 쓰면 멈춘다던데?** → 데이터가 계속 쌓이는 한(=커밋이 계속 생기는 한) 자동으로 안 멈춥니다.

### 나(Claude)에게 무언가 요청하는 법

코드를 직접 고칠 필요 없습니다. **하고 싶은 말을 평소 말로** 하시면 됩니다. 예:
- "수집 심볼에 SOLUSDT 추가해줘"
- "Task 0 판정 B 나왔어, 바꿔줘"
- "Actions에 에러 떴어" (+ 화면 캡처)
- "데이터 잘 쌓이는지 같이 확인해줘"

내가 코드를 수정하면, 당신은 **GitHub Desktop을 열고 `Push origin`(또는 `Commit` 후 `Push`) 버튼만
누르면** 변경 내용이 GitHub에 반영됩니다.

### 용어 미니사전

- **저장소(Repository)**: 이 프로젝트 파일들이 사는 인터넷 폴더.
- **커밋(Commit)**: "이 시점 상태로 저장" 도장 찍기.
- **푸시(Push)**: 내 컴퓨터의 변경을 인터넷(GitHub)에 올려 반영하기.
- **Actions**: GitHub이 내 코드를 정해진 시간마다 자동 실행해주는 기능(=로봇의 심장).
- **workflow(워크플로)**: 그 자동 실행의 대본. 여기선 "접근성 테스트"와 "하루 1회 수집" 두 개.

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
| `binance_api.py` | 무인증 fapi 접근 모듈 (451/429 처리, 재시도) — 로컬 verify·접근성테스트용 |
| `vision_source.py` | **data.binance.vision 일/월 덤프 접근** (판정 B 경로, collector가 사용) |
| `netls.py` | 지표 공식 + 시간 유틸 |
| `connectivity_test.py` | **Task 0** 접근성 테스트 (A/B/C 판정, 실제 zip 다운로드 확인 포함) |
| `verify_netls.py` | **Task 1** 지표 재현 검증 + 대조 리포트 생성 (fapi 기반) |
| `collector.py` | **Task 2** 백필형 수집기 (**source=vision**, 일 단위 백필) |
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

# Task 2: 수집 (config.json 기반 전 심볼, source=vision 일 덤프)
python collector.py
python collector.py --symbol BTCUSDT       # 단일 심볼
```

> 참고: `verify_netls.py`는 실시간 대조를 위해 **fapi**를 쓰므로 fapi 접근 가능한
> 환경(예: 한국 로컬)에서 실행한다. `collector.py`는 Actions(미국)에서도 되도록
> **vision 일 덤프**를 쓴다. 즉 검증은 로컬, 상시 수집은 Actions로 역할이 나뉜다.

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

### ADR-1. 실행 환경·수집원 — GitHub Actions + data.binance.vision (판정 B 채택)

`connectivity_test.py`의 판정에 따라 분기한다:

- **A. fapi 전부 정상** → fapi 직접 호출(`source=fapi`).
- **B. fapi 차단 + vision zip 다운로드 정상** → 일 단위 공개 덤프로 전환(`source=vision`),
  폴링 일 1회. 1일 지연되지만 백테스트 용도엔 무영향.
- **C. 둘 다 차단** → Actions 포기, 아시아 리전 무료 VM 검토.

> **판정 기록 (2026-07-12)**: 로컬(한국 IP)=A, 그러나 **실제 Actions 러너(미국 리전)=B**
> (fapi 지역 차단, `data.binance.vision` zip 다운로드 정상). 이에 따라 **본 리포는
> `source=vision`으로 확정**했다: `collector.py`는 vision 일/월 덤프를 백필하고,
> `collect.yml`은 일 1회(08:00 UTC) 실행한다.
>
> **부수 효과(오히려 이점)**: vision `metrics` 덤프에는 fapi의 **OI 30일 제한이 없어**
> 과거 OI를 길게 소급할 수 있다(`config.json`의 `vision.backfill_start_date`로 조절).
> 즉 판정 B 전환이 데이터셋 확보에는 유리하게 작용한다.
>
> **fapi 경로가 다시 필요하면**(예: 아시아 리전 러너/프록시 확보 시) `config.json`의
> `source`를 `fapi`로 되돌리고 `collect.yml` cron을 30분으로 바꾸면 된다. fapi용 코드
> 경로(`binance_api.py`)는 그대로 보존돼 있다.

### ADR-2. 저장 포맷 — 월별 CSV 파티션 (SQLite/Parquet 대신)

| 후보 | git diff | 리포 증가 | 의존성 | 분석 편의 |
|---|---|---|---|---|
| SQLite 단일파일 | ✗ 바이너리 전체 재저장 | ✗ 커밋마다 전체 스냅샷 | 낮음 | 높음 |
| Parquet 월분할 | △ 바이너리, 월파일 재작성 | △ | pyarrow 필요 | 높음 |
| **CSV 월분할** ✅ | ✅ 텍스트 라인 diff | ✅ 현재 월만 churn | **없음** | 중(파싱 필요) |

**채택: 월별 CSV 파티션.** 주기적으로 커밋되는 공개 데이터 리포에서 지배적 비용은
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

## 5. 수집 설계 핵심 (source=vision)

- **백필 우선(idempotent)**: 매 실행 시 마지막 저장 날짜 조회 → 그 날부터 어제(UTC)까지
  일 덤프 재수집. cron 지연·중복 실행과 무관하게 연속성 보장. 재실행해도 PK 중복은 0.
- **완결일만**: 아직 발행 안 된 오늘 덤프는 제외(어제까지).
- **최초 백필**: `vision.backfill_start_date`부터 klines·OI(metrics)를 소급.
  vision 덤프는 **fapi의 OI 30일 제한이 없어** 과거로 길게 받을 수 있다. 각 도메인 백필
  시작점을 `data/_meta.json`에 "데이터셋 생일"로 1회 고정 기록.
- **펀딩 특수성**: 펀딩은 **월 덤프만** 존재(일 덤프 없음)하고 그 달이 끝나야 발행된다.
  따라서 진행 중인 달의 펀딩은 다음 달에 채워진다. collector는 펀딩 범위를 봉 날짜와
  분리해 매번 최근 ~40일을 재훑어 뒤늦게 올라온 월 덤프를 흡수한다.
- **무결성**: 결측일 탐지·로그, PK로 중복 차단, 실행별 로그(`_runlog.csv`)에 수집 봉 수·
  결측일·소요시간 기록.

---

## 6. 알려진 한계

- **OI 스냅샷 vs 캔들 경계**: OI `timestamp`(순간 스냅샷)와 kline 구간
  `[open, open+5m)` 의 정의가 미세하게 달라 Coinglass 대조 시 소폭 오차가 날 수 있다
  (허용오차의 주요 원인).
- **1일 지연 (vision 경로)**: 일 덤프는 전일 데이터라 항상 하루 늦게 채워진다. 백테스트
  용도엔 무영향이나 실시간 용도로는 부적합(Phase 4에서 별도 환경 검토).
- **펀딩 최대 ~1개월 지연**: 펀딩은 월 덤프만 있어 진행 중인 달 값은 다음 달에 채워진다
  (§5 참조). 본 지표(Net L/S)는 klines+OI만으로 계산되므로 펀딩 지연은 핵심에 무영향.
- **OI 소급 범위**: fapi 30일 제한과 달리 vision `metrics`는 과거로 길게 제공되나, 특정
  과거일 덤프가 없을 수 있다(그런 날은 자동 스킵·로그). 실제 시작 가능일은 첫 실행 로그의
  `oi_backfill_start` 확인.
- **추정치**: 본 지표는 테이커 방향 + OI 분해 기반 추정치이며 거래소 공식 포지션
  데이터가 아니다. Coinglass가 복수 거래소 집계라면 배율 차이가 날 수 있고, 그 경우
  "바이낸스판 Net L/S"를 자체 표준으로 채택한다(전략 유효성엔 무영향).
- **60일 비활성화 규칙**: 데이터 커밋이 지속되는 한 자동 회피된다.

---

## 7. 리포 운영 메모

- 본 리포는 **public** 권장(Actions 무료 무제한, 시장 공개 데이터라 문제없음).
- 분석·전략 코드는 본 리포에 포함하지 않는다(추후 별도 private/로컬).
- 범위 외: 이벤트 태깅·백테스트(Phase 2), 실시간 시그널 봇(Phase 4).

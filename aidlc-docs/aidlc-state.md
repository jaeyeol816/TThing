# AI-DLC State Tracking

## Project Information
- **Project Name**: 대리 에이전트 메시 (Delegate Agent Mesh)
- **Project Type**: Greenfield
- **Start Date**: 2026-08-19T00:00:00Z
- **Last Updated**: 2026-08-20T15:10:00Z
- **Current Stage**: CONSTRUCTION - Code Generation Part 2 (U1·U2·U3·U4 완료)
- **Next Stage**: Build and Test — U5 배포는 선택 (AGENT_TRANSPORT=direct 로 동작)

## Workspace State
- **Existing Code**: No
- **Reverse Engineering Needed**: No
- **Workspace Root**: /Users/jaeyeol/prompthon
- **Requirements Source**: `requirements/hackathon-mvp-design.md`, `requirements/scenarios.md`

## Code Location Rules
- **Application Code**: Workspace root (`src/mesh/`, `data/`, `config/`, `tests/`, `scripts/`, `infra/`)
- **Documentation**: `aidlc-docs/` only (markdown)
- **Structure**: Greenfield single-unit layout + separate `infra/` for CDK

## Extension Configuration
| Extension | Enabled | Mode | Decided At |
|---|---|---|---|
| Security Baseline | **Yes** | Blocking | Requirements Analysis |
| Resiliency Baseline | **No** | — | Requirements Analysis |
| Property-Based Testing | **Yes** | **Partial** (PBT-02, 03, 07, 08, 09 enforced) | Requirements Analysis |

**Note**: 사용자가 "aidlc-docs 포맷을 채워달라"고 요청했으므로 AI가 근거와 함께 결정했다.
근거는 `inception/requirements/requirement-verification-questions.md` Q6~Q8. 변경을 원하면 알려주면 된다.

## Execution Plan Summary
- **Total Units**: 6
- **Stages Executed**: INCEPTION 6 + CONSTRUCTION per-unit design 12 + Code Generation Part 1
- **Stages Skipped**: Reverse Engineering (greenfield) + 유닛별 CONDITIONAL 스킵 (근거는 execution-plan.md §3)

## Stage Progress

### 🔵 INCEPTION PHASE
- [x] Workspace Detection
- [x] Reverse Engineering — **SKIPPED** (greenfield)
- [x] Requirements Analysis (Comprehensive) — 55 FR + 34 NFR
- [x] User Stories — 31 stories, 6 personas
- [x] Workflow Planning
- [x] Application Design — 16 components, 5 artifacts
- [x] Units Generation — 6 units

### 🟢 CONSTRUCTION PHASE

| Unit | Functional Design | NFR Requirements | NFR Design | Infrastructure Design | Code Gen P1 | Code Gen P2 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **U1** gatekeeper-core | [x] | [x] | [x] | SKIP | [x] | **[x]** |
| **U2** knowledge-edge | [x] | SKIP | SKIP | SKIP | [x] | **[x]** |
| **U3** agent-mesh | [x] | [x] | SKIP | SKIP | [x] | **[x]** |
| **U4** console-web | [x] | SKIP | SKIP | SKIP | [x] | **[x]** |
| **U5** cloud-broker | SKIP | [x] | [x] | [x] | [x] | [ ] |
| **U6** demo-corpus-eval | [x] | SKIP | SKIP | SKIP | [x] | **[x]** |

- [ ] Build and Test — 전 유닛 Code Gen P2 완료 후

### 🟡 OPERATIONS PHASE
- [ ] Operations — PLACEHOLDER

## Units of Work

| ID | Unit | Owner | Schedule | Stories (primary) |
|---|---|:---:|---|:---:|
| U1 | `gatekeeper-core` | A | Day 1~2 | 15 |
| U2 | `knowledge-edge` | B | Day 1, 3 | 7 |
| U3 | `agent-mesh` | B | Day 3 | 9 |
| U4 | `console-web` | C | Day 4 | 10 |
| U5 | `cloud-broker` | A | Day 2~3 | 3 |
| U6 | `demo-corpus-eval` | C | Day 1~2, 4 | 9 |

**Critical path**: U6 → U1 → U3 → U4
**Bottleneck**: U1 (주담당 스토리 15/31). Day 1에 스텁을 먼저 커밋해 U3의 Day 3을 막지 않는다.

## Quality Gates

| Gate | When | Criteria | Status |
|---|---|---|:---:|
| **SG1** | Day 1 첫 커밋 전 | `.gitignore`가 자격증명 3종 커버 🔴 | **[x]** 커밋 c91c1b8 검증 |
| **G1** | Day 1 종료 | `schemas.py` + `vocab.json` 동결 | **[x]** 계약 6종 동결 |
| **G2** | Day 2 종료 | **기밀 재현율 100%**, 정확도 ≥90% 🔴 | **[x] 통과** `make eval-classify` 11/11=100% · 기밀 3/3 · 함정 1/1 · 하향 0건 |
| **G3** | Day 3 종료 | 시나리오 1 종단 통과, 인용 0개 차단 | **[x] 통과** `make eval` 44개 · 3막 + 업로드 종단 · 전수 유출 0건 |
| **SG2~4** | U5 배포 전 | 브로커 인증·최소권한·감사 무결성 | [ ] |
| **G4** | Day 5 | **유출 0건** (자동 + 육안 전수) 🔴 | **[x] 자동 통과 · 육안 수행** 페이로드 10건 × 문서 11건 → 원문 0 · 금칙어 0. **육안 확인이 자동 검사가 놓친 결함 2건을 찾아 수정했다** (아래 §Day 4~5). 덤프: `construction/g4-payload-dump.md`. 체크박스 서명은 확인자 몫 |
| **G5** | Day 5 | 목업 모드로 3막 전체 통과 | **[x] 통과** 네트워크 0회로 4막 + 유출 검사 exit=0 · `make eval-dump-payloads` 도 오프라인 재생 |
| **SG5** | Day 5 | 로그·감사에 원문·토큰·`reasoning*` 부재 | **[x]** 감사 로그가 금지 필드를 **거부**한다 (`reject_forbidden`) |

**추가 게이트 (Day 1 에 신설)**

| Gate | Criteria | Status |
|---|---|:---:|
| SG6 | 의존성 취약점 0건 (`make audit`) | **[x]** 8건 발견 → 0건 |
| SG7 | import 경계 3개 규칙 (ast 검사) | **[x]** |
| SG8 | `logging` 예약어 충돌 0건 (ast 검사) | **[x]** |
| SG9 | 차단 목록 ∩ 가명화 목록 = ∅ (로드 시점 강제) | **[x]** |
| SG10 | `validator.py` 순수성 — I/O·설정 참조 0건 (ast, Lambda 번들 조건) | **[x]** Day 2 신설 |
| SG11 | `audit` 테이블 추가 전용 — `DELETE`/`UPDATE` 문 부재 (정규식) | **[x]** Day 2 신설 |
| SG12 | 관문 8개에 `NotImplementedError` 스텁 부재 (ast) | **[x]** Day 2 신설 |
| SG13 | `orchestrator.py` 에 모델 호출 부재 (ast) | **[x]** Day 3 신설 |
| SG14 | `agent.py` 에 경계 클라이언트 부재 (ast) | **[x]** Day 3 신설 |
| SG15 | 최상위 `mesh.llm.broker` import 0건 (전 모듈) | **[x]** Day 3 신설 |
| SG16 | `inbox` UPDATE 가 감사 흔적을 건드리지 않음 | **[x]** Day 3 신설 |
| SG17 | 웹 UI 정적 검사 — XSS·인라인·외부 CDN·경로 노출·페이로드 절단 | **[x]** Day 4 신설 (`scripts/lint_web.py`) |
| SG18 | 검사기 자체를 검사 — 심은 위반 12종을 잡는지 | **[x]** Day 4 신설 (`test_lint_web.py`) |
| SG19 | 오류 응답이 요청 본문을 되비추지 않음 | **[x]** Day 4 신설 (FastAPI 기본 422 교체) |
| SG20 | 재녹화가 기존 픽스처를 덮어쓰지 않음 | **[x]** Day 4 신설 (게이트가 조용히 뒤집히는 것을 막는다) |

**G2를 통과하지 못하면 Day 3으로 넘어가지 않는다** (설계 §7.2).

## Verified Technical Facts (실측, 2026-08-19)

`aidlc-docs/construction/preflight-findings.md` 참조.

| # | 사실 | 설계 문서 대비 |
|---|---|---|
| 1 | EXAONE(Friendli) OpenAI 호환, 0.8~1.0s, `json_object` 지원 | 확인 |
| 2 | `enable_thinking:true`면 `reasoning*`에 원문이 실릴 수 있다 | **신규 발견 → 설계 변경** |
| 3 | 전체 JSON 생성 방식은 어휘 사전을 이탈한다 (실측) | **설계 변경 → 슬롯 채우기 + 코드 조립** |
| 4 | `claude-sonnet-5`는 이 계정에서 AccessDenied | **설계 변경 → `us.anthropic.claude-sonnet-4-5-20250929-v1:0`** |
| 5 | 모든 Claude가 추론 프로파일 필수 (`us.` 접두사) | 신규 |
| 6 | AWS 자격증명이 임시 STS → 만료 | **설계 변경 → Lambda 브로커** |
| 7 | 계정이 us-east-1 외 Deny, CDK 미부트스트랩 | 신규 |
| 8 | 로컬 Python 3.9.12 (부족), `uv`·Node 있음, `aws` CLI 구버전 | 신규 |
| 9 | 워크스페이스에 자격증명 평문 + `.gitignore` 없음 | **blocking 조치 필요** |
| 10 | EXAONE 엔드포인트가 사외 SaaS → **신뢰 경계가 시뮬레이션** | 정직하게 고지 |
| 11 | 여러 문서를 한 프롬프트에 넣으면 상충하는 사실이 하나로 합쳐진다 | **설계 변경 → 문서별 추출 + `facts` 를 ref 별로 분리** |
| 12 | `json.dumps` 의 `\n` 이스케이프가 5-gram 대조를 우회시킨다 | **설계 변경 → 원시 문자열 값을 함께 대조** |
| 13 | 헤더 등급 표기를 금칙어 검사보다 먼저 보면 하향 경로가 생긴다 | **설계 변경 → 규칙 순서 재배치** |
| 14 | 슬롯 채우기 실측 0.43s/문서 · 페이로드 562 bytes · 원문 0개 | 확인 |
| 15 | AWS 임시 자격증명 만료됨 (발견 5가 예측한 대로) | Agent 실호출 전 갱신 필요 |
| 16 | `select_paths` 프롬프트 326자 · 0.44s — 본문 미포함 확인 | BR-S-02 효과 실측 |
| 17 | `focus_topic` 닫힌 어휘 3/3 정확 · 0.23~0.26s | **설계 변경 → 자유 문장 요약 폐기** |
| 18 | 초안 프롬프트에 제목·시점·세션 사실을 넣을 수 없다 | **설계 변경 → 로컬에서 덧붙임** |

## Current Status
- **Lifecycle Phase**: CONSTRUCTION
- **Current Stage**: Code Generation Part 2 — **Day 1~2 완료**
- **Next Stage**: Day 3 (U2 Step + U3) — Store 완성 · Agent · Orchestrator (소유 B)
- **Status**: 게이트 G2·G3 통과. Day 4 착수 가능
- **Blocker**: 없음. AWS 자격증명이 갱신됐고 `make preflight` 27건 실패 0 통과.
  ⚠️ `.kiro/.env` 에 **만료된** `AWS_*` 가 남아 있다 — `. ./.env` 만 로드할 것
  (두 파일을 순서대로 로드하면 만료된 것이 이긴다)

### Day 1 완료 내역 (2026-08-19)

| 항목 | 결과 |
|---|---|
| 테스트 | **382개 통과** |
| lint / format | 통과 |
| 의존성 취약점 | **0건** (초기 선정값에서 8건 발견 → 버전 상향) |
| `make preflight` | 실패 0 · 경고 2 (경계 시뮬레이션, CDK 미부트스트랩 — 예상됨) |
| 규칙 기반 분류 정확도 | **11/11 = 100%** (G2 예비 통과) |
| 기밀 재현율 | **3/3 = 100%** · 함정 문서 1/1 탐지 |
| LLM 호출 | EXAONE 3회 · Bedrock 3회 (검증 목적만) |

**동결된 계약** (변경은 3인 합의로만, NFR-M-02)
- `src/mesh/schemas.py` — 타입 계약
- `data/vocab.json` — 어휘 사전 v1.0.0 (슬롯 8개, task 3개)
- `src/mesh/gatekeeper.py` — 7개 메서드 시그니처 (`test_gatekeeper_contract.py` 가 강제)
- `src/mesh/api_models.py` — HTTP API 계약 8개
- `config/agents.yaml` — 에이전트 3개
- `data/fixtures/api/*.json` — 11개 (실제 모델로 생성, 역파싱 검증됨)

**Day 1 에 발견·수정한 결함 4건** (`preflight-findings.md` §7)
1. `Tier` 비교가 알파벳 순 → `max(INTERNAL, OPEN) == OPEN` 조용한 유출. 4개 비교 메서드 명시
2. `extra={"name":...}` 가 `LogRecord.name` 과 충돌 → 로그 한 줄이 요청을 죽인다. `log_extra()` + ast 정적 검사
3. 🔴 차단 목록과 가명화 목록을 섞어 정확도 55% → `pseudonyms.json` 분리 + 로드 시점 겹침 거부
4. 의존성 취약점 8건 → 버전 상향 + `starlette` 직접 고정

### Day 2 완료 내역 (2026-08-19) — U1 Step 9~18

보고서: `aidlc-docs/construction/day2-implementation-report.md`

| 항목 | 결과 |
|---|---|
| 테스트 | **712개 통과** (unit 681 + property 31) |
| **게이트 G2** | **정확도 11/11 = 100% · 기밀 재현율 3/3 = 100% · 함정 1/1 · 하향 0건** |
| lint / format | 통과 |
| 의존성 취약점 | **0건** |
| LLM 호출 | EXAONE **5회** (추출 검증 4 + preflight 1) · Bedrock 0회 |
| 구조 추출 실측 | 검증 6/6 · 562 bytes · 원문 5-gram 0건 · 지연 0.86s (문서 2건) |

**신규 모듈 6개**

| 모듈 | 레이어 | 역할 |
|---|:---:|---|
| `validator.py` | L1 | 검증 6단계 — **순수 함수** (U5 Lambda 번들 조건) |
| `rehydrator.py` | L1 | 기호 → 실제 이름. 매핑 없는 기호는 유지 |
| `classifier.py` | L3 | `max(규칙, EXAONE)`. 규칙이 하한선 |
| `extractor.py` | L3 | 슬롯 채우기 + 화이트리스트 조립 |
| `pseudonymizer.py` | L3 | 식별자 치환, 기술 용어 보존 (LLM 0회) |
| `audit.py` | L4 | SQLite 추가 전용 + 원문 검색 + 전수 유출 검사 |

`gatekeeper.py` 8개 관문 구현 완료 + `send_and_rehydrate()` 신설
(매핑 폐기를 `try/finally` 로 보장).

**Day 2 에 발견·수정한 설계 결함 3건** (`preflight-findings.md` §9)

1. 🔴 **`BR-C-03` 규칙 순서에 조용한 하향 경로** — 헤더(작성자 자기 신고)가
   금칙어 검사(기계적)보다 먼저라서 `보안등급: 사내` 한 줄로 금액 탐지(FR-52)를
   무력화할 수 있었다. SECRET 을 만드는 기계적 검사를 앞으로 옮겼다.
   함께: `OPEN` 은 헤더 + 경로 **두 신호**를 요구한다
2. 🔴 **`json.dumps` 가 5-gram 대조를 우회시킨다** — 문자열 값의 실제 개행이
   `\n` 두 글자로 직렬화되어 공백 정규화를 빠져나갔다. BR-V-05 가 막겠다고 한
   우회가 실제로 뚫려 있었다
3. 🔴 **평탄한 `facts` 가 상충하는 사실을 합쳐 버린다** — 검증 6/6 통과 + 원문
   0개인데 **답이 틀린다.** `facts` 를 `{ref: {슬롯: 값}}` 으로 분리하고
   문서마다 따로 슬롯을 채운다

부수: Day 1 계약에 사내·공개 페이로드 형태가 없었다 → `excerpts` 키 도입 +
표현별 허용 키 분리 (`STRUCTURED` 에서는 텍스트 키 금지)

### Day 3 완료 내역 (2026-08-19) — U2 Step 5~9 + U3 Step 1~7

보고서: `aidlc-docs/construction/day3-implementation-report.md`

| 항목 | 결과 |
|---|---|
| 테스트 | **976개 통과** (unit 896 + property 42 + eval 38) |
| **게이트 G3** | **3막 종단 통과 · 전수 유출 0건 · 인용 0개 차단** |
| lint / audit | 통과 · 취약점 0건 |
| LLM 호출 | EXAONE **5회** (select_paths 1 + focus_topic 3 + preflight 1) · Bedrock 0회 |

**신규 모듈 5개 + Store 완성**

| 모듈 | 레이어 | 역할 |
|---|:---:|---|
| `store.read()` | L5 | 2중 검사 · 종류별 파싱 · `run_log` 마지막 200줄 |
| `store.select_paths()` | L5 | 인덱스 선택. **본문 미포함** (프롬프트 326자 실측) |
| `store.list_agents()` | L5 | 닫힌 어휘 주제 라벨 · 캐시 · `disclose` 반영 |
| `agent.py` | L5 | Gatekeeper 경유 호출 · 에스컬레이션 초안 |
| `inbox.py` | L5 | 3버튼 · `VerifiedQA` 환류 · UPDATE 범위 제한 |
| `orchestrator.py` | L6 | `prepare`/`send` · `branch`/`merge` · 30초 상한 |
| `main.py` | L7 | FastAPI 9개 엔드포인트 · 보안 헤더 4개 |
| `scripts/demo.py` | — | 3막 시연 대본 (대역 주입 테스트로 검증) |

**Day 3 에 발견·수정한 결함 4건** (`preflight-findings.md` §11)

1. 🔴 **초안 입력이 전부 경계를 넘어서는 안 되는 것들이었다** — 근거 제목·시점·
   세션 사실·부분 응답. 설계가 "`display_title` 만"이라고 쓴 것은 `internal_path`
   와 비교한 말이었지만 경계를 넘는 맥락에서는 제목도 원문 파생물이다.
   → 검증된 envelope 재사용 + 제목·시점은 **신뢰 구역 안에서** 덧붙인다
2. 🔴 **목록 요약을 자유 문장으로 만들면 검사할 방법이 없다** — 사후 검사 구조는
   §3.1 에서 기각한 것이다. → 닫힌 라벨 집합(`FOCUS_TOPICS` 7개)에서 선택
3. **브로커를 `main.py` 가 만들면 경계를 넘는 모듈이 늘어난다** →
   `Gatekeeper.build()` 팩토리로 생성도 통로 안에 뒀다
4. **`api_models` 를 소유(U3) 기준으로 L5 에 뒀다** — 레이어는 소유가 아니라
   의존 순서다 → L1

### 첫 완전 live 실행 (2026-08-20) — 결함 5건 발견·수정

AWS 자격증명 갱신 후 **EXAONE + Bedrock 을 모두 실제로 쓰는 첫 종단 실행**.
대역 테스트 976개가 전부 통과한 상태였는데 **결함 5건이 나왔다** —
넷은 대역으로 잡을 수 없는 종류다 (`preflight-findings.md` §13).

| # | 결함 | 영향 |
|---|---|---|
| 22 | 초안 프롬프트가 원래 답을 다시 만들게 했다 (`answer_format` 지시 충돌) | 인박스 초안이 내용 없는 폴백으로 |
| 23 | 기본 스키마가 "묻지 않은 것에 자신 있게 답"하게 했다 | p99 질문에 인증 충돌을 신뢰도 0.75 로 답변 |
| 24 | 초안 응답이 본 응답의 픽스처를 덮어썼다 (키에 `model_id` 없음) | 오프라인 데모에서 답변이 인계 메모로 바뀜 |
| 25 | 차단된 호출의 등급 배지가 낮게 표시됐다 | `[기밀]` 자리에 `[사내]` |
| 26 | 전수 유출 검사가 오탐 1076건 (등급별 규칙 미적용) | 진짜 유출을 가리는 도구가 되어 있었다 |

수정 후 실측: **시나리오 1 = 처분 `auto` · 신뢰도 0.85 · 검증 6/6 · 원문 문장 수 0**.
실제 Claude 가 원문을 한 글자도 보지 않고 세 문서를 대조해 세 가지 충돌을 짚고
대응 방안 5개를 냈다. **전수 유출 검사 0건** (structured 7 + pseudonymized 4).

목업 픽스처 24개 녹화 완료 → **네트워크 0회로 4막 전체 통과 (게이트 G5)**.

### Day 4~5 (2026-08-20) — 업로드 입구 · 화면 · 데스크톱 셸 · 게이트 G4

전체 보고서: `construction/day4-5-implementation-report.md`
설계 자료: `docs/설계자료.md` (Mermaid 15개) · 사용 설명서: `docs/사용설명서.md`

**테스트 1,101개 통과** (Day 3 대비 +125). lint · audit · G2 · G3 · G4 자동 · G5 전부 통과.

#### G4 육안 전수가 찾은 결함 2건 — **자동 검사는 통과했다**

| # | 결함 | 왜 자동 검사가 놓쳤나 | 고친 방법 |
|---|---|---|---|
| 27 | `person:park` 같은 **entity_id** 가 사내 등급 발췌에 실려 나갔다 | 치환 목록에 사람 이름(`박선영`)만 있었다. `person:park` 는 목록에 없으므로 "식별자가 아니었다" — **목록에 없는 것은 검사되지 않는다** | `agents.yaml` 에서 entity_id·display_name 을 **유도해** 치환 대상에 자동 추가. 두 파일을 고치게 만들면 하나를 잊고 유출된다 (FR-23 과 같은 근거) |
| 28 | 사내 등급 미리보기가 "원문 문장·제품명·버전·일정 없음"이라고 **거짓 표시** | 유출이 아니라 **거짓 보증**이다. 검사할 규칙이 없었다. `excluded_categories` 가 자기 docstring 을 어기고 있었다 | `EXCLUDED_CATEGORIES_BY_REPRESENTATION` 신설. 가명화는 원문 문장을 유지하는 것이 정의이므로 그것을 약속하지 않는다 |

28번이 유출보다 나쁜 이유: 사용자는 그 목록을 읽고 [전송] 을 누른다.
목록이 거짓이면 **"사람이 미리보기를 확인한다"는 방어 겹 자체가 무의미해진다.**

#### 그 밖에 찾아 고친 결함 6건

| # | 결함 | 영향 |
|---|---|---|
| 29 | FastAPI 기본 422 가 **요청 본문을 되비췄다** | 업로드가 상한을 넘기면 기밀 문서 전문(최대 200,000자)이 오류 응답에 실렸다 |
| 30 | 재녹화가 기존 픽스처를 **덮어썼다** | `classify` 픽스처의 `tier` 가 `secret`→`internal` 로 바뀌었다. **G2 를 통과시킨 값이다.** 키는 입력에서 유도되므로 `git diff` 에 한 줄로만 나타난다 |
| 31 | NUL 바이트가 `PathEscapeError` 대신 `ValueError` | 업로드 경로에서 500 이 되고 감사 로그에 남지 않는다. 속성 테스트(PB-S1)가 찾았다 |
| 32 | `Makefile run:` 이 존재하지 않는 `mesh.main:app` 을 가리켰다 | `make run` 이 깨져 있었다 → `--factory mesh.main:create_app` |
| 33 | 시드 JSON 이 데모마다 줄바꿈을 잃었다 | 매 실행 `git diff` 잡음 → 실제 변경과 구별 불가 |
| 34 | 테스트 픽스처가 `data/corpus/*/uploads/` 를 복사했다 | "어제 시연에서 올린 파일" 때문에 오늘 테스트가 깨졌다 (실제로 깨졌다) |

#### 결정

| 결정 | 기각한 대안 | 근거 |
|---|---|---|
| Tauri 가 **백엔드 URL 을 직접 연다** | 정적 파일 번들 | 번들하면 origin 이 `tauri://localhost` 가 되어 `fetch("/api/...")` 가 도달하지 못한다. 절대 URL 로 바꾸면 `connect-src` 개방 + 호스트 하드코딩 + "외부 URL 0건" 규칙 붕괴. URL 을 열면 같은 origin → FastAPI CSP 가 그대로 적용된다 |
| Rust `setup()` 에서 창 생성 | `tauri.conf.json` 의 `windows` | 백엔드 준비 전에 빈 창을 띄우지 않는다. 120초 초과면 창을 만들지 않고 실패를 알린다 |
| 업로드를 **텍스트 JSON** 으로 | multipart | 대상이 전부 텍스트. multipart 파서는 파일명·인코딩 사고가 잦다. 클라이언트가 무엇을 보내는지 보여줄 수 있다 |
| `.sh`·`.sql`·`.py` **허용** | 실행 위험으로 차단 | 기준은 "실행 위험"이 아니라 "텍스트로 읽히는가". 배포 스크립트는 실제로 팀이 서로 묻는 지식이고, 이 시스템은 파일을 실행하지 않는다 |
| `mesh.documents` 를 **L6** | L5 | `store`(L5) 를 import 한다. 같은 층이면 레이어 규칙 위반 |
| 업로드 시 `session.updated_at` **미변경** | 갱신 | 파일을 올린 것만으로 "활동 중"이 되면 STALE 보정(BR-S-04)이 의미를 잃는다 |
| `DocumentView.internal_path` **허용** | 제거 | 소유자가 자기 문서 경로를 보는 것은 권한 우회가 아니다. FR-43 이 막는 것은 *다른 사람* 지식 인용 시 경로 유출 |
| `lint-web.sh` → **`lint_web.py`** | bash grep 유지 | grep 이 규칙을 설명하는 **주석까지** 잡았다 (오탐 3건, Day 2·3 에서 같은 문제 2회). 주석 제거 + 줄 단위 허용마커 + 영역 한정 |
| 픽스처 재녹화는 **빠진 것만 채운다** | 무조건 덮어쓰기 | 재녹화 한 번이 게이트를 조용히 뒤집었다 (결함 30) |
| 실측 스크립트를 **Python** 으로 | bash + heredoc | heredoc 5개의 인용 사고 + HTTP 오류에 원시 traceback 만 보였다 |

## Open Items for User

| # | 항목 | 위치 |
|---|---|---|
| 1 | Extension 설정 확인 (Security=Yes / Resiliency=No / PBT=Partial) | `requirement-verification-questions.md` Q6~Q8 |
| 2 | 미결 설계 결정 6건 확인 (AI가 결정했음) | 같은 문서 Round 2 (Q9~Q14) |
| 3 | **사내망 EXAONE 엔드포인트 확보 가능 여부** | 같은 문서 Q15 |
| 4 | 이식성 대비 수준 확인 | 같은 문서 Q16 |
| 5 | **G4 육안 확인 체크박스 서명** (10건) | `construction/g4-payload-dump.md` |

**1~4번은 blocking이 아니다.** AI 결정대로 진행 가능하며, 다르게 가고 싶은 것만 알려주면 된다.

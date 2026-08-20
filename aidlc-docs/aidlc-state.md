# AI-DLC State Tracking

## Project Information
- **Project Name**: 대리 에이전트 메시 (Delegate Agent Mesh)
- **Project Type**: Greenfield
- **Start Date**: 2026-08-19T00:00:00Z
- **Last Updated**: 2026-08-19T13:10:00Z
- **Current Stage**: CONSTRUCTION - Code Generation Part 2 (U1 Day 2 완료)
- **Next Stage**: Day 3 — U2 Store 완성 + U3 Agent/Orchestrator (소유 B)

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
| **U2** knowledge-edge | [x] | SKIP | SKIP | SKIP | [x] | [ ] |
| **U3** agent-mesh | [x] | [x] | SKIP | SKIP | [x] | [ ] |
| **U4** console-web | [x] | SKIP | SKIP | SKIP | [x] | [ ] |
| **U5** cloud-broker | SKIP | [x] | [x] | [x] | [x] | [ ] |
| **U6** demo-corpus-eval | [x] | SKIP | SKIP | SKIP | [x] | [ ] |

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
| **G3** | Day 3 종료 | 시나리오 1 종단 통과, 인용 0개 차단 | [ ] |
| **SG2~4** | U5 배포 전 | 브로커 인증·최소권한·감사 무결성 | [ ] |
| **G4** | Day 5 | **유출 0건** (자동 + 육안 전수) 🔴 | [ ] |
| **G5** | Day 5 | 목업 모드로 3막 전체 통과 | [ ] |
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
| 15 | AWS 임시 자격증명 만료됨 (발견 5가 예측한 대로) | Day 3 전 갱신 필요 |

## Current Status
- **Lifecycle Phase**: CONSTRUCTION
- **Current Stage**: Code Generation Part 2 — **Day 1~2 완료**
- **Next Stage**: Day 3 (U2 Step + U3) — Store 완성 · Agent · Orchestrator (소유 B)
- **Status**: 게이트 G2 통과. Day 3 착수 가능
- **Blocker (Day 3)**: AWS 임시 자격증명 만료 → 갱신 필요. `AGENT_TRANSPORT=mock` 으로 우회 가능

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

**Day 3 에 남은 것** (소유 B)
- `store.read()` / `select_paths()` / `list_agents()`
- `agent.py` · `orchestrator.py` · `inbox.py` · `main.py`
- 게이트 G3: 시나리오 1 종단 통과 + 인용 0개 차단

## Open Items for User

| # | 항목 | 위치 |
|---|---|---|
| 1 | Extension 설정 확인 (Security=Yes / Resiliency=No / PBT=Partial) | `requirement-verification-questions.md` Q6~Q8 |
| 2 | 미결 설계 결정 6건 확인 (AI가 결정했음) | 같은 문서 Round 2 (Q9~Q14) |
| 3 | **사내망 EXAONE 엔드포인트 확보 가능 여부** | 같은 문서 Q15 |
| 4 | 이식성 대비 수준 확인 | 같은 문서 Q16 |

**1~4번은 blocking이 아니다.** AI 결정대로 진행 가능하며, 다르게 가고 싶은 것만 알려주면 된다.

# AI-DLC State Tracking

## Project Information
- **Project Name**: 대리 에이전트 메시 (Delegate Agent Mesh)
- **Project Type**: Greenfield
- **Start Date**: 2026-08-19T00:00:00Z
- **Last Updated**: 2026-08-19T11:30:00Z
- **Current Stage**: CONSTRUCTION - Code Generation Part 1 (Planning) Complete
- **Next Stage**: CONSTRUCTION - Code Generation Part 2 (Generation) — **awaiting approval**

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
| **U1** gatekeeper-core | [x] | [x] | [x] | SKIP | [x] | [ ] |
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
| **SG1** | Day 1 첫 커밋 전 | `.gitignore`가 자격증명 3종 커버 🔴 | [ ] |
| **G1** | Day 1 종료 | `schemas.py` + `vocab.json` 동결 | [ ] |
| **G2** | Day 2 종료 | **기밀 재현율 100%**, 정확도 ≥90% 🔴 | [ ] |
| **G3** | Day 3 종료 | 시나리오 1 종단 통과, 인용 0개 차단 | [ ] |
| **SG2~4** | U5 배포 전 | 브로커 인증·최소권한·감사 무결성 | [ ] |
| **G4** | Day 5 | **유출 0건** (자동 + 육안 전수) 🔴 | [ ] |
| **G5** | Day 5 | 목업 모드로 3막 전체 통과 | [ ] |
| **SG5** | Day 5 | 로그·감사에 원문·토큰·`reasoning*` 부재 | [ ] |

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

## Current Status
- **Lifecycle Phase**: CONSTRUCTION
- **Current Stage**: Code Generation Part 1 (Planning) Complete — 6개 유닛 전부
- **Next Stage**: Code Generation Part 2 (Generation) — U1 Step 1부터
- **Status**: **Awaiting approval to begin implementation**
- **First Action on Approval**: U1 Step 1 (`.gitignore` → `git init`) 🔴

## Open Items for User

| # | 항목 | 위치 |
|---|---|---|
| 1 | Extension 설정 확인 (Security=Yes / Resiliency=No / PBT=Partial) | `requirement-verification-questions.md` Q6~Q8 |
| 2 | 미결 설계 결정 6건 확인 (AI가 결정했음) | 같은 문서 Round 2 (Q9~Q14) |
| 3 | **사내망 EXAONE 엔드포인트 확보 가능 여부** | 같은 문서 Q15 |
| 4 | 이식성 대비 수준 확인 | 같은 문서 Q16 |

**1~4번은 blocking이 아니다.** AI 결정대로 진행 가능하며, 다르게 가고 싶은 것만 알려주면 된다.

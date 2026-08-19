# U3 `agent-mesh` — Code Generation Plan

**소유**: B · **일정**: Day 3 · **스토리**: 9개 주담당
**설계 근거**: `aidlc-docs/construction/u3-agent-mesh/`
**코드 위치**: `src/mesh/agent.py`, `orchestrator.py`, `inbox.py`, `main.py`

---

## 유닛 컨텍스트

| | |
|---|---|
| **구현 스토리** | S-09, S-11, S-16, S-18, S-20, S-22 주담당 + S-01/S-03/S-15 협력 |
| **의존 (강함)** | U1 `Gatekeeper` + `schemas.py` · U2 `KnowledgeStore` |
| **제공** | HTTP API 8개 — U4가 소비 |
| **경계** | **Bedrock을 직접 부르지 않는다.** `Gatekeeper.ask_agent()` 경유 |

---

## Step 0 · Day 1 선행 작업 (계약 확정)

Day 1에 U1의 스텁과 함께 확정한다. C(U4)가 이걸 보고 목업을 만든다.

- [x] 0.1 API 요청/응답 모델을 `schemas.py` 또는 `api_models.py`에 정의
- [x] 0.2 `AskRequest`, `PrepareResult`, `PreparedCall`, `SendRequest`
- [x] 0.3 `MergedAnswer`(**`divergent`**, `conflict` 아님), `AskResult`
- [x] 0.4 `EscalationDraft`, `InboxItem`, `ResolveRequest`, `HealthStatus`
- [x] 0.5 **`PrepareResult.agents_notified: Literal[False]`** 🔴 (BR-O-03)
- [x] 0.6 C에게 계약 전달 → `data/fixtures/api/*.json` 작성 요청
- [x] 0.7 커밋

---

# Day 3 — 구현

## Step 1 · `agent.py`

- [ ] 1.1 필수 시스템 프롬프트 문구 5개 상수 (BR-AG-02)
- [ ] 1.2 `build_system_prompt()` — 페르소나 + 필수 5개 + 등급별 문구
- [ ] 1.3 **`assert_all_mandatory_present()`** — 누락 시 예외 🔴
- [ ] 1.4 등급별 추가 문구 (BR-AG-03) — `INTERNAL`에 "placeholder를 추측하지 마십시오"
- [ ] 1.5 `ask()` — **`Gatekeeper.ask_agent()` 경유**. Bedrock 직접 호출 금지
- [ ] 1.6 `draft_escalation()` — `DRAFT_MODEL_ID`(haiku), **원문 미포함**
- [ ] 1.7 `daily_limit` 확인
- [ ] 1.8 `tests/unit/test_agent.py` — 필수 문구 검사, 프롬프트에 원문 부재

**1.3이 중요하다.** `agents.yaml`을 편집하다 페르소나 프롬프트로 덮어써도 필수 문구는 남아야 한다. 특히 "참조 기호로 지칭하십시오"가 없으면 재수화가 성립하지 않는다.

## Step 2 · `orchestrator.py` — 순수 함수 먼저

순수 함수를 먼저 만들면 U1/U2 없이 테스트할 수 있다.

- [ ] 2.1 **`branch()`** — 인용 0개 검사를 **신뢰도보다 먼저** 🔴 (BR-O-04)
- [ ] 2.2 2명일 때 `min(confidence)` (BR-O-05)
- [ ] 2.3 **`merge()`** — 요청 순서 유지, 답을 버리지 않는다 (BR-O-06, BR-O-07)
- [ ] 2.4 `divergent` 판정 — 텍스트 다름 **AND** 근거 문서 다름. **LLM 미사용** 🔴
- [ ] 2.5 `divergence_note` 고정 템플릿 ("둘 다 사실일 수 있습니다...")
- [ ] 2.6 `agent_cards()` — U2 `list_agents()` 위임
- [ ] 2.7 `tests/unit/test_branch.py` — 경계값 (0.44/0.45/0.74/0.75), 인용 0개
- [ ] 2.8 `tests/unit/test_merge.py` — 순서 유지, 답 보존, divergent 판정

## Step 3 · `orchestrator.py` — 조율

- [ ] 3.1 `prepare()` — 흐름 (`u3/business-logic-model.md` §2)
- [ ] 3.2 대상별 `asyncio.gather` 병렬
- [ ] 3.3 `daily_limit` 초과 대상 거부
- [ ] 3.4 `blocked`인 call에 `fallback` 동봉 (한 왕복에 끝)
- [ ] 3.5 **`agents_notified=False`** — `prepare`에서 인박스에 아무것도 쓰지 않는다 🔴
- [ ] 3.6 `send()` — 흐름 (§3)
- [ ] 3.7 `AuditLog.record()`를 `ask_agent()` **전에**
- [ ] 3.8 재수화 후 **`try/finally` 매핑 폐기 + 캐시 제거**
- [ ] 3.9 **하위 질문별 처분 분리** (`branch()`를 call 단위로 호출)
- [ ] 3.10 `ESCALATE`/`UNVERIFIED` 시 초안 생성 + `Inbox.add(thread_id=request_id)`
- [ ] 3.11 카운터 (`interrupts_avoided`, `minutes_saved_estimate`)
- [ ] 3.12 `asyncio.wait_for(30s)`. 타임아웃 시 도착한 답만 반환 (BR-O-08)
- [ ] 3.13 `return_exceptions=True` — 2명 중 1명 실패 시 나머지 반환 (R-02)
- [ ] 3.14 `AuditLog.mirror()` fire-and-forget
- [ ] 3.15 **`grep -c "exaone\|bedrock\|broker" orchestrator.py` == 0 확인** 🔴 (M-03)
- [ ] 3.16 `tests/unit/test_orchestrator.py`

## Step 4 · `inbox.py`

- [ ] 4.1 SQLite `inbox` 테이블 (U1 `audit.py`가 만든 것 사용) + 인덱스 2개
- [ ] 4.2 `add()` — `thread_id`, `tier` 보존
- [ ] 4.3 `list_for(owner)` — `owner_entity_id` + `status` 필터
- [ ] 4.4 `resolve()` — 3버튼 (BR-I-01)
- [ ] 4.5 `approve`/`approve_with_edit` → **`VerifiedQA` 환류, `tier` 보존** (BR-I-02)
- [ ] 4.6 `not_me` → `redirect_to`. **자동 재지목 안 함** (BR-I-03)
- [ ] 4.7 같은 `thread_id` 그룹 조회 (BR-I-04)
- [ ] 4.8 **`UPDATE`는 `status`/`resolved_at`/`resolution_text`/`redirect_to`만** (감사 흔적 보존)
- [ ] 4.9 `tests/unit/test_inbox.py` — 상태 전이 3개, 환류

## Step 5 · `main.py` — FastAPI

- [ ] 5.1 `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` 🔴 (SECURITY-09)
- [ ] 5.2 `MESH_DEV=1`일 때만 `/docs` 활성
- [ ] 5.3 `CorrelationIdMiddleware` — contextvar + `X-Correlation-Id` 응답 헤더
- [ ] 5.4 **`SecurityHeadersMiddleware`** — 4개 헤더 (BR-M-03) 🔴
- [ ] 5.5 `ConcurrencyLimitMiddleware` — 세마포어 5
- [ ] 5.6 **CORS 미들웨어를 추가하지 않는다** (동일 출처만, BR-M-04)
- [ ] 5.7 `GET /` `/app.js` `/style.css` — **명시 매핑** (디렉터리 리스팅 없음)
- [ ] 5.8 `GET /api/agents`
- [ ] 5.9 `POST /api/ask/prepare`
- [ ] 5.10 `POST /api/ask/send` — `envelope_id` 없으면 422, 만료면 **410 Gone**
- [ ] 5.11 `GET /api/inbox?owner=`
- [ ] 5.12 `POST /api/inbox/{id}/resolve`
- [ ] 5.13 `GET /api/audit?q=` — `q` 200자 제한, 파라미터화
- [ ] 5.14 `GET /api/health` — **`trust_boundary_simulated` 포함** 🔴
- [ ] 5.15 **전역 예외 핸들러** — 스택 트레이스 금지, `correlation_id`만 (BR-M-05)
- [ ] 5.16 **`MESH_BIND_HOST` != localhost면 시작 경고 + 확인 요구** 🔴 (BR-M-01)
- [ ] 5.17 앱 시작 시 `list_agents()` 워밍업
- [ ] 5.18 `tests/unit/test_api.py` — `TestClient` 기반

## Step 6 · PBT

- [ ] 6.1 `tests/generators.py`에 `rehydrated_answers()`, `ask_requests()` 추가
- [ ] 6.2 **PB-O1: 인용 0개면 항상 `ESCALATE`** (신뢰도 무관) 🔴
- [ ] 6.3 PB-O2: `branch()` 단조성
- [ ] 6.4 **PB-O3: `merge()`가 답을 하나도 버리지 않는다** 🔴
- [ ] 6.5 PB-O4: `merge()` 교환법칙 (병렬 응답 순서 무관)
- [ ] 6.6 PB-O5: `AskRequest` 직렬화 왕복
- [ ] 6.7 PB-O6: `AskResult` JSON에 `internal_path` 문자열 부재

## Step 7 · 종단 확인 (게이트 G3)

- [ ] 7.1 시나리오 1을 CLI로 종단 실행 (`scripts/demo.py` 초안)
- [ ] 7.2 `send`를 `approved_by` 없이 → **422**
- [ ] 7.3 만료된 `envelope_id` → **410**
- [ ] 7.4 같은 `envelope_id` 두 번 → 두 번째 **410** (일회용)
- [ ] 7.5 인용 0개 강제 (신뢰도 0.99) → **`ESCALATE`**
- [ ] 7.6 `/docs` `/redoc` `/openapi.json` → **404**
- [ ] 7.7 응답 헤더에 보안 헤더 4개
- [ ] 7.8 `agents.yaml`에 4번째 에이전트 추가 → **코드 변경 없이** 목록에 나타남
- [ ] 7.9 2명 중 1명 강제 실패 → 나머지 답변 반환
- [ ] 7.10 import 경계 테스트 통과
- [ ] 7.11 커밋 + **C에게 API 준비 완료 통보**

---

## 스토리 추적

| Story | 단계 | 완료 |
|---|---|:---:|
| S-09 직접 지목 | 2.6, 5.8 | [ ] |
| S-11 방해받지 않는다 🔴 | 3.5, 3.11 | [ ] |
| S-15 질문 분해 (협력) | 3.9 | [ ] |
| S-16 초안 인박스 25초 | 1.6, 4 | [ ] |
| S-18 사람이 되돌린다 | 4.6 | [ ] |
| S-20 갈리는 답 병기 🔴 | 2.3~2.5, 6.4 | [ ] |
| S-22 근거 없는 답 차단 🔴 | 2.1, 6.2 | [ ] |
| S-01 승인 (협력) | 5.9, 5.10 | [ ] |
| S-17 환류 (협력) | 4.5 | [ ] |

---

## 완료 기준

- [ ] 인용 0개 차단 (신뢰도 무관) 🔴
- [ ] 신뢰도 3구간 경계값 테스트
- [ ] 2명 병렬 + `divergent` 병기 (상충 자동 판정 없음) 🔴
- [ ] `agents.yaml` 항목 추가로 에이전트 증가 (코드 변경 0)
- [ ] `prepare`/`send` 2단계 — 승인 없이 `send` 실패 🔴
- [ ] `envelope` 일회용 (중복 전송 차단)
- [ ] 보안 헤더 4개 + `/docs` 404
- [ ] `orchestrator.py`에 모델 호출 코드 부재 🔴
- [ ] `agent.py`에 `boto3` import 부재 🔴
- [ ] 필수 시스템 프롬프트 문구 누락 시 예외
- [ ] 30초 상한 동작
- [ ] `AskResult` JSON에 `internal_path` 부재
- [ ] PB-O1~PB-O6 통과
- [ ] `/api/health`에 `trust_boundary_simulated` 노출

## 보안 준수 요약

| 규칙 | 상태 | 단계 |
|---|---|---|
| SECURITY-01 | 준수 (U1 SQLite 권한 공유) | — |
| SECURITY-02 | **N/A** — 로드밸런서·게이트웨이 없음 (localhost 직결) | — |
| SECURITY-03 | 준수 | 5.3 |
| SECURITY-04 | **준수** (HSTS는 localhost HTTP이므로 N/A, 근거 주석) | 5.4 |
| SECURITY-05 | 준수 | 0.2, 5.13 |
| SECURITY-06 | N/A (IAM 없음) | — |
| SECURITY-07 | **준수 (대체)** — VPC 없음. `127.0.0.1` 바인딩 강제 | 5.16 |
| SECURITY-08 | **부분 N/A** — 사용자 인증 범위 밖. 대체 통제 4개 (`u3/nfr-requirements.md` §2.1) | 5.7, 5.10, 5.16 |
| SECURITY-09 | 준수 — `/docs` 비활성, 오류 일반화, 명시 정적 매핑 | 5.1, 5.7, 5.15 |
| SECURITY-10 | 준수 (새 의존성 없음) | — |
| SECURITY-11 | 준수 — 보안 로직을 갖지 않는다. 레이트 리밋은 U5 | 3.15 |
| SECURITY-12 | 부분 N/A (인증 없음). 하드코딩 자격증명 0건 | — |
| SECURITY-13 | 준수 — `json.loads`만. 인박스 변경에 감사 흔적 | 4.8 |
| SECURITY-14 | 이전 (U5) | — |
| SECURITY-15 | 준수 — 전역 핸들러 + `try/finally` | 3.8, 5.15 |

| PBT 규칙 | 상태 |
|---|---|
| PBT-01 | 준수 (`u3/domain-entities.md` §8) |
| PBT-02 | 준수 (PB-O5) |
| PBT-03 | 준수 (PB-O1~O4, O6) |
| PBT-04 | **N/A** — `resolve`는 상태 전이로 멱등이 아니다. 예제 테스트가 전이 3개 커버 |
| PBT-05 | **N/A** — 참조 구현 없음 |
| PBT-06 | **N/A** — `Inbox`는 상태 4개·전이 3개로 자명. 5일 일정에서 모델 정의 비용이 이득을 넘는다 |
| PBT-07 | 준수 (6.1) |
| PBT-08 | 준수 (U1 프로파일) |
| PBT-09 | 준수 (U1 의존성) |
| PBT-10 | 준수 (U6 시나리오 예제 테스트) |

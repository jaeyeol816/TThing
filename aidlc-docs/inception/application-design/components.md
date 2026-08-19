# Components

**설계 목표**: 컴포넌트 경계 = 신뢰 경계. 파일 수를 작게 유지해 3인이 전체 구조를 머릿속에 담을 수 있게 한다 (NFR-M-01).

---

## 0. 신뢰 구역 배치

| 구역 | 컴포넌트 |
|---|---|
| **신뢰 구역 안 (노트북)** | Console, Orchestrator, AgentClient, **Gatekeeper**, Classifier, Extractor, Validator, Pseudonymizer, Rehydrator, KnowledgeStore, Inbox, AuditLog, ExaoneClient, SchemaRegistry, Config |
| **신뢰 구역 밖 (AWS)** | AgentBrokerFunction, AuditMirrorTable, AgentRegistryTable, InboxTable |

**단일 통로 규칙**: 신뢰 구역 밖으로 나가는 호출은 `Gatekeeper.ask_agent()`와 `AuditLog.mirror()` **두 개뿐**이다. 다른 어떤 모듈도 Bedrock/브로커 클라이언트를 import하지 않는다. 테스트로 강제한다 (`tests/unit/test_import_boundary.py`).

---

## 1. Gatekeeper — 보안 코어

| | |
|---|---|
| **파일** | `src/mesh/gatekeeper.py` |
| **한 줄** | Agent를 감싸는 막. Agent가 무엇을 볼 수 있는지 통제한다 |
| **소유** | A |
| **요구사항** | FR-01~15 |

**책임**
- 3개 관문 조율: ① 질문 변환 ② 지식 변환 ③ 재수화
- 신뢰 구역 밖 호출의 **유일한 진입점** (`ask_agent`)
- 등급 상향 / 질문 분해 판정 (FR-11, FR-12)
- 실패 시 닫기 (fail closed) — 판정 실패·검증 실패는 전부 `secret` 취급 + 사내 처리 폴백

**하지 않는 일**
- 등급을 스스로 판정하지 않는다 → `Classifier`에 위임
- 페이로드를 스스로 만들지 않는다 → `Extractor` / `Pseudonymizer`에 위임
- 통과 여부를 모델에게 묻지 않는다 → `Validator`(순수 코드)가 정한다

**인터페이스**: `classify`, `plan_calls`, `to_payload`, `validate`, `preview`, `ask_agent`, `rehydrate`

---

## 2. Classifier — 등급 판정

| | |
|---|---|
| **파일** | `src/mesh/classifier.py` |
| **한 줄** | 텍스트와 경로를 받아 `open`/`internal`/`secret`을 정한다 |
| **소유** | A |
| **요구사항** | FR-01, FR-02 |

**책임**
- 규칙 기반 판정: 경로 패턴 · 문서 헤더 등급 표기 · 고객사명 사전 · 계약번호/제품코드 정규식
- EXAONE 보조 판정 (문맥적 기밀성)
- **`max(rule_tier, exaone_tier)`** 채택 — 둘 중 하나만 기밀이라 해도 기밀
- 규칙이 이미 `secret`이면 **EXAONE을 호출하지 않는다** (왕복 절약, NFR-P-02)

**설계 근거**: 안전성이 모델 판단에만 의존하면 프롬프트 인젝션과 환각에 노출된다. 규칙이 하한선을 만든다.

---

## 3. Extractor — 구조 추출 (슬롯 채우기)

| | |
|---|---|
| **파일** | `src/mesh/extractor.py` |
| **한 줄** | `secret` 등급 원문을 어휘 사전 안의 값만 담은 구조 페이로드로 바꾼다 |
| **소유** | A |
| **요구사항** | FR-03, FR-04 |

**책임**
- task 스키마에 선언된 **슬롯을 순회**하며 EXAONE에게 값 하나씩 고르게 한다
- 응답에서 **화이트리스트 키만 골라 페이로드를 새로 조립**한다
- 모델이 반환한 미등록 키는 **검증 실패가 아니라 조립 단계에서 버린다(drop)**
- 타입 강제: `"false"` → `False`, `"8"` → `8` (실측된 모델 습성)
- `__unknown__` 처리: 슬롯 미상. 필수 슬롯이 `__unknown__`이면 추출 실패로 간주
- `ref` 라벨(`REQ_A`, `COMP_B`)을 **자동 생성**하고 매핑 테이블을 함께 반환

**설계 근거 (실측)**: 모델에게 JSON 전체를 만들게 하면 어휘 사전을 벗어난다 — 첫 시도에서 3개 필드가 이탈했다. 슬롯 채우기로 바꾸면 3회 반복 모두 완전히 in-vocab이었다. 상세: `preflight-findings.md` §1.

> 이 컴포넌트가 이 프로젝트의 심장이다. **"무엇을 지울까"가 아니라 "무엇만 보낼까"** 를 정하는 지점.

---

## 4. Validator — 검증 6단계 (순수 함수)

| | |
|---|---|
| **파일** | `src/mesh/validator.py` |
| **한 줄** | 페이로드가 경계를 넘어도 되는지 기계적으로 판정한다 |
| **소유** | A |
| **요구사항** | FR-07, FR-08 |

**책임 (6단계, 순서 고정)**

| # | 검사 | 실패 시 |
|---|---|---|
| 1 | **스키마** — 모든 키가 정의된 슬롯인가 | 차단 |
| 2 | **어휘** — 모든 문자열 값이 `vocab.json`에 있는가 | 차단 |
| 3 | **범위** — 모든 숫자가 허용 범위인가 | 차단 |
| 4 | **금칙어** — 고객사명 사전 / 계약번호·제품코드 정규식 | 차단 |
| 5 | **원문 대조** — 원본의 5-gram 중 하나라도 페이로드에 있는가 | 차단 |
| 6 | **크기** — 2KB 초과인가 (자유 텍스트 혼입 신호) | 차단 |

**특성**
- **순수 함수.** I/O 없음, 모델 호출 없음, 전역 상태 없음 → PBT 대상 (NFR-T-03)
- U5 브로커 Lambda가 **같은 모듈을 번들해 독립 재검증**한다 (다층 방어, NFR-S-11)
- 5단계가 가장 강력하다: 원문 문장이 한 조각이라도 있으면 기계적으로 잡힌다

---

## 5. Pseudonymizer / Rehydrator — 가역적 가명화

| | |
|---|---|
| **파일** | `src/mesh/pseudonymizer.py`, `src/mesh/rehydrator.py` |
| **한 줄** | `internal` 등급의 식별자만 치환하고, 응답을 실제 이름으로 되돌린다 |
| **소유** | A |
| **요구사항** | FR-05, FR-06, FR-13 |

**Pseudonymizer 책임**
- 고유명사·사내 프로젝트명·경로만 `<SYS_1>`, `<PROJ_1>` 형태로 치환
- **기술 용어는 치환하지 않는다** (`RandomOverSampler`, `SSO`, `claim mapping`)
- 한 질의 안에서 **일관성 보장** (같은 대상 = 같은 번호)
- 매핑 테이블을 반환 (앱 메모리 전용)

**Rehydrator 책임**
- `ref` / placeholder → 실제 이름. **순수 문자열 치환**
- 매핑 테이블은 요청 스코프에만 존재하고 응답 후 폐기 — 파일·DB·클라우드에 쓰지 않는다

**속성 (PBT-02)**: `rehydrate(pseudonymize(x).text, mapping) == x` 임의 입력에 대해 항등

---

## 6. KnowledgeStore — 세션 + 파일

| | |
|---|---|
| **파일** | `src/mesh/store.py` |
| **한 줄** | 세션을 유지하고, 지목된 경로의 파일을 읽는다 |
| **소유** | B |
| **요구사항** | FR-16~22 |

**책임**
- 사람별 세션 로드 (`data/sessions/{entity_id}.json`)
- `verified_qa` 병합 (`data/verified/{entity_id}.json`) — **등급 보존**
- 세션 `open_paths` 중에서 관련 경로 선택 (EXAONE 보조, **파일 본문 미포함** 프롬프트)
- 선택된 파일만 읽어 `Chunk`로 반환
- 신선도 판정: `live` / `stale` / `expired` (FR-19)
- `${MESH_DATA_ROOT}` 치환 + **경로 탈출(`..`) 거부** (NFR-S-05)

**하지 않는 일**
- 벡터 검색·임베딩·청크 인덱스 (`scenarios.md` §0 확정 모델)
- 전역 검색 — 세션에 없는 것은 못 찾는다. 이건 한계이자 "지금 이 사람의 관심사"라는 강력한 사전 필터

---

## 7. AgentClient — Claude 대리인

| | |
|---|---|
| **파일** | `src/mesh/agent.py` |
| **한 줄** | 한 주체를 대리해 답한다. 못 하면 초안을 만들어 넘긴다 |
| **소유** | B |
| **요구사항** | FR-23~28 |

**책임**
- 페르소나 시스템 프롬프트 구성 + 변환된 질문·지식 전달
- 답변 · 신뢰도 · 인용 회수
- 신뢰도 낮으면 에스컬레이션 초안 생성 (질문 요약 + 근거 + 답변 초안)

**설계 규칙**
- **구현은 하나다.** 사람마다 다른 것은 설정뿐 (페르소나 프롬프트 · 지식 범위 · 에스컬레이션 대상). 클래스를 나누지 않는다
- **1인칭으로 사람인 척하지 않는다.** 모든 답변에 "○○의 Agent" 라벨
- 시스템 프롬프트에 못 박는다: *"받은 것은 실제 문서가 아니라 구조 요약입니다. 대상은 참조 기호로 지칭하십시오"* — 그래야 재수화가 성립한다
- **Bedrock을 직접 부르지 않는다.** `Gatekeeper.ask_agent()`에 페이로드를 넘긴다

---

## 8. Orchestrator — 전달과 분기

| | |
|---|---|
| **파일** | `src/mesh/orchestrator.py` |
| **한 줄** | 지목된 에이전트에게 질문을 넘기고, 답을 신뢰도로 분기한다 |
| **소유** | B |
| **요구사항** | FR-29~37 |

**책임**
- 지목된 대상(최대 2)에게 질문 전달 → 병렬 호출 → 답변 수집
- 신뢰도 분기: ≥0.75 & 인용≥1 자동 / 0.45~0.75 미검증 배지 / <0.45 에스컬레이션 / **인용 0개 → 무조건 에스컬레이션**
- 답이 갈리면 `divergent: true`로 병기 (**상충 여부를 자동 판정하지 않는다**)
- 에이전트 목록 구성 (`disclose:` 설정 반영)
- 전체 30초 상한

**하지 않는 일**
- **모델을 부르지 않는다.** 앱 코드일 뿐이다
- 전문성 매칭 · 임베딩 · 브로드캐스트 · 자동 재지목

---

## 9. Console — 웹 UI

| | |
|---|---|
| **파일** | `src/mesh/web/index.html`, `app.js`, `style.css` |
| **한 줄** | 탭 3개. 질문 · 인박스 · 감사 로그 |
| **소유** | C |
| **요구사항** | FR-38~44, NFR-S-04 |

**책임**
- 질문 탭: 에이전트 지목 목록 → 질문 → 답변 (배지·출처·신뢰도)
- **전송 미리보기 모달**: 나갈 JSON 전문 + 검증 결과 + "포함되지 않은 것" + `[전송]`/`[취소]`
- 인박스 탭: 초안 + 3버튼
- 감사 로그 탭: 페이로드 전량 + **원문 검색**
- 빌드 파이프라인 없음. FastAPI가 정적 서빙. 외부 CDN 미사용 (SRI N/A)
- `data-testid` 부여 (자동화 친화)

---

## 10. Inbox / AuditLog — 영속 상태

| | |
|---|---|
| **파일** | `src/mesh/inbox.py`, `src/mesh/audit.py` |
| **소유** | B (Inbox), A (AuditLog) |

**Inbox 책임**: 에스컬레이션 항목 저장, 3버튼 처리(승인/수정후승인/내가아님), 승인 답변을 `verified_qa`로 환류

**AuditLog 책임**
- 경계를 넘은 **모든** 페이로드를 SQLite에 기록 (로컬이 원본)
- 필드: 시각 · 행위자 · 모델 ID · **`trusted_zone_llm_base_url`** · 등급 · 페이로드 전문 · SHA-256 · 크기 · 검증 결과 · 사용자 승인 여부
- 원문 검색 지원
- `mirror()`로 클라우드 미러 (선택, 실패해도 로컬은 유지)
- **원문·토큰·`reasoning*`을 절대 기록하지 않는다** (NFR-S-03)

> 감사 로그에 `trusted_zone_llm_base_url`을 매 질의 기록하는 이유: **원문이 어디로 갔는지가 로그로 증명**돼야 한다. 신뢰 경계가 설정값이라면 그 설정값도 감사 대상이다.

---

## 11. ExaoneClient / SchemaRegistry / Config — 지원 컴포넌트

| 컴포넌트 | 파일 | 책임 |
|---|---|---|
| **ExaoneClient** | `src/mesh/llm/exaone.py` | Friendli OpenAI 호환 호출. **`enable_thinking:false` 고정**, `response_format: json_object`, **응답의 `reasoning*` 키를 파싱 전에 삭제**, 2회 재시도, 목업 모드 |
| **BrokerClient** | `src/mesh/llm/broker.py` | 브로커 API 호출 (`broker` 모드) 또는 Bedrock 직접 호출 (`direct` 모드). `gatekeeper.py`만 import한다 |
| **SchemaRegistry** | `src/mesh/schemas.py` | task 스키마 · 슬롯 정의 · `vocab.json` 로더 · pydantic 모델. **Day 1 종료 시 동결 (3인의 계약)** |
| **Config** | `src/mesh/config.py` | 환경변수 + `config/agents.yaml` 로더. `MESH_DATA_ROOT` 해석. **절대 경로 금지 강제** |

---

## 12. 클라우드 컴포넌트 (U5)

| 컴포넌트 | 책임 |
|---|---|
| **AgentBrokerFunction** (Lambda) | ① `validator`로 **독립 재검증** ② Bedrock Converse 호출 ③ 감사 기록 ④ ref 기반 응답 반환. 실행 역할로 Bedrock 접근 → 노트북에 자격증명 불필요 |
| **AuditMirrorTable** (DynamoDB) | 감사 레코드. 저장 시 암호화 · 삭제 방지 · PITR. **Lambda가 `DeleteItem` 권한을 갖지 않는다** |
| **AgentRegistryTable** (DynamoDB) | 에이전트 공개 프로필 (담당 영역, `disclose` 설정). 다중 노트북 확장 대비 |
| **InboxTable** (DynamoDB) | 에스컬레이션 항목의 사외 표현 (post-gatekeeper). 로컬 인박스가 원본 |
| **BrokerApi** (API Gateway REST) | API Key + Usage Plan + 스로틀 + 액세스/실행 로깅 |

---

## 13. 컴포넌트 수 점검

애플리케이션 파이썬 모듈 **17개** (설계 문서 §6.1의 "파일 8개"보다 많다).

**늘어난 이유와 정당성**

| 추가 모듈 | 정당성 |
|---|---|
| `classifier.py`, `extractor.py`, `validator.py`, `pseudonymizer.py`, `rehydrator.py` | 설계 문서에서 `gatekeeper.py` 한 파일이던 것을 5개로 쪼갰다. **보안 로직을 격리하라는 SECURITY-11 요건**이고, `validator.py`는 순수 함수여야 PBT와 Lambda 번들 공유가 가능하다 |
| `inbox.py`, `audit.py` | 영속 상태를 다루므로 분리 |
| `llm/broker.py` | `direct`/`broker` 전환 (FR-49) |
| `config.py` | 이식성 요건 (NFR-PO-01) |

**대신 유지한 제약**: 각 모듈은 단일 책임이고, 파일당 300줄을 넘기지 않는 것을 목표로 한다. `gatekeeper.py`가 조율만 하고 로직을 갖지 않으므로 전체 구조는 여전히 머릿속에 담긴다.

# Services

로컬 FastAPI 애플리케이션 1개 + 클라우드 서버리스 API 1개. 마이크로서비스가 아니다.

---

## 1. Local Edge Service (`src/mesh/main.py`)

| | |
|---|---|
| **런타임** | FastAPI 단일 프로세스, Python 3.12 |
| **바인딩** | **`127.0.0.1:8080` 고정** (`0.0.0.0` 바인딩 금지) |
| **구역** | 신뢰 구역 안 |
| **책임** | 오케스트레이션 + 정적 서빙 + 상태 영속 |

**왜 localhost 고정인가**: 이 서비스는 원문 파일을 읽고 재수화된 답변(실제 이름 포함)을 반환한다. 즉 **신뢰 구역 안의 평문을 다루는 유일한 HTTP 표면**이다. 인증이 없는 MVP에서 네트워크에 노출하면 권한 우회 도구가 된다. 다중 노트북으로 확장할 때는 Hub 경유 릴레이를 쓰고 이 서비스는 계속 localhost에 둔다.

### 엔드포인트

| Method | Path | 책임 | 요구사항 |
|---|---|---|---|
| `GET` | `/` | 웹 UI (정적) | FR-40 |
| `GET` | `/api/agents` | 지목 목록. `disclose` 반영, `current_focus`는 식별자 제거 요약 | FR-30, FR-31 |
| `POST` | `/api/ask/prepare` | 질문 접수 → 세션 조회 → 파일 읽기 → 등급 판정 → 분해/상향 → 페이로드 조립 → 검증. **미리보기 카드 반환. Agent를 호출하지 않는다** | FR-01~12, FR-41 |
| `POST` | `/api/ask/send` | 사용자 승인 후 Agent 호출 → 재수화 → 신뢰도 분기 → 병기 | FR-13, FR-32~36 |
| `GET` | `/api/inbox` | 담당자별 에스컬레이션 목록 + 초안 | FR-27, FR-38 |
| `POST` | `/api/inbox/{id}/resolve` | `approve` / `approve_with_edit` / `not_me` | FR-38, FR-39 |
| `GET` | `/api/audit?q=` | 감사 로그 조회 + **원문 검색** | FR-15, FR-42 |
| `GET` | `/api/health` | preflight 상태 (EXAONE·브로커·모드·`trusted_zone_llm_base_url`) | NFR-PO-05 |

### `prepare` / `send` 를 2단계로 나눈 이유

FR-09(사람 확인)를 **API 수준에서 강제**하기 위해서다. 단일 엔드포인트로 만들면 "미리보기 후 승인"이 UI의 매너에 의존하지만, 두 단계로 나누면 `send`가 `prepare`가 발급한 `envelope_id` + `approved_by` 없이는 동작하지 않는다. 즉 **승인 없는 전송이 구조적으로 불가능**하다.

```
POST /api/ask/prepare  -> { envelope_id, preview_card, validation }
                          (검증 실패 시 disposition=BLOCKED + 폴백 답변 동봉)
POST /api/ask/send     <- { envelope_id, approved_by }
                          envelope 는 서버 메모리에 TTL 5분으로 보관
```

### 오케스트레이션 흐름 (`/api/ask/prepare`)

```
1. 입력 검증 (pydantic): question <= 4000자, targets <= 2
2. 대상별 병렬:
   a. Store.load_session(entity_id)          [신뢰 구역]
   b. Store.freshness()                       -> live/stale/expired
   c. Gatekeeper.classify(question)           -> 질문 등급  (관문 ①)
   d. Store.select_paths(session, question)   [EXAONE, 본문 미포함]
   e. Store.read(paths)                       -> Chunk[] (원문)
   f. Gatekeeper.classify(chunk) for each     -> 파일 등급
   g. Gatekeeper.plan_calls()                 -> 분해 or 상향, AgentCall[]
   h. Gatekeeper.to_payload(call)             -> PayloadEnvelope  (관문 ②)
   i. Gatekeeper.validate(env, originals)     -> 6단계
   j. 실패 -> Gatekeeper.answer_in_zone()     -> 폴백, 감사 레코드 없음
3. Gatekeeper.preview()                       -> PreviewCard
4. envelope 를 메모리 캐시에 TTL 5분으로 저장 (매핑 테이블 포함)
```

### 오케스트레이션 흐름 (`/api/ask/send`)

```
1. envelope_id 조회. 없으면 410 Gone
2. 전제조건 확인: validation.passed && approved_by
3. AuditLog.record()                          [경계를 넘기 직전]
4. 병렬 Gatekeeper.ask_agent()                *** 경계 통과 ***
5. Gatekeeper.rehydrate()                     [신뢰 구역, 매핑 적용]
6. 매핑 테이블 폐기 + envelope 캐시 제거
7. Orchestrator.branch() -> AUTO / UNVERIFIED / ESCALATE
8. ESCALATE 면 AgentClient.draft_escalation() -> Inbox.add()
9. Orchestrator.merge() -> divergent 병기
10. AuditLog.mirror() (비동기, 실패 무시)
```

**5번과 6번의 순서가 중요하다.** 재수화가 끝나기 전에 매핑을 폐기하면 답변이 기호로 남고, 폐기를 잊으면 매핑이 메모리에 누적된다. `try/finally`로 강제한다 (NFR-S-15 자원 정리).

---

## 2. Cloud Broker Service (`infra/`, AWS CDK)

| | |
|---|---|
| **런타임** | Lambda (Python 3.12) + API Gateway REST |
| **리전** | `us-east-1` (계정 정책 제약) |
| **구역** | 신뢰 구역 **밖** |
| **책임** | 독립 검증 + Bedrock 호출 + 감사 미러 |

### 엔드포인트

| Method | Path | 책임 | 인증 |
|---|---|---|---|
| `POST` | `/agent/invoke` | 재검증 → Bedrock Converse → 감사 기록 → 응답 | API Key |
| `POST` | `/audit/mirror` | 감사 레코드 미러링 | API Key |
| `GET` | `/agents` | 에이전트 레지스트리 (다중 노트북 확장용) | API Key |

### 이 서비스가 존재하는 이유 (4가지)

| # | 근거 |
|---|---|
| 1 | **자격증명 만료 해소.** 노트북의 AWS 자격증명은 STS 임시 토큰이라 시연 중 만료된다. Lambda 실행 역할은 만료되지 않는다 |
| 2 | **이식성.** 컴퓨터를 바꿔도 API 키 하나만 옮기면 된다. AWS 자격증명 재발급이 필요 없다 |
| 3 | **독립 검증 = 진짜 다층 방어.** 노트북의 검증기에 버그가 있어도 클라우드의 검증기가 다시 막는다. 두 검증기가 같은 코드를 공유하지만 **다른 프로세스·다른 신뢰 도메인**에서 돈다 |
| 4 | **감사 로그의 증거력.** 노트북 앱이 자기 감사 로그를 지울 수 있으면 로그의 의미가 약하다. DynamoDB에 삭제 방지 + PITR을 걸고 **Lambda에 `DeleteItem` 권한을 주지 않으면**, 제3자가 검증할 수 있는 기록이 된다 |

### 이 서비스가 보안 모델을 약화시키지 않는 이유

경계를 넘는 지점이 하나 늘어난 것처럼 보이지만 아니다.

- 브로커에 가는 페이로드는 **이미 Claude에게 갈 것과 동일한 것**이다. 검증을 통과했고 사용자가 승인했다
- 즉 브로커와 Claude는 **같은 편(경계 밖)** 이고, 새로운 유출 등급이 생기지 않는다
- 매핑 테이블은 브로커에 가지 않는다. 재수화는 노트북에서만 일어난다
- 원문은 어느 경우에도 브로커에 가지 않는다

**단, 다음은 지킨다**: 브로커 응답을 신뢰하지 않는다. 응답의 `ref`가 매핑 테이블에 없으면 치환하지 않고 기호를 그대로 남긴다 (프롬프트 인젝션으로 임의 문자열을 치환시키는 것을 막는다).

---

## 3. 서비스 간 계약

### Local → Broker

```json
POST /agent/invoke
x-api-key: <from Secrets Manager, 로컬 .env>
{
  "envelope_id": "env_01J...",
  "task_schema_id": "constraint_conflict_check",
  "tier": "secret",
  "payload": { "task": "...", "domain": "...", "entities": [...] },
  "system_prompt": "당신은 김철수 책임의 Agent입니다. ...",
  "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
  "payload_sha256": "9f2a..."
}
```

**응답**
```json
{
  "envelope_id": "env_01J...",
  "answer": { "conflict": true, "reason": "REQ_A는 ...", "mitigations": ["..."] },
  "confidence": 0.83,
  "citations": ["REQ_A", "COMP_B"],
  "usage": { "inputTokens": 412, "outputTokens": 288 },
  "revalidated": true
}
```

`revalidated: true`가 없으면 로컬이 응답을 거부한다. 브로커가 검증을 건너뛴 경로로 배포됐다는 신호이므로 fail closed한다.

### 검증 실패 응답 (400)

```json
{ "error": "payload_rejected", "stage": "vocab",
  "detail": "value not in vocabulary", "envelope_id": "env_01J..." }
```

필드 이름·값을 응답에 담지 않는다. 브로커 응답 자체가 원문 추측 채널이 되지 않게 한다.

---

## 4. 상태 소유

| 상태 | 원본 위치 | 사본 | 이유 |
|---|---|---|---|
| 세션 | `data/sessions/*.json` (로컬) | 없음 | 원문 요약을 담을 수 있다 |
| 코퍼스 원문 | `data/corpus/**` (로컬) | 없음 | **절대 경계를 넘지 않는다** |
| 매핑 테이블 | 앱 메모리 (요청 스코프) | 없음 | 응답 후 폐기 |
| 감사 로그 | SQLite (로컬) | DynamoDB 미러 | 로컬이 원본, 클라우드가 증거 |
| 인박스 | SQLite (로컬) | DynamoDB (post-gatekeeper 표현) | 로컬이 원본 |
| 승인된 QA | `data/verified/*.json` | 없음 | 등급 보존, 원문 담을 수 있다 |
| 에이전트 레지스트리 | `config/agents.yaml` | DynamoDB | 공개 프로필만 |

**규칙**: 클라우드에 있는 것은 전부 **post-gatekeeper 표현**이거나 **공개 프로필**이다. 원문·매핑·세션 원문은 클라우드에 없다.

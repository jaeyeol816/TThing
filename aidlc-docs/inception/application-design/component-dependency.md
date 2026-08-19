# Component Dependency

---

## 1. 의존 행렬

행 = 의존하는 쪽, 열 = 의존받는 쪽. `->` 는 직접 호출.

| | GK | CLS | EXT | VAL | PSD | RHD | STO | AGT | ORC | INB | AUD | EXA | BRK | SCH | CFG |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| **Gatekeeper** (GK) | — | -> | -> | -> | -> | -> | | | | | -> | -> | **->** | -> | -> |
| **Classifier** (CLS) | | — | | | | | | | | | | -> | | -> | -> |
| **Extractor** (EXT) | | | — | | | | | | | | | -> | | -> | |
| **Validator** (VAL) | | | | — | | | | | | | | | | -> | |
| **Pseudonymizer** (PSD) | | | | | — | | | | | | | -> | | -> | |
| **Rehydrator** (RHD) | | | | | | — | | | | | | | | | |
| **KnowledgeStore** (STO) | | | | | | | — | | | | | -> | | -> | -> |
| **AgentClient** (AGT) | -> | | | | | | | — | | | | | | -> | -> |
| **Orchestrator** (ORC) | -> | | | | | | -> | -> | — | -> | | | | -> | -> |
| **Inbox** (INB) | | | | | | | -> | | | — | | | | -> | -> |
| **AuditLog** (AUD) | | | | | | | | | | | — | | **->** | -> | -> |
| **main.py** (FastAPI) | | | | | | | | | -> | -> | -> | | | | -> |

**굵은 `->`는 신뢰 경계를 넘는 호출이다. 정확히 2개.**

| 구분 | 순수 함수 (I/O·모델 호출 없음) |
|---|---|
| ✅ | `Validator`, `Rehydrator`, `Classifier.rule_tier`, `Extractor.assemble`, `Extractor.coerce`, `Orchestrator.branch`, `Orchestrator.merge` |
| ❌ | 그 외 |

순수 함수 목록이 곧 **PBT 대상 목록**이다 (NFR-T-02~04).

---

## 2. 통신 패턴

| 관계 | 패턴 | 근거 |
|---|---|---|
| main.py → Orchestrator | 직접 호출 (async) | 단일 프로세스 |
| Orchestrator → 2개 대상 | `asyncio.gather` 병렬 | FR-32, 30초 상한 |
| Gatekeeper → EXAONE | HTTPS, 2회 재시도, 타임아웃 10s | FR-46 |
| Gatekeeper → BrokerClient | HTTPS + API Key, 타임아웃 25s | FR-49 |
| BrokerClient → Bedrock (direct 모드) | boto3 `Converse`, 타임아웃 25s | FR-49 |
| AuditLog → DynamoDB | 비동기 fire-and-forget, 실패 무시 | 로컬이 원본 |
| Broker Lambda → Bedrock | 실행 역할, 동기 | NFR-S-06 |
| Agent ↔ Agent | **없음** | 순환·토큰 폭발 방지 (FR-36) |

---

## 3. 강제되는 아키텍처 규칙

### 규칙 1 — 단일 통로 (SECURITY-11)

```
경계 밖 클라이언트(BrokerClient, boto3 bedrock)를 import 할 수 있는 모듈:
  - src/mesh/gatekeeper.py
  - src/mesh/audit.py       (미러링 전용)
그 외 전부 금지.
```

**강제 방법**: `tests/unit/test_import_boundary.py`가 `ast`로 전 모듈의 import 문을 파싱해 위반을 실패시킨다. 코드 리뷰 매너에 의존하지 않는다.

### 규칙 2 — 원문 전파 경계

```
Chunk.text (원문) 를 인자로 받을 수 있는 모듈:
  - classifier.py     (등급 판정)
  - extractor.py      (슬롯 채우기)
  - pseudonymizer.py  (가명화)
  - validator.py      (5-gram 대조 — 원문을 보지만 밖으로 내보내지 않는다)
  - store.py          (읽기)
그 외는 PayloadEnvelope 만 받는다.
```

**강제 방법**: 타입 시스템. `AgentClient`·`Orchestrator`·`BrokerClient`의 시그니처에 `Chunk`가 등장하지 않는다. `PayloadEnvelope`에 원문 필드가 없다.

### 규칙 3 — 매핑 테이블 비영속

```
Mapping 을 인자로 받을 수 있는 모듈: rehydrator.py, gatekeeper.py
Mapping 을 직렬화·저장하는 코드: 없음
```

**강제 방법**: `Mapping`을 `@dataclass(frozen=True)`로 정의하고 `__getstate__`에서 `TypeError`를 던져 pickle·json 직렬화를 차단한다. 테스트로 확인한다.

### 규칙 4 — 등급 단일값

```
AgentCall.tier 는 Tier 하나. list[Tier] 가 아니다.
PayloadEnvelope.tier 도 하나.
```

**강제 방법**: pydantic 모델의 타입이 `Tier`이므로 등급이 섞인 페이로드는 생성 시점에 실패한다. `scenarios.md` §2 ②의 "한 번의 Agent 호출에는 한 등급만"이 타입으로 표현된다.

### 규칙 5 — 승인 없는 전송 불가

```
Gatekeeper.ask_agent(env, persona, approved_by)
  assert env.validation.passed
  assert approved_by
```

**강제 방법**: API를 `prepare`/`send` 2단계로 분리. `send`는 `envelope_id` + `approved_by` 없이 호출할 수 없다.

---

## 4. 데이터 흐름 — 시나리오 1 (기밀 경로)

```
[신뢰 구역 안]

  질문자 브라우저
    | POST /api/ask/prepare  { question, targets:[person:kim] }
    v
  main.py --> Orchestrator.ask()
    |
    +-> Store.load_session("person:kim")
    |     data/sessions/person_kim.json + data/verified/person_kim.json
    |
    +-> Gatekeeper.classify(question)  -----> Classifier.rule_tier  = internal
    |                                  \---> Classifier.exaone_tier = internal   [EXAONE]
    |                                        => 질문 등급 internal
    |
    +-> Store.select_paths(session, question)                                    [EXAONE]
    |     세션 요약만 전달. 파일 본문 미포함
    |     => ["customer-H/req-spec-2026H.md", "sdk/docs/auth-design.md"]
    |
    +-> Store.read(paths) -> Chunk[]   *** 원문이 메모리에 로드되는 지점 ***
    |
    +-> Gatekeeper.classify(chunk) for each
    |     req-spec-2026H.md : rule=secret (경로 customer-*/**) => secret
    |     auth-design.md    : rule=internal                    => internal
    |
    +-> Gatekeeper.plan_calls()
    |     분해 가능? 두 파일이 하나의 충돌 판단에 함께 필요 => 분해 불가
    |     => tier = max(internal, secret, internal) = SECRET   [상향]
    |     => AgentCall(tier=SECRET, schema=constraint_conflict_check)
    |
    +-> Gatekeeper.to_payload(call) -> Extractor.extract()
    |     schema.slots 순회 -> EXAONE 슬롯 채우기                                 [EXAONE]
    |     *** 원문이 신뢰 구역 안에서 마지막으로 읽히는 지점 ***
    |     assemble(): 화이트리스트 키만 골라 재조립
    |     => PayloadEnvelope(payload=..., mapping={REQ_A:..., COMP_B:...})
    |
    +-> Gatekeeper.validate(env, originals) -> Validator 6단계
    |     schema OK / vocab OK / range OK / banned 0 / 5-gram 0 / 1.1KB OK
    |
    +-> Gatekeeper.preview() -> PreviewCard
    |
    v
  응답: { envelope_id, preview_card }    (Agent 호출 없음)
    |
  [사용자가 모달에서 JSON 전문을 읽고 전송 클릭]
    |
    | POST /api/ask/send  { envelope_id, approved_by:"person:choi" }
    v
  AuditLog.record(...)   시각·행위자·모델·base_url·등급·페이로드·sha256·검증·승인
    |
    | Gatekeeper.ask_agent()
================ 신뢰 경계 ================
    v
  [신뢰 구역 밖]
  Broker Lambda
    +-> validator 재검증 (번들된 vocab.json)
    +-> Bedrock Converse (us.anthropic.claude-sonnet-4-5-...)
    +-> DynamoDB PutItem (감사)
    |
    v  ref 기반 응답 { conflict, reason, mitigations, confidence, citations }
================ 신뢰 경계 ================
  [신뢰 구역 안]
    |
    +-> Gatekeeper.rehydrate(resp, mapping)
    |     REQ_A -> "고객사 H REQ-4412",  COMP_B -> "SDK v3.2"
    |     finally: mapping 폐기
    |
    +-> Orchestrator.branch(confidence=0.83, citations=2) => AUTO
    |
    v
  질문자 브라우저: 실제 이름으로 된 자연스러운 답변
                  근거: display_title + section + tier (internal_path 없음)
```

---

## 5. 데이터 흐름 — 시나리오 3 후속 (검증 실패 폴백)

```
질문: "그 3천 TPS 테스트, 실제 수치가 어떻게 나왔나요?"

  Gatekeeper.classify -> secret (고객 환경 벤치마크, 경로 customer-*/**)
  Extractor.extract()
    EXAONE 이 p99_latency_ms / throughput_tps 를 만들려 한다
    assemble(): 두 키가 schema.slots 에 없다 -> *** drop ***
    필수 슬롯이 하나도 채워지지 않음 -> ExtractionFailed
  Gatekeeper.answer_in_zone()                                  [EXAONE]
    신뢰 구역 안에서 직접 답변 생성
  AuditLog: *** 레코드 없음 ***   (경계를 넘은 것이 없으므로)

  화면: "[기밀 · 사내망 밖으로 나간 것 없음]
         정확한 수치는 이 화면에서 제공할 수 없습니다. ..."
```

> 성능 수치 필드를 어휘 사전에 **의도적으로 넣지 않았다.** 사전에 없는 것은 실수로도 나갈 수 없다 — 화이트리스트 방식의 실질적 효과이며, 이것이 데모 3막의 결정적 장면이다.

---

## 6. 의존성 방향 검증

순환 의존이 없어야 한다. 계층 순서:

```
Layer 0 (기반)    config.py, schemas.py
Layer 1 (순수)    validator.py, rehydrator.py
Layer 2 (모델)    llm/exaone.py, llm/broker.py
Layer 3 (변환)    classifier.py, extractor.py, pseudonymizer.py
Layer 4 (경계)    gatekeeper.py, audit.py
Layer 5 (도메인)  store.py, agent.py, inbox.py
Layer 6 (조율)    orchestrator.py
Layer 7 (전달)    main.py
```

의존은 항상 **위 → 아래** 방향이다. 예외 1건:
- `agent.py`(L5) → `gatekeeper.py`(L4) — 정상 (아래 방향)
- `store.py`(L5) → `llm/exaone.py`(L2) — 정상

`gatekeeper.py`가 `store.py`를 import하지 않는 것이 중요하다. 게이트키퍼는 **어디서 온 지식인지 몰라도** 되고, 몰라야 재사용 가능하다. 파일을 읽는 것은 Orchestrator의 일이다.

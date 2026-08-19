# U1 — Logical Components

NFR 달성에 필요한 논리적 구성 요소. 인프라 서비스 매핑은 U5의 `infrastructure-design.md`.

---

## 1. 구성 요소 목록

| 구성 요소 | 구현 | NFR | 위치 |
|---|---|---|---|
| **Tier Ladder** | `Tier` StrEnum + `__lt__` | FR-11 등급 상향 | `schemas.py` |
| **Slot Whitelist** | `TaskSchema.slot_names` + `assemble()` | FR-04 | `schemas.py`, `extractor.py` |
| **Vocabulary Registry** | `vocab.json` + `Vocabulary` 로더 | FR-03, FR-07 | `schemas.py`, `data/vocab.json` |
| **Six-Stage Validator** | 순수 함수 6개 | FR-07 | `validator.py` |
| **N-gram Index** | 요청 스코프 `set[str]` | BR-V-05 | `validator.py` |
| **Ephemeral Mapping Cache** | `dict[envelope_id, (envelope, Mapping)]` + TTL | BR-G-06, BR-G-09 | `gatekeeper.py` |
| **Audit Store** | SQLite `audit` 테이블 | FR-15, NFR-S-13 | `audit.py` |
| **Local Query Store** | SQLite `local_queries` 테이블 | BR-A-03 | `audit.py` |
| **Retry Wrapper** | 2회 재시도 + 지수 백오프 | FR-46 | `llm/exaone.py` |
| **Response Sanitizer** | `reasoning*` 삭제 | FR-14 | `llm/exaone.py` |
| **Structured Logger** | JSON 로거 + 금지 필드 필터 | NFR-S-03 | `config.py` |
| **Path Guard** | `safe_resolve()` | NFR-S-05 | `config.py` |
| **Transport Switch** | `broker`/`direct`/`mock` | FR-49 | `llm/broker.py` |
| **Mock Fixture Player** | `data/fixtures/` 재생 | FR-48 | `llm/*.py` |
| **Preflight Checker** | 환경 진단 스크립트 | NFR-PO-03 | `scripts/preflight.py` |
| **Import Boundary Test** | `ast` 기반 검사 | NFR-S-11 | `tests/unit/` |
| **Hypothesis Generators** | 도메인 생성기 | PBT-07 | `tests/generators.py` |

---

## 2. Ephemeral Mapping Cache

```python
@dataclass
class _CacheEntry:
    envelope: PayloadEnvelope
    mapping: Mapping
    originals: tuple[str, ...]     # 5-gram 재검증용
    created_at: float

class EnvelopeCache:
    TTL_SECONDS = 300

    def put(self, env, mapping, originals) -> None: ...
    def take(self, envelope_id) -> _CacheEntry | None:
        """조회 후 즉시 제거 (일회용). 없으면 None -> 410 Gone."""
    def sweep(self) -> int:
        """TTL 만료 항목 제거. 매 요청 시작 시 호출."""
```

**설계 판단 3가지**

1. **`take()`가 조회와 제거를 함께 한다.** 같은 `envelope_id`로 두 번 전송하는 것을 막는다 (재생 공격 방지 + 중복 과금 방지).
2. **인메모리다.** Redis를 쓰지 않는다 — 매핑을 프로세스 밖으로 내보내지 않는 것이 BR-G-09이고, 단일 프로세스라 공유가 필요 없다.
3. **TTL 5분.** 사용자가 미리보기를 보고 승인할 시간. 자리를 비우면 자동 소멸해 매핑이 메모리에 누적되지 않는다.

**용량**: 동시 사용자 1~5명 × 진행 중 질의 1~2개 = 항목 10개 이내. 메모리 무시 가능.

---

## 3. N-gram Index

```python
def ngram_set(text: str, n: int = 5) -> frozenset[str]:
    toks = re.sub(r"\s+", " ", text).strip().lower().split()
    return frozenset(" ".join(toks[i:i+n]) for i in range(max(0, len(toks)-n+1)))
```

**범위**: 이 호출에 동원된 원문만 (전체 코퍼스가 아니다).
전체 코퍼스 대조는 U6의 `sweep_for_leaks()`가 `make eval`에서 수행한다.

**비용**: 파일 3개 × 2000 토큰 → 6000개 5-gram. `frozenset` 교집합이 밀리초.

**한국어 처리**: 형태소 분리 없이 공백 토큰 기준. 5-gram이면 우연 일치가 거의 없다.
단, 한국어는 공백이 적어 5-gram이 긴 구간을 덮는다 → **탐지가 다소 느슨하다.** 보완책으로 `INTERNAL` 등급에는 3-gram도 추가 검사한다 (설정 `NGRAM_SIZE_INTERNAL=3`).

**한계 (문서화)**: 모델이 원문을 **의역**해서 슬롯값에 넣으면 n-gram으로 잡히지 않는다. 이건 어휘 사전(②겹)이 막는 영역이고, n-gram은 축자 인용만 잡는다. 두 겹이 함께 필요한 이유.

---

## 4. Structured Logger + 금지 필드 필터

```python
FORBIDDEN_LOG_KEYS = frozenset({
    "text", "chunk_text", "raw_document", "mapping", "table",
    "reasoning", "reasoning_content",
    "FRIENDLI_TOKEN", "BROKER_API_KEY",
    "aws_secret_access_key", "aws_session_token", "authorization", "x-api-key",
})

class RedactingFilter(logging.Filter):
    def filter(self, record):
        # record.__dict__ 를 재귀 순회해 금지 키를 "<redacted>" 로 치환
        ...
        return True
```

**설계 판단**: 개발자가 실수로 `logger.info("chunk: %s", chunk)`를 써도 원문이 로그에 남지 않게 한다. 규율이 아니라 필터로 막는다.

**로그 형식** (JSON, NFR-S-03)
```json
{"at":"2026-08-19T14:33:41Z","level":"INFO","correlation_id":"req_01J...",
 "component":"gatekeeper","message":"payload validated","tier":"secret",
 "validation":"6/6","size_bytes":1124}
```

`correlation_id`는 요청 시작 시 생성해 `contextvars`로 전파한다.

---

## 5. Retry Wrapper + Response Sanitizer

```python
async def complete_json(self, system, user, max_tokens=800) -> dict:
    last_err = None
    for attempt in range(3):                      # 1회 + 재시도 2회
        try:
            raw = await self._post(system, user, max_tokens, attempt)
            for k in STRIP_KEYS:                  # reasoning, reasoning_content
                raw.get("choices", [{}])[0].get("message", {}).pop(k, None)
            return json.loads(content_of(raw))
        except (json.JSONDecodeError, KeyError) as e:
            last_err = e
            user = user + "\n\nOutput valid JSON only. No prose."
            await asyncio.sleep(0.2 * (attempt + 1))
        except httpx.TimeoutException as e:
            last_err = e
            break                                  # 타임아웃은 재시도하지 않는다
    raise ExaoneUnavailable(last_err)
```

**설계 판단 2가지**

1. **타임아웃은 재시도하지 않는다.** 10초 타임아웃 × 3회 = 30초로 전체 예산을 다 먹는다. 파싱 실패만 재시도한다.
2. **`reasoning*` 삭제를 파싱보다 먼저 한다.** 파싱 중 예외가 나면 예외 메시지에 원문이 실릴 수 있다.

---

## 6. Transport Switch

```python
class BrokerClient:
    def __init__(self, cfg: Config):
        self._mode = cfg.agent_transport         # broker | direct | mock

    async def invoke(self, env, system_prompt, model_id) -> AgentResponse:
        match self._mode:
            case Transport.BROKER:
                r = await self._http_broker(env, system_prompt, model_id)
                if not r.revalidated:
                    raise BrokerError("broker did not revalidate")   # fail closed
                return r
            case Transport.DIRECT:
                r = await self._bedrock_converse(env, system_prompt, model_id)
                return r.model_copy(update={"revalidated": True})    # 로컬 검증이 유일
            case Transport.MOCK:
                return self._fixture(env)
```

`direct` 모드에서 `revalidated=True`로 설정하는 것은 **의도적 예외**다. 브로커가 없으므로 로컬 검증이 유일한 검증이고, 그 사실을 감사 로그의 `transport=direct`로 남긴다. 데모에서 `broker` 모드를 쓰는 것이 방어가 한 겹 더 두껍다는 점을 설명할 수 있다.

---

## 7. Mock Fixture Player

```
data/fixtures/
  exaone/
    classify_<sha1(text)[:12]>.json
    extract_<sha1(text+schema_id)[:12]>.json
    select_paths_<sha1>.json
  agent/
    <schema_id>_<sha1(payload)[:12]>.json
```

**녹화**: `MESH_RECORD_FIXTURES=1`로 live 모드 실행 시 응답을 저장한다.
**재생**: 키가 없으면 **명시적으로 실패**한다 (조용히 기본값을 반환하지 않는다). 데모 리허설에서 누락을 발견하게 만드는 장치.

**Day 4 작업**: 3막 전체를 live 모드로 한 번 돌려 픽스처를 녹화한 뒤 커밋한다.

---

## 8. Audit Store — 무결성

| 요건 | 로컬 (SQLite) | 클라우드 미러 (U5) |
|---|---|---|
| 추가 전용 | 앱 코드에 `DELETE`/`UPDATE` 문이 없다 (grep으로 확인) | Lambda 역할에 `DeleteItem` 부여 안 함 |
| 변조 방지 | 파일 권한 `0600` | 삭제 방지 + PITR |
| 보존 | 무제한 (로컬 파일) | 90일 (NFR-S-14) |
| 무결성 | `payload_sha256` | 동일 |

**로컬 감사 로그는 사용자가 파일을 지울 수 있다.** 이건 근본적 한계이고, 그래서 클라우드 미러가 필요하다. 이 점을 문서에 명시한다.

**`audit`/`local_queries` 분리 (BR-A-03)**: 감사 로그 탭에는 `audit`만 보인다. "레코드가 없다"가 명확한 증거가 되게 하려는 것이며, 신뢰 구역 내 처리 이력은 별도 테이블에 있어 디버깅도 가능하다.

---

## 9. Preflight Checker

**확인 항목**

| # | 확인 | 실패 시 |
|---|---|---|
| 1 | Python 버전 3.12 | FAIL |
| 2 | `MESH_DATA_ROOT` 상대 경로 + 존재 | FAIL |
| 3 | `.gitignore`가 자격증명 3종 커버 | **FAIL (blocking)** |
| 4 | `MESH_BIND_HOST` == localhost | WARN |
| 5 | EXAONE 왕복 (1회 호출, 지연 측정) | FAIL |
| 6 | `TRUSTED_ZONE_LLM_BASE_URL`이 공개 SaaS인가 | **WARN (경계 시뮬레이션 고지)** |
| 7 | AWS 자격증명 유효 (`sts.get_caller_identity`) | FAIL (direct 모드) |
| 8 | 자격증명이 임시(STS)인가 | WARN (만료 위험) |
| 9 | `AGENT_MODEL_ID` 실제 호출 가능 | FAIL |
| 10 | 리전 == `us-east-1` | WARN |
| 11 | CDK 부트스트랩 여부 | WARN (broker 모드면 FAIL) |
| 12 | `vocab.json`·`labels.json`·`agents.yaml` 로드 | FAIL |
| 13 | 목업 픽스처 존재 여부 | INFO |

**출력은 사람이 읽을 진단이다.** `[OK]/[WARN]/[FAIL]` + 조치 방법. 다른 컴퓨터로 옮겼을 때 가장 먼저 실행할 스크립트다 (NFR-PO-03).

#6이 중요하다. **경계가 시뮬레이션이라는 사실을 도구가 매번 알려주게** 만든다. 그러면 팀이 잊지 않고, 데모에서 먼저 밝힐 수 있다.

---

## 10. Hypothesis Generators (PBT-07)

```python
# tests/generators.py
def tiers() -> SearchStrategy[Tier]
def slot_defs() -> SearchStrategy[SlotDef]          # enum/int/bool 유효 조합
def task_schemas() -> SearchStrategy[TaskSchema]    # 슬롯 1~8개
def korean_technical_text() -> SearchStrategy[str]  # 한글 + 영문 기술어 + 숫자 + 코드
def chunks() -> SearchStrategy[Chunk]               # tier <-> path 상관관계 유지
def payloads(schema) -> SearchStrategy[dict]        # 스키마 유효 페이로드
def adversarial_raw(schema) -> SearchStrategy[dict] # ⚠️ 핵심 생성기
def identifier_texts() -> SearchStrategy[tuple[str, dict]]  # 가명화 대상 + 정답 매핑
```

**`adversarial_raw()`가 핵심이다.** 다음을 섞어 생성한다.
- 스키마에 없는 키 (`max_session_duration`)
- 어휘 사전 밖의 값 (`"challenge-response"` 하이픈 변형)
- 자유 문자열 (`"8 hours"`, 원문 문장 조각)
- 중첩 구조 (`{"facts": {"nested": {...}}}`)
- 타입 불일치 (`"false"`, `8.0`, `null`)
- `__unknown__` 혼재

이 생성기가 없으면 PB-3/PB-4/PB-5는 형식적인 테스트가 된다. 원시 타입 생성기만으로는 화이트리스트 조립의 실효성을 시험할 수 없다 (PBT-07 요건).

**설정** (PBT-08)
```python
settings.register_profile("ci", max_examples=200, print_blob=True, derandomize=False)
```
`print_blob=True`가 실패 시 `@reproduce_failure` blob을 출력한다. CI 로그에서 그 blob으로 정확히 재현한다.

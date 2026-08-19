# U1 — Domain Entities

`src/mesh/schemas.py`. **Day 1 종료 시 동결. 3인의 계약** (NFR-M-02).
Python 3.12 + pydantic v2.

---

## 1. 열거형

```python
from enum import StrEnum
from functools import total_ordering

@total_ordering
class Tier(StrEnum):
    OPEN = "open"
    INTERNAL = "internal"
    SECRET = "secret"

    @property
    def rank(self) -> int:
        return {"open": 0, "internal": 1, "secret": 2}[self.value]

    def __lt__(self, other: "Tier") -> bool:
        return self.rank < other.rank
```

**`Tier`에 순서를 준 이유**: 등급 상향이 `max(...)`로 표현되게 하려는 것이다.
FR-11("동원된 지식 중 최고 등급이 호출 전체에 걸린다")이 `max(tiers)` 한 줄이 된다.
비교 연산자를 직접 구현하지 않으면 `max()`가 알파벳 순으로 동작해 `secret < open`이 되어 **조용히 유출된다.** 그래서 이 클래스에 대한 단위 테스트가 필수다.

```python
class Freshness(StrEnum):
    LIVE = "live"          # updated_at < 15분
    STALE = "stale"        # < 24시간 — 신뢰도 x0.8 + 시각 명시
    EXPIRED = "expired"    # >= 24시간 — 실시간 주장에서 제외

class Disposition(StrEnum):
    AUTO = "auto"              # 신뢰도 >= 0.75 & 인용 >= 1
    UNVERIFIED = "unverified"  # 0.45 ~ 0.75
    ESCALATE = "escalate"      # < 0.45 또는 인용 0개
    BLOCKED = "blocked"        # 검증 실패 -> 신뢰 구역 내 답변

class Transport(StrEnum):
    BROKER = "broker"
    DIRECT = "direct"
    MOCK = "mock"
```

---

## 2. 지식 (`Chunk`)

```python
class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    entity_id: str                                  # "person:kim"
    text: str                                       # ⚠️ 원문. 경계를 넘지 않는다
    tier: Tier
    display_title: str                               # "고객사 H 요구사항명세서"
    internal_path: str                               # ⚠️ 로컬 전용. API 응답 금지
    section: str | None = None                       # "§3.2"
    as_of: date | None = None
    formality: Literal["official", "informal"] = "official"
    source_kind: Literal["design_doc","minutes","note","script","config","run_log","spec","benchmark"]
```

`display_title` / `internal_path` 분리가 FR-43(인용이 권한을 우회하지 않는다)의 구현 지점이다.
경로 자체가 정보를 준다 — `corpus/customer-H/`는 고객사명을 그대로 노출한다.

---

## 3. task 스키마와 슬롯

```python
class SlotDef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["enum", "int", "bool"]
    allowed: tuple[str, ...] | None = None   # kind=="enum" 일 때 필수
    min: int | None = None
    max: int | None = None
    required: bool = True
    description: str = ""                     # EXAONE 프롬프트에 쓰인다

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "enum" and not self.allowed:
            raise ValueError("enum slot must declare allowed values")
        if self.kind == "int" and (self.min is None or self.max is None):
            raise ValueError("int slot must declare min and max")
        return self
```

**`kind`가 `enum`/`int`/`bool` 세 가지뿐인 것이 핵심이다.**
자유 문자열 슬롯(`kind="str"`)을 **의도적으로 만들지 않았다.** 자유 문자열을 허용하면 원문이 새어나갈 채널이 생긴다. 새 task를 추가할 때 자유 문자열이 필요해 보이면, 그건 그 task가 이 방식에 맞지 않는다는 신호다 (NFR-M-03).

```python
class TaskSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_id: str                            # "constraint_conflict_check"
    domain: str                               # "authentication"
    question_template: str                    # "conflict_and_mitigation"
    answer_format: dict[str, str]             # {"conflict":"bool", ...}
    entity_roles: tuple[str, ...]             # ("external_requirement","our_component")
    slots: tuple[SlotDef, ...]

    @property
    def slot_names(self) -> frozenset[str]:
        return frozenset(s.name for s in self.slots)

    @property
    def required_slots(self) -> frozenset[str]:
        return frozenset(s.name for s in self.slots if s.required)
```

`slot_names`가 **화이트리스트 조립의 기준**이다 (BR-G-03).

```python
class Vocabulary(BaseModel):
    slots: dict[str, SlotDef]
    tasks: tuple[str, ...]
    domains: tuple[str, ...]
    question_templates: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "Vocabulary": ...

class BannedTerms(BaseModel):
    literals: tuple[str, ...]         # 고객사명 사전
    patterns: tuple[str, ...]         # 계약번호 · 제품코드 정규식
    def compiled(self) -> tuple[re.Pattern, ...]: ...

class ClassificationRules(BaseModel):
    secret_path_globs: tuple[str, ...]    # ("customer-*/**", "**/benchmark/**")
    internal_path_globs: tuple[str, ...]
    header_markers: dict[str, Tier]       # {"보안등급: 기밀": Tier.SECRET}
    banned: BannedTerms
```

---

## 4. 등급 판정 결과

```python
class TierDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    tier: Tier                        # max(rule, exaone)
    rule_tier: Tier
    exaone_tier: Tier | None          # None = 호출 생략 또는 실패
    reasons: tuple[str, ...]          # 사람이 읽을 근거
    exaone_skipped: bool = False      # 규칙이 이미 SECRET
    exaone_failed: bool = False       # 실패 -> SECRET 로 간주됨
```

`exaone_failed`를 별도로 남기는 이유: 데모 중 "왜 이게 기밀로 나왔지?"를 즉시 설명할 수 있어야 한다. 그리고 실패가 조용히 지나가면 §3.1 (3)의 "놓친 것을 아무도 모른다" 문제가 재발한다.

---

## 5. 매핑 테이블 — 영속화 불가

```python
@dataclass(frozen=True, slots=True)
class Mapping:
    """ref/placeholder -> 실제 이름. 앱 메모리에만 존재하고 응답 후 폐기.

    직렬화를 타입 수준에서 차단한다 (BR-G-09).
    """
    table: dict[str, str]

    def __getstate__(self):
        raise TypeError("Mapping must never be serialized or persisted")

    def __reduce__(self):
        raise TypeError("Mapping must never be pickled")
```

pydantic `BaseModel`이 아니라 `dataclass`인 것이 의도적이다. pydantic 모델은 `model_dump()`로 쉽게 dict가 되고, 그 dict가 로그·응답에 실려 나갈 수 있다. `Mapping`은 그 경로를 막는다.

**테스트**: `json.dumps`, `pickle.dumps`, `copy.deepcopy` 각각에 대해 `TypeError`를 확인한다.

---

## 6. 페이로드와 검증

```python
class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    stage: Literal["schema","vocab","range","banned","ngram","size"]
    passed: bool
    detail: str = ""
    offending: tuple[str, ...] = ()     # 로컬 진단 전용. 브로커 응답에 넣지 않는다

class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    checks: tuple[CheckResult, ...]
    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)
    @property
    def summary(self) -> str:
        return f"{sum(c.passed for c in self.checks)}/{len(self.checks)}"   # "6/6"

class PayloadEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    envelope_id: str                  # "env_01J..."
    tier: Tier                        # ⚠️ 단일값. 등급 혼합 불가 (BR-G-08)
    task_schema_id: str
    payload: dict                     # 조립된 것. 원문 필드 없음
    representation: Literal["structured", "pseudonymized", "verbatim"]
    validation: ValidationResult | None = None
    payload_sha256: str
    size_bytes: int
```

**`PayloadEnvelope`에 `mapping` 필드가 없는 것이 설계다.** 매핑은 서버 메모리 캐시에서 `envelope_id`로 별도 관리한다. 그래야 `envelope.model_dump()`가 실수로 매핑을 함께 직렬화하지 않는다.

```python
class PreviewCard(BaseModel):
    envelope_id: str
    tier: Tier
    representation: str
    payload_pretty: str                    # 사람이 읽을 들여쓴 JSON 전문
    size_bytes: int
    validation_summary: str                # "6/6"
    checks: tuple[CheckResult, ...]
    excluded_categories: tuple[str, ...]   # ("고객사명","제품명","요구사항 번호","원문 문장")
    verbatim_sentence_count: int           # 항상 0 이어야 한다
```

`verbatim_sentence_count`를 명시적으로 계산해 화면에 띄운다. "원문 문장 0개"가 주장이 아니라 **측정값**이 된다.

---

## 7. Agent 요청/응답

```python
class Persona(BaseModel):
    entity_id: str
    display_name: str                  # "김철수 책임의 Agent"
    expertise: str                      # 본인 작성. 항상 공개
    system_prompt_template: str
    escalation_inbox: str
    daily_limit: int = 50

class AgentCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    call_id: str
    entity_id: str
    tier: Tier                          # ⚠️ 단일값
    task_schema_id: str
    sub_question_id: str | None = None  # 분해된 경우
    chunk_ids: tuple[str, ...]

class AgentResponse(BaseModel):
    answer: dict                         # task 의 answer_format 에 맞춤
    confidence: float = Field(ge=0.0, le=1.0)
    citations: tuple[str, ...]           # ref 라벨
    usage: dict | None = None
    revalidated: bool = False            # 브로커가 재검증했는가 (없으면 fail closed)

class Citation(BaseModel):
    """UI 로 나가는 인용. internal_path 가 없다 (FR-43)."""
    ref: str
    display_title: str
    section: str | None
    tier: Tier
    as_of: date | None
    formality: Literal["official","informal"]

class RehydratedAnswer(BaseModel):
    entity_id: str
    agent_label: str                     # "김철수 책임의 Agent"
    text: str                             # 실제 이름으로 치환됨
    confidence: float
    citations: tuple[Citation, ...]
    tier: Tier
    used_external_agent: bool
    freshness: Freshness | None = None
    session_as_of: datetime | None = None
```

`used_external_agent`가 화면 배지의 근거다. 폴백(`answer_in_zone`)으로 답한 경우 `False`이고, UI에 `[사내망 밖으로 나간 것 없음]`이 뜬다.

---

## 8. 감사 레코드

```python
class AuditRecord(BaseModel):
    record_id: str
    at: datetime
    actor: str                                  # "person:choi"
    target_entity_id: str
    model_id: str                                # "us.anthropic.claude-sonnet-4-5-..."
    transport: Transport
    trusted_zone_llm_base_url: str               # ⚠️ 신뢰 경계의 위치를 기록
    tier: Tier
    representation: str
    payload: dict                                # ⚠️ 전문 보관 (이미 sanitize 됨)
    payload_sha256: str
    size_bytes: int
    validation_summary: str
    approved_by: str
    envelope_id: str
```

**`trusted_zone_llm_base_url`을 기록하는 이유**: 이 프로젝트의 신뢰 경계는 설정값이다. 설정값이 경계를 정한다면 그 설정값도 감사 대상이어야 한다. 데모에서 "원문이 어디로 갔는지"를 로그로 보여줄 수 있다.

**기록하지 않는 것**: 원문(`Chunk.text`), 매핑 테이블, API 키, EXAONE `reasoning*`, Bedrock 요청 헤더.

```python
class LeakReport(BaseModel):
    payloads_scanned: int
    documents_scanned: int
    ngram_size: int
    hits: tuple[dict, ...]                # 비어 있어야 한다
    banned_hits: tuple[dict, ...]
    @property
    def clean(self) -> bool:
        return not self.hits and not self.banned_hits
```

---

## 9. 테스트 가능한 속성 (PBT-01)

| # | 속성 | 범주 | 대상 | 규칙 |
|---|---|---|---|---|
| **PB-1** | `rehydrate(pseudonymize(x).text, mapping) == x` | 왕복 | `pseudonymizer` + `rehydrator` | PBT-02 |
| **PB-2** | `PayloadEnvelope` 직렬화 → 역직렬화 = 항등 | 왕복 | `schemas` | PBT-02 |
| **PB-3** | `set(assemble(raw, schema)) <= schema.slot_names` — **임의의 `raw`에 대해** | 불변식 | `extractor.assemble` | PBT-03 |
| **PB-4** | 조립된 페이로드의 모든 문자열 값 ∈ 어휘 사전 | 불변식 | `extractor` | PBT-03 |
| **PB-5** | 임의 원문의 어떤 5-gram도 조립된 페이로드에 없다 | 불변식 | `extractor` + `validator` | PBT-03 |
| **PB-6** | placeholder 일관성: 같은 대상 → 같은 번호 | 불변식 | `pseudonymizer` | PBT-03 |
| **PB-7** | `max(tiers)`가 항상 최고 등급 — 특히 `Tier` 순서 정의 | 불변식 | `Tier` | PBT-03 |
| **PB-8** | `AgentCall.tier`는 단일값. 등급 혼합 페이로드는 생성 불가 | 불변식 | `plan_calls` | PBT-03 |
| **PB-9** | `Mapping` 직렬화 시도는 항상 `TypeError` | 불변식 | `Mapping` | PBT-03 |
| **PB-10** | `coerce`는 멱등: `coerce(coerce(v)) == coerce(v)` | 멱등 | `extractor.coerce` | PBT-04 (advisory) |

**PB-5가 이 프로젝트에서 가장 중요한 테스트다.** 예제 기반 테스트로는 절대 증명할 수 없다 — 우리가 생각해낸 원문에 대해서만 확인하게 되기 때문이다. Hypothesis로 임의의 한국어·영어·코드 혼합 텍스트를 생성해 검사한다.

**PBT 미적용 (N/A 근거)**
- **PBT-05 오라클**: 참조 구현이 없다. 단 "EXAONE 단독 vs 구조추출+Agent" 품질 비교가 U6 평가 하네스에 별도로 있다
- **PBT-06 상태 기반**: 5일 일정에서 제외. `KnowledgeStore`가 상태를 갖지만 대체로 읽기 전용이고, 쓰기는 `append_verified` 하나뿐이다

**도메인 생성기** (`tests/generators.py`, PBT-07)
```python
tiers()                # Tier 3개
slot_defs()            # enum/int/bool 각각 유효한 정의
task_schemas()         # slots 1~8개
korean_technical_text()# 한국어 + 영문 기술 용어 + 숫자 혼합 (원문 모사)
chunks()               # display_title/internal_path/tier 상관관계 유지
sessions()             # open_paths 가 실제 존재하는 경로
payloads(schema)       # 주어진 스키마에 유효한 페이로드
adversarial_raw()      # 미등록 키 · 자유 문자열 · 중첩 · 원문 조각을 섞은 모델 출력
```

`adversarial_raw()`가 PB-3/PB-4/PB-5를 실제로 검증하게 만드는 핵심 생성기다. 원시 타입 생성기만으로는 이 불변식을 시험할 수 없다.

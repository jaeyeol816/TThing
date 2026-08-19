# U3 — Domain Entities

`src/mesh/agent.py`, `orchestrator.py`, `inbox.py`, `main.py`.
`Tier`·`PayloadEnvelope`·`AgentResponse`·`RehydratedAnswer`·`Citation`은 U1 `schemas.py`에서 온다.

---

## 1. 질문과 하위 질문

```python
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    asker: str = Field(pattern=r"^person:[a-z0-9_]{1,32}$")
    targets: list[str] = Field(min_length=1, max_length=2)   # MAX_TARGETS

    @field_validator("targets")
    def _unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("duplicate targets")
        return v

class SubQuestion(BaseModel):
    """등급 분기를 위한 질문 분해 결과 (BR-G-07)."""
    id: str                                  # "q1"
    kind: str                                # "technique" | "current_state_and_permission" | ...
    text: str
    needs: tuple[str, ...]                    # 필요한 지식 출처
    answer_format: dict[str, str] | None      # 있으면 분해 후보
    tier: Tier
```

`SubQuestion.answer_format`이 `None`이면 분해 불가다 (BR-G-07 조건 1).

---

## 2. 준비 결과 (`prepare` 응답)

```python
class PreparedCall(BaseModel):
    envelope_id: str
    target_entity_id: str
    sub_question_id: str | None
    tier: Tier
    preview: PreviewCard                      # U1
    disposition: Literal["ready", "blocked"]
    fallback: RehydratedAnswer | None = None  # blocked 인 경우 신뢰 구역 내 답변

class PrepareResult(BaseModel):
    request_id: str
    calls: tuple[PreparedCall, ...]
    agents_notified: Literal[False] = False   # ⚠️ 이 시점에 사람에게 알림 없음
```

**`agents_notified: Literal[False]`가 타입으로 못 박은 약속이다.**
`prepare`에서 담당자에게 알림이 가면 P1("사람을 깨우지 않는다")이 무너진다. 타입이 `False`만 허용하므로 코드가 `True`를 넣을 수 없다.

---

## 3. 전송과 답변

```python
class SendRequest(BaseModel):
    request_id: str
    envelope_ids: list[str] = Field(min_length=1, max_length=2)
    approved_by: str = Field(pattern=r"^person:[a-z0-9_]{1,32}$")

class MergedAnswer(BaseModel):
    answers: tuple[RehydratedAnswer, ...]     # 1개 또는 2개
    divergent: bool                            # ⚠️ conflict 가 아니다
    divergence_note: str | None = None
    disposition: Disposition

class AskResult(BaseModel):
    request_id: str
    merged: MergedAnswer
    escalations: tuple[str, ...] = ()          # 생성된 inbox item_id
    elapsed_seconds: float
    interrupts_avoided: int = 0
    minutes_saved_estimate: int = 0
```

### `divergent` vs `conflict` (Round 2 Q11)

필드명을 `conflict`에서 `divergent`로 바꿨다.

| | 의미 | 문제 |
|---|---|---|
| `conflict: true` | "상충한다"는 **단정** | 둘 다 맞을 수 있다. 오탐이 잦다 |
| `divergent: true` | "서로 다른 답이 나왔다"는 **관찰** | 판단을 사람에게 남긴다 |

`divergence_note`는 자유 문장이 아니라 고정 템플릿이다:
> `"둘 다 사실일 수 있습니다. 시점이 {gap} 차이이고 문서 성격이 다릅니다."`

**상충 여부를 LLM에게 판정시키지 않는다.** 별도 호출도, 프롬프트도 없다.

---

## 4. 인박스

```python
class EscalationDraft(BaseModel):
    summary: str                               # 질문 요약
    situation: tuple[str, ...]                  # 근거 항목들
    draft_answer: str                            # 답변 초안
    already_answered: tuple[str, ...] = ()       # Agent 가 이미 답한 하위 질문

class InboxItem(BaseModel):
    item_id: str
    at: datetime
    owner_entity_id: str
    asker: str
    thread_id: str                               # 2명 지목 시 같은 스레드
    question_summary: str
    draft: EscalationDraft
    citations: tuple[Citation, ...]
    tier: Tier                                   # ⚠️ 인박스 항목도 등급을 갖는다
    status: Literal["open","approved","approved_with_edit","redirected"]
    resolved_at: datetime | None = None
    resolution_text: str | None = None
    redirect_to: str | None = None

class ResolveRequest(BaseModel):
    action: Literal["approve", "approve_with_edit", "not_me"]
    edited_text: str | None = None               # approve_with_edit 필수
    redirect_to: str | None = None                # not_me 필수

    @model_validator(mode="after")
    def _check(self):
        if self.action == "approve_with_edit" and not self.edited_text:
            raise ValueError("edited_text required")
        if self.action == "not_me" and not self.redirect_to:
            raise ValueError("redirect_to required")
        return self
```

**`InboxItem.tier`가 필요한 이유**: 인박스 항목의 `draft_answer`는 Claude가 생성한 것이라 sanitize돼 있지만, `situation`(근거)과 `question_summary`는 신뢰 구역 안에서 만들어진 원문 기반 텍스트다. 시나리오 2의 인박스 화면에 `"13:47에 스크립트를 고쳤으니"`가 뜨는 것처럼.

→ **인박스는 로컬이 원본이고, 클라우드 미러에는 등급이 `open`인 항목만 올린다.** 나머지는 참조만 올린다.

---

## 5. 에이전트 목록 (U2에서 옴)

`AgentCard`는 U2 `domain-entities.md` §3에 정의. U3는 그대로 전달한다.

---

## 6. 신뢰도 분기 (`Disposition`)

`Disposition`은 U1 `schemas.py`에 정의. 분기 규칙 (FR-34, FR-35):

```python
def branch(answers) -> Disposition:
    for a in answers:
        if not a.citations:
            return Disposition.ESCALATE          # ⚠️ 신뢰도 무관. 최우선 규칙
    conf = min(a.confidence for a in answers)     # 2명이면 낮은 쪽 기준
    if conf >= 0.75: return Disposition.AUTO
    if conf >= 0.45: return Disposition.UNVERIFIED
    return Disposition.ESCALATE
```

**인용 검사가 신뢰도 검사보다 먼저다.** 근거 없는 생성은 사용자에게 도달하지 않는다.

**2명일 때 `min`을 쓰는 이유**: 한쪽이 근거가 약하면 그 사실이 화면에 반영돼야 한다. `max`를 쓰면 약한 답이 강한 답에 편승한다.

`confidence`는 이미 U2의 `STALE` 보정(×0.8)이 적용된 값이다.

---

## 7. API 요청/응답 매핑

| 엔드포인트 | 요청 | 응답 |
|---|---|---|
| `GET /api/agents` | — | `list[AgentCard]` |
| `POST /api/ask/prepare` | `AskRequest` | `PrepareResult` |
| `POST /api/ask/send` | `SendRequest` | `AskResult` |
| `GET /api/inbox?owner=` | — | `list[InboxItem]` |
| `POST /api/inbox/{id}/resolve` | `ResolveRequest` | `InboxItem` |
| `GET /api/audit?q=` | — | `list[AuditRecord]` (payload 전문 포함) |
| `GET /api/health` | — | `HealthStatus` |

```python
class HealthStatus(BaseModel):
    exaone_mode: str                            # live | mock
    agent_transport: str                         # broker | direct | mock
    trusted_zone_llm_base_url: str               # ⚠️ 화면에 표시한다
    trust_boundary_simulated: bool                # 공개 SaaS 엔드포인트인가
    agent_model_id: str
    mirror_backlog: int
    demo_now_override: datetime | None
```

`trust_boundary_simulated`를 API로 노출해 **UI에 표시**한다. 데모에서 먼저 밝히는 것을 도구가 돕게 한다 (Round 2 Q15).

---

## 8. 테스트 가능한 속성 (PBT-01)

| # | 속성 | 범주 | 대상 |
|---|---|---|---|
| PB-O1 | `branch()`는 인용 0개면 항상 `ESCALATE` (신뢰도 무관) | 불변식 | `branch` |
| PB-O2 | `branch()`는 신뢰도에 대해 단조: 신뢰도가 높아지면 처분이 나빠지지 않는다 | 불변식 | `branch` |
| PB-O3 | `merge()`는 입력 답변을 하나도 버리지 않는다 (`len(out) == len(in)`) | 불변식 | `merge` |
| PB-O4 | `merge()`는 답변 순서에 무관 (교환법칙) | 교환 | `merge` |
| PB-O5 | `AskRequest` 직렬화 → 역직렬화 = 항등 | 왕복 | `AskRequest` |
| PB-O6 | `AskResult` JSON에 `internal_path` 문자열이 없다 | 불변식 | 응답 직렬화 |

**PB-O3가 FR-33의 검증이다** — "하나를 조용히 고르지 않는다"를 속성으로 표현했다. `merge()`가 답을 버리는 코드 경로가 있으면 실패한다.

**PB-O4가 필요한 이유**: 병렬 호출이므로 응답 도착 순서가 비결정적이다. 순서에 따라 화면이 달라지면 데모가 흔들린다.

**PBT 미적용 (N/A 근거)**
- `agent.py` — Bedrock 호출. 프롬프트 구성만 예제 테스트
- `inbox.py` — SQLite I/O. 3버튼 상태 전이는 예제 테스트로 충분 (상태 4개, 전이 3개)
- `main.py` — FastAPI 라우팅. 승인 없는 `send` 실패는 예제 테스트

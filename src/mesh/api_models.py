"""HTTP API 계약 — U3(B)가 제공하고 U4(C)가 소비한다.

⚠️ Day 1 동결. C 가 이 스키마로 목업 JSON 을 만들어 화면을 선행 개발한다.
   형태가 실제 응답과 다르면 Day 4 에 UI 를 다시 만들어야 한다.

두 가지 타입 수준 결정이 여기 있다:

  1. `PrepareResult.agents_notified: Literal[False]`
     `prepare` 단계에서 담당자에게 알림이 가면 P1("사람을 깨우지 않는다")이
     무너진다. 타입이 False 만 허용하므로 코드가 True 를 넣을 수 없다.

  2. `MergedAnswer.divergent` (`conflict` 가 아니다)
     `conflict: true` 는 "상충한다"는 단정이고 `divergent: true` 는
     "서로 다른 답이 나왔다"는 관찰이다. 판단은 사람에게 남긴다.

API 는 `prepare` / `send` 2단계다. 승인 없는 전송이 **구조적으로 불가능**하게
만들기 위한 것이다 — `send` 는 `prepare` 만 발급하는 `envelope_id` 를 요구한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mesh.schemas import (
    ENTITY_ID_PATTERN,
    ENVELOPE_ID_PATTERN,
    Citation,
    Disposition,
    EscalationDraft,
    Freshness,
    PreviewCard,
    RehydratedAnswer,
    Tier,
    Transport,
)

MAX_QUESTION_CHARS = 4000
MAX_ANSWER_CHARS = 4000
MAX_SEARCH_CHARS = 200


# ══════════════════════════════════════════════════════════════════════
# 질문 — prepare
# ══════════════════════════════════════════════════════════════════════


class AskRequest(BaseModel):
    """질문 접수. 입력 검증은 여기서 선언적으로 끝난다 (NFR-S-05)."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    asker: str = Field(pattern=ENTITY_ID_PATTERN)
    targets: list[str] = Field(min_length=1, max_length=2)  # MAX_TARGETS

    @field_validator("targets")
    @classmethod
    def _unique_and_wellformed(cls, v: list[str]) -> list[str]:
        import re

        if len(set(v)) != len(v):
            raise ValueError("중복 지목은 허용하지 않는다")
        for t in v:
            if not re.match(ENTITY_ID_PATTERN, t):
                raise ValueError(f"entity_id 형식 위반: {t!r}")
        return v

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("질문이 비어 있다")
        return v


class SubQuestionView(BaseModel):
    """질문 분해 결과 (BR-G-07). 화면에 분해됐음을 보여준다."""

    id: str
    kind: str
    text: str
    tier: Tier


class PreparedCall(BaseModel):
    """대상 1명(또는 하위 질문 1개)에 대한 준비 결과.

    `disposition == "blocked"` 이면 `fallback` 이 함께 온다 —
    "차단됐고 대신 이 답이 있다"가 한 왕복에 끝난다 (시나리오 3 후속).
    """

    envelope_id: str | None = Field(default=None, pattern=ENVELOPE_ID_PATTERN)
    target_entity_id: str = Field(pattern=ENTITY_ID_PATTERN)
    agent_label: str
    sub_question: SubQuestionView | None = None
    tier: Tier
    disposition: Literal["ready", "blocked"]
    preview: PreviewCard | None = None
    fallback: RehydratedAnswer | None = None
    blocked_reason: str | None = None

    @model_validator(mode="after")
    def _shape_matches_disposition(self) -> PreparedCall:
        if self.disposition == "ready":
            if self.envelope_id is None or self.preview is None:
                raise ValueError("ready 인데 envelope_id/preview 가 없다")
        elif self.fallback is None:
            raise ValueError("blocked 인데 fallback 이 없다 — 차단만 하고 답을 안 주면 안 된다")
        return self


class PrepareResult(BaseModel):
    """`POST /api/ask/prepare` 응답. **Agent 를 호출하지 않은 상태다.**"""

    request_id: str
    calls: tuple[PreparedCall, ...]
    decomposed: bool = False
    upgraded_tier: Tier | None = None  # 상향이 일어났으면 그 등급
    upgrade_reason: str | None = None

    #: ⚠️ 타입으로 못 박은 약속 (BR-O-03).
    #: prepare 단계에서 담당자 인박스에 아무것도 쓰지 않는다.
    agents_notified: Literal[False] = False

    @property
    def ready_envelope_ids(self) -> tuple[str, ...]:
        return tuple(c.envelope_id for c in self.calls if c.envelope_id)


# ══════════════════════════════════════════════════════════════════════
# 전송 — send
# ══════════════════════════════════════════════════════════════════════


class SendRequest(BaseModel):
    """사용자 승인 후 전송.

    `envelope_ids` 는 `prepare` 가 발급한 것이어야 하고 일회용이다.
    `approved_by` 없이는 `Gatekeeper.ask_agent()` 가 거부한다 (BR-G-02).
    """

    request_id: str
    envelope_ids: list[str] = Field(min_length=1, max_length=2)
    approved_by: str = Field(pattern=ENTITY_ID_PATTERN)

    @field_validator("envelope_ids")
    @classmethod
    def _wellformed(cls, v: list[str]) -> list[str]:
        import re

        if len(set(v)) != len(v):
            raise ValueError("중복 envelope_id")
        for e in v:
            if not re.match(ENVELOPE_ID_PATTERN, e):
                raise ValueError(f"envelope_id 형식 위반: {e!r}")
        return v


class MergedAnswer(BaseModel):
    """1개 또는 2개 답변.

    ⚠️ 필드명이 `divergent` 인 것이 설계 결정이다 (Round 2 Q11).
       `conflict: true` = "상충한다" (단정, 오탐 잦음)
       `divergent: true` = "서로 다른 답이 나왔다" (관찰, 판단은 사람에게)

    답변 순서는 요청 순서를 유지한다. 신뢰도로 정렬하지 않는다 —
    정렬하면 사용자가 위쪽 답을 정답으로 읽는다 (BR-O-07).
    """

    answers: tuple[RehydratedAnswer, ...]
    divergent: bool = False
    divergence_note: str | None = None
    disposition: Disposition


class AskResult(BaseModel):
    """`POST /api/ask/send` 응답."""

    request_id: str
    merged: MergedAnswer
    escalations: tuple[str, ...] = ()  # 생성된 inbox item_id
    elapsed_seconds: float = 0.0
    interrupts_avoided: int = 0
    minutes_saved_estimate: int = 0


# ══════════════════════════════════════════════════════════════════════
# 인박스
# ══════════════════════════════════════════════════════════════════════


class InboxItem(BaseModel):
    """에스컬레이션 항목.

    `tier` 를 갖는 이유: `situation`(근거)과 `question_summary` 는 신뢰 구역
    안에서 만든 원문 기반 텍스트다. 시나리오 2 인박스의
    "13:47에 스크립트를 고쳤으니"가 그 예다.
    -> 클라우드 미러에는 `tier == open` 인 항목만 전문을 올린다 (BR-I-05).
    """

    item_id: str
    at: datetime
    owner_entity_id: str = Field(pattern=ENTITY_ID_PATTERN)
    asker: str = Field(pattern=ENTITY_ID_PATTERN)
    thread_id: str  # 2명 지목 시 같은 스레드 (BR-I-04)
    question_summary: str
    draft: EscalationDraft
    citations: tuple[Citation, ...] = ()
    tier: Tier
    status: Literal["open", "approved", "approved_with_edit", "redirected"] = "open"
    resolved_at: datetime | None = None
    resolution_text: str | None = None
    redirect_to: str | None = None


class ResolveRequest(BaseModel):
    """인박스 3버튼 (BR-I-01)."""

    action: Literal["approve", "approve_with_edit", "not_me"]
    edited_text: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)
    redirect_to: str | None = None

    @model_validator(mode="after")
    def _action_requires_its_field(self) -> ResolveRequest:
        if self.action == "approve_with_edit" and not (self.edited_text or "").strip():
            raise ValueError("approve_with_edit 는 edited_text 가 필요하다")
        if self.action == "not_me":
            import re

            if not self.redirect_to:
                raise ValueError("not_me 는 redirect_to 가 필요하다")
            if not re.match(ENTITY_ID_PATTERN, self.redirect_to):
                raise ValueError(f"redirect_to 형식 위반: {self.redirect_to!r}")
        return self


# ══════════════════════════════════════════════════════════════════════
# 감사 로그
# ══════════════════════════════════════════════════════════════════════


class AuditRowView(BaseModel):
    """감사 로그 한 줄. `AuditRecord` 에서 UI 표시용 필드만 뽑은 것.

    `trusted_zone_llm_base_url` 과 `transport` 를 표시하는 이유:
    이 프로젝트의 신뢰 경계는 설정값이다. **"원문이 어디로 갔는지"가
    로그로 증명돼야 한다** (U1 Auditable Trust Boundary 패턴).
    """

    record_id: str
    at: datetime
    actor: str
    target_entity_id: str
    model_id: str
    transport: Transport
    trusted_zone_llm_base_url: str
    tier: Tier
    representation: str
    payload: dict  # 전문. 심사자가 직접 확인한다
    payload_sha256: str
    size_bytes: int
    validation_summary: str
    approved_by: str
    envelope_id: str


class AuditSearchResult(BaseModel):
    """원문 검색 결과 (FR-42).

    `zero_hit` 이 1막의 결정적 장면이다. 검색어가 있고 결과가 0건일 때
    UI 가 "0건 — 이 문구는 경계를 넘은 적이 없습니다"를 크게 표시한다.

    ⚠️ `local_queries`(신뢰 구역 내 처리)는 포함하지 않는다 (BR-U-11).
       "레코드가 없다"가 증거가 되려면 섞이면 안 된다.
    """

    query: str | None = None
    rows: tuple[AuditRowView, ...] = ()
    total_records: int = 0

    @property
    def zero_hit(self) -> bool:
        return bool(self.query) and not self.rows


# ══════════════════════════════════════════════════════════════════════
# 에이전트 목록 · 상태
# ══════════════════════════════════════════════════════════════════════


class AgentCardView(BaseModel):
    """지목 목록의 카드 1개.

    ⚠️ `null` 인 필드는 UI 가 "비공개"라고 표시하지 않고 **아예 렌더하지
       않는다** (BR-U-08). "비공개"라는 표시 자체가 정보이기 때문이다.

    ⚠️ `current_focus_summary` 는 `Session.focus` 원문이 아니다.
       식별자를 제거한 요약이며 그 변환도 게이트키퍼를 통과한다 (FR-31).
    """

    entity_id: str = Field(pattern=ENTITY_ID_PATTERN)
    display_name: str
    expertise: str  # 항상 있다 (Disclose.expertise 는 Literal[True])
    activity_status: Literal["active", "away", "offline"] | None = None
    away_minutes: int | None = None
    question_count_today: int | None = None
    current_focus_summary: str | None = None
    session_as_of: datetime | None = None
    freshness: Freshness | None = None
    daily_limit_reached: bool = False


class HealthStatus(BaseModel):
    """`GET /api/health`.

    `trust_boundary_simulated` 를 노출해 **UI 헤더가 상시 표시**한다.
    숨기면 심사자를 속이는 것이다. 먼저 밝히는 것이 지적당하는 것보다 낫다
    (Round 2 Q15, BR-U-09).
    """

    model_config = ConfigDict(frozen=True)

    exaone_mode: Literal["live", "mock"]
    agent_transport: Transport
    trusted_zone_llm_base_url: str
    trust_boundary_simulated: bool
    agent_model_id: str
    draft_model_id: str
    vocab_version: str
    vocab_sha256: str
    vocab_drift: bool = False
    mirror_backlog: int = 0
    demo_now_override: datetime | None = None
    envelope_cache_size: int = 0

    #: 처분 분포 — 그대로 평가 지표가 된다 (요구사항 §6)
    disposition_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def auto_answer_rate(self) -> float | None:
        """자동 응답률. 목표 >= 50%."""
        total = sum(self.disposition_counts.values())
        if not total:
            return None
        return self.disposition_counts.get("auto", 0) / total


class ErrorResponse(BaseModel):
    """오류 응답.

    ⚠️ 스택 트레이스·내부 경로·프레임워크 버전을 담지 않는다 (NFR-S-09).
       `correlation_id` 만 주고 상세는 로그에서 찾는다.
    """

    error: str
    correlation_id: str
    detail: str | None = None

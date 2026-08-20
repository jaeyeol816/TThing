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

from mesh.org import OrgChartView
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
    #: 처리 경과를 열어 볼 수 있는 열쇠 (`GET /api/trace/{trace_id}`).
    #: `None` 이면 기록에 실패한 것이고, 그때도 답변은 정상으로 나온다 —
    #: 트레이스는 설명이지 기능이 아니다.
    trace_id: str | None = None

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
    #: 이 사람이 어느 컴퓨터에 있는가. `None` 이면 **이 컴퓨터**다.
    #:
    #: 화면이 "이 컴퓨터" / 노드 이름 배지를 그린다. 사용자가 남의 컴퓨터에
    #: 질문을 보내고 있다는 사실을 모르면 안 된다 — 답변에는 재수화된 실제
    #: 이름이 들어오고, 그것이 LAN 을 건너온 것이기 때문이다.
    node_name: str | None = None

    # ── 조직도 좌표 ──────────────────────────────────────────────
    #: 전부 `None`/빈 값이면 조직도에 자리가 없다는 뜻이고, 화면은 그 사람을
    #: "미배치" 묶음에 그린다. 조직도가 아예 없어도 목록은 그대로 동작한다.
    unit_id: str | None = None
    unit_path: tuple[str, ...] = ()
    rank_id: str | None = None
    rank_label: str | None = None
    rank_badge: str | None = None
    rank_order: int | None = None
    org_title: str | None = None


# ══════════════════════════════════════════════════════════════════════
# 조직도 (`GET /api/org`)
# ══════════════════════════════════════════════════════════════════════
#
# 구조 자체는 `mesh.org` 가 정의한다. 여기서 다시 선언하지 않고 그대로
# 내보내는 이유: 두 곳에 같은 모양을 적으면 한쪽만 고쳐지는 날이 온다.
#
# ⚠️ 이 응답은 **인증 없이 보인다.** `org.yaml` 의 문자열이 그대로 나가므로
#    로드 시점에 금칙어를 검사한다 (`OrgChart.validate_no_banned`).


class OrgChartResponse(OrgChartView):
    """`GET /api/org`. `OrgChartView` 를 그대로 쓴다.

    사람의 이름·직급은 여기 없다 — `member_ids` 만 있고 표시에 필요한 것은
    `GET /api/agents` 가 준다. 두 응답이 각자 자기 것만 말하게 해서,
    "조직도에는 있는데 카드에는 없는 사람" 같은 상태를 화면이 감지할 수 있다.
    """


# ══════════════════════════════════════════════════════════════════════
# 브로드캐스트 (`POST /api/ask/broadcast`)
# ══════════════════════════════════════════════════════════════════════
#
# ⚠️ **이 왕복은 경계를 넘지 않는다.** 문서를 읽지 않고, 경계 밖 Agent 를
#    부르지 않으며, 감사 레코드를 만들지 않는다. 판정 재료는 전부 이미
#    인증 없이 보이는 값이다 (`mesh.triage` 파일 머리말 참조).
#
#    그래서 `agents_notified` 같은 필드가 여기 없다 — 알릴 사람이 없다.


class BroadcastRequest(BaseModel):
    """질문 하나를 전원에게 뿌린다. **지목이 없다.**"""

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    asker: str = Field(pattern=ENTITY_ID_PATTERN)
    #: 후보로 남길 최대 인원. 화면이 한 번에 다룰 수 있는 수를 넘기지 않는다.
    max_relevant: int = Field(default=6, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("질문이 비어 있다")
        return v


class AgentRelevanceView(BaseModel):
    """브로드캐스트를 받은 한 명의 판정 결과.

    ⚠️ `reason` 은 **코드가 조립한 문장**이다 (`triage.REASON_TEMPLATES`).
       모델이 쓴 자유 문장이 아니다 — 그것을 허용하면 판정 결과에 원문이
       섞여 나올 채널이 생긴다.
    """

    entity_id: str = Field(pattern=ENTITY_ID_PATTERN)
    display_name: str
    relevant: bool
    score: float = 0.0
    reason_code: str = "no_match"
    reason: str = ""
    matched: tuple[str, ...] = ()
    decided_by: Literal["rule", "model", "rule+model"] = "rule"
    #: 지금 질문을 받을 수 없는 상태 (일일 한도 초과 등). 화면이 이유를 밝힌다.
    available: bool = True
    unavailable_reason: str | None = None


class BroadcastResult(BaseModel):
    """`POST /api/ask/broadcast` 응답.

    `results` 는 **전원**을 담는다. 관련 없는 사람을 목록에서 지우지 않는
    이유: 화면이 "관련 없음으로 판정돼 흐려졌다" 를 보여줘야 사용자가
    판정이 틀렸을 때 되돌릴 수 있다. 지워 버리면 되돌릴 대상이 없다.
    """

    broadcast_id: str
    question: str
    results: tuple[AgentRelevanceView, ...] = ()
    threshold: float = 0.5
    #: 선별 모델이 실제로 돌았는가. 규칙만으로 좁힌 것과 구분해 표시한다.
    model_used: bool = False
    model_note: str | None = None
    elapsed_seconds: float = 0.0

    #: ⚠️ 타입으로 못 박은 약속. 브로드캐스트는 경계를 넘지 않는다.
    crossed_boundary: Literal[False] = False

    @property
    def relevant_ids(self) -> tuple[str, ...]:
        return tuple(r.entity_id for r in self.results if r.relevant)


# ══════════════════════════════════════════════════════════════════════
# 게이트키퍼 트레이스 (`GET /api/trace/{trace_id}`)
# ══════════════════════════════════════════════════════════════════════
#
# ⚠️ 여기에 `TraceResponse` 를 두지 않는다. `mesh.trace` 는 이 모듈과 같은
#    층(L1 지원)이고, 같은 층끼리의 의존은 금지돼 있다
#    (`tests/unit/test_import_boundary.py::test_dependencies_flow_downward_only`).
#
#    래퍼를 하나 만들자고 레이어 규칙에 예외를 내는 것은 교환비가 나쁘다.
#    라우트가 `mesh.trace.GatekeeperTrace` 를 그대로 `response_model` 로 쓴다 —
#    `main`(L7)에서 L1 을 보는 것은 정방향이다.


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


# ══════════════════════════════════════════════════════════════════════
# 문서 업로드 (Day 4 신설)
# ══════════════════════════════════════════════════════════════════════
#
# 사용자가 자기 컴퓨터의 문서를 올리는 순간부터 이 도구가 시작된다.
# 그래서 **업로드 응답이 곧 첫 데모 장면**이다:
#
#     "방금 올린 문서는 기밀로 판정됐습니다 — 본문의 금액 표기 때문입니다"
#
# 판정 근거를 즉시 보여주는 것이 중요하다. 나중에 답변이 무뎌졌을 때
# "왜?"를 되짚을 수 있어야 하고, 등급 판정이 블랙박스가 아니라는 것을
# 이 화면 하나로 보인다.

#: 업로드 크기 상한. 텍스트 문서만 받으므로 넉넉하다.
#: 이 값을 넘기면 413 이 아니라 422 다 — pydantic 이 막는다.
MAX_UPLOAD_CHARS = 200_000

#: 파일명 상한. 파일시스템 한계보다 짧게 잡는다.
MAX_FILENAME_CHARS = 120

#: 받는 확장자. **허용 목록**이다 (차단 목록이 아니다).
#:
#: 실행 파일·아카이브를 받지 않는 이유: 이 도구는 파일을 **읽어서 모델에
#: 넘기는** 것이 전부다. 압축을 풀거나 실행할 이유가 없고, 그런 경로를
#: 만들면 그것 자체가 공격면이 된다.
ALLOWED_UPLOAD_SUFFIXES: tuple[str, ...] = (
    ".md",
    ".txt",
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".log",
    ".csv",
    ".sql",
    ".sh",
    ".toml",
    ".ini",
    ".cfg",
)


class UploadRequest(BaseModel):
    """문서 업로드.

    파일을 **텍스트로** 받는다 (multipart 가 아니다). 근거:
      - 대상이 설계 문서·스크립트·설정·로그다. 전부 텍스트다
      - multipart 파서는 파일명·인코딩·경계 처리에서 사고가 잦다
      - 브라우저가 `FileReader` 로 읽어 보내면 클라이언트가 무엇을 보내는지
        사용자에게 보여줄 수 있다 (이 프로젝트의 원칙과 맞는다)

    바이너리를 받지 않는 것은 기능 제약이 아니라 **범위를 좁힌 결정**이다.
    """

    owner: str = Field(pattern=ENTITY_ID_PATTERN)
    filename: str = Field(min_length=1, max_length=MAX_FILENAME_CHARS)
    content: str = Field(min_length=1, max_length=MAX_UPLOAD_CHARS)
    #: 세션의 `open_paths` 에 추가할지. 끄면 저장만 하고 질의 후보가 되지 않는다.
    attach_to_session: bool = True

    @field_validator("filename")
    @classmethod
    def _safe_filename(cls, v: str) -> str:
        """경로 구분자·상위 참조·숨김 파일·확장자를 여기서 막는다.

        ⚠️ 서버가 다시 검증한다 (`store.save_upload`). 이 검증은 사용자에게
           **왜 거부됐는지 빨리 알려주기 위한 것**이고, 신뢰의 근거가 아니다.
        """
        import re as _re

        name = v.strip()
        if not name:
            raise ValueError("파일명이 비어 있다")
        if "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("파일명에 경로 구분자를 쓸 수 없다")
        if name.startswith("."):
            raise ValueError("숨김 파일은 올릴 수 없다")
        if ".." in name:
            raise ValueError("파일명에 '..' 를 쓸 수 없다")
        if not _re.fullmatch(r"[\w.\-() \[\]가-힣]+", name):
            raise ValueError("파일명에 쓸 수 없는 문자가 있다")
        suffix = name[name.rfind(".") :].lower() if "." in name else ""
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise ValueError(
                f"받지 않는 확장자다: {suffix or '(없음)'}. "
                f"허용: {', '.join(ALLOWED_UPLOAD_SUFFIXES)}"
            )
        return name


class TierEvidence(BaseModel):
    """등급 판정 근거 한 줄. 사람이 읽는다."""

    model_config = ConfigDict(frozen=True)

    rule: int
    reason: str


class DocumentView(BaseModel):
    """저장된 문서 하나.

    ⚠️ `internal_path` 가 **있다.** 다른 응답과 다른 점이다.

       근거: 이 화면은 **소유자가 자기 문서를 관리하는 화면**이다. 자기가
       방금 올린 파일의 경로를 자기가 보는 것은 권한 우회가 아니다.
       FR-43 이 막는 것은 *다른 사람의* 지식을 인용할 때 경로가 새는 것이다.

       그래서 이 필드는 `GET /api/documents?owner=` 에만 나오고
       답변·인용·감사 응답에는 나오지 않는다.
    """

    document_id: str
    owner: str = Field(pattern=ENTITY_ID_PATTERN)
    filename: str
    internal_path: str
    size_bytes: int
    uploaded_at: datetime
    tier: Tier
    tier_evidence: tuple[TierEvidence, ...] = ()
    attached: bool = False
    #: 업로드가 아니라 저장소에 원래 있던 샘플 문서인가
    seeded: bool = False


class UploadResult(BaseModel):
    """업로드 응답. **판정 결과를 즉시 준다.**

    `tier` 가 `secret` 이면 화면이 그 사실과 근거를 크게 보여준다 —
    "이 문서를 쓰는 질문은 원문이 나가지 않습니다"를 미리 알리는 것이
    나중에 답변이 무뎌졌을 때의 설명 비용을 줄인다.
    """

    document: DocumentView
    #: 이 문서가 다른 사람의 질의에 동원될 수 있는가 (scope 안인가)
    in_scope: bool = True
    warnings: tuple[str, ...] = ()


class DocumentList(BaseModel):
    owner: str = Field(pattern=ENTITY_ID_PATTERN)
    documents: tuple[DocumentView, ...] = ()

    @property
    def secret_count(self) -> int:
        return sum(1 for d in self.documents if d.tier is Tier.SECRET)


# ══════════════════════════════════════════════════════════════════════
# 사용자 · 질문 프리셋 (프런트가 하드코딩하지 않게)
# ══════════════════════════════════════════════════════════════════════


class UserView(BaseModel):
    """전환 가능한 사용자.

    ⚠️ **인증이 아니다.** 데모용 관점 전환이며 화면에 그 사실을 표시한다
       (BR-U-15). 실배포에서는 원본 시스템의 권한을 승계해야 한다.
    """

    entity_id: str = Field(pattern=ENTITY_ID_PATTERN)
    display_name: str
    expertise: str


class PresetQuestion(BaseModel):
    label: str
    question: str
    targets: tuple[str, ...] = ()
    note: str | None = None


# ══════════════════════════════════════════════════════════════════════
# 보안 프로토콜 API 모델
# ══════════════════════════════════════════════════════════════════════


class ProtocolUpsertRequest(BaseModel):
    """프로토콜 생성/수정 요청."""

    level: Literal["company", "team", "personal"]
    owner: str = Field(min_length=1, max_length=64)
    description: str = ""

    secret_keywords: list[str] = Field(default_factory=list)
    secret_patterns: list[str] = Field(default_factory=list)
    secret_directories: list[str] = Field(default_factory=list)
    secret_extensions: list[str] = Field(default_factory=list)
    secret_content_patterns: list[str] = Field(default_factory=list)

    internal_keywords: list[str] = Field(default_factory=list)
    internal_directories: list[str] = Field(default_factory=list)
    internal_extensions: list[str] = Field(default_factory=list)

    open_directories: list[str] = Field(default_factory=list)

    exaone_context_hints: list[str] = Field(default_factory=list)


class ProtocolView(BaseModel):
    """프로토콜 응답."""

    level: str
    owner: str
    description: str
    updated_at: str

    secret_keywords: list[str]
    secret_patterns: list[str]
    secret_directories: list[str]
    secret_extensions: list[str]
    secret_content_patterns: list[str]

    internal_keywords: list[str]
    internal_directories: list[str]
    internal_extensions: list[str]

    open_directories: list[str]

    exaone_context_hints: list[str]


class MergedRulesView(BaseModel):
    """현재 머지된 규칙 미리보기."""

    secret_keywords: list[str]
    secret_patterns: list[str]
    secret_path_globs: list[str]
    open_path_globs: list[str]
    internal_path_globs: list[str]
    protocol_count: int


# ══════════════════════════════════════════════════════════════════════
# 저장 위치 — 내 컴퓨터의 실제 경로
# ══════════════════════════════════════════════════════════════════════


class StorageInfo(BaseModel):
    """`GET /api/storage` — 파일이 실제로 어디에 있는가."""

    model_config = ConfigDict(frozen=True)

    data_root: str
    uploads_root: str
    my_uploads: str | None = None
    audit_db: str
    sessions_root: str
    configured_relative: bool = True
    configured_value: str = "./data"
    exists: bool = True
    reveal_command: str | None = None


# ══════════════════════════════════════════════════════════════════════
# 피어 메시 — 같은 네트워크의 다른 컴퓨터
# ══════════════════════════════════════════════════════════════════════


class PeerIdentity(BaseModel):
    """`GET /api/peer/hello`"""

    model_config = ConfigDict(frozen=True)

    node_name: str
    version: str
    peer_ready: bool
    agent_count: int


class PeerNodeView(BaseModel):
    """`GET /api/peers` — 피어 목록 한 줄."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    status: Literal["connected", "unreachable", "token_invalid", "self"]
    node_name: str | None = None
    agent_count: int = 0
    latency_ms: int | None = None
    detail: str | None = None


class PeerStatus(BaseModel):
    """`GET /api/peers` 전체."""

    model_config = ConfigDict(frozen=True)

    node_name: str
    lan_mode: bool
    listen_url: str
    peer_token_set: bool
    peers: tuple[PeerNodeView, ...] = ()
    hint: str | None = None


class PeerPrepareRequest(BaseModel):
    """피어에게 준비 요청."""

    asker: str = Field(pattern=ENTITY_ID_PATTERN)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    target: str = Field(pattern=ENTITY_ID_PATTERN)
    asker_node: str = Field(min_length=1, max_length=64)


class PeerPreparedCall(BaseModel):
    """피어 준비 결과."""

    model_config = ConfigDict(frozen=True)

    node_name: str
    call: PreparedCall


class PeerSendRequest(BaseModel):
    """승인 후 전송 요청."""

    request_id: str
    envelope_id: str
    approved_by: str = Field(pattern=ENTITY_ID_PATTERN)
    asker_node: str = Field(min_length=1, max_length=64)


class PeerAnswer(BaseModel):
    """피어 답변."""

    model_config = ConfigDict(frozen=True)

    node_name: str
    answer: RehydratedAnswer
    escalated: bool = False
    escalation_note: str | None = None
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
    #: 이 사람이 어느 컴퓨터에 있는가. `None` 이면 **이 컴퓨터**다.
    #:
    #: 화면이 "이 컴퓨터" / 노드 이름 배지를 그린다. 사용자가 남의 컴퓨터에
    #: 질문을 보내고 있다는 사실을 모르면 안 된다 — 답변에는 재수화된 실제
    #: 이름이 들어오고, 그것이 LAN 을 건너온 것이기 때문이다.
    node_name: str | None = None


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
# 저장 위치 — 내 컴퓨터의 실제 경로
# ══════════════════════════════════════════════════════════════════════


class StorageInfo(BaseModel):
    """`GET /api/storage` — 파일이 실제로 어디에 있는가.

    ──────────────────────────────────────────────────────────────────
    왜 절대 경로를 보여주는가
    ──────────────────────────────────────────────────────────────────

    사용자가 자기 컴퓨터에서 문서를 올린다. 그런데 "저장됐습니다"만 보여주면
    **그 파일이 어디로 갔는지 알 수 없다.** 지우고 싶을 때, 백업하고 싶을 때,
    "정말 내 컴퓨터에 있나"를 확인하고 싶을 때 경로가 필요하다.

    이 프로젝트의 주장은 "원문이 경계를 넘지 않는다"다. 그 주장을 확인하는
    가장 직접적인 방법은 **파일을 파인더에서 열어 보는 것**이고, 그러려면
    경로를 알아야 한다. 경로를 숨기면 주장을 검증할 수 없다.

    ──────────────────────────────────────────────────────────────────
    ⚠️ 이 응답은 loopback 전용이다
    ──────────────────────────────────────────────────────────────────

    절대 경로는 사용자 이름·디렉터리 구조를 담는다. 피어에게 줄 이유가 없고,
    준다면 그것 자체가 정보다. `main._origin_gate` 가 원격 출처를 막는다
    (`/api/peer/*` 만 허용).

    FR-43 이 막는 것은 *다른 사람* 지식을 인용할 때의 경로 유출이다.
    자기 문서가 자기 컴퓨터의 어디에 있는지 보는 것은 다른 일이다.
    """

    model_config = ConfigDict(frozen=True)

    #: `MESH_DATA_ROOT` 의 절대 경로.
    data_root: str
    #: 업로드가 들어가는 뿌리 (`corpus/`). 사람별 하위 디렉터리가 그 아래에 생긴다.
    uploads_root: str
    #: 현재 사용자의 업로드 디렉터리. 없으면 아직 아무것도 올리지 않은 것이다.
    my_uploads: str | None = None
    #: 감사 로그 SQLite 파일.
    audit_db: str
    #: 세션 파일 디렉터리.
    sessions_root: str
    #: `data_root` 가 상대 경로로 설정되어 있는가 (이식성 — NFR-PO-01).
    configured_relative: bool = True
    #: 설정에 적힌 값 그대로. 절대 경로와 대조해 보여 준다.
    configured_value: str = "./data"
    #: 업로드 디렉터리가 실제로 존재하는가.
    exists: bool = True
    #: 이 컴퓨터에서 열어 볼 때 쓸 수 있는 명령 (macOS `open`, Linux `xdg-open`).
    reveal_command: str | None = None


# ══════════════════════════════════════════════════════════════════════
# 피어 메시 — 같은 네트워크의 다른 컴퓨터
# ══════════════════════════════════════════════════════════════════════
#
# ──────────────────────────────────────────────────────────────────────
# 왜 "한 서버에 여러 브라우저" 가 아닌가
# ──────────────────────────────────────────────────────────────────────
#
# 가장 쉬운 방법은 한 컴퓨터에서 서버를 띄우고 모두가 그 주소를 여는 것이다.
# 그러면 **모든 사람의 기밀 문서가 한 컴퓨터에 모인다.** 이 프로젝트가 막으려는
# 것과 정확히 반대다.
#
# 그래서 각자 자기 컴퓨터에서 노드를 띄운다.
#
#   · 내 문서는 내 컴퓨터에만 있다
#   · 남이 내 Agent 에 물으면 **내 노드가** 판정·조립·검증을 하고
#   · 경계를 넘는 페이로드도 **내 노드가** 만들고 내 감사 로그에 남는다
#   · 질문자에게 가는 것은 재수화된 답변뿐이다
#
# 즉 원문은 그것을 가진 컴퓨터를 떠나지 않는다. 이름이 "메시" 인 이유다.


class PeerIdentity(BaseModel):
    """`GET /api/peer/hello` — 토큰 없이도 답하는 유일한 피어 경로.

    ⚠️ **여기에 사람 목록·문서·경로를 담지 않는다.** 토큰 없이 답하므로
       담는 순간 인증 없는 정보 공개가 된다. 노드 이름과 버전만 준다.
    """

    model_config = ConfigDict(frozen=True)

    node_name: str
    version: str
    #: 이 노드가 피어 요청을 받을 준비가 됐는가 (토큰이 설정되어 있는가).
    peer_ready: bool
    #: 등록된 에이전트 수. 목록이 아니라 개수다 — "이 노드에 3명이 있다" 까지만.
    agent_count: int


class PeerNodeView(BaseModel):
    """`GET /api/peers` — 화면의 피어 목록 한 줄. **loopback 전용.**

    `status` 를 네 가지로 나눈 이유: 전부 "실패" 로 뭉치면 사람이 원인을
    짐작해야 한다. 토큰이 틀린 것과 노드가 꺼진 것은 고치는 방법이 다르다.
    """

    model_config = ConfigDict(frozen=True)

    base_url: str
    status: Literal["connected", "unreachable", "token_invalid", "self"]
    node_name: str | None = None
    agent_count: int = 0
    #: 왕복 시간(ms). 느린 노드를 사람이 알아볼 수 있게 한다.
    latency_ms: int | None = None
    detail: str | None = None


class PeerStatus(BaseModel):
    """`GET /api/peers` 전체. **loopback 전용.**"""

    model_config = ConfigDict(frozen=True)

    #: 이 노드의 이름.
    node_name: str
    #: LAN 모드인가 (`MESH_BIND_HOST` 가 localhost 가 아닌가).
    lan_mode: bool
    #: 이 노드가 바인딩한 주소. 다른 컴퓨터에 알려 줄 값이다.
    listen_url: str
    #: 피어 토큰이 설정되어 있는가. **값은 담지 않는다.**
    peer_token_set: bool
    peers: tuple[PeerNodeView, ...] = ()
    #: 사람이 읽을 안내. LAN 모드가 아니면 켜는 방법을 알려 준다.
    hint: str | None = None


class PeerPrepareRequest(BaseModel):
    """피어에게 "이 질문을 네 Agent 에게 준비해 달라"고 청한다.

    ⚠️ `target` 은 **그 노드의** entity_id 다. 질문자가 임의로 정하지 않고
       `/api/peer/agents` 가 알려 준 것을 그대로 쓴다.
    """

    asker: str = Field(pattern=ENTITY_ID_PATTERN)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    target: str = Field(pattern=ENTITY_ID_PATTERN)
    #: 질문자 노드의 이름. 피어의 감사 로그와 인박스에 "누가 물었나" 로 남는다.
    asker_node: str = Field(min_length=1, max_length=64)


class PeerPreparedCall(BaseModel):
    """피어가 돌려주는 준비 결과.

    `PreparedCall` 과 같은 모양이다 — 화면이 로컬과 원격을 구분해 렌더하지
    않아도 되게 한다. 다른 것은 `node_name` 이 붙는 것뿐이다.
    """

    model_config = ConfigDict(frozen=True)

    node_name: str
    call: PreparedCall


class PeerSendRequest(BaseModel):
    """승인 후 "이제 보내 달라".

    ⚠️ `envelope_id` 는 **그 피어가 발급한 것**이다. 일회용이고 TTL 이 있다.
       질문자 노드는 이것을 보관만 하고 내용을 알지 못한다.
    """

    request_id: str
    envelope_id: str
    approved_by: str = Field(pattern=ENTITY_ID_PATTERN)
    asker_node: str = Field(min_length=1, max_length=64)


class PeerAnswer(BaseModel):
    """피어가 돌려주는 답변.

    ⚠️ **재수화된 실제 이름이 들어 있다.** 사내망 안의 사람에게 가는 것이므로
       의도된 동작이지만(설계 §3.6), 그래서 피어 표면에 토큰이 필요하다.
       토큰이 이 응답의 유일한 보호 장치다.

    에스컬레이션은 **그 피어의 인박스**에 만들어진다 — 담당자가 그 컴퓨터에
    있으니 당연하다. 질문자는 "확인 요청이 전달됐다"만 알면 된다.
    """

    model_config = ConfigDict(frozen=True)

    node_name: str
    answer: RehydratedAnswer
    #: 그 노드에서 에스컬레이션이 생겼는가.
    escalated: bool = False
    #: 사람이 읽을 안내. "김책임에게 확인을 요청했습니다" 같은 것.
    escalation_note: str | None = None

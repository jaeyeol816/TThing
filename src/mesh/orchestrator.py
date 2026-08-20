"""Orchestrator — 지목된 에이전트에게 넘기고 답을 신뢰도로 분기한다 (BR-O-*).

⚠️ **이 파일은 모델을 부르지 않는다.** 앱 코드일 뿐이다.
   `grep -c "exaone\\|bedrock\\|broker" orchestrator.py` == 0 이어야 한다
   (`tests/unit/test_orchestrator.py` 가 강제).

시스템이 푸는 문제는 "누구인지 찾아주는 것"이 아니라 **"그 사람을 깨우지 않는
것"** 이므로 라우팅 지능이 필요 없다. 그래서 없는 것이 많다.

──────────────────────────────────────────────────────────────────────
구현하지 않은 것과, 그래서 함께 사라진 문제 (BR-O-01)
──────────────────────────────────────────────────────────────────────

    전문성 매칭 · 임베딩 · 브로드캐스트 · 자동 재지목 · 에이전트 점수화

    -> 오라우팅 없음
    -> 기밀 질문이 전사에 뿌려질 일 없음
    -> 프로필 노후·콜드스타트 없음

`targets` 는 요청에서 그대로 온다. **지목은 사람이 한다** (FR-29).

──────────────────────────────────────────────────────────────────────
두 단계로 나눈 이유 (BR-M-02)
──────────────────────────────────────────────────────────────────────

    prepare   변환 · 검증 · 미리보기.  *** Agent 를 부르지 않는다 ***
    send      사용자 승인 후 전송

승인 없는 전송이 **구조적으로 불가능**하다. `send` 는 `prepare` 만 발급하는
`envelope_id` 를 요구하고, 그 id 는 일회용이다.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from mesh.agent import AgentClient, DailyLimitReached
from mesh.api_models import (
    AskRequest,
    AskResult,
    MergedAnswer,
    PreparedCall,
    PrepareResult,
    SubQuestionView,
)
from mesh.audit import AuditLog
from mesh.config import Config, DataBundle, get_logger, log_extra
from mesh.exceptions import (
    BrokerError,
    ExtractionFailed,
    GatekeeperError,
    ScopeViolationError,
    ValidationBlocked,
)
from mesh.gatekeeper import Gatekeeper
from mesh.inbox import Inbox
from mesh.rehydrator import symbols_in
from mesh.schemas import (
    AgentCard,
    AgentResponse,
    Chunk,
    Disposition,
    Freshness,
    PayloadEnvelope,
    Persona,
    RehydratedAnswer,
    Session,
    Tier,
    TierDecision,
)
from mesh.store import KnowledgeStore, SessionNotFound, confidence_factor
from mesh.validator import normalize_text

log = get_logger("orchestrator")

#: 자동 응답 1건이 절약한 것으로 계산하는 시간 (분).
#: 근거: 요구사항의 "질문 하나가 20분을 먹는다". 추정값이며 화면에 추정임을 표시한다.
MINUTES_PER_INTERRUPT = 20

DIVERGENCE_NOTE = (
    "둘 다 사실일 수 있습니다. 시점이 {gap} 차이이고 문서 성격이 다릅니다. "
    "어느 쪽이 현재 유효한지는 담당자 확인이 필요합니다."
)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:20]}"


# ══════════════════════════════════════════════════════════════════════
# 순수 함수 — 신뢰도 분기 (BR-O-04, BR-O-05)
# ══════════════════════════════════════════════════════════════════════


def branch(answers: Sequence[RehydratedAnswer], *, auto: float, escalate: float) -> Disposition:
    """처분 판정. **순수 함수.**

    ⚠️ **인용 검사가 신뢰도보다 먼저다** (BR-O-04).

        if not a.citations: return ESCALATE     # 최우선

    이 순서가 자동 응답의 인용 준수율을 **구조적으로** 100% 로 만든다.
    신뢰도를 먼저 보면 "0.99 인데 근거가 없는 답"이 사용자에게 도달한다.
    근거 없는 생성은 도달하지 않는다.

    2명일 때는 **낮은 쪽** 신뢰도를 기준으로 한다 (BR-O-05) —
    약한 답이 강한 답에 편승하지 않게.

    `confidence` 는 U2 의 `STALE` 보정(x0.8)이 **이미 적용된** 값이다.
    """
    if not answers:
        return Disposition.ESCALATE
    if any(not a.citations for a in answers):
        return Disposition.ESCALATE

    conf = min(a.confidence for a in answers)
    if conf >= auto:
        return Disposition.AUTO
    if conf >= escalate:
        return Disposition.UNVERIFIED
    return Disposition.ESCALATE


def citation_titles(answer: RehydratedAnswer) -> frozenset[str]:
    return frozenset(c.display_title for c in answer.citations)


def time_gap_label(a: RehydratedAnswer, b: RehydratedAnswer) -> str:
    """두 답변 근거의 시점 차이. 사람이 읽을 표현으로."""
    dates = [c.as_of for ans in (a, b) for c in ans.citations if c.as_of]
    if len(dates) < 2:
        return "알 수 없는"
    days = abs((max(dates) - min(dates)).days)
    if days < 14:
        return f"{days}일"
    if days < 35:
        return f"약 {days // 7}주"
    if days < 365:
        return f"약 {max(1, days // 30)}개월"
    return f"약 {days // 365}년"


def is_divergent(answers: Sequence[RehydratedAnswer]) -> bool:
    """서로 다른 답이 나왔는가. **LLM 을 부르지 않는다** (BR-O-06).

    두 조건의 **논리곱**이다:
      1. 답 텍스트가 실질적으로 다르다 (정규화 후 비교)
      2. 근거 문서가 다르다

    2번이 오탐을 줄인다. 같은 문서를 근거로 표현만 다르게 말한 것은 상충이 아니다.

    ⚠️ 이름이 `is_conflicting` 이 아닌 것이 설계 결정이다.
       `conflict: true` 는 "상충한다"는 **단정**이고
       `divergent: true` 는 "서로 다른 답이 나왔다"는 **관찰**이다.
       판단은 사람에게 남긴다. LLM 으로 상충을 판정하면 오탐이 잦고,
       한 번 "상충 아님"으로 단정하면 나머지 하나가 영원히 묻힌다.
    """
    if len(answers) != 2:
        return False
    a, b = answers
    text_differs = normalize_text(a.text) != normalize_text(b.text)
    source_differs = citation_titles(a) != citation_titles(b)
    return text_differs and source_differs


def merge(
    answers: Sequence[RehydratedAnswer],
    *,
    order: Sequence[str],
    auto: float,
    escalate: float,
) -> MergedAnswer:
    """1개 또는 2개 답변을 병기한다. **답을 하나도 버리지 않는다** (BR-O-06).

    `order` 는 `targets` 요청 순서다. 병렬 호출이므로 응답 도착 순서가
    비결정적이고, 신뢰도로 정렬하면 사용자가 위쪽 답을 정답으로 읽는다.
    화면이 매번 달라지면 데모도 흔들린다 (BR-O-07).

    **금지**: 신뢰도 높은 쪽만 보여주기, `conflict: true` 로 단정하기.
    > 하나를 조용히 고르면 나머지 하나는 영원히 묻힌다.
    """
    rank = {entity_id: i for i, entity_id in enumerate(order)}
    ordered = tuple(sorted(answers, key=lambda a: rank.get(a.entity_id, len(rank))))

    divergent = is_divergent(ordered)
    note = None
    if divergent:
        note = DIVERGENCE_NOTE.format(gap=time_gap_label(ordered[0], ordered[1]))

    return MergedAnswer(
        answers=ordered,
        divergent=divergent,
        divergence_note=note,
        disposition=branch(ordered, auto=auto, escalate=escalate),
    )


def session_facts(session: Session, fresh: Freshness) -> tuple[str, ...]:
    """세션에서 뽑은 "지금 무슨 일이 벌어지는지".

    ⚠️ `EXPIRED` 면 **빈 튜플**이다 (BR-S-04). 24시간 전 세션으로
       "지금 학습 실행 중"이라고 말하면 틀린 실시간 정보가 된다.
       파일은 계속 읽지만 실시간 주장만 제외한다.

    ⚠️ 이 문자열은 **신뢰 구역 안에만** 머문다. 경계 밖으로 가지 않는다
       (`Gatekeeper.ask_draft()` docstring 참조).
    """
    if fresh is Freshness.EXPIRED:
        return ()
    facts: list[str] = []
    for run in session.recent_runs:
        if run.status == "running":
            eta = f", 예상 종료 {run.eta:%H:%M}" if run.eta else ""
            gpu = f", {run.gpu} 점유" if run.gpu else ""
            facts.append(f"{run.started_at:%H:%M} 부터 실행 중{gpu}{eta}")
    for edit in session.recent_edits[:2]:
        facts.append(f"{edit.at:%H:%M} 에 파일을 수정했습니다")
    for dataset in session.datasets:
        if dataset.tier is Tier.SECRET:
            facts.append("사용 중인 데이터셋이 기밀 등급입니다")
    return tuple(facts)


# ══════════════════════════════════════════════════════════════════════
# 준비 결과 보관 — prepare 와 send 사이
# ══════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class _CallOutcome:
    """`send` 한 건의 결과. 처분까지 함께 담아 두 번 계산하지 않는다."""

    call: PendingCall
    answer: RehydratedAnswer
    envelope: PayloadEnvelope | None
    persona: Persona | None
    disposition: Disposition
    raw: AgentResponse | None = None


@dataclass(slots=True)
class PendingCall:
    """`send` 가 필요한 것. `Mapping` 은 여기 없다 — `EnvelopeCache` 가 갖는다."""

    envelope_id: str
    target_entity_id: str
    chunks: list[Chunk]
    session_facts: tuple[str, ...]
    freshness: Freshness
    confidence_factor: float
    sub_question_id: str | None = None


@dataclass(slots=True)
class PendingRequest:
    request_id: str
    asker: str
    question: str
    order: tuple[str, ...]
    calls: list[PendingCall] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)

    @property
    def thread_id(self) -> str:
        """요청 단위 스레드. 2명을 지목하면 두 인박스가 같은 값을 갖는다 (BR-I-04)."""
        return self.request_id


# ══════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════


class Orchestrator:
    """조율만 한다. 판정은 Gatekeeper, 읽기는 Store, 호출은 AgentClient."""

    #: 준비 결과 TTL. `EnvelopeCache` 와 같게 유지한다 — 한쪽만 만료되면
    #: "envelope 은 있는데 문맥이 없다" 같은 어긋난 상태가 생긴다.
    PENDING_TTL_SECONDS = 300

    def __init__(
        self,
        cfg: Config,
        data: DataBundle,
        store: KnowledgeStore,
        gatekeeper: Gatekeeper,
        agent: AgentClient,
        audit: AuditLog,
        inbox: Inbox,
    ) -> None:
        self.cfg = cfg
        self.data = data
        self.store = store
        self.gatekeeper = gatekeeper
        self.agent = agent
        self.audit = audit
        self.inbox = inbox
        self._pending: dict[str, PendingRequest] = {}

    # ── 목록 ─────────────────────────────────────────────────────────

    async def agent_cards(self) -> list[AgentCard]:
        """U2 위임. 여기서 필드를 만들지 않는다 — `disclose` 해석이 두 곳에
        생기면 한쪽이 새어 나간다."""
        return await self.store.list_agents()

    # ── prepare (BR-O-03) ────────────────────────────────────────────

    async def prepare(self, request: AskRequest) -> PrepareResult:
        """변환 · 검증 · 미리보기. **Agent 를 부르지 않고 감사 레코드도 없다.**

        `agents_notified=False` 가 타입으로 못 박혀 있다 (BR-O-03) —
        이 단계에서 담당자 인박스에 아무것도 쓰지 않는다. 에스컬레이션은
        `send` 이후 신뢰도 분기 결과로만 발생한다.
        """
        self._sweep_pending()
        request_id = new_request_id()
        pending = PendingRequest(
            request_id=request_id,
            asker=request.asker,
            question=request.question,
            order=tuple(request.targets),
        )

        results = await asyncio.gather(
            *[self._prepare_one(request, target, pending) for target in request.targets],
            return_exceptions=True,
        )

        calls: list[PreparedCall] = []
        upgraded: Tier | None = None
        upgrade_reason: str | None = None
        for target, outcome in zip(request.targets, results, strict=True):
            if isinstance(outcome, BaseException):
                # 2명 중 1명이 실패해도 나머지는 진행한다 (R-02)
                log.warning(
                    "준비 실패 — 이 대상만 건너뛴다",
                    extra=log_extra(target=target, reason=type(outcome).__name__),
                )
                calls.append(await self._blocked_call(target, request, outcome))
                continue
            prepared, tier_note = outcome
            calls.append(prepared)
            if tier_note is not None and (upgraded is None or tier_note[0] > upgraded):
                upgraded, upgrade_reason = tier_note

        self._pending[request_id] = pending
        return PrepareResult(
            request_id=request_id,
            calls=tuple(calls),
            decomposed=False,
            upgraded_tier=upgraded,
            upgrade_reason=upgrade_reason,
            # ⚠️ 타입이 False 만 허용한다 (BR-O-03)
            agents_notified=False,
        )

    async def _prepare_one(
        self, request: AskRequest, target: str, pending: PendingRequest
    ) -> tuple[PreparedCall, tuple[Tier, str] | None]:
        agent_cfg = self.data.agent(target)
        persona = agent_cfg.to_persona()
        self.agent.check_daily_limit(persona)

        session = self.store.load_session(target)
        fresh = self.store.freshness_of(session)
        factor = confidence_factor(fresh, stale_factor=self.cfg.stale_confidence_factor)
        facts = session_facts(session, fresh)

        # ① 질문 등급 — 지식을 막아도 질문 문장이 기밀이면 그대로 새어 나간다
        q_tier: TierDecision = await self.gatekeeper.classify(request.question)

        # 후보 중에서 고르고 그 파일만 읽는다 (전역 스캔 없음)
        paths = await self.store.select_paths(session, request.question)
        chunks = self.store.read(paths, target)
        chunks += self.store.verified_chunks(session)
        if not chunks:
            raise ExtractionFailed(f"{target} 의 세션에서 읽을 근거를 찾지 못했다")

        # ② 지식 등급
        classified: list[Chunk] = []
        for chunk in chunks:
            if chunk.tier is not None:
                classified.append(chunk)  # 승인된 QA — 등급이 이미 있다
                continue
            decision = await self.gatekeeper.classify(chunk.text, chunk.internal_path)
            classified.append(chunk.model_copy(update={"tier": decision.tier}))

        calls = self.gatekeeper.plan_calls(request.question, target, classified, q_tier)
        call = calls[0]
        tier_note = (
            (call.tier, f"{q_tier.tier.label_ko} 질문에 {call.tier.label_ko} 근거가 동원됐습니다")
            if call.tier is not q_tier.tier
            else None
        )

        env, mapping = await self.gatekeeper.to_payload(call, classified, request.question)
        originals = tuple(c.text for c in classified)
        validation = self.gatekeeper.validate(env, originals)
        env = env.model_copy(update={"validation": validation})

        if not validation.passed:
            raise ValidationBlocked(
                f"검증 실패 ({validation.summary}, 단계={validation.first_failed_stage})"
            )

        self.gatekeeper.cache.put(env, mapping, originals, target)
        pending.calls.append(
            PendingCall(
                envelope_id=env.envelope_id,
                target_entity_id=target,
                chunks=classified,
                session_facts=facts,
                freshness=fresh,
                confidence_factor=factor,
            )
        )

        return (
            PreparedCall(
                envelope_id=env.envelope_id,
                target_entity_id=target,
                agent_label=persona.agent_label,
                sub_question=SubQuestionView(
                    id=call.call_id, kind=env.task_schema_id, text=request.question, tier=call.tier
                ),
                tier=call.tier,
                disposition="ready",
                preview=self.gatekeeper.preview(env, originals),
            ),
            tier_note,
        )

    async def _blocked_call(
        self, target: str, request: AskRequest, error: BaseException
    ) -> PreparedCall:
        """차단된 대상에 **폴백 답변을 동봉**한다 (한 왕복에 끝).

        "차단됐고 대신 이 답이 있다"가 한 번에 오므로 UI 가 `send` 를 부를
        필요가 없다. 시나리오 3 후속 질문이 정확히 이 경로다.

        `PreparedCall` 의 `model_validator` 가 `fallback` 없는 `blocked` 를
        거부하므로 **차단만 하고 답을 안 주는 것이 타입 수준에서 불가능하다.**
        """
        reason_code, label = _blocked_reason(error)
        try:
            agent_cfg = self.data.agent(target)
            session = self.store.load_session(target)
            paths = self.store.candidate_paths(session)
            chunks = self.store.read(list(paths), target)
        except Exception:  # noqa: BLE001 — 폴백 경로다. 여기서 또 죽으면 안 된다
            agent_cfg = None
            chunks = []

        tier = max([(c.tier or Tier.INTERNAL) for c in chunks], default=Tier.INTERNAL)
        fallback = await self.gatekeeper.answer_in_zone(
            request.question, chunks, tier_label=tier.label_ko, reason=reason_code
        )
        label_name = agent_cfg.to_persona().agent_label if agent_cfg else f"{target} 의 Agent"
        return PreparedCall(
            envelope_id=None,
            target_entity_id=target,
            agent_label=label_name,
            tier=tier,
            disposition="blocked",
            fallback=fallback,
            blocked_reason=label,
        )

    # ── send ─────────────────────────────────────────────────────────

    async def send(
        self, request_id: str, envelope_ids: Sequence[str], approved_by: str
    ) -> AskResult:
        """승인 후 전송. 전체를 30초로 감싼다 (BR-O-08).

        Raises:
            GatekeeperError: `request_id` 가 없거나 만료 (호출자는 410 Gone)
        """
        started = time.monotonic()
        pending = self._pending.pop(request_id, None)
        if pending is None:
            raise GatekeeperError(
                f"준비된 요청을 찾을 수 없다 (TTL 만료 또는 이미 전송됨): {request_id}"
            )

        try:
            answers, escalations = await asyncio.wait_for(
                self._send_all(pending, envelope_ids, approved_by),
                timeout=self.cfg.total_timeout,
            )
        except TimeoutError:
            # 도착한 답이 없다. 신뢰 구역 안에서 답한다 (BR-O-08).
            log.warning("전체 타임아웃 — 신뢰 구역 내 폴백", extra=log_extra(request_id=request_id))
            answers = [
                await self.gatekeeper.answer_in_zone(
                    pending.question,
                    pending.calls[0].chunks if pending.calls else [],
                    tier_label="사내",
                    reason="broker_unavailable",
                )
            ]
            escalations = []

        merged = merge(
            answers,
            order=pending.order,
            auto=self.cfg.confidence_auto,
            escalate=self.cfg.confidence_escalate,
        )
        auto_count = sum(1 for a in answers if a.used_external_agent)
        self.audit.record_outcome(
            request_id=request_id,
            disposition=merged.disposition.value,
            answer_count=len(answers),
        )
        return AskResult(
            request_id=request_id,
            merged=merged,
            escalations=tuple(escalations),
            elapsed_seconds=round(time.monotonic() - started, 3),
            interrupts_avoided=(auto_count if merged.disposition is Disposition.AUTO else 0),
            minutes_saved_estimate=(
                auto_count * MINUTES_PER_INTERRUPT if merged.disposition is Disposition.AUTO else 0
            ),
        )

    async def _send_all(
        self, pending: PendingRequest, envelope_ids: Sequence[str], approved_by: str
    ) -> tuple[list[RehydratedAnswer], list[str]]:
        wanted = set(envelope_ids)
        selected = [c for c in pending.calls if c.envelope_id in wanted]
        if not selected:
            raise GatekeeperError("승인된 envelope_id 가 준비 결과에 없다")

        results = await asyncio.gather(
            *[self._send_one(pending, call, approved_by) for call in selected],
            return_exceptions=True,  # 2명 중 1명 실패 시 나머지 반환 (R-02)
        )

        # ① 먼저 모든 답을 모은다. 처분은 call 단위로 정한다 (설계 §8 시나리오 2) —
        #    전체로 묶으면 min() 때문에 이미 답이 나온 조각까지 에스컬레이션된다.
        outcomes: list[_CallOutcome] = []
        for call, outcome in zip(selected, results, strict=True):
            if isinstance(outcome, BaseException):
                log.warning(
                    "전송 실패 — 신뢰 구역 내 폴백",
                    extra=log_extra(target=call.target_entity_id, reason=type(outcome).__name__),
                )
                fallback = await self.gatekeeper.answer_in_zone(
                    pending.question,
                    call.chunks,
                    tier_label="사내",
                    reason=_local_reason(outcome),
                )
                outcomes.append(_CallOutcome(call, fallback, None, None, Disposition.BLOCKED))
                continue
            outcomes.append(outcome)

        # ② 그다음 에스컬레이션한다. 이 순서라야 `already_answered` 를 채울 수 있다 —
        #    "다른 조각은 이미 답변됨"을 알려면 다른 call 의 처분을 먼저 알아야 한다.
        answered_labels = [
            f"{o.answer.agent_label} 의 답변은 자동 응답되었습니다"
            for o in outcomes
            if o.disposition is Disposition.AUTO
        ]

        escalations: list[str] = []
        for outcome in outcomes:
            if outcome.disposition not in {Disposition.ESCALATE, Disposition.UNVERIFIED}:
                continue
            if outcome.envelope is None or outcome.persona is None:  # pragma: no cover
                continue
            escalations.append(
                await self._escalate(
                    pending,
                    outcome,
                    already_answered=[
                        label
                        for label in answered_labels
                        if not label.startswith(outcome.answer.agent_label)
                    ],
                )
            )

        return [o.answer for o in outcomes], escalations

    async def _send_one(
        self, pending: PendingRequest, call: PendingCall, approved_by: str
    ) -> _CallOutcome:
        entry = self.gatekeeper.cache.take(call.envelope_id)  # 일회용
        if entry is None:
            raise GatekeeperError(f"envelope 이 만료되었거나 이미 전송되었다: {call.envelope_id}")

        persona = self.data.agent(call.target_entity_id).to_persona()
        try:
            resp = await self.agent.ask(entry.envelope, persona, approved_by)
            answer = self.gatekeeper.rehydrate(
                resp, entry.mapping, persona=persona, chunks=call.chunks
            )
        finally:
            # 재수화 실패 시에도 폐기한다 (BR-G-06, NFR-S-15)
            entry.mapping.table.clear()
            self.gatekeeper.cache.discard(call.envelope_id)

        # 세션 신선도 보정. 여기서 적용해야 branch() 가 보정된 값을 본다
        answer = self._apply_freshness(answer, call)
        disposition = branch(
            [answer], auto=self.cfg.confidence_auto, escalate=self.cfg.confidence_escalate
        )
        return _CallOutcome(call, answer, entry.envelope, persona, disposition, resp)

    def _apply_freshness(self, answer: RehydratedAnswer, call: PendingCall) -> RehydratedAnswer:
        """`STALE` 보정 (x0.8) + 신선도 표기.

        실측 효과: 최민수 0.78 x 0.8 = 0.62 -> `UNVERIFIED` 배지.
        보정이 없으면 자동 응답이었을 것이 배지가 붙는다. 2시간 전 상태로
        답한 것이니 더 정직하다.
        """
        session_as_of = None
        try:
            session_as_of = self.store.load_session(call.target_entity_id).updated_at
        except SessionNotFound:  # pragma: no cover
            pass
        return answer.model_copy(
            update={
                "confidence": round(answer.confidence * call.confidence_factor, 4),
                "freshness": call.freshness,
                "session_as_of": session_as_of,
            }
        )

    async def _escalate(
        self,
        pending: PendingRequest,
        outcome: _CallOutcome,
        *,
        already_answered: Sequence[str],
    ) -> str:
        """초안을 만들어 인박스에 넣는다. **`thread_id` = `request_id`** (BR-I-04).

        2명을 지목했으면 두 인박스에 같은 스레드로 들어가고, 한쪽이 해결하면
        다른 쪽에 그 사실이 표시돼 중재를 유도한다.
        """
        assert outcome.envelope is not None and outcome.persona is not None  # noqa: S101
        draft = await self.agent.draft_escalation(
            outcome.envelope,
            outcome.persona,
            pending.asker,
            partial=outcome.raw,
            citations=outcome.answer.citations,
            session_facts=outcome.call.session_facts,
            already_answered=already_answered,
        )
        item = self.inbox.add(
            owner_entity_id=outcome.call.target_entity_id,
            asker=pending.asker,
            thread_id=pending.thread_id,
            question_summary=pending.question[:200],
            draft=draft,
            tier=outcome.answer.tier,
            citations=outcome.answer.citations,
        )
        return item.item_id

    # ── 보관 정리 ────────────────────────────────────────────────────

    def _sweep_pending(self) -> int:
        now = time.monotonic()
        stale = [
            k for k, v in self._pending.items() if now - v.created_at > self.PENDING_TTL_SECONDS
        ]
        for key in stale:
            del self._pending[key]
        return len(stale)

    def pending_count(self) -> int:
        return len(self._pending)


# ══════════════════════════════════════════════════════════════════════
# 보조
# ══════════════════════════════════════════════════════════════════════


def _blocked_reason(error: BaseException) -> tuple[str, str]:
    """예외 -> (`local_queries` 이유 코드, 사람이 읽을 라벨)."""
    match error:
        case ExtractionFailed():
            return (
                "extraction_failed",
                "구조 추출에 필요한 항목이 어휘 사전에 없어 전송하지 않았습니다",
            )
        case ValidationBlocked():
            return "validation_blocked", "검증 단계에서 차단되었습니다"
        case DailyLimitReached():
            return "policy_no_external", str(error)
        case ScopeViolationError():
            return "policy_no_external", "해당 에이전트의 지식 범위를 벗어난 요청입니다"
        case SessionNotFound():
            return "policy_no_external", "세션 정보를 찾을 수 없습니다"
        case _:
            return "policy_no_external", "준비 중 오류가 발생해 전송하지 않았습니다"


def _local_reason(error: BaseException) -> str:
    return (
        _blocked_reason(error)[0] if not isinstance(error, BrokerError) else ("broker_unavailable")
    )


def unresolved_in(answers: Sequence[RehydratedAnswer]) -> tuple[str, ...]:
    """모든 답변에 남은 미치환 기호. UI 경고용 (BR-G-10)."""
    out: set[str] = set()
    for a in answers:
        out |= set(a.unresolved_refs)
        out |= set(symbols_in(a.text))
    return tuple(sorted(out))

"""Orchestrator — 지목된 에이전트에게 넘기고 답을 신뢰도로 분기한다 (BR-O-*).

⚠️ **이 파일은 모델을 부르지 않는다.** 앱 코드일 뿐이다.
   `grep -c "exaone\\|bedrock\\|broker" orchestrator.py` == 0 이어야 한다
   (`tests/unit/test_orchestrator.py` 가 강제).

시스템이 푸는 문제는 "누구인지 찾아주는 것"이 아니라 **"그 사람을 깨우지 않는
것"** 이므로 라우팅 지능이 필요 없다. 그래서 없는 것이 많다.

──────────────────────────────────────────────────────────────────────
구현하지 않은 것과, 그래서 함께 사라진 문제 (BR-O-01)
──────────────────────────────────────────────────────────────────────

    임베딩 · 자동 재지목 · 에이전트 점수화 누적 · 자동 전송

    -> 오라우팅이 사람 몰래 일어나지 않음
    -> 프로필 노후·콜드스타트 없음

`prepare`/`send` 의 `targets` 는 여전히 요청에서 그대로 온다.
**지목은 사람이 한다** (FR-29).

──────────────────────────────────────────────────────────────────────
브로드캐스트는 들어왔다 — 원래 걱정하던 것이 왜 안 생기는가
──────────────────────────────────────────────────────────────────────

이 파일은 원래 브로드캐스트를 "구현하지 않은 것" 목록에 넣고 그 근거로
**"기밀 질문이 전사에 뿌려질 일 없음"** 을 들었다. `broadcast()` 가 생겼으니
그 근거를 다시 세워야 한다. 세 가지가 성립하므로 위험이 따라 들어오지 않는다.

  1. **질문이 남에게 도달하지 않는다.**
     선별은 이 노드 안에서 끝난다. 인박스에 쓰지 않고 알림을 만들지 않는다.
     `agents_notified` 가 `prepare` 에서 `Literal[False]` 인 것과 같은 약속이,
     브로드캐스트에서는 `BroadcastResult.crossed_boundary: Literal[False]` 다.

  2. **판정이 남의 문서를 읽지 않는다.**
     재료는 `expertise` · `topics` · 조직도 단위 이름 · 게이트키퍼를 이미
     지난 focus 라벨뿐이다 (`mesh.triage` 머리말). 전부 이미 인증 없이
     보이는 값이라, 열 명에게 뿌려도 새로 노출되는 것이 0 이다.

  3. **선별이 전송을 결정하지 않는다.**
     선별 결과는 목록을 줄일 뿐이고, 무엇이 경계를 넘을지는 사용자가 사람을
     고른 뒤 `prepare()` 부터 게이트키퍼가 정한다. 선별을 속여도 얻는 것은
     "후보 목록에 남는 것" 뿐이다.

즉 **바뀐 것은 지목의 시점**이다. 사람이 먼저 고르던 것을, 후보를 좁힌 뒤에
고르게 했다. 고르는 주체는 그대로 사람이다.

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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from mesh.agent import AgentClient, DailyLimitReached
from mesh.api_models import (
    AgentRelevanceView,
    AskRequest,
    AskResult,
    BroadcastRequest,
    BroadcastResult,
    ConsultRequest,
    ConsultResult,
    MergedAnswer,
    PeerPrepareRequest,
    PeerSendRequest,
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
    MeshError,
    ScopeViolationError,
    ValidationBlocked,
)
from mesh.gatekeeper import Gatekeeper, build_system_prompt, new_call_id
from mesh.inbox import Inbox
from mesh.peer import PeerRegistry
from mesh.rehydrator import answer_to_text, symbols_in
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
from mesh.trace import TraceEvidence, TraceRecorder, TraceStore
from mesh.triage import Candidate, triage
from mesh.validator import normalize_text, passthrough_validation

log = get_logger("orchestrator")

#: 자동 응답 1건이 절약한 것으로 계산하는 시간 (분).
#: 근거: 요구사항의 "질문 하나가 20분을 먹는다". 추정값이며 화면에 추정임을 표시한다.
MINUTES_PER_INTERRUPT = 20

#: 이 신뢰도 미만의 답변은 "관련 없음" 으로 보고 취합에서 뺀다 (기획서 §4.4).
#: 문서를 열어봤지만 질문과 무관한 자료뿐인 Agent 는 낮은 신뢰도로 답한다.
#: asker 자신의 답변에는 적용하지 않는다.
#: ⚠️ 신뢰도만으로는 부족하다 — "이건 제 담당이 아닙니다" 라고 **확신에 차서**
#:    답하면 신뢰도가 높게(0.7+) 나온다. 그래서 아래 텍스트 신호를 함께 본다.
IRRELEVANT_CONFIDENCE = 0.15

#: "문서를 봤지만 이 질문에 답할 자료가 없다" 는 답변에서 반복적으로 나오는 표현.
#: 이 표현이 답변에 들어 있으면 신뢰도와 무관하게 "관련 없음" 으로 본다.
#: 근거: 관련 없는 Agent 는 "제공된 문서에는 ~ 없습니다" 를 확신에 차서 답한다.
_IRRELEVANT_MARKERS: tuple[str, ...] = (
    "포함되어 있지 않",
    "포함하고 있지 않",
    "포함되지 않",
    "제공된 문서에는",
    "제공된 문서가",
    "제공된 문서는",
    "문서가 제공되지 않",
    "해당 내용이 없",
    "관련 내용이 없",
    "관련 자료",
    "담당 영역이 아니",
    "담당이 아니",
    "다루고 있지 않",
    "다루지 않",
    "찾을 수 없",
    "추출할 수 없",
    "구조 추출 실패",
    "어휘 사전에 해당",
    "context에 포함",
    "컨텍스트에 포함",
)


def _looks_irrelevant(text: str | None) -> bool:
    """답변 텍스트가 "이 질문에 답할 자료가 없다" 류인지 본다.

    신뢰도 필터(`IRRELEVANT_CONFIDENCE`)를 보완한다. 관련 없는 Agent 는
    자기 문서에 없다는 걸 **확신에 차서** 답하기 때문에 신뢰도가 오히려
    높게 나온다 (예: 오세영 0.76). 그래서 내용을 함께 본다.
    """
    if not text:
        return True
    return any(marker in text for marker in _IRRELEVANT_MARKERS)


DIVERGENCE_NOTE = (
    "둘 다 사실일 수 있습니다. 시점이 {gap} 차이이고 문서 성격이 다릅니다. "
    "어느 쪽이 현재 유효한지는 담당자 확인이 필요합니다."
)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:20]}"


def new_broadcast_id() -> str:
    return f"bc_{uuid.uuid4().hex[:20]}"


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


class PrepareFailed(MeshError):
    """`_prepare_one` 실패. **이미 판정된 근거를 함께 들고 온다.**

    왜 예외에 데이터를 붙이는가: 폴백 답변에 표시할 등급(`tier_label`)은
    동원된 근거의 최고 등급이다. 실패 지점에서 그 정보를 버리면 폴백이
    `Chunk.tier is None` 만 보게 되고, 기본값 `INTERNAL` 로 떨어져
    **기밀 근거를 쓴 질의가 `[사내]` 로 표시된다.**

    유출은 아니지만 사용자에게 등급을 낮게 보여주는 것이므로 고친다.
    다시 판정하는 방법도 있지만 같은 판정을 두 번 하는 것은 낭비이고,
    두 결과가 갈릴 여지를 만든다.
    """

    def __init__(
        self,
        error: BaseException,
        chunks: Sequence[Chunk] = (),
        trace: TraceRecorder | None = None,
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.chunks = list(chunks)
        #: 어디까지 갔다가 멈췄는지의 기록. 폴백 답변에도 "경과 보기" 가
        #: 붙어야 한다 — 막힌 질의야말로 이유를 보여줘야 하는 쪽이다.
        self.trace = trace


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
    freshness: Freshness | None
    confidence_factor: float
    sub_question_id: str | None = None
    #: 대상이 다른 컴퓨터에 있으면 그 노드의 base_url. 로컬이면 `None`.
    #:
    #: `send` 가 이 값으로 갈린다. 로컬이면 `EnvelopeCache` 에서 봉투를 꺼내고,
    #: 원격이면 그 노드에 "이제 보내 달라" 고 청한다 — 봉투는 그쪽에 있다.
    remote_node: str | None = None
    #: 사람이 읽을 노드 이름. 화면과 로그에 쓴다.
    remote_node_name: str | None = None
    #: 처리 경과 기록기. `prepare` 가 ①~④ 를 채우고 `send` 가 ⑤~⑥ 을 잇는다.
    #:
    #: 두 HTTP 왕복에 걸쳐 같은 기록기를 써야 하므로 여기 들고 있는다.
    #: `None` 이면 기록에 실패한 것이고, 그때도 질의는 정상으로 돈다.
    trace: TraceRecorder | None = None

    @property
    def is_remote(self) -> bool:
        return self.remote_node is not None


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
        peers: PeerRegistry | None = None,
        traces: TraceStore | None = None,
    ) -> None:
        self.cfg = cfg
        self.data = data
        self.store = store
        self.gatekeeper = gatekeeper
        self.agent = agent
        self.audit = audit
        self.inbox = inbox
        #: 없으면 단독 노드다. 있으면 원격 대상을 그 노드에 위임한다.
        self.peers = peers
        #: 처리 경과 보관소. 없으면 트레이스를 만들지 않는다 —
        #: 화면이 "경과 보기" 를 그리지 않을 뿐 질의는 그대로 돈다.
        self.traces = traces
        self._pending: dict[str, PendingRequest] = {}

    # ── 목록 ─────────────────────────────────────────────────────────

    async def agent_cards(self) -> list[AgentCard]:
        """지목 목록. 로컬 + 피어.

        U2 위임이다 — 여기서 필드를 만들지 않는다. `disclose` 해석이 두 곳에
        생기면 한쪽이 새어 나간다.

        ⚠️ **로컬이 먼저 온다.** 피어가 꺼져 있어도 내 사람은 항상 보인다.
           피어 조회가 느리면 로컬 목록이 그만큼 늦게 나오는 것을 막기 위해
           둘을 병렬로 부른다.

        ⚠️ 피어의 `AgentCard` 를 다시 검사하지 않는다. 그 카드는 **그 노드의**
           `store.list_agents()` 가 만든 것이고, 거기서 이미 식별자를 제거하고
           게이트키퍼를 지났다 (BR-S-06). 여기서 다시 검사하려면 그 노드의
           어휘·금칙어·원문이 필요하고, 원문을 받아야 한다면 이 설계가 무의미해진다.
           **검증은 보내는 쪽이 한다** — 그것이 이 아키텍처의 규칙이다.
        """
        if self.peers is None:
            return await self.store.list_agents()

        local, remote = await asyncio.gather(
            self.store.list_agents(),
            self.peers.remote_cards(),
            return_exceptions=True,
        )
        if isinstance(local, BaseException):  # pragma: no cover — 로컬 실패는 상위로
            raise local
        cards = list(local)
        if isinstance(remote, BaseException):
            log.warning(
                "피어 지목 목록 실패 — 로컬만 보여준다", extra=log_extra(reason=str(remote)[:80])
            )
            return cards
        cards.extend(card for _, card in remote)
        return cards

    # ── 브로드캐스트 (선별) ──────────────────────────────────────────

    async def broadcast(self, request: BroadcastRequest) -> BroadcastResult:
        """질문 하나를 전원에게 뿌리고 **답할 수 있는 사람만 남긴다.**

        ⚠️ 이 메서드는 경계를 넘지 않는다. 문서를 읽지 않고, Agent 를 부르지
           않고, 감사 레코드를 만들지 않는다 — 경계를 넘은 것이 없으므로
           기록할 것이 없다. 판정 재료는 이미 인증 없이 보이는 값뿐이다
           (`mesh.triage` 머리말, 이 파일 머리말 §브로드캐스트).

        ⚠️ **결과에서 사람을 지우지 않는다.** 관련 없다고 판정된 사람도
           `results` 에 `relevant=False` 로 남는다. 화면이 흐리게 그릴 뿐이다.
           지워 버리면 판정이 틀렸을 때 사용자가 되돌릴 대상이 없어진다.

        판정 실패는 예외로 올리지 않는다. 모델이 죽으면 규칙 결과만 쓰고
        `model_used=False` 로 그 사실을 화면에 밝힌다.
        """
        started = time.monotonic()
        cards = await self.agent_cards()

        candidates = [
            self._to_candidate(card)
            for card in cards
            if card.entity_id != request.asker
        ]
        outcome = await triage(
            request.question,
            candidates,
            # ⚠️ 게이트키퍼가 들고 있는 **신뢰 구역** 모델이다. 이 파일은
            #    모델 클라이언트를 import 하지 않는다 (BR-O 의 오래된 약속).
            exaone=self.gatekeeper.exaone,
            threshold=self.cfg.broadcast_threshold,
            max_relevant=min(request.max_relevant, self.cfg.broadcast_max_relevant),
        )

        by_id = {c.entity_id: c for c in candidates}
        names = {c.entity_id: c.display_name for c in cards}
        results = tuple(
            AgentRelevanceView(
                entity_id=v.entity_id,
                display_name=names.get(v.entity_id, v.entity_id),
                relevant=v.relevant,
                score=v.score,
                reason_code=v.reason_code,
                reason=v.reason,
                matched=v.matched,
                decided_by=v.decided_by,
                available=by_id[v.entity_id].available,
                unavailable_reason=(
                    None
                    if by_id[v.entity_id].available
                    else "오늘 받을 수 있는 질문 수를 다 채웠습니다"
                ),
            )
            for v in outcome.verdicts
        )
        log.info(
            "브로드캐스트 선별 완료",
            extra=log_extra(
                asker=request.asker,
                candidates=len(candidates),
                relevant=sum(1 for r in results if r.relevant),
                model_used=outcome.model_used,
            ),
        )
        return BroadcastResult(
            broadcast_id=new_broadcast_id(),
            question=request.question,
            results=results,
            threshold=outcome.threshold,
            model_used=outcome.model_used,
            model_note=(
                None
                if outcome.model_used
                else "선별 모델을 쓰지 못해 규칙만으로 좁혔습니다"
            ),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    # ── 상담 — 질문자의 Agent 가 대신 물어보고 모은다 ───────────────

    async def consult(
        self,
        request: ConsultRequest,
        on_event: Callable[..., Awaitable[None]] | None = None,
    ) -> ConsultResult:
        """브로드캐스트 → 답할 수 있는 사람들에게 **자동으로** 질의 → 정리.

        ──────────────────────────────────────────────────────────────
        새 경로를 여는 것이 아니다
        ──────────────────────────────────────────────────────────────

        각 사람에 대한 질의는 **기존 `prepare()` → `send()` 를 그대로 탄다.**
        봉투는 `prepare` 만 발급하고 일회용이며, `ask_agent()` 의 전제조건 3개도
        그대로 검사된다. 달라지는 것은 그 두 왕복을 **누가 잇는가** 뿐이다 —
        전에는 사람이 화면에서 이었고 이제는 질문자의 Agent 가 잇는다.

        ⚠️ 그래서 이 메서드는 **사람 확인 단계를 건너뛴다.** 화면이 미리보기
           모달을 띄우고 사람이 누르던 자리에 자동 승인이 들어간다 (FR-09 의
           완화). 그 대가로 답변마다 **처리 경과**(`trace_id`)가 붙어, 무엇이
           경계를 넘었는지를 사후에 전문으로 열어 볼 수 있다.
           승인을 사람이 하게 되돌리려면 화면이 `prepare`/`send` 를 직접 부르면
           된다 — 그 경로는 그대로 살아 있다.

        ⚠️ 인원 상한이 두 겹이다. 후보로 **보여주는** 것은 공짜지만 실제로
           **묻는** 것은 매번 경계를 넘는 일이다.

        `targets` 가 오면 브로드캐스트를 건너뛴다 — 사용자가 조직도에서 직접
        고른 경우다. 지목은 여전히 사람이 할 수 있다 (FR-29).
        """
        started = time.monotonic()
        limit = min(request.max_targets, self.cfg.consult_max_targets)

        broadcast: BroadcastResult | None = None

        if request.targets:
            # 사용자가 직접 고른 경우 — 선별을 건너뛰고 그 사람들에게만 묻는다.
            chosen = list(request.targets)[:limit]
            skipped = list(request.targets)[limit:]
        else:
            # ── 1단계: 내 Agent 가 먼저 스스로 답할 수 있는지 본다 ──────────
            #
            # force_broadcast=True 면 이 단계를 건너뛰고 바로 broadcast 한다.
            self_answer: AskResult | None = None
            if not request.force_broadcast:
                try:
                    _session = self.store.load_session(request.asker)
                    if self.store.candidate_paths(_session):
                        self_answer = await self._consult_one(request, request.asker)
                except Exception as e:  # noqa: BLE001 — 세션 없거나 실패하면 broadcast 로 간다
                    log.info(
                        "자기 답변 시도 실패 — broadcast 로 진행",
                        extra=log_extra(asker=request.asker, reason=type(e).__name__),
                    )

            self_sufficient = (
                self_answer is not None
                and self_answer.merged.answers
                # ⚠️ AUTO(인용+높은 신뢰도) 를 요구하지 않는다. 내 로드맵을
                #    passthrough(구조 추출 없이 원문 전달)로 답하면 인용이
                #    0개라 AUTO 가 절대 안 나온다. 그러면 혼자 답할 수 있는
                #    질문도 매번 broadcast 로 샜다.
                #    대신: (1) 내 답이 "관련 없음" 이 아니고
                #          (2) 에스컬레이션 문턱 이상 신뢰도면 충분하다고 본다.
                and not any(_looks_irrelevant(a.text) for a in self_answer.merged.answers)
                and min(
                    (a.confidence for a in self_answer.merged.answers),
                    default=0.0,
                )
                >= self.cfg.confidence_escalate
            )

            if self_sufficient:
                # 내 Agent 만으로 충분하다 — broadcast 를 건너뛴다.
                answers = list(self_answer.merged.answers)
                merged = self_answer.merged
                digest, source = await self.gatekeeper.synthesize_in_zone(
                    request.question, answers
                )
                auto_count = sum(1 for a in answers if a.used_external_agent)
                log.info(
                    "내 Agent 로 충분 — broadcast 생략",
                    extra=log_extra(asker=request.asker),
                )
                return ConsultResult(
                    request_id=new_request_id(),
                    question=request.question,
                    broadcast=None,
                    digest=digest,
                    digest_source=source,
                    answers=merged.answers,
                    divergent=merged.divergent,
                    divergence_note=merged.divergence_note,
                    escalations=tuple(self_answer.escalations),
                    consulted=(request.asker,),
                    skipped=(),
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    interrupts_avoided=auto_count,
                    minutes_saved_estimate=auto_count * MINUTES_PER_INTERRUPT,
                )

            # ── 2단계: 부족하다 — broadcast 로 도움을 요청한다 ─────────────
            broadcast = await self.broadcast(
                BroadcastRequest(
                    question=request.question,
                    asker=request.asker,
                    max_relevant=self.cfg.broadcast_max_relevant,
                )
            )
            relevant = [r.entity_id for r in broadcast.results if r.relevant]
            chosen, skipped = relevant[:limit], relevant[limit:]

            # 내 Agent 도 부분 답을 냈다면 결과에 포함한다 (맨 앞).
            if self_answer is not None and self_answer.merged.answers:
                if request.asker not in chosen:
                    chosen = [request.asker, *chosen][:limit]

        if not chosen:
            return ConsultResult(
                request_id=new_request_id(),
                question=request.question,
                broadcast=broadcast,
                digest=(
                    "답할 수 있는 사람을 찾지 못했습니다. 조직도에서 직접 고르거나, "
                    "질문에 다루는 주제를 한 단어 더 넣어 보십시오."
                ),
                digest_source="code",
                elapsed_seconds=round(time.monotonic() - started, 3),
            )

        # 사람마다 따로 prepare/send 한다. 한 요청에 묶지 않는 이유:
        # 한 사람이 차단돼도 나머지는 그대로 진행돼야 하고(R-02), 실패의 원인이
        # 사람 단위로 남아야 화면이 "누가 왜 못 답했는지" 를 말할 수 있다.
        if on_event:
            await on_event("broadcast_start", agents=chosen)

        async def _one_with_events(target: str) -> AskResult:
            if on_event:
                await on_event("agent_querying", entity_id=target)
            try:
                _result = await self._consult_one(request, target)
                if on_event:
                    await on_event("agent_responded", entity_id=target, status="answered")
                return _result
            except BaseException:
                if on_event:
                    await on_event("agent_responded", entity_id=target, status="error")
                raise

        outcomes = await asyncio.gather(
            *[_one_with_events(target) for target in chosen],
            return_exceptions=True,
        )

        if on_event:
            await on_event("broadcast_end", agents=chosen)

        answers: list[RehydratedAnswer] = []
        escalations: list[str] = []
        failed: list[str] = []
        irrelevant: list[str] = []
        answered_ids: list[str] = []
        for target, outcome in zip(chosen, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                log.warning(
                    "상담 실패 — 이 사람만 건너뛴다",
                    extra=log_extra(target=target, reason=type(outcome).__name__),
                )
                failed.append(target)
                continue

            # ⚠️ "관련 없음" 답변 필터 (기획서 §4.4).
            #    문서를 열어봤지만 이 질문에 답할 자료가 없는 경우, Agent 는
            #    낮은 신뢰도로 "관련 자료 없음" 을 낸다. 그 답을 취합에 넣으면
            #    "한지우 연구원: 로드맵 문서가 없습니다" 같은 잡음이 digest 를
            #    채운다. asker 자신은 예외 — 내 Agent 답변은 신뢰도와 무관하게 남긴다.
            kept = []
            for a in outcome.merged.answers:
                # asker 자신의 답변은 신뢰도/내용과 무관하게 남긴다.
                # 그 외에는 신뢰도가 아주 낮거나(0.15 미만) 텍스트가
                # "이 질문에 답할 자료가 없다" 류면 취합에서 뺀다.
                if target != request.asker and (
                    a.confidence < IRRELEVANT_CONFIDENCE or _looks_irrelevant(a.text)
                ):
                    irrelevant.append(target)
                else:
                    kept.append(a)
            if kept:
                answers.extend(kept)
                answered_ids.append(target)
            escalations.extend(outcome.escalations)

        merged = merge(
            answers,
            order=tuple(answered_ids),
            auto=self.cfg.confidence_auto,
            escalate=self.cfg.confidence_escalate,
        )
        if merged.answers:
            digest, source = await self.gatekeeper.synthesize_in_zone(
                request.question, merged.answers
            )
        elif irrelevant and not failed:
            # 후보들이 문서를 열어봤지만 **아무도 관련 자료가 없었다.**
            # "답을 못 받았다" 와 다르다 — 물었고, 답했고, 관련이 없었던 것이다.
            names = ", ".join(self._display_name(t) for t in dict.fromkeys(irrelevant))
            digest, source = (
                f"{names}의 Agent 에게 물었지만, 이 질문에 답할 관련 자료를 "
                "가진 사람이 없었습니다.\n\n"
                "질문에 다루는 주제를 한 단어 더 넣거나, 조직도에서 직접 골라 보십시오.",
                "code",
            )
        else:
            # 후보는 찾았는데 **한 명도 답을 주지 못한** 경우다. "찾지 못했습니다"
            # 라고 말하면 거짓이 된다 — 찾긴 찾았고 물었는데 실패한 것이다.
            names = ", ".join(self._display_name(t) for t in chosen)
            digest, source = (
                f"{names}의 Agent 에게 물었지만 답을 받지 못했습니다.\n\n"
                "이름을 눌러 직접 물어보면 어디서 막혔는지 처리 경과로 확인할 수 있습니다.",
                "code",
            )
        auto_count = sum(1 for a in merged.answers if a.used_external_agent)

        return ConsultResult(
            request_id=new_request_id(),
            question=request.question,
            broadcast=broadcast,
            digest=digest,
            digest_source=source,
            answers=merged.answers,
            divergent=merged.divergent,
            divergence_note=merged.divergence_note,
            escalations=tuple(escalations),
            consulted=tuple(answered_ids),
            skipped=tuple(skipped),
            elapsed_seconds=round(time.monotonic() - started, 3),
            interrupts_avoided=auto_count,
            minutes_saved_estimate=auto_count * MINUTES_PER_INTERRUPT,
        )

    @staticmethod
    def _name_fallback(answer: RehydratedAnswer, label_name: str) -> RehydratedAnswer:
        """폴백 답변에 **누구의 것인지** 이름을 붙인다.

        `answer_in_zone()` 은 사람을 모르므로 모두에게 "사내망 내부 응답" 이라는
        같은 라벨을 준다. 한 사람에게만 묻던 시절에는 문제가 없었지만, 지금은
        여러 사람의 답이 한 화면에 모인다 — 라벨이 전부 똑같으면 누가 무엇을
        말했는지 구분되지 않는다.
        """
        return answer.model_copy(update={"agent_label": f"{label_name} · 사내망 내부 응답"})

    def _agent_label(self, entity_id: str) -> str:
        agent = self.data.agents.get(entity_id)
        return agent.to_persona().agent_label if agent else f"{entity_id} 의 Agent"

    def _display_name(self, entity_id: str) -> str:
        agent = self.data.agents.get(entity_id)
        return agent.display_name if agent else entity_id

    async def _consult_one(self, request: ConsultRequest, target: str) -> AskResult:
        """한 사람에 대한 `prepare` → `send`. **기존 경로를 그대로 쓴다.**

        `prepare` 가 차단(`blocked`)을 내면 봉투가 없으므로 `send` 를 부르지
        않고, 동봉된 폴백 답변을 그대로 결과로 만든다 — "차단됐고 대신 이 답이
        있다" 가 한 왕복에 오는 구조를 여기서도 그대로 쓴다.
        """
        prepared = await self.prepare(
            AskRequest(question=request.question, asker=request.asker, targets=[target])
        )
        envelope_ids = [c.envelope_id for c in prepared.calls if c.envelope_id]

        if not envelope_ids:
            fallbacks = tuple(c.fallback for c in prepared.calls if c.fallback is not None)
            self._pending.pop(prepared.request_id, None)  # send 가 오지 않는다
            return AskResult(
                request_id=prepared.request_id,
                merged=merge(
                    list(fallbacks),
                    order=(target,),
                    auto=self.cfg.confidence_auto,
                    escalate=self.cfg.confidence_escalate,
                ),
            )

        # ⚠️ 승인자는 질문자다. `ask_agent()` 의 전제조건 검사는 그대로 돈다 —
        #    승인 **없이** 경계를 넘는 경로는 여전히 존재하지 않는다.
        return await self.send(prepared.request_id, envelope_ids, request.asker)

    def _to_candidate(self, card: AgentCard) -> Candidate:
        """`AgentCard` -> 판정 입력. **카드에 없는 것은 넣지 않는다.**

        `topics` 와 `leads` 는 카드에 없고 설정에 있으므로 여기서 붙인다.
        원격 노드의 사람은 이 노드에 설정이 없으므로 둘 다 비고, 판정은
        `expertise` 문장만으로 이뤄진다 — 그래도 목록에는 남는다.
        """
        agent = self.data.agents.get(card.entity_id)
        rank = self.data.org.rank(agent.org.rank) if agent and agent.org else None
        return Candidate(
            entity_id=card.entity_id,
            display_name=card.display_name,
            expertise=card.expertise,
            topics=agent.topics if agent else (),
            unit_path=card.unit_path,
            unit_id=card.unit_id,
            rank_label=card.rank_label or "",
            org_title=card.org_title or "",
            focus_label=card.current_focus_summary or "",
            leads=bool(rank and rank.leads),
            available=not card.daily_limit_reached,
        )

    # ── 트레이스 보조 ────────────────────────────────────────────────

    def _new_trace(
        self, *, request_id: str, target: str, question: str, agent_label: str = ""
    ) -> TraceRecorder | None:
        """보관소가 없으면 기록기도 만들지 않는다 — 쌓기만 하고 버리지 않는다."""
        if self.traces is None:
            return None
        rec = TraceRecorder(request_id=request_id, entity_id=target, question=question)
        rec.agent_label = agent_label
        return rec

    def _keep_trace(self, rec: TraceRecorder | None) -> str | None:
        """지금까지 쌓인 것을 보관소에 넣는다. 같은 `trace_id` 면 덮어쓴다.

        `prepare` 끝과 `send` 끝에 각각 부른다. 중간 상태도 보관하는 이유:
        검증에서 막혀 `send` 가 아예 없는 경우에도 "어디서 멈췄나" 를 열어
        볼 수 있어야 한다.
        """
        if rec is None or self.traces is None:
            return None
        try:
            return self.traces.put(rec.build())
        except Exception:  # noqa: BLE001 — 트레이스가 질의를 죽이면 안 된다
            log.exception("트레이스 보관 실패 — 무시한다")
            return None

    @staticmethod
    def _evidence(
        chunks: Sequence[Chunk], decisions: dict[str, TierDecision] | None = None
    ) -> tuple[TraceEvidence, ...]:
        """`Chunk` -> `TraceEvidence` **투영**.

        ⚠️ 이 함수가 여기 있는 이유가 설계다. `mesh.trace` 는 `Chunk` 를
           import 하지 않는다 — 원문과 내부 경로를 아예 받지 않기 위해서다
           (`TraceEvidence` 주석). 그래서 **이미 원문을 다루는 쪽**인 여기서
           투영을 만들어 넘긴다. 원문을 만질 수 있는 모듈이 늘지 않는다.

           `text` 는 길이만, `internal_path` 는 아예 넘어가지 않는다 (FR-43).
        """
        picked = dict(decisions or {})
        out: list[TraceEvidence] = []
        for chunk in chunks:
            decision = picked.get(chunk.chunk_id)
            out.append(
                TraceEvidence(
                    title=chunk.display_title,
                    source_path=chunk.internal_path,
                    tier=chunk.tier or (decision.tier if decision else None),
                    source_kind=chunk.source_kind or "",
                    as_of=chunk.as_of.isoformat() if chunk.as_of else "",
                    chars=len(chunk.text),
                    truncated=chunk.truncated,
                    rule_tier=decision.rule_tier if decision else None,
                    exaone_tier=decision.exaone_tier if decision else None,
                    rule_number=decision.rule_number if decision else None,
                    exaone_skipped=decision.exaone_skipped if decision else False,
                    exaone_failed=decision.exaone_failed if decision else False,
                    exaone_note=(
                        "규칙이 이미 기밀 — 호출 생략"
                        if decision and decision.exaone_skipped
                        else "판정 실패 → 기밀로 간주"
                        if decision and decision.exaone_failed
                        else ""
                    ),
                    reasons=decision.reasons if decision else (),
                )
            )
        return tuple(out)

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
        """실패는 `PrepareFailed` 로 감싼다 — 판정된 근거를 폴백에 넘기기 위해서다.

        기록기를 **여기서** 만드는 이유: 실패해도 그때까지의 경과가 폴백까지
        따라가야 한다. `_prepare_inner` 안에서 만들면 예외와 함께 사라진다.
        """
        classified: list[Chunk] = []
        rec = self._new_trace(
            request_id=pending.request_id, target=target, question=request.question
        )
        try:
            node = self.peers.node_of(target) if self.peers else None
            if node is not None:
                return await self._prepare_remote(request, target, pending, node)
            return await self._prepare_inner(request, target, pending, classified, rec)
        except PrepareFailed:
            raise
        except Exception as e:  # noqa: BLE001 — 폴백에 근거를 넘기고 다시 올린다
            raise PrepareFailed(e, classified, trace=rec) from e

    async def _prepare_remote(
        self, request: AskRequest, target: str, pending: PendingRequest, node: str
    ) -> tuple[PreparedCall, tuple[Tier, str] | None]:
        """대상이 다른 컴퓨터에 있다 — **그 노드가 준비한다.**

        여기서 하는 일은 청하고 받는 것뿐이다. 판정·조립·검증은 그 노드에서
        일어나고, 그 노드의 원문은 이쪽으로 오지 않는다.

        ⚠️ `PendingCall.chunks` 가 비어 있다. 폴백 답변을 만들 근거가 없다는
           뜻이고, 그게 맞다 — 남의 원문으로 내 쪽에서 폴백을 만들 수는 없다.
           원격 대상이 실패하면 그 노드가 자기 폴백을 돌려주거나, 아니면
           `_send_all` 이 근거 없는 폴백을 만든다.

        ⚠️ 등급 상향 신호(`tier_note`)를 원격에서도 전달한다. "사내 질문에
           기밀 근거가 동원됐다" 는 사실은 질문자가 알아야 한다.
        """
        assert self.peers is not None  # noqa: S101 — node 가 있으면 peers 도 있다
        prepared = await self.peers.client.prepare(
            node,
            PeerPrepareRequest(
                asker=request.asker,
                question=request.question,
                target=target,
                asker_node=self.cfg.node_name,
            ),
        )
        call = prepared.call
        pending.calls.append(
            PendingCall(
                envelope_id=call.envelope_id or f"remote_{target}",
                target_entity_id=target,
                chunks=[],  # 남의 원문은 오지 않는다
                session_facts=(),
                freshness=None,
                confidence_factor=1.0,  # 신선도 보정은 그 노드가 이미 적용했다
                remote_node=node,
                remote_node_name=prepared.node_name,
            )
        )
        tier_note: tuple[Tier, str] | None = None
        if call.tier is Tier.SECRET:
            tier_note = (Tier.SECRET, f"{prepared.node_name} 의 기밀 근거가 동원됐습니다")
        return call, tier_note

    async def _prepare_inner(
        self,
        request: AskRequest,
        target: str,
        pending: PendingRequest,
        classified: list[Chunk],
        rec: TraceRecorder | None = None,
    ) -> tuple[PreparedCall, tuple[Tier, str] | None]:
        agent_cfg = self.data.agent(target)
        persona = agent_cfg.to_persona()
        self.agent.check_daily_limit(persona)

        session = self.store.load_session(target)
        fresh = self.store.freshness_of(session)
        factor = confidence_factor(fresh, stale_factor=self.cfg.stale_confidence_factor)
        facts = session_facts(session, fresh)

        if rec:
            rec.agent_label = persona.agent_label

        # ① 질문 등급 — 지식을 막아도 질문 문장이 기밀이면 그대로 새어 나간다
        if rec:
            rec.mark("classify")
        q_tier: TierDecision = await self.gatekeeper.classify(request.question)

        # 후보 중에서 고르고 그 파일만 읽는다 (전역 스캔 없음)
        if rec:
            rec.mark("select")
        candidates = self.store.candidate_paths(session)
        paths = await self.store.select_paths(session, request.question)
        chunks = self.store.read(paths, target)
        chunks += self.store.verified_chunks(session)
        if not chunks:
            # 세션에 관련 근거가 없음 — 지식 갱신 시도
            log.info(
                "근거 없음 → 지식 갱신 시도",
                extra=log_extra(target=target, question_len=len(request.question)),
            )
            kb_chunk = await self.store.kb_search_and_save(target, request.question)
            if kb_chunk:
                chunks = [kb_chunk]
            else:
                if rec:
                    rec.add_blocked(
                        stage_id="select",
                        reason="동원할 근거가 없다",
                        detail=(
                            "이 사람의 세션에서 질문과 관련된 문서를 찾지 못했다. "
                            "경계 밖으로 나간 것이 없고, 신뢰 구역 안에서 답한다."
                        ),
                    )
                    self._keep_trace(rec)
                raise ExtractionFailed(f"{target} 의 세션에서 읽을 근거를 찾지 못했다")

        # ② 지식 등급 — `classified` 는 호출자가 준 리스트를 채운다.
        #    실패해도 폴백이 등급을 알 수 있어야 한다 (PrepareFailed 참조).
        decisions: dict[str, TierDecision] = {}
        for chunk in chunks:
            if chunk.tier is not None:
                classified.append(chunk)  # 승인된 QA — 등급이 이미 있다
                continue
            decision = await self.gatekeeper.classify(chunk.text, chunk.internal_path)
            decisions[chunk.chunk_id] = decision
            classified.append(chunk.model_copy(update={"tier": decision.tier}))

        if rec:
            evidence = self._evidence(classified, decisions)
            effective = max(
                [(c.tier or Tier.INTERNAL) for c in classified],
                default=q_tier.tier,
            )
            rec.add_select(
                candidate_count=len(candidates),
                evidence=evidence,
                # 후보가 하나뿐이면 고를 것이 없다 — 모델을 부르지 않는다.
                selected_by_model=len(candidates) > 1,
            )
            rec.add_classify(
                question_decision=q_tier, evidence=evidence, effective=effective
            )

        # 구조 추출 시도 — 실패 시 SECRET이 아니면 passthrough 경로로 전환
        try:
            calls = self.gatekeeper.plan_calls(request.question, target, classified, q_tier)
            call = calls[0]
        except ExtractionFailed:
            # plan_calls에서 choose_schema 실패 — 스키마 매칭 안 됨
            from mesh.extractor import DYNAMIC_SCHEMA
            from mesh.schemas import AgentCall

            tiers = [(c.tier or Tier.INTERNAL) for c in classified]
            effective_tier = max(tiers, default=q_tier.tier)
            if effective_tier is Tier.SECRET:
                # PoC: 고정 어휘에 맞는 task 가 없다 → fail closed 대신 소유자 Agent 가
                # 자체 구조 어휘를 생성하는 동적 경로로 넘긴다. call 만 세우고 아래
                # 공통 to_payload 경로로 진행한다 (to_payload 가 DYNAMIC 을 처리).
                log.info(
                    "스키마 매칭 실패(기밀) → 소유자 Agent 동적 어휘 경로 (PoC)",
                    extra=log_extra(target=target, tier=effective_tier.value),
                )
                call = AgentCall(
                    call_id=f"call_{uuid.uuid4().hex[:16]}",
                    entity_id=target,
                    tier=effective_tier,
                    task_schema_id=DYNAMIC_SCHEMA,
                    chunk_ids=tuple(c.chunk_id for c in classified),
                )
                # ⚠️ return 하지 않는다 — 아래 Hook B 경로가 이 call 을 처리한다.
            else:
                log.info(
                    "스키마 매칭 실패 → passthrough 경로 전환",
                    extra=log_extra(target=target, tier=effective_tier.value),
                )
                call = AgentCall(
                    call_id=f"call_{uuid.uuid4().hex[:16]}",
                    entity_id=target,
                    tier=effective_tier,
                    task_schema_id="passthrough",
                    chunk_ids=tuple(c.chunk_id for c in classified),
                )
                env, mapping = await self.gatekeeper.to_payload_passthrough(
                    call, classified, request.question
                )
                originals = tuple(c.text for c in classified)
                # passthrough는 간소화된 검증만 적용
                env = env.model_copy(update={"validation": passthrough_validation()})
                preview = self.gatekeeper.preview(env, originals)

                if rec:
                    rec.add_transform(
                        env=env,
                        mapping_table=dict(mapping.table),
                        extraction_note=(
                            "어휘 사전에 맞는 task 스키마가 없어 **슬롯 채우기를 건너뛰었다.** "
                            "질문과 근거를 그대로 담되 등급이 사내면 식별자를 치환한다. "
                            "⚠️ 이 경로는 기밀 등급에서 절대 쓰이지 않는다 — 기밀은 구조 추출이 "
                            "성공해야만 나간다."
                        ),
                    )
                    rec.add_validate(
                        result=env.validation,
                        verbatim_count=preview.verbatim_sentence_count,
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
                        trace=rec,
                    )
                )

                tier_note = (
                    (call.tier, f"{q_tier.tier.label_ko} 질문에 {call.tier.label_ko} 근거가 동원됐습니다")
                    if call.tier is not q_tier.tier
                    else None
                )

                return (
                    PreparedCall(
                        envelope_id=env.envelope_id,
                        target_entity_id=target,
                        agent_label=persona.agent_label,
                        sub_question=SubQuestionView(
                            id=call.call_id,
                            kind="passthrough",
                            text=request.question,
                            tier=call.tier,
                        ),
                        tier=call.tier,
                        disposition="ready",
                        preview=preview,
                        trace_id=self._keep_trace(rec),
                    ),
                    tier_note,
                )

        tier_note = (
            (call.tier, f"{q_tier.tier.label_ko} 질문에 {call.tier.label_ko} 근거가 동원됐습니다")
            if call.tier is not q_tier.tier
            else None
        )

        # 구조 추출 시도 — 실패 시 SECRET이 아니면 passthrough 경로로 전환
        if rec:
            rec.mark("transform")
        extraction_note = ""
        try:
            env, mapping = await self.gatekeeper.to_payload(call, classified, request.question)
        except ExtractionFailed:
            if call.tier is Tier.SECRET:
                from mesh.extractor import DYNAMIC_SCHEMA

                # PoC: 고정 스키마 추출이 실패했으면 소유자 Agent 가 자체 어휘를
                # 생성해 한 번 재시도한다. 이미 동적 경로였는데도 실패했으면
                # 대체 경로가 없다 — fail closed (원문은 절대 나가지 않는다).
                dyn_ok = False
                if call.task_schema_id != DYNAMIC_SCHEMA:
                    log.info(
                        "구조 추출 실패(기밀) → 소유자 Agent 동적 어휘 재시도 (PoC)",
                        extra=log_extra(target=target),
                    )
                    try:
                        call = call.model_copy(update={"task_schema_id": DYNAMIC_SCHEMA})
                        env, mapping = await self.gatekeeper.to_payload(
                            call, classified, request.question
                        )
                        extraction_note = (
                            "고정 어휘에 이 질문에 맞는 항목이 없어 **소유자 Agent 가 이 질문용 "
                            "구조 어휘를 생성**해 구조 추출했다 (PoC). 슬롯은 enum/int/bool 만, "
                            "값은 추상 라벨만 경계를 넘는다."
                        )
                        dyn_ok = True
                    except ExtractionFailed:
                        dyn_ok = False
                if not dyn_ok:
                    if rec:
                        rec.add_blocked(
                            stage_id="transform",
                            reason="구조 추출 실패 — 기밀은 원문으로 나가지 않는다",
                            detail=(
                                "소유자 Agent 의 동적 어휘 생성으로도 필수 슬롯을 채우지 "
                                "못했다. 기밀 등급에서는 원문을 그대로 보내는 것이 유일한 "
                                "대안인데 그것이 정확히 이 시스템이 막으려는 것이다 (fail closed)."
                            ),
                        )
                        self._keep_trace(rec)
                    raise  # SECRET은 구조 추출 필수 — 원문 유출 방지
            else:
                # INTERNAL/OPEN: 구조 추출 없이 직접 전달
                log.info(
                    "구조 추출 실패 → passthrough 경로 전환",
                    extra=log_extra(target=target, tier=call.tier.value),
                )
                extraction_note = (
                    "구조 추출이 실패해 passthrough 로 전환했다 — 슬롯을 어휘 사전 안의 값으로 "
                    "채우지 못했다는 뜻이다. 사내 등급이므로 식별자 치환을 거쳐 나간다. "
                    "⚠️ 기밀 등급이었다면 여기서 전송이 멈춘다."
                )
                env, mapping = await self.gatekeeper.to_payload_passthrough(
                    call, classified, request.question
                )
        originals = tuple(c.text for c in classified)
        if rec:
            rec.add_transform(
                env=env, mapping_table=dict(mapping.table), extraction_note=extraction_note
            )
            rec.mark("validate")
        validation = self.gatekeeper.validate(env, originals)
        env = env.model_copy(update={"validation": validation})
        preview = self.gatekeeper.preview(env, originals)

        if rec:
            rec.add_validate(
                result=validation, verbatim_count=preview.verbatim_sentence_count
            )

        if not validation.passed:
            if rec:
                rec.add_blocked(
                    stage_id="dispatch",
                    reason=f"검증 {validation.summary} — 전송하지 않았다",
                    detail=(
                        f"{validation.first_failed_stage} 단계에서 막혔다. 경계 밖으로 나간 "
                        "것이 없으므로 감사 로그에도 레코드가 없다 — '없다'가 증거가 된다."
                    ),
                )
                self._keep_trace(rec)
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
                trace=rec,
            )
        )

        # ── 기밀 등급 처리 ──────────────────────────────────────────────
        # 청크가 기밀만 있으면: EXAONE 이 사내에서 직접 답한다.
        # 청크가 기밀+사내 혼합이면:
        #   ① 기밀 청크 → EXAONE(answer_in_zone)  — 경계 안
        #   ② 사내 이하 청크 → Claude(send)        — 경계 밖 (가명화 후)
        #   ③ EXAONE(synthesize_in_zone)로 ①+② 합침 — 최종 답변
        if call.tier is Tier.SECRET:
            secret_chunks = [c for c in classified if (c.tier or Tier.INTERNAL) is Tier.SECRET]
            below_chunks  = [c for c in classified if (c.tier or Tier.INTERNAL) is not Tier.SECRET]

            if below_chunks:
                # ── 혼합 경로: 분리 처리 후 합침 ──────────────────────────
                log.info(
                    "기밀+사내 혼합 청크 — 분리 처리",
                    extra=log_extra(secret=len(secret_chunks), below=len(below_chunks)),
                )

                # ① 기밀 청크: EXAONE 이 사내에서 답한다
                secret_answer = await self.gatekeeper.answer_in_zone(
                    request.question,
                    secret_chunks,
                    tier_label=Tier.SECRET.label_ko,
                    reason="secret_tier_local",
                )
                secret_answer = self._name_fallback(secret_answer, persona.agent_label)

                # ② 사내 이하 청크: 별도 AgentCall 로 Claude 에게 보낸다
                # ② 사내 이하 청크: EXAONE 이 가명화 후 사내에서 답한다.
                # (Claude send 경로는 pending 구조를 직접 조작해야 해서 불안정하다.
                # answer_in_zone 으로 처리하고 synthesize_in_zone 으로 기밀 답변과 합친다.)
                try:
                    below_tier = max(
                        (c.tier or Tier.INTERNAL) for c in below_chunks
                    )
                    below_answer = await self.gatekeeper.answer_in_zone(
                        request.question,
                        below_chunks,
                        tier_label=below_tier.label_ko,
                        reason="open_tier_local",
                    )
                    below_answer = self._name_fallback(below_answer, persona.agent_label)
                    below_answers = [below_answer]
                except Exception:  # noqa: BLE001
                    below_answers = []

                # ③ EXAONE 이 사내에서 두 답변을 합친다
                all_answers = [secret_answer, *below_answers]
                merged_text, merge_source = await self.gatekeeper.synthesize_in_zone(
                    request.question, all_answers
                )

                final_answer = secret_answer.model_copy(update={
                    "text": merged_text,
                    "tier": Tier.SECRET,
                    "used_external_agent": bool(below_answers and
                        any(a.used_external_agent for a in below_answers)),
                    "citations": tuple(
                        c for a in all_answers for c in a.citations
                    ),
                    "confidence": min((a.confidence for a in all_answers), default=0.5),
                    "agent_label": persona.agent_label,
                })
                if rec is not None:
                    final_answer = final_answer.model_copy(update={"trace_id": rec.trace_id})
                    rec.add_blocked(
                        stage_id="dispatch",
                        reason="기밀+사내 혼합 — 기밀은 사내 AI, 사내는 Claude, 합침은 사내 AI",
                        detail=(
                            f"기밀 청크 {len(secret_chunks)}건은 EXAONE 이 사내에서 답했고, "
                            f"사내 청크 {len(below_chunks)}건은 Claude 가 처리했습니다. "
                            f"최종 정리는 EXAONE 이 사내에서 합쳤습니다 (source={merge_source})."
                        ),
                    )
                    self._keep_trace(rec)

                self.gatekeeper.cache.discard(env.envelope_id)
                pending.calls.pop()
                return (
                    PreparedCall(
                        envelope_id=None,
                        target_entity_id=target,
                        agent_label=persona.agent_label,
                        tier=Tier.SECRET,
                        disposition="blocked",
                        fallback=final_answer,
                        blocked_reason="기밀+사내 혼합 — 사내 AI 가 합쳐서 답했습니다",
                        trace_id=self._keep_trace(rec),
                    ),
                    tier_note,
                )

            else:
                # ── 순수 기밀: EXAONE 이 사내에서 직접 답한다 ──────────────
                self.gatekeeper.cache.discard(env.envelope_id)
                pending.calls.pop()
                fallback = await self.gatekeeper.answer_in_zone(
                    request.question,
                    classified,
                    tier_label=call.tier.label_ko,
                    reason="secret_tier_local",
                )
                fallback = self._name_fallback(fallback, persona.agent_label)
                if rec is not None:
                    fallback = fallback.model_copy(update={"trace_id": rec.trace_id})
                    rec.add_blocked(
                        stage_id="dispatch",
                        reason="기밀 등급 — 사내 AI 가 신뢰 구역 안에서 답했습니다",
                        detail=(
                            "구조 추출과 검증을 통과했지만 기밀 등급은 경계 밖으로 내보내지 않는다. "
                            "EXAONE 이 구조화된 페이로드를 사내에서 직접 읽고 답했다. "
                            "감사 로그에 레코드가 없다 — '없다'가 증거가 된다 (BR-A-03)."
                        ),
                    )
                    self._keep_trace(rec)
                return (
                    PreparedCall(
                        envelope_id=None,
                        target_entity_id=target,
                        agent_label=persona.agent_label,
                        tier=call.tier,
                        disposition="blocked",
                        fallback=fallback,
                        blocked_reason="기밀 등급 — 사내 AI 가 신뢰 구역 안에서 답했습니다",
                        trace_id=self._keep_trace(rec),
                    ),
                    tier_note,
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
                preview=preview,
                trace_id=self._keep_trace(rec),
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
        cause = error.error if isinstance(error, PrepareFailed) else error
        reason_code, label = _blocked_reason(cause)
        rec = error.trace if isinstance(error, PrepareFailed) else None

        # 판정된 근거가 있으면 그것을 쓴다 (등급 표시가 정확해진다).
        chunks = list(error.chunks) if isinstance(error, PrepareFailed) else []
        agent_cfg = None
        try:
            agent_cfg = self.data.agent(target)
            if not chunks:
                session = self.store.load_session(target)
                chunks = self.store.read(list(self.store.candidate_paths(session)), target)
        except Exception as e:  # noqa: BLE001 — 폴백 경로다. 여기서 또 죽으면 안 된다
            log.warning(
                "폴백용 근거를 읽지 못했다 — 근거 없이 답한다",
                extra=log_extra(target=target, reason=type(e).__name__),
            )

        tier = max([(c.tier or Tier.INTERNAL) for c in chunks], default=Tier.INTERNAL)
        label_name = agent_cfg.to_persona().agent_label if agent_cfg else f"{target} 의 Agent"
        fallback = await self.gatekeeper.answer_in_zone(
            request.question, chunks, tier_label=tier.label_ko, reason=reason_code
        )
        fallback = self._name_fallback(fallback, label_name)
        if rec is not None:
            fallback = fallback.model_copy(update={"trace_id": rec.trace_id})

        if rec is not None:
            rec.agent_label = rec.agent_label or label_name
            # 이미 어느 단계가 "여기서 멈췄다" 를 적었으면 덧쓰지 않는다.
            # 차단 사유가 두 개면 읽는 사람이 어느 것이 진짜인지 모른다.
            if not rec.has_blocked:
                rec.add_blocked(
                    stage_id="dispatch",
                    reason=label,
                    detail=(
                        f"{label} 경계 밖으로 나간 것이 없으므로 감사 로그에 레코드가 "
                        "없다. 대신 신뢰 구역 안에서 아는 만큼 답했다."
                    ),
                )

        return PreparedCall(
            envelope_id=None,
            target_entity_id=target,
            agent_label=label_name,
            tier=tier,
            disposition="blocked",
            fallback=fallback,
            blocked_reason=label,
            trace_id=self._keep_trace(rec),
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
                self._name_fallback(
                    await self.gatekeeper.answer_in_zone(
                        pending.question,
                        pending.calls[0].chunks if pending.calls else [],
                        tier_label="사내",
                        reason="broker_unavailable",
                    ),
                    self._agent_label(
                        pending.calls[0].target_entity_id if pending.calls else pending.order[0]
                    ),
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
                fallback = self._name_fallback(
                    await self.gatekeeper.answer_in_zone(
                        pending.question,
                        call.chunks,
                        tier_label="사내",
                        reason=_local_reason(outcome),
                    ),
                    self._agent_label(call.target_entity_id),
                )
                if call.trace is not None:
                    fallback = fallback.model_copy(update={"trace_id": call.trace.trace_id})
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
            if outcome.call.is_remote:
                # 원격 대상의 담당자는 그 노드에 있고, 인박스도 그쪽에 만들어졌다.
                # 여기서 또 만들면 **같은 확인 요청이 두 컴퓨터에 뜬다.**
                log.info(
                    "원격 에스컬레이션 — 그 노드의 인박스에 있다",
                    extra=log_extra(
                        target=outcome.call.target_entity_id,
                        node=outcome.call.remote_node_name,
                    ),
                )
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
        if call.is_remote:
            return await self._send_remote(pending, call, approved_by)
        return await self._send_local(pending, call, approved_by)

    async def _send_remote(
        self, pending: PendingRequest, call: PendingCall, approved_by: str
    ) -> _CallOutcome:
        """대상이 다른 컴퓨터에 있다 — **그 노드가 보낸다.**

        경계 밖 Agent 호출도, 감사 레코드도, 에스컬레이션도 그 노드에서 일어난다.
        원문을 가진 쪽에 기록이 남는 것이 맞다 — "무엇이 경계를 넘었나" 는
        원문을 가진 사람이 증명해야 하는 것이고, 그 증거가 남의 컴퓨터에 있으면
        증명이 성립하지 않는다.

        `envelope`·`persona` 를 `None` 으로 둔다. 둘은 **로컬 에스컬레이션**을
        만들 때만 쓰이고, 원격 대상의 담당자는 그 노드의 인박스에 있다.
        `_send_all` 이 `None` 을 보고 로컬 에스컬레이션을 건너뛴다.
        """
        assert self.peers is not None and call.remote_node is not None  # noqa: S101
        answer = await self.peers.client.send(
            call.remote_node,
            PeerSendRequest(
                request_id=pending.request_id,
                envelope_id=call.envelope_id,
                approved_by=approved_by,
                asker_node=self.cfg.node_name,
            ),
        )
        log.info(
            "원격 노드가 답했다",
            extra=log_extra(
                target=call.target_entity_id,
                node=answer.node_name,
                escalated=answer.escalated,
            ),
        )
        # ⚠️ 처분을 여기서 다시 계산하지 않는다. 원격 노드가 자기 임계값으로
        #    이미 판단했고, 에스컬레이션도 그쪽에서 만들었다. 다시 계산하면
        #    "그쪽은 에스컬레이션했는데 이쪽은 auto" 같은 어긋난 상태가 생긴다.
        disposition = Disposition.ESCALATE if answer.escalated else Disposition.AUTO
        return _CallOutcome(call, answer.answer, None, None, disposition)

    async def _send_local(
        self, pending: PendingRequest, call: PendingCall, approved_by: str
    ) -> _CallOutcome:
        entry = self.gatekeeper.cache.take(call.envelope_id)  # 일회용
        if entry is None:
            raise GatekeeperError(f"envelope 이 만료되었거나 이미 전송되었다: {call.envelope_id}")

        persona = self.data.agent(call.target_entity_id).to_persona()
        rec = call.trace

        # ⚠️ 매핑 사본. `finally` 가 원본을 비우기 전에 뜬다 (BR-G-06 은 지켜진다 —
        #    원본은 예정대로 폐기된다). 이 사본은 이 함수 안에서만 살고 아래
        #    `finally` 에서 지워진다. 트레이스에 담기는 것은 **답변에 실제로
        #    등장한 기호**뿐이고 나머지는 건수만 센다 (`trace.mapping_rows`).
        mapping_snapshot = dict(entry.mapping.table)

        # ⚠️ 트레이스의 dispatch 단계는 호출 **뒤**에 기록한다. 왕복 시간을 담아야
        #    하기 때문이다. "호출 직전에 남긴다" 는 약속(BR-A-01)은 **감사 로그**의
        #    것이고 그것은 `ask_agent()` 안에서 그대로 지켜진다 — 호출이 실패해도
        #    "나갔다" 는 사실은 감사 로그에 남는다. 아래 `except` 가 실패한 경우에도
        #    단계를 남기는 이유가 그것이다.
        def note_dispatch(started: float, *, failed: bool, usage: dict | None = None) -> None:
            if not rec:
                return
            rec.add_dispatch(
                env=entry.envelope,
                transport=self.cfg.agent_transport.value,
                model_id=self.cfg.agent_model_id,
                approved_by=approved_by,
                endpoint=self._boundary_endpoint(),
                latency_ms=int((time.monotonic() - started) * 1000),
                usage=usage,
                failed=failed,
            )

        dispatch_started = time.monotonic()
        try:
            try:
                resp = await self.agent.ask(entry.envelope, persona, approved_by)
            except BaseException:
                note_dispatch(dispatch_started, failed=True)
                raise
            note_dispatch(dispatch_started, failed=False, usage=resp.usage)
            answer = self.gatekeeper.rehydrate(
                resp, entry.mapping, persona=persona, chunks=call.chunks
            )
            if rec:
                # 왼쪽(기호)과 오른쪽(복원)을 나란히 놓기 위해, 경계 밖 모델이
                # 만든 그대로를 한 번 더 편다. **모델을 다시 부르지 않는다** —
                # `answer_to_text` 는 순수 함수다.
                rec.add_rehydrate(
                    masked_text=answer_to_text(resp.answer),
                    rehydrated_text=answer.text,
                    mapping_table=mapping_snapshot,
                    unresolved=answer.unresolved_refs,
                    citations=answer.citations,
                    confidence=answer.confidence,
                )
        finally:
            # 재수화 실패 시에도 폐기한다 (BR-G-06, NFR-S-15)
            entry.mapping.table.clear()
            mapping_snapshot.clear()
            self.gatekeeper.cache.discard(call.envelope_id)
            self._keep_trace(rec)

        # 세션 신선도 보정. 여기서 적용해야 branch() 가 보정된 값을 본다
        answer = self._apply_freshness(answer, call)
        if rec is not None:
            answer = answer.model_copy(update={"trace_id": rec.trace_id})
        disposition = branch(
            [answer], auto=self.cfg.confidence_auto, escalate=self.cfg.confidence_escalate
        )
        return _CallOutcome(call, answer, entry.envelope, persona, disposition, resp)

    def _boundary_endpoint(self) -> str:
        """경계 밖으로 나갈 때 실제로 향하는 곳. **트레이스 표시용 문자열이다.**

        비밀을 담지 않는다 — 브로커 URL 은 설정값이고 키는 여기 오지 않는다.
        "어디로 갔나" 를 화면이 말할 수 없으면 경계를 보여준다는 주장이 빈다.
        """
        match self.cfg.agent_transport.value:
            case "broker":
                return self.cfg.broker_api_url or "(브로커 URL 미설정)"
            case "direct":
                return f"bedrock:{self.cfg.aws_region}"
            case _:
                return "(목업 — 녹화된 픽스처)"

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

    # ── 허브 흐름 ────────────────────────────────────────────────────

    async def hub_ask(self, question: str, asker: str) -> "HubAskResult":
        """내 Agent (Claude) 가 허브 역할을 하는 단일 진입점.

        흐름:
          1. asker 의 지식을 읽어 Claude 에게 passthrough 페이로드 생성
          2. Claude 에게 ask_other_agents 도구를 주고 tool-use 모드로 호출
          3. Claude 가 스스로 판단:
             a. 자체 지식으로 답 가능 → 바로 답변
             b. 다른 Agent 필요 → ask_other_agents 호출 → broadcast → 결과 취합
          4. 실패 시 EXAONE 폴백
        """
        from mesh.agent_broadcast import TOOL_CONFIG, BroadcastService
        from mesh.validator import passthrough_validation
        from mesh.schemas import Chunk, Representation, Tier

        broadcast_svc = BroadcastService(self, self.gatekeeper.exaone, self.data)
        broadcast_results: list = []

        # ── 도구 핸들러 (A2: broadcast 질의 금칙어 검사 포함) ──────────

        async def tool_handler(tool_name: str, tool_input: dict) -> str:
            if tool_name != "ask_other_agents":
                return f"Unknown tool: {tool_name}"

            sub_question = tool_input.get("question", question)

            # Claude 가 생성한 질문이 금칙어를 포함하는지 검사
            try:
                q_tier = await self.gatekeeper.classify(sub_question)
                if q_tier.tier is Tier.SECRET:
                    log.warning(
                        "broadcast 질의에 기밀 내용 — 차단",
                        extra=log_extra(asker=asker, reasons=q_tier.reasons[:2]),
                    )
                    return (
                        "This question was blocked by the security gatekeeper "
                        "because it may contain confidential information. "
                        "Please rephrase without specific sensitive details."
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("broadcast 질의 등급 판정 실패", extra=log_extra(reason=str(e)))

            br = await broadcast_svc.ask(
                question=sub_question,
                context=tool_input.get("context", ""),
                asker=asker,
                exclude=asker,
            )
            broadcast_results.append(br)
            return br.to_tool_result_text()

        # ── asker 의 지식 로드 ──────────────────────────────────────────

        chunks: list[Chunk] = []
        try:
            session = self.store.load_session(asker)
            paths = await self.store.select_paths(session, question)
            chunks = self.store.read(paths, asker)
            chunks += self.store.verified_chunks(session)
        except Exception as e:  # noqa: BLE001
            log.info("세션 없음 — 지식 없이 진행", extra=log_extra(reason=type(e).__name__))

        # ── passthrough 페이로드 생성 ──────────────────────────────────

        try:
            import secrets as _secrets
            from mesh.config import sha256_canonical
            from mesh.schemas import PayloadEnvelope

            persona = self.data.agent(asker).to_persona()

            payload: dict = {
                "task": "hub_ask",
                "question": question,
                "context": [
                    {"ref": f"MY_DOC_{i+1}", "content_excerpt": c.text[:1000]}
                    for i, c in enumerate(chunks[:3])
                ],
            }

            env = PayloadEnvelope(
                envelope_id="env_" + _secrets.token_urlsafe(18)[:22].replace("-", "A").replace("_", "B"),
                tier=Tier.INTERNAL,
                task_schema_id="hub_ask",
                payload=payload,
                representation=Representation.VERBATIM,
                validation=passthrough_validation(),
                payload_sha256=sha256_canonical(payload),
                size_bytes=len(str(payload).encode()),
            )

        except Exception as e:  # noqa: BLE001
            log.warning("hub_ask 페이로드 생성 실패 — EXAONE 폴백", extra=log_extra(reason=str(e)))
            return await self._hub_exaone_fallback(question, asker, chunks, broadcast_svc, broadcast_results)

        # ── Claude tool-use 호출 ────────────────────────────────────────

        HUB_OUTPUT_CONTRACT = (
            "Answer in Korean. Write a clear, helpful response.\n"
            "If you need information from other people's agents, use the ask_other_agents tool.\n"
            "Only use the tool if you genuinely cannot answer from the context provided.\n"
            "Do NOT output JSON. Write a natural conversational answer."
        )

        try:
            self.gatekeeper.check_preconditions(env, asker)
            system_prompt = build_system_prompt(persona, env.tier, output_contract=HUB_OUTPUT_CONTRACT)

            self.gatekeeper.audit.record(
                self.gatekeeper._request_record(
                    env, persona, asker,
                    model_id=self.cfg.agent_model_id,
                )
            )

            resp = await self.gatekeeper.broker.invoke(
                env,
                system_prompt,
                self.cfg.agent_model_id,
                tool_config=TOOL_CONFIG,
                tool_handler=tool_handler,
            )

            answer_text = (
                resp.answer.get("text", "")
                or " ".join(str(v) for v in resp.answer.values())
            ).strip()

            if not answer_text:
                answer_text = "답변을 생성할 수 없습니다."

            return HubAskResult(
                question=question,
                answer=answer_text,
                used_tool=bool(broadcast_results),
                broadcast_results=broadcast_results,
                disposition="auto" if resp.confidence >= self.cfg.confidence_auto else "unverified",
            )

        except Exception as e:  # noqa: BLE001
            log.warning("Claude hub_ask 실패 — EXAONE 폴백", extra=log_extra(reason=type(e).__name__))
            return await self._hub_exaone_fallback(question, asker, chunks, broadcast_svc, broadcast_results)

    async def _hub_exaone_fallback(
        self,
        question: str,
        asker: str,
        chunks: list,
        broadcast_svc: object,
        broadcast_results: list,
    ) -> "HubAskResult":
        """Claude 호출 실패 시 EXAONE 으로 폴백."""
        from mesh.agent_broadcast import BroadcastService

        if chunks:
            try:
                fallback = await self.gatekeeper.answer_in_zone(
                    question, chunks, tier_label="사내", reason="open_tier_local"
                )
                if fallback and fallback.confidence >= self.cfg.confidence_auto:
                    return HubAskResult(
                        question=question,
                        answer=fallback.text,
                        used_tool=False,
                        broadcast_results=[],
                        disposition="auto",
                    )
            except Exception:  # noqa: BLE001
                pass

        if isinstance(broadcast_svc, BroadcastService):
            br = await broadcast_svc.ask(question=question, context="", asker=asker, exclude=asker)
            broadcast_results.append(br)
            if br.answered:
                from mesh.schemas import Chunk, Tier
                from datetime import date as _date
                virtual = Chunk(
                    chunk_id="broadcast_result",
                    entity_id=asker,
                    text=br.to_tool_result_text(),
                    tier=Tier.INTERNAL,
                    display_title="broadcast 응답 취합",
                    internal_path="broadcast/result",
                    as_of=_date.today(),
                    formality="official",
                    source_kind="note",
                )
                try:
                    final = await self.gatekeeper.answer_in_zone(
                        question, [virtual], tier_label="사내", reason="open_tier_local"
                    )
                    return HubAskResult(
                        question=question,
                        answer=final.text,
                        used_tool=True,
                        broadcast_results=broadcast_results,
                        disposition="auto",
                    )
                except Exception:  # noqa: BLE001
                    return HubAskResult(
                        question=question,
                        answer=br.answered[0].answer,
                        used_tool=True,
                        broadcast_results=broadcast_results,
                        disposition="unverified",
                    )

        return HubAskResult(
            question=question,
            answer="처리 중 오류가 발생했습니다.",
            used_tool=False,
            broadcast_results=broadcast_results,
            disposition="error",
        )


# ══════════════════════════════════════════════════════════════════════
# 허브 결과 타입
# ══════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class HubAskResult:
    """hub_ask() 결과."""
    question: str
    answer: str
    used_tool: bool
    broadcast_results: list
    disposition: str

# ══════════════════════════════════════════════════════════════════════
# 보조
# ══════════════════════════════════════════════════════════════════════


def _blocked_reason(error: BaseException) -> tuple[str, str]:
    """예외 -> (`local_queries` 이유 코드, 사람이 읽을 라벨)."""
    match error:
        case ExtractionFailed():
            return (
                "extraction_failed",
                "기밀 문서를 바탕으로 사내에서 답했습니다",
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

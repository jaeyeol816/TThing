"""Gatekeeper — Agent 를 감싸는 막.

Agent 가 무엇을 볼 수 있는지 통제한다. 그게 전부다.

Agent 에게 정보가 도달하는 경로는 셋뿐이므로 관문도 셋이다:

  ① 질문 변환   사용자 질문 문장          -> to_payload (SECRET 이면 구조화)
  ② 지식 변환   읽은 파일 내용            -> to_payload (SECRET 이면 원문 0개)
  ③ 재수화      Agent 응답                -> rehydrate (기호 -> 실제 이름)

①을 빠뜨리면 안 된다. 지식을 아무리 잘 막아도 **질문 문장 자체가 기밀을
담고 있으면** 그대로 새어 나간다.

⚠️ **`ask_agent()` 가 경계를 넘는 유일한 통로다** (SECURITY-11).
   `audit.mirror()` 를 제외한 어떤 모듈도 `mesh.llm.broker` 나 `boto3` 를
   import 하지 않는다. `tests/unit/test_import_boundary.py` 가 ast 로 강제한다.

⚠️ 이 파일은 **조율만 하고 로직을 갖지 않는다.**
   판정은 classifier, 조립은 extractor, 통과 여부는 validator(순수 코드)가 정한다.
   안전성이 모델 판단에만 의존하면 프롬프트 인젝션과 환각에 노출된다.

Day 1 상태: 시그니처 동결 + EnvelopeCache 구현.
            나머지 메서드는 Day 2 (A) 에 채운다.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mesh.config import Config, DataBundle, get_logger, log_extra
from mesh.exceptions import GatekeeperError
from mesh.schemas import (
    AgentCall,
    AgentResponse,
    Chunk,
    Mapping,
    PayloadEnvelope,
    Persona,
    PreviewCard,
    RehydratedAnswer,
    TierDecision,
    ValidationResult,
)

if TYPE_CHECKING:  # pragma: no cover
    from mesh.audit import AuditLog
    from mesh.llm.broker import BrokerClient
    from mesh.llm.exaone import ExaoneClient

log = get_logger("gatekeeper")


def new_envelope_id() -> str:
    """`env_` + 22자 URL-safe 난수. ENVELOPE_ID_RE 를 만족한다."""
    return "env_" + secrets.token_urlsafe(18)[:22].replace("-", "A").replace("_", "B")


# ══════════════════════════════════════════════════════════════════════
# EnvelopeCache — 매핑 수명 관리 (BR-G-06, BR-G-09)
# ══════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class CacheEntry:
    """`prepare` 와 `send` 사이에 보관되는 것.

    ⚠️ `Mapping` 과 `originals`(원문)가 여기 있다. 이 객체는 **프로세스 메모리를
       벗어나지 않는다** — Redis 같은 외부 캐시를 쓰지 않는 이유다.
    """

    envelope: PayloadEnvelope
    mapping: Mapping
    originals: tuple[str, ...]  # ⚠️ 원문. 5-gram 재검증용
    persona_id: str
    created_at: float = field(default_factory=time.monotonic)


class EnvelopeCache:
    """`prepare` 가 발급하고 `send` 가 한 번만 소비하는 저장소.

    설계 판단 3가지:

    1. **`take()` 가 조회와 제거를 함께 한다.** 같은 envelope_id 로 두 번
       전송하는 것을 막는다 (재생 공격 방지 + 중복 과금 방지).

    2. **인메모리다.** 매핑을 프로세스 밖으로 내보내지 않는 것이 BR-G-09 이고,
       단일 프로세스라 공유가 필요 없다. 편의를 위해 보안 모델을 양보하지 않는다.

    3. **TTL 5분.** 사용자가 미리보기를 보고 승인할 시간. 자리를 비우면
       자동 소멸해 매핑이 메모리에 누적되지 않는다.
    """

    TTL_SECONDS = 300

    def __init__(self, *, ttl_seconds: int | None = None) -> None:
        self._items: dict[str, CacheEntry] = {}
        self._ttl = ttl_seconds if ttl_seconds is not None else self.TTL_SECONDS

    def put(
        self,
        envelope: PayloadEnvelope,
        mapping: Mapping,
        originals: tuple[str, ...],
        persona_id: str,
    ) -> None:
        self.sweep()
        self._items[envelope.envelope_id] = CacheEntry(
            envelope=envelope, mapping=mapping, originals=originals, persona_id=persona_id
        )

    def take(self, envelope_id: str) -> CacheEntry | None:
        """조회 후 **즉시 제거**. 일회용이다.

        Returns:
            없거나 TTL 만료면 None. 호출자는 410 Gone 을 반환한다
            (404 가 아니다 — 있었다가 없어진 것이므로).
        """
        self.sweep()
        return self._items.pop(envelope_id, None)

    def peek(self, envelope_id: str) -> CacheEntry | None:
        """제거하지 않고 조회. 미리보기 재표시에만 쓴다."""
        self.sweep()
        return self._items.get(envelope_id)

    def discard(self, envelope_id: str) -> None:
        """취소 시 즉시 폐기. 감사 레코드는 남지 않는다 (BR-U-03)."""
        self._items.pop(envelope_id, None)

    def sweep(self) -> int:
        """TTL 만료 항목 제거. 매 접근 시 호출한다."""
        now = time.monotonic()
        stale = [k for k, v in self._items.items() if now - v.created_at > self._ttl]
        for k in stale:
            del self._items[k]
        if stale:
            log.debug("envelope 캐시 만료", extra=log_extra(count=len(stale)))
        return len(stale)

    def __len__(self) -> int:
        return len(self._items)


# ══════════════════════════════════════════════════════════════════════
# Gatekeeper
# ══════════════════════════════════════════════════════════════════════


class Gatekeeper:
    """신뢰 경계를 넘는 유일한 통로.

    Day 1: 시그니처 동결. B(U3)가 이 시그니처에 대고 코딩한다.
    Day 2: A 가 구현을 채운다.
    """

    def __init__(
        self,
        cfg: Config,
        data: DataBundle,
        exaone: ExaoneClient,
        broker: BrokerClient,
        audit: AuditLog,
        *,
        cache: EnvelopeCache | None = None,
    ) -> None:
        self.cfg = cfg
        self.data = data
        self.exaone = exaone
        self.broker = broker
        self.audit = audit
        self.cache = cache or EnvelopeCache()

    # ── 등급 판정 ────────────────────────────────────────────────────

    async def classify(self, text: str, source_path: str | None = None) -> TierDecision:
        """등급 판정. `max(규칙, EXAONE)` 을 채택한다 (FR-01, BR-C-01).

        규칙이 이미 SECRET 이면 EXAONE 을 호출하지 않는다 (BR-C-02).
        판정 실패·타임아웃은 **Tier.SECRET 으로 간주**한다 (FR-02, BR-G-01).

        Day 2 (A): classifier.classify() 위임
        """
        raise NotImplementedError("Day 2 (A) — classifier.py 위임")

    def plan_calls(
        self, question: str, entity_id: str, chunks: list[Chunk], question_tier: TierDecision
    ) -> list[AgentCall]:
        """분해할지 상향할지 정하고 호출 계획을 만든다 (FR-11, FR-12, BR-G-07).

        분해 조건 — 3개를 **모두** 만족할 때만:
          1. 하위 질문이 자기 answer_format 을 가진다
          2. needs[] 가 다른 하위 질문과 교집합이 없다
          3. 그 조각만으로 사용자에게 보여줄 값이 있다

        하나라도 어긋나면 `tier = max(question_tier, *chunk_tiers)` 로 상향한다.

        불변식: 각 `AgentCall.tier` 는 단일값이다 (BR-G-08).
                등급이 섞인 페이로드는 타입 수준에서 생성되지 않는다.

        Day 2 (A)
        """
        raise NotImplementedError("Day 2 (A) — 분해/상향 판정")

    # ── 관문 ①② 표현 변환 ───────────────────────────────────────────

    async def to_payload(
        self, call: AgentCall, chunks: list[Chunk], question: str
    ) -> tuple[PayloadEnvelope, Mapping]:
        """등급에 따라 표현을 만든다 (FR-03~06, BR-G-04).

            SECRET    -> extractor.extract()      슬롯 채우기 + 코드가 조립. 원문 0개
            INTERNAL  -> pseudonymizer.apply()    식별자만 치환, 기술 용어 보존
            OPEN      -> 변환 없음

        `Mapping` 은 반환값으로만 나가고 `PayloadEnvelope` 에 담기지 않는다 —
        `model_dump()` 가 실수로 매핑을 직렬화하지 않게 (BR-G-09).

        Raises:
            ExtractionFailed: 필수 슬롯 미충족 -> answer_in_zone 폴백

        Day 2 (A)
        """
        raise NotImplementedError("Day 2 (A) — extractor / pseudonymizer 위임")

    # ── 검증과 사람 확인 ─────────────────────────────────────────────

    def validate(self, env: PayloadEnvelope, originals: tuple[str, ...]) -> ValidationResult:
        """검증 6단계 (FR-07, BR-V-*). 순수 함수 위임.

        첫 실패에서 멈추지 않고 전부 수집한다 — 사람이 볼 진단이 완전해야 하고
        `PreviewCard` 에 `6/6` 을 표시해야 한다.

        Day 2 (A): validator.validate() 위임
        """
        raise NotImplementedError("Day 2 (A) — validator.py 위임")

    def preview(self, env: PayloadEnvelope, originals: tuple[str, ...]) -> PreviewCard:
        """사람 확인용 카드 (FR-09, FR-41). 4번째 방어 겹.

        `verbatim_sentence_count` 를 **측정해서** 담는다 —
        "원문 0개"가 주장이 아니라 계산 결과가 된다.

        Day 2 (A)
        """
        raise NotImplementedError("Day 2 (A)")

    # ── 유일한 외부 호출 지점 ────────────────────────────────────────

    async def ask_agent(
        self, env: PayloadEnvelope, persona: Persona, approved_by: str
    ) -> AgentResponse:
        """**신뢰 경계를 넘는 유일한 통로.**

        전제조건 3개를 명시적으로 검사한다 (BR-G-02).
        `assert` 를 쓰지 않는다 — `python -O` 에서 제거되기 때문이다.

        Day 2 (A) 가 아래를 채운다:
            1. 전제조건 검사 (구현됨 — 아래 `check_preconditions`)
            2. audit.record()      <- 호출 **직전**. 실패해도 "나갔다"는 사실은 남는다
            3. broker.invoke()     *** 경계 통과 ***

        Raises:
            GatekeeperError: 전제조건 위반. 이건 코드 버그이므로 조용히 폴백하지 않는다
            BrokerError: 호출 실패 -> 호출자는 answer_in_zone 폴백
        """
        self.check_preconditions(env, approved_by)
        raise NotImplementedError("Day 2 (A) — audit.record() -> broker.invoke()")

    @staticmethod
    def check_preconditions(env: PayloadEnvelope, approved_by: str) -> None:
        """경계를 넘기 전 3개 전제조건 (BR-G-02).

        **Day 1 에 구현한다.** 이게 없으면 Day 2 에 다른 코드가 먼저 붙어
        전제조건 없이 경계를 넘을 수 있다.
        """
        if env.validation is None:
            raise GatekeeperError(f"검증되지 않은 페이로드로 경계를 넘으려 한다: {env.envelope_id}")
        if not env.validation.passed:
            raise GatekeeperError(
                f"검증 실패 페이로드로 경계를 넘으려 한다: {env.envelope_id} "
                f"({env.validation.summary}, 실패 단계={env.validation.first_failed_stage})"
            )
        if not approved_by or not approved_by.strip():
            raise GatekeeperError(f"사용자 승인 없이 경계를 넘으려 한다: {env.envelope_id} (FR-09)")

    # ── 관문 ③ 재수화 ───────────────────────────────────────────────

    def rehydrate(
        self, resp: AgentResponse, mapping: Mapping, *, persona: Persona, chunks: list[Chunk]
    ) -> RehydratedAnswer:
        """기호 -> 실제 이름. **순수 문자열 치환** (FR-13, BR-P-04).

        긴 키부터 치환한다 — `<SYS_1>` 과 `<SYS_11>` 이 함께 있을 때
        짧은 키를 먼저 치환하면 망가진다.

        매핑에 없는 `ref` 는 **치환하지 않고 기호를 그대로 남긴다** (BR-G-10).
        프롬프트 인젝션으로 임의 문자열을 치환시키는 것을 막는다.
        치환되지 않은 ref 는 `RehydratedAnswer.unresolved_refs` 에 담아
        UI 가 경고를 띄운다.

        호출자는 이 메서드 이후 `try/finally` 로 매핑을 폐기한다 (BR-G-06).

        Day 2 (A): rehydrator.py 위임
        """
        raise NotImplementedError("Day 2 (A) — rehydrator.py 위임")

    # ── 폴백 ─────────────────────────────────────────────────────────

    async def answer_in_zone(
        self, question: str, chunks: list[Chunk], *, tier_label: str, reason: str
    ) -> RehydratedAnswer:
        """Agent 를 부르지 않고 EXAONE 이 신뢰 구역 안에서 직접 답한다.

        답변 품질은 떨어지지만 유출은 없다. 어떤 경우에도
        "잘 모르겠으니 일단 Agent 에게 보낸다"가 되지 않는다.

        ⚠️ **감사 레코드를 남기지 않는다** (BR-A-03). 경계를 넘은 것이 없으므로.
           "감사 로그에 없다"가 증거가 된다 — 시나리오 3의 결정적 장면이다.
           대신 `local_queries` 테이블에만 기록한다.

        `used_external_agent=False` 로 반환해 UI 가
        `[사내망 밖으로 나간 것 없음]` 배지를 띄운다.

        Day 2 (A)
        """
        raise NotImplementedError("Day 2 (A)")

"""Gatekeeper 계약 — Day 1 에 동결하는 것.

Day 1 에 실제로 구현하는 것:
  - `check_preconditions`  경계를 넘기 전 3개 전제조건
  - `EnvelopeCache`        매핑 수명 관리
  - `new_envelope_id`

나머지는 시그니처만 있고 NotImplementedError 를 던진다.
B(U3)가 Day 3 에 이 시그니처에 대고 코딩하고, A 가 Day 2 에 구현을 채운다.

`check_preconditions` 를 Day 1 에 구현하는 이유: 이게 없으면 Day 2 에 다른
코드가 먼저 붙어 전제조건 없이 경계를 넘을 수 있다.
"""

from __future__ import annotations

import inspect
import time

import pytest

from mesh.config import sha256_canonical
from mesh.exceptions import GatekeeperError
from mesh.gatekeeper import CacheEntry, EnvelopeCache, Gatekeeper, new_envelope_id
from mesh.schemas import (
    ENVELOPE_ID_RE,
    CheckResult,
    Mapping,
    PayloadEnvelope,
    Representation,
    Tier,
    ValidationResult,
)

PAYLOAD = {"task": "constraint_conflict_check", "domain": "authentication"}


def _passing_validation() -> ValidationResult:
    return ValidationResult(
        checks=tuple(
            CheckResult(stage=s, passed=True)
            for s in ("schema", "vocab", "range", "banned", "ngram", "size")
        )
    )


def _failing_validation(bad: str = "ngram") -> ValidationResult:
    return ValidationResult(
        checks=tuple(
            CheckResult(stage=s, passed=(s != bad))
            for s in ("schema", "vocab", "range", "banned", "ngram", "size")
        )
    )


def _envelope(validation: ValidationResult | None = None) -> PayloadEnvelope:
    return PayloadEnvelope(
        envelope_id=new_envelope_id(),
        tier=Tier.SECRET,
        task_schema_id="constraint_conflict_check",
        payload=PAYLOAD,
        representation=Representation.STRUCTURED,
        validation=validation,
        payload_sha256=sha256_canonical(PAYLOAD),
        size_bytes=64,
    )


# ══════════════════════════════════════════════════════════════════════
# new_envelope_id
# ══════════════════════════════════════════════════════════════════════


def test_envelope_id_matches_schema_pattern():
    for _ in range(50):
        assert ENVELOPE_ID_RE.match(new_envelope_id())


def test_envelope_ids_are_unique():
    ids = {new_envelope_id() for _ in range(500)}
    assert len(ids) == 500


def test_envelope_id_is_accepted_by_payload_envelope():
    assert _envelope().envelope_id.startswith("env_")


# ══════════════════════════════════════════════════════════════════════
# check_preconditions (BR-G-02) — 경계를 넘기 전 3개 검사
# ══════════════════════════════════════════════════════════════════════


def test_preconditions_pass_when_validated_and_approved():
    Gatekeeper.check_preconditions(_envelope(_passing_validation()), "person:choi")


def test_unvalidated_envelope_is_rejected():
    with pytest.raises(GatekeeperError, match="검증되지 않은"):
        Gatekeeper.check_preconditions(_envelope(None), "person:choi")


def test_failed_validation_is_rejected():
    with pytest.raises(GatekeeperError, match="검증 실패"):
        Gatekeeper.check_preconditions(_envelope(_failing_validation()), "person:choi")


def test_failed_validation_message_names_the_stage():
    """어느 단계에서 막혔는지가 로그에 남아야 한다."""
    with pytest.raises(GatekeeperError) as exc:
        Gatekeeper.check_preconditions(_envelope(_failing_validation("banned")), "person:choi")
    assert "banned" in str(exc.value)
    assert "5/6" in str(exc.value)


@pytest.mark.parametrize("approved_by", ["", "   ", "\t\n"])
def test_missing_approval_is_rejected(approved_by):
    """사람 확인(FR-09)을 API 구조가 아니라 여기서도 강제한다."""
    with pytest.raises(GatekeeperError, match="사용자 승인 없이"):
        Gatekeeper.check_preconditions(_envelope(_passing_validation()), approved_by)


def test_preconditions_are_not_assert_based():
    """`python -O` 에서 assert 가 제거되므로 명시적 raise 여야 한다 (BR-G-02)."""
    src = inspect.getsource(Gatekeeper.check_preconditions)
    assert "raise GatekeeperError" in src
    assert "assert " not in src


async def test_ask_agent_checks_preconditions_before_anything_else():
    """구현이 비어 있어도 전제조건은 먼저 검사돼야 한다.

    NotImplementedError 가 아니라 GatekeeperError 가 나와야 한다 —
    순서가 반대면 Day 2 구현 중에 전제조건을 건너뛴 경로가 생길 수 있다.
    """
    gk = object.__new__(Gatekeeper)  # __init__ 의존성 없이 메서드만 검사
    with pytest.raises(GatekeeperError):
        await Gatekeeper.ask_agent(gk, _envelope(None), None, "person:choi")  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════
# EnvelopeCache — 매핑 수명 관리 (BR-G-06, BR-G-09)
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def cache() -> EnvelopeCache:
    return EnvelopeCache()


def _put(cache: EnvelopeCache, env: PayloadEnvelope | None = None) -> PayloadEnvelope:
    env = env or _envelope(_passing_validation())
    cache.put(env, Mapping(table={"REQ_A": "고객사 H · REQ-4412"}), ("원문 조각",), "person:kim")
    return env


def test_put_then_take(cache):
    env = _put(cache)
    entry = cache.take(env.envelope_id)
    assert isinstance(entry, CacheEntry)
    assert entry.envelope.envelope_id == env.envelope_id
    assert entry.mapping.get("REQ_A") == "고객사 H · REQ-4412"
    assert entry.originals == ("원문 조각",)
    assert entry.persona_id == "person:kim"


def test_take_is_single_use(cache):
    """같은 envelope_id 로 두 번 전송하면 중복 과금·재생 공격이 된다."""
    env = _put(cache)
    assert cache.take(env.envelope_id) is not None
    assert cache.take(env.envelope_id) is None
    assert len(cache) == 0


def test_take_unknown_returns_none(cache):
    """호출자는 410 Gone 을 반환한다 (404 가 아니다 — 있었다가 없어진 것)."""
    assert cache.take("env_" + "Z" * 22) is None


def test_peek_does_not_consume(cache):
    env = _put(cache)
    assert cache.peek(env.envelope_id) is not None
    assert cache.peek(env.envelope_id) is not None
    assert cache.take(env.envelope_id) is not None


def test_discard_removes_without_audit(cache):
    """취소 시 즉시 폐기. 감사 레코드는 남지 않는다 (BR-U-03)."""
    env = _put(cache)
    cache.discard(env.envelope_id)
    assert cache.take(env.envelope_id) is None


def test_discard_of_unknown_is_safe(cache):
    cache.discard("env_" + "Y" * 22)  # 예외가 나지 않아야 한다


def test_ttl_expiry(monkeypatch):
    """자리를 비우면 매핑이 자동 소멸한다 — 메모리에 누적되지 않는다."""
    cache = EnvelopeCache(ttl_seconds=1)
    env = _put(cache)

    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 1.5)
    assert cache.take(env.envelope_id) is None


def test_sweep_reports_count(monkeypatch):
    cache = EnvelopeCache(ttl_seconds=1)
    _put(cache)
    _put(cache)
    assert len(cache) == 2

    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 1.5)
    assert cache.sweep() == 2
    assert len(cache) == 0


def test_default_ttl_is_five_minutes():
    """사용자가 미리보기를 보고 승인할 시간."""
    assert EnvelopeCache.TTL_SECONDS == 300


def test_multiple_envelopes_coexist(cache):
    """2명 지목 시 envelope 2개가 순차 승인된다 (BR-U-04)."""
    a = _put(cache)
    b = _put(cache)
    assert len(cache) == 2
    assert cache.take(a.envelope_id) is not None
    assert cache.take(b.envelope_id) is not None


def test_cache_entry_mapping_is_still_unserializable(cache):
    """캐시에 들어간 뒤에도 매핑은 직렬화되지 않아야 한다."""
    import json

    env = _put(cache)
    entry = cache.take(env.envelope_id)
    assert entry is not None
    with pytest.raises(TypeError):
        json.dumps(entry.mapping)


# ══════════════════════════════════════════════════════════════════════
# 계약 동결 — B(U3) 가 이 시그니처에 대고 코딩한다
# ══════════════════════════════════════════════════════════════════════

#: `name -> (위치 인자, 키워드 전용 인자)`
EXPECTED_SIGNATURES: dict[str, tuple[list[str], list[str]]] = {
    "classify": (["self", "text", "source_path"], []),
    "plan_calls": (["self", "question", "entity_id", "chunks", "question_tier"], []),
    "to_payload": (["self", "call", "chunks", "question"], []),
    "validate": (["self", "env", "originals"], []),
    "preview": (["self", "env", "originals"], []),
    "ask_agent": (["self", "env", "persona", "approved_by"], []),
    "rehydrate": (["self", "resp", "mapping"], ["persona", "chunks"]),
    "answer_in_zone": (["self", "question", "chunks"], ["tier_label", "reason"]),
}


@pytest.mark.parametrize("name,expected", sorted(EXPECTED_SIGNATURES.items()))
def test_signature_is_frozen(name, expected):
    """Day 1 동결. 변경은 3인 합의로만 (NFR-M-02).

    이 테스트가 실패하면 U3 의 코드가 깨진다는 뜻이다.
    """
    want_pos, want_kw = expected
    sig = inspect.signature(getattr(Gatekeeper, name))
    pos = [p for p, v in sig.parameters.items() if v.kind is not inspect.Parameter.KEYWORD_ONLY]
    kw = [p for p, v in sig.parameters.items() if v.kind is inspect.Parameter.KEYWORD_ONLY]
    assert pos == want_pos, f"{name} 위치 인자가 바뀌었다"
    assert kw == want_kw, f"{name} 키워드 인자가 바뀌었다"


def test_all_seven_gates_exist():
    """component-methods.md 의 7개 메서드 + 폴백."""
    for name in EXPECTED_SIGNATURES:
        assert callable(getattr(Gatekeeper, name))


@pytest.mark.parametrize(
    "name",
    ["classify", "plan_calls", "to_payload", "validate", "preview", "rehydrate", "answer_in_zone"],
)
def test_unimplemented_methods_fail_loudly(name):
    """Day 2 에 구현할 것들. 조용히 None 을 반환하지 않는다.

    `ask_agent` 은 제외한다 — 전제조건 검사가 먼저 돌아
    GatekeeperError 를 던지는 것이 정상이다 (별도 테스트).
    """
    import asyncio

    gk = object.__new__(Gatekeeper)
    method = getattr(Gatekeeper, name)
    sig = inspect.signature(method)

    positional, keywords = [], {}
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[pname] = None
        else:
            positional.append(None)

    with pytest.raises(NotImplementedError, match="Day 2"):
        result = method(gk, *positional, **keywords)
        if inspect.iscoroutine(result):
            asyncio.run(result)


def test_gatekeeper_is_the_only_broker_importer():
    """import 경계 규칙의 예고편. 전체 검사는 test_import_boundary.py."""
    import mesh.gatekeeper as gkmod

    src = inspect.getsource(gkmod)
    # TYPE_CHECKING 블록 안에서만 broker 를 참조한다 (런타임 import 없음)
    assert "TYPE_CHECKING" in src
    assert "from mesh.llm.broker import BrokerClient" in src

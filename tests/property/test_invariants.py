"""PB-1 ~ PB-10 — 속성 기반 테스트 (PBT-01 ~ PBT-04).

**PB-5 가 이 프로젝트에서 가장 중요한 테스트다.** 예제 기반 테스트로는 절대
증명할 수 없다 — 우리가 생각해낸 원문에 대해서만 확인하게 되기 때문이다.

실패 시 `print_blob=True` 가 `@reproduce_failure` blob 을 출력한다.
CI 로그의 그 blob 으로 정확히 재현한다 (`tests/conftest.py`).
"""

from __future__ import annotations

import copy
import json
import pickle
from itertools import permutations

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from mesh import validator
from mesh.extractor import assemble, build_payload, coerce
from mesh.pseudonymizer import apply as pseudonymize
from mesh.rehydrator import rehydrate_text, symbols_in
from mesh.schemas import (
    DROP,
    Mapping,
    PayloadEnvelope,
    Representation,
    Tier,
)
from tests.generators import (
    adversarial_raw,
    identifier_texts,
    slot_defs,
    source_texts,
    task_schemas,
    valid_facts,
)

# ══════════════════════════════════════════════════════════════════════
# PB-1 · 왕복 — 가명화 -> 재수화 = 항등
# ══════════════════════════════════════════════════════════════════════


@given(identifier_texts())
def test_pb1_pseudonymize_rehydrate_roundtrip(pair):
    text, targets = pair
    result = pseudonymize([text], targets)
    restored, unresolved = rehydrate_text(result.texts[0], result.mapping)
    assert restored == text
    assert unresolved == ()


@given(identifier_texts())
def test_pb1_no_identifier_survives_substitution(pair):
    """왕복이 성립한다는 것만으로는 부족하다 — 치환된 형태에 식별자가 없어야 한다."""
    text, targets = pair
    result = pseudonymize([text], targets)
    for literal in result.substituted:
        assert literal not in result.texts[0]


# ══════════════════════════════════════════════════════════════════════
# PB-2 · 왕복 — PayloadEnvelope 직렬화
# ══════════════════════════════════════════════════════════════════════


@given(
    tier=st.sampled_from(list(Tier)),
    representation=st.sampled_from(list(Representation)),
    payload=st.dictionaries(
        st.text(min_size=1, max_size=10),
        st.one_of(st.integers(), st.booleans(), st.text(max_size=20)),
        max_size=5,
    ),
)
def test_pb2_envelope_serialization_roundtrip(tier, representation, payload):
    env = PayloadEnvelope(
        envelope_id="env_AAAAAAAAAAAAAAAAAAAAAA",
        tier=tier,
        task_schema_id="t",
        payload=payload,
        representation=representation,
        payload_sha256="0" * 64,
        size_bytes=1,
    )
    again = PayloadEnvelope.model_validate(json.loads(env.model_dump_json()))
    assert again == env


@given(
    tier=st.sampled_from(list(Tier)),
    representation=st.sampled_from(list(Representation)),
)
def test_pb2_envelope_never_serializes_a_mapping(tier, representation):
    """`PayloadEnvelope` 에 `mapping` 필드가 없다는 것을 직렬화로 확인한다."""
    env = PayloadEnvelope(
        envelope_id="env_AAAAAAAAAAAAAAAAAAAAAA",
        tier=tier,
        task_schema_id="t",
        payload={"facts": {}},
        representation=representation,
        payload_sha256="0" * 64,
        size_bytes=1,
    )
    assert "mapping" not in env.model_dump_json()


# ══════════════════════════════════════════════════════════════════════
# PB-3 · 불변식 — 조립 결과의 키는 항상 슬롯 이름의 부분집합
# ══════════════════════════════════════════════════════════════════════


@given(schema=task_schemas(), data=st.data())
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_pb3_assembled_keys_are_a_subset_of_slot_names(schema, data):
    """🔴 임의의 `raw` 에 대해 성립해야 한다.

    이것이 "무엇을 지울까가 아니라 무엇만 보낼까"의 기계적 표현이다.
    """
    raw = data.draw(adversarial_raw(schema))
    out = assemble(raw, schema)
    assert set(out) <= schema.slot_names


@given(
    schema=task_schemas(), raw=st.one_of(st.none(), st.text(), st.integers(), st.lists(st.text()))
)
def test_pb3_holds_for_non_dict_raw(schema, raw):
    assert assemble(raw, schema) == {}


@given(schema=task_schemas(), data=st.data())
def test_pb3_assembled_values_are_never_containers(schema, data):
    """중첩 구조가 페이로드에 들어가면 그 안에 원문을 숨길 수 있다."""
    raw = data.draw(adversarial_raw(schema))
    for value in assemble(raw, schema).values():
        assert isinstance(value, str | int | bool)
        assert not isinstance(value, dict | list | tuple)


# ══════════════════════════════════════════════════════════════════════
# PB-4 · 불변식 — 모든 문자열 값이 어휘 사전 안
# ══════════════════════════════════════════════════════════════════════


@given(schema=task_schemas(), data=st.data())
def test_pb4_all_string_values_are_in_vocabulary(schema, data):
    raw = data.draw(adversarial_raw(schema))
    out = assemble(raw, schema)
    allowed = {v for s in schema.slots if s.kind == "enum" for v in (s.allowed or ())}
    for name, value in out.items():
        if isinstance(value, str):
            assert value in allowed, f"{name}={value!r} 이 어휘 사전 밖이다"


@given(schema=task_schemas(), data=st.data())
def test_pb4_int_slots_only_hold_ints(schema, data):
    raw = data.draw(adversarial_raw(schema))
    out = assemble(raw, schema)
    for slot in schema.slots:
        if slot.name not in out:
            continue
        value = out[slot.name]
        if slot.kind == "int":
            assert isinstance(value, int) and not isinstance(value, bool)
        elif slot.kind == "bool":
            assert isinstance(value, bool)


# ══════════════════════════════════════════════════════════════════════
# PB-5 · 불변식 — 원문의 어떤 5-gram 도 페이로드에 없다  🔴 가장 중요
# ══════════════════════════════════════════════════════════════════════


@given(schema=task_schemas(), source=source_texts(), data=st.data())
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_pb5_no_source_ngram_reaches_the_payload(schema, source, data):
    """🔴 임의의 원문 × 임의의 적대적 모델 응답에 대해 성립해야 한다.

    모델이 원문 조각을 슬롯 값에 넣으려 해도 `coerce()` 가 허용값이 아니면
    버리므로 조립 결과에 남을 수 없다. 그 성질을 검증기로 확인한다.
    """
    raw = data.draw(adversarial_raw(schema, source=source))
    facts = assemble(raw, schema)
    payload = build_payload(schema, (), {"REQ_A": facts})

    check = validator.check_no_source_ngram(
        payload, (source,), representation=Representation.STRUCTURED, n=5
    )
    assert check.passed, f"원문 조각 유출: {check.offending}"


@given(schema=task_schemas(), source=source_texts(), data=st.data())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_pb5_holds_for_short_originals_too(schema, source, data):
    """짧은 원문은 5-gram 이 없으므로 전체가 하나의 gram 으로 검사된다."""
    short = " ".join(source.split()[:3])
    assume(short)
    raw = data.draw(adversarial_raw(schema, source=short))
    payload = build_payload(schema, (), {"REQ_A": assemble(raw, schema)})
    check = validator.check_no_source_ngram(
        payload, (short,), representation=Representation.STRUCTURED, n=5
    )
    assert check.passed, check.offending


@given(source=source_texts())
def test_pb5_detector_catches_a_planted_quote(source):
    """검사기가 아무것도 못 잡으면 무의미하다 — 심은 인용을 잡는지 확인한다."""
    tokens = source.split()
    assume(len(tokens) >= 5)
    quote = " ".join(tokens[:5])
    check = validator.check_no_source_ngram(
        {"answer_format": {"reason": quote}},
        (source,),
        representation=Representation.STRUCTURED,
        n=5,
    )
    assert not check.passed


@given(source=source_texts())
def test_pb5_whitespace_variants_do_not_evade(source):
    tokens = source.split()
    assume(len(tokens) >= 5)
    quote = "  \n\t ".join(tokens[:5])
    check = validator.check_no_source_ngram(
        {"answer_format": {"reason": quote}},
        (source,),
        representation=Representation.STRUCTURED,
        n=5,
    )
    assert not check.passed


# ══════════════════════════════════════════════════════════════════════
# PB-6 · 불변식 — placeholder 일관성
# ══════════════════════════════════════════════════════════════════════


@given(identifier_texts())
def test_pb6_same_target_gets_the_same_placeholder(pair):
    """일관성이 깨지면 Claude 가 관계를 추론하지 못한다 (BR-P-02)."""
    text, targets = pair
    result = pseudonymize([text, text], targets)
    assert result.texts[0] == result.texts[1]

    # 같은 리터럴이 여러 번 나와도 placeholder 는 하나다
    for placeholder, literal in result.mapping.table.items():
        assert text.count(literal) == 0 or result.texts[0].count(placeholder) >= 1


@given(identifier_texts())
def test_pb6_placeholders_are_unique_per_literal(pair):
    text, targets = pair
    result = pseudonymize([text], targets)
    values = list(result.mapping.table.values())
    assert len(values) == len(set(values))
    assert len(result.mapping.table) == len(set(result.mapping.table))


@given(identifier_texts())
def test_pb6_numbering_is_order_independent(pair):
    """문서 순서가 바뀌어도 같은 번호가 나온다."""
    text, targets = pair
    a = pseudonymize([text, ""], targets)
    b = pseudonymize(["", text], targets)
    assert a.mapping.table == b.mapping.table


# ══════════════════════════════════════════════════════════════════════
# PB-7 · 불변식 — max(tiers)
# ══════════════════════════════════════════════════════════════════════


@given(st.lists(st.sampled_from(list(Tier)), min_size=1, max_size=8))
def test_pb7_max_returns_the_highest_tier(tiers):
    """🔴 `StrEnum` 의 기본 비교는 알파벳 순이다.
    `Tier.__gt__` 를 잊으면 `max(INTERNAL, OPEN) == OPEN` 이 되어 조용히 유출된다."""
    got = max(tiers)
    assert got.rank == max(t.rank for t in tiers)
    if Tier.SECRET in tiers:
        assert got is Tier.SECRET


@given(st.lists(st.sampled_from(list(Tier)), min_size=1, max_size=6))
def test_pb7_max_is_order_independent(tiers):
    baseline = max(tiers)
    for order in permutations(tiers):
        assert max(order) is baseline


@given(a=st.sampled_from(list(Tier)), b=st.sampled_from(list(Tier)))
def test_pb7_comparisons_are_consistent(a, b):
    assert (a < b) == (a.rank < b.rank)
    assert (a > b) == (a.rank > b.rank)
    assert (a <= b) == (a.rank <= b.rank)
    assert (a >= b) == (a.rank >= b.rank)


# ══════════════════════════════════════════════════════════════════════
# PB-8 · 불변식 — AgentCall.tier 는 단일값
# ══════════════════════════════════════════════════════════════════════


@given(st.lists(st.sampled_from(list(Tier)), min_size=1, max_size=5))
def test_pb8_agent_call_tier_is_never_a_collection(tiers):
    """등급이 섞인 페이로드는 타입 수준에서 생성되지 않는다 (BR-G-08)."""
    from pydantic import ValidationError

    from mesh.schemas import AgentCall

    with pytest.raises(ValidationError):
        AgentCall(
            call_id="c",
            entity_id="person:kim",
            tier=tiers,  # type: ignore[arg-type]
            task_schema_id="t",
        )


@given(tier=st.sampled_from(list(Tier)))
def test_pb8_single_tier_is_accepted(tier):
    from mesh.schemas import AgentCall

    call = AgentCall(call_id="c", entity_id="person:kim", tier=tier, task_schema_id="t")
    assert isinstance(call.tier, Tier)


# ══════════════════════════════════════════════════════════════════════
# PB-9 · 불변식 — Mapping 직렬화는 항상 TypeError
# ══════════════════════════════════════════════════════════════════════


@given(
    st.dictionaries(st.text(min_size=1, max_size=10), st.text(min_size=1, max_size=20), max_size=8)
)
def test_pb9_mapping_cannot_be_pickled(table):
    """매핑이 유출되면 과거의 모든 감사 로그가 복호화된다 (BR-G-09)."""
    m = Mapping(table=table)
    with pytest.raises(TypeError):
        pickle.dumps(m)


@given(
    st.dictionaries(st.text(min_size=1, max_size=10), st.text(min_size=1, max_size=20), max_size=8)
)
def test_pb9_mapping_cannot_be_deep_copied(table):
    with pytest.raises(TypeError):
        copy.deepcopy(Mapping(table=table))


@given(
    st.dictionaries(
        st.text(min_size=1, max_size=10),
        # 3자 이상 + 고정 문구("Mapping(<N entries redacted>)")와 겹치지 않는 값.
        # 한 글자 값("e")은 "entries" 의 부분 문자열이라 검사가 무의미해진다.
        st.from_regex(r"\A[가-힣]{3,20}\Z", fullmatch=True),
        min_size=1,
        max_size=8,
    )
)
def test_pb9_mapping_repr_hides_values(table):
    m = Mapping(table=table)
    text = repr(m)
    assert "redacted" in text
    for value in table.values():
        assert value not in text


# ══════════════════════════════════════════════════════════════════════
# PB-10 · 멱등 — coerce
# ══════════════════════════════════════════════════════════════════════


@given(
    slot=slot_defs(),
    value=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(10**6), max_value=10**6),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.text(max_size=30),
        st.lists(st.text(max_size=5), max_size=3),
        st.dictionaries(st.text(max_size=5), st.text(max_size=5), max_size=2),
    ),
)
def test_pb10_coerce_is_idempotent(slot, value):
    once = coerce(value, slot)
    twice = coerce(once, slot)
    if once is DROP:
        assert twice is DROP
    else:
        assert twice == once
        assert type(twice) is type(once)


@given(slot=slot_defs(), value=st.text(max_size=30))
def test_pb10_coerce_output_matches_slot_kind(slot, value):
    out = coerce(value, slot)
    if out is DROP:
        return
    match slot.kind:
        case "enum":
            assert out in (slot.allowed or ())
        case "int":
            assert isinstance(out, int) and not isinstance(out, bool)
        case "bool":
            assert isinstance(out, bool)


# ══════════════════════════════════════════════════════════════════════
# 보조 — 유효 입력은 통과해야 한다 (거짓 음성 방지)
# ══════════════════════════════════════════════════════════════════════


@given(schema=task_schemas(), data=st.data())
def test_valid_facts_survive_assembly(schema, data):
    """모든 것을 버리는 조립기는 PB-3/4/5 를 자동으로 통과한다.

    유효한 입력이 살아남는지 함께 확인해 그 함정을 막는다.
    """
    facts = data.draw(valid_facts(schema))
    out = assemble(facts, schema)
    assert out == facts


@given(schema=task_schemas(), data=st.data())
def test_valid_facts_pass_range_and_vocab_checks(schema, data):
    facts = data.draw(valid_facts(schema))
    payload = build_payload(schema, (), {"REQ_A": facts})
    assert validator.check_ranges(payload, schema).passed
    assert validator.check_schema(payload, schema, Representation.STRUCTURED).passed


@given(identifier_texts())
def test_technical_terms_are_never_substituted(pair):
    text, targets = pair
    body = f"{text} RandomOverSampler OAuth SSO"
    result = pseudonymize([body], targets)
    for term in ("RandomOverSampler", "OAuth", "SSO"):
        assert term in result.texts[0]


@given(source=source_texts())
def test_symbols_in_finds_nothing_in_plain_text(source):
    """기호 탐지기가 일반 텍스트를 기호로 오인하면 `unresolved_refs` 가 늘 채워진다."""
    for sym in symbols_in(source):
        assert "_" in sym


# ══════════════════════════════════════════════════════════════════════
# 생성기 자체 검사 — 아무것도 시험하지 않는 생성기는 무의미하다 (PBT-07)
# ══════════════════════════════════════════════════════════════════════


def test_adversarial_generator_actually_produces_adversarial_input():
    """PB-3/4/5 가 형식적 테스트가 되지 않았는지 확인한다.

    임의 문자열 키만 만드는 생성기는 슬롯 이름과 거의 겹치지 않아
    `assemble()` 이 늘 빈 dict 를 반환하고, 테스트가 **아무 일도 하지 않는데
    통과한다.** 그 함정을 여기서 잡는다.
    """
    from hypothesis import find

    from tests.generators import task_schemas as ts

    schema = find(ts(min_slots=3, max_slots=3), lambda s: len(s.slots) == 3)
    source = "고객사 요구사항 세션 바인딩 필수 최대 여덟 시간 재사용 금지"

    saw_unregistered_key = False
    saw_source_fragment = False
    saw_type_mismatch = False
    saw_surviving_value = False

    # `.example()` 은 권장되지 않으므로 `@given` 으로 표본을 모은다
    samples: list[dict] = []

    @given(data=st.data())
    @settings(max_examples=200, deadline=None, database=None)
    def collect(data):
        samples.append(data.draw(adversarial_raw(schema, source=source)))

    collect()

    slot_names = schema.slot_names
    fragments = set(source.split())
    for raw in samples:
        if any(k not in slot_names for k in raw):
            saw_unregistered_key = True
        for value in raw.values():
            if isinstance(value, str) and fragments & set(value.split()):
                saw_source_fragment = True
            if isinstance(value, str | float | dict | list) or value is None:
                saw_type_mismatch = True
        if assemble(raw, schema):
            saw_surviving_value = True

    assert saw_unregistered_key, "미등록 키를 만들지 않는다"
    assert saw_source_fragment, "원문 조각을 슬롯 값에 넣지 않는다"
    assert saw_type_mismatch, "타입 불일치를 만들지 않는다"
    assert saw_surviving_value, "모든 값이 버려진다 — 조립기를 시험하지 못한다"

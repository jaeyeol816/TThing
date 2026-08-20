"""검증 6단계 — 각 단계의 실패 케이스와 등급별 차이.

이 파일이 확인하는 것 중 가장 중요한 둘:
  - 6단계 각각이 **실제로 무언가를 잡는다** (통과만 하는 검사는 무의미하다)
  - `validator.py` 가 **순수하다** (U5 Lambda 번들 조건)
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from mesh import validator
from mesh.schemas import EXCLUDED_CATEGORIES_DEFAULT, Representation, Tier

# ══════════════════════════════════════════════════════════════════════
# 순수성 — U5 Lambda 번들 조건 (BR-V-07)
# ══════════════════════════════════════════════════════════════════════

#: `validator.py` 가 import 해도 되는 것. 늘리면 Lambda 번들이 깨진다.
ALLOWED_VALIDATOR_IMPORTS = frozenset(
    {"__future__", "json", "re", "collections.abc", "mesh.schemas"}
)

FORBIDDEN_VALIDATOR_NAMES = ("os", "pathlib", "sqlite3", "httpx", "boto3", "yaml", "mesh.config")


def _validator_tree() -> ast.Module:
    return ast.parse(Path(inspect.getfile(validator)).read_text(encoding="utf-8"))


def test_validator_imports_are_minimal():
    """설정·I/O 를 끌어들이지 않는다.

    `mesh.config` 를 import 하면 Lambda 가 `yaml` 과 환경변수까지 함께 들고
    가야 하고, 순수 함수라는 성질이 조용히 깨진다.
    """
    tree = _validator_tree()
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    bad = sorted(m for m in imported if m not in ALLOWED_VALIDATOR_IMPORTS)
    assert not bad, f"validator.py 가 허용되지 않은 모듈을 import 한다: {bad}"


@pytest.mark.parametrize("name", FORBIDDEN_VALIDATOR_NAMES)
def test_validator_has_no_io_names(name):
    src = Path(inspect.getfile(validator)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name != name for a in node.names), f"{name} import 금지"
        if isinstance(node, ast.ImportFrom):
            assert node.module != name, f"{name} import 금지"


def test_validator_has_no_module_level_mutable_state():
    """전역 가변 상태가 있으면 Lambda 의 웜 스타트에서 요청이 섞인다."""
    tree = _validator_tree()
    bad: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if isinstance(value, ast.Dict | ast.List | ast.Set):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bad += [t.id for t in targets if isinstance(t, ast.Name)]
    assert not bad, f"가변 전역: {bad}"


# ══════════════════════════════════════════════════════════════════════
# 정규화와 n-gram
# ══════════════════════════════════════════════════════════════════════


def test_normalize_collapses_whitespace_and_lowercases():
    assert validator.normalize_text("  Session   Binding\n\tREQUIRED ") == (
        "session binding required"
    )


def test_normalize_defeats_whitespace_evasion():
    """공백만 바꿔 5-gram 대조를 우회하려는 시도를 막는다."""
    assert validator.normalize_text("a b c d e") == validator.normalize_text("a  b\nc\td   e")


def test_ngram_set_basic():
    grams = validator.ngram_set("a b c d e f", 5)
    assert grams == {"a b c d e", "b c d e f"}


def test_short_text_is_still_checked_as_one_gram():
    """토큰이 n 개보다 적은 원문이 검사를 통째로 빠져나가면 안 된다.

    "세션 바인딩 필수" 같은 세 단어 원문이 빈 집합이 되면 그 문장은
    페이로드에 그대로 실려도 잡히지 않는다.
    """
    grams = validator.ngram_set("세션 바인딩 필수", 5)
    assert grams == {"세션 바인딩 필수"}


def test_ngram_rejects_zero_n():
    with pytest.raises(ValueError, match="1 이상"):
        validator.ngram_set("a b c", 0)


def test_sentences_drops_short_fragments():
    text = "짧다. 이 문장은 충분히 길어서 대조 대상이 된다 그렇다."
    out = validator.sentences(text)
    assert len(out) == 1
    assert "충분히" in out[0]


# ══════════════════════════════════════════════════════════════════════
# 유효 페이로드 만들기
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def good_payload(conflict_schema):
    return {
        "task": conflict_schema.schema_id,
        "domain": conflict_schema.domain,
        "question_template": conflict_schema.question_template,
        "entities": [
            {"ref": "REQ_A", "role": "external_requirement"},
            {"ref": "COMP_B", "role": "our_component"},
        ],
        "facts": {
            "auth_mechanism_class": "challenge_response",
            "session_binding": "required",
            "credential_reuse_allowed": False,
            "max_session_hours": 8,
            "credential_lifetime_hours": 24,
            "renewal_mode": "background_silent",
        },
        "answer_format": dict(conflict_schema.answer_format),
    }


ORIGINALS = (
    "H社 5G 코어망 요구사항 REQ-4412 는 인증이 세션에 바인딩된 EAP-AKA 방식이어야 하며 "
    "세션 최대 유지시간은 8시간이다. 계약 금액은 12억원이다.",
    "우리 SDK v3.2 는 토큰 수명 24시간에 무음 갱신을 사용하고 세션 바인딩이 없다.",
)


def test_good_payload_passes_all_six(good_payload, conflict_schema, vocab, banned):
    result = validator.validate(
        good_payload,
        schema=conflict_schema,
        vocab=vocab,
        banned=banned,
        originals=ORIGINALS,
        representation=Representation.STRUCTURED,
    )
    assert result.passed, [c for c in result.checks if not c.passed]
    assert result.summary == "6/6"
    assert len(result.checks) == 6


def test_stage_order_is_fixed(good_payload, conflict_schema, vocab, banned):
    result = validator.validate(
        good_payload,
        schema=conflict_schema,
        vocab=vocab,
        banned=banned,
        originals=(),
        representation=Representation.STRUCTURED,
    )
    assert [c.stage for c in result.checks] == list(validator.ALL_STAGES)


# ══════════════════════════════════════════════════════════════════════
# 1단계 · 스키마
# ══════════════════════════════════════════════════════════════════════


def test_schema_rejects_unregistered_key(good_payload, conflict_schema):
    good_payload["facts"]["max_session_duration"] = "8 hours"
    r = validator.check_schema(good_payload, conflict_schema, Representation.STRUCTURED)
    assert not r.passed
    assert "max_session_duration" in r.offending


def test_schema_rejects_excerpts_in_secret_tier(good_payload, conflict_schema):
    """기밀 등급에는 텍스트를 담을 키가 없다 — 이것이 "원문 0개"의 구조적 근거다."""
    good_payload["excerpts"] = {"REQ_A": "원문 문장"}
    r = validator.check_schema(good_payload, conflict_schema, Representation.STRUCTURED)
    assert not r.passed
    assert any("excerpts" in o for o in r.offending)


def test_schema_allows_excerpts_in_pseudonymized_tier(conflict_schema):
    payload = {
        "task": conflict_schema.schema_id,
        "domain": conflict_schema.domain,
        "question_template": conflict_schema.question_template,
        "entities": [{"ref": "COMP_A", "role": "our_component"}],
        "excerpts": {"COMP_A": "임의의 본문"},
        "answer_format": dict(conflict_schema.answer_format),
    }
    r = validator.check_schema(payload, conflict_schema, Representation.PSEUDONYMIZED)
    assert r.passed


def test_schema_rejects_non_ref_excerpt_keys(conflict_schema):
    payload = {"excerpts": {"자유로운 키": "본문"}}
    r = validator.check_schema(payload, conflict_schema, Representation.PSEUDONYMIZED)
    assert not r.passed


def test_schema_rejects_non_dict_payload(conflict_schema):
    r = validator.check_schema(["not", "a", "dict"], conflict_schema, Representation.STRUCTURED)
    assert not r.passed


def test_answer_format_keys_are_allowed(good_payload, conflict_schema):
    """`conflict`·`mitigations` 는 슬롯이 아니지만 동결된 스키마에서 온다."""
    ok = validator.allowed_keys(conflict_schema, Representation.STRUCTURED)
    assert set(conflict_schema.answer_format) <= ok


# ══════════════════════════════════════════════════════════════════════
# 2단계 · 어휘
# ══════════════════════════════════════════════════════════════════════


def test_vocab_rejects_out_of_vocabulary_enum(good_payload, conflict_schema, vocab):
    good_payload["facts"]["session_binding"] = "mandatory"  # 허용값이 아니다
    r = validator.check_vocab(good_payload, conflict_schema, vocab, Representation.STRUCTURED)
    assert not r.passed
    assert "mandatory" in r.offending


def test_vocab_rejects_free_text_anywhere(good_payload, conflict_schema, vocab):
    good_payload["entities"].append({"ref": "REQ_C", "role": "고객사 H 요구사항"})
    r = validator.check_vocab(good_payload, conflict_schema, vocab, Representation.STRUCTURED)
    assert not r.passed


def test_vocab_allows_ref_labels_and_placeholders(conflict_schema, vocab):
    payload = {"entities": [{"ref": "COMP_A", "role": "our_component"}]}
    assert validator.check_vocab(payload, conflict_schema, vocab, Representation.STRUCTURED).passed


def test_vocab_skips_excerpt_bodies_for_internal(conflict_schema, vocab):
    """사내 등급의 정의는 "어휘 제한"이 아니라 "식별자 제거"다."""
    payload = {
        "entities": [{"ref": "COMP_A", "role": "our_component"}],
        "excerpts": {"COMP_A": "자유 문장이 그대로 들어 있다 어휘 사전에 없는 말이다"},
    }
    assert not validator.check_vocab(
        payload, conflict_schema, vocab, Representation.STRUCTURED
    ).passed
    assert validator.check_vocab(
        payload, conflict_schema, vocab, Representation.PSEUDONYMIZED
    ).passed


# ══════════════════════════════════════════════════════════════════════
# 3단계 · 범위
# ══════════════════════════════════════════════════════════════════════


def test_range_rejects_out_of_bounds(good_payload, conflict_schema):
    good_payload["facts"]["max_session_hours"] = 99999
    r = validator.check_ranges(good_payload, conflict_schema)
    assert not r.passed
    assert any("99999" in o for o in r.offending)


def test_range_rejects_string_in_int_slot(good_payload, conflict_schema):
    good_payload["facts"]["max_session_hours"] = "8"
    assert not validator.check_ranges(good_payload, conflict_schema).passed


def test_range_rejects_bool_in_int_slot(good_payload, conflict_schema):
    """파이썬에서 `True == 1` 이라 조용히 통과할 수 있는 자리다."""
    good_payload["facts"]["max_session_hours"] = True
    assert not validator.check_ranges(good_payload, conflict_schema).passed


def test_range_rejects_int_in_bool_slot(good_payload, conflict_schema):
    good_payload["facts"]["credential_reuse_allowed"] = 0
    assert not validator.check_ranges(good_payload, conflict_schema).passed


# ══════════════════════════════════════════════════════════════════════
# 4단계 · 금칙어 — 모든 등급의 하한선
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "leak",
    ["H社", "하나텔", "REQ-4412", "CTR-204817", "12억원", "1,200,000원", "EAP-AKA"],
)
def test_banned_catches_each_category(conflict_schema, banned, leak):
    payload = {"entities": [{"ref": "REQ_A", "role": leak}]}
    assert not validator.check_banned(payload, banned).passed


def test_banned_applies_to_excerpts_too(banned):
    """가명화가 실패해도 금칙어는 여기서 걸린다."""
    payload = {"excerpts": {"COMP_A": "고객사 H 와의 계약 CTR-204817"}}
    assert not validator.check_banned(payload, banned).passed


def test_banned_passes_clean_payload(good_payload, banned):
    assert validator.check_banned(good_payload, banned).passed


# ══════════════════════════════════════════════════════════════════════
# 5단계 · 원문 대조 — 가장 강력한 검사
# ══════════════════════════════════════════════════════════════════════


def test_ngram_catches_verbatim_quote(conflict_schema):
    payload = {
        "entities": [{"ref": "REQ_A", "role": "인증이 세션에 바인딩된 EAP-AKA 방식이어야 하며"}]
    }
    r = validator.check_no_source_ngram(
        payload, ORIGINALS, representation=Representation.STRUCTURED
    )
    assert not r.passed
    assert r.offending


def test_ngram_defeats_whitespace_evasion():
    original = "세션 최대 유지시간은 여덟 시간으로 제한한다"
    payload = {"note": "세션    최대\n유지시간은 여덟 시간으로 제한한다"}
    r = validator.check_no_source_ngram(
        payload, (original,), representation=Representation.STRUCTURED
    )
    assert not r.passed


def test_ngram_passes_structured_payload(good_payload):
    r = validator.check_no_source_ngram(
        good_payload, ORIGINALS, representation=Representation.STRUCTURED
    )
    assert r.passed


def test_internal_tier_checks_identifiers_only():
    """사내 등급은 기술 내용이 남아도 통과하고 식별자가 남으면 실패한다 (BR-P-03)."""
    original = "atlas_ml 파이프라인은 RandomOverSampler 로 소수 클래스를 오버샘플링 한다"
    identifiers = ("atlas_ml", "atlas-ml")

    leaked = {"excerpts": {"COMP_A": original}}
    clean = {
        "excerpts": {
            "COMP_A": "<PROJ_1> 파이프라인은 RandomOverSampler 로 소수 클래스를 오버샘플링 한다"
        }
    }

    assert not validator.check_no_source_ngram(
        leaked,
        (original,),
        representation=Representation.PSEUDONYMIZED,
        identifiers=identifiers,
    ).passed
    assert validator.check_no_source_ngram(
        clean,
        (original,),
        representation=Representation.PSEUDONYMIZED,
        identifiers=identifiers,
    ).passed


def test_internal_tier_would_fail_under_full_ngram_rule():
    """왜 사내 등급에 별도 규칙이 필요한지 — 전체 5-gram 규칙이면 반드시 실패한다."""
    original = "atlas_ml 파이프라인은 RandomOverSampler 로 소수 클래스를 오버샘플링 한다"
    clean = {
        "excerpts": {
            "COMP_A": "<PROJ_1> 파이프라인은 RandomOverSampler 로 소수 클래스를 오버샘플링 한다"
        }
    }
    assert not validator.check_no_source_ngram(
        clean, (original,), representation=Representation.STRUCTURED
    ).passed


def test_verbatim_tier_records_that_check_is_not_applicable():
    """조용히 통과시키지 않는다 — 이유가 `detail` 에 남는다."""
    r = validator.check_no_source_ngram(
        {"excerpts": {"COMP_A": ORIGINALS[0]}},
        ORIGINALS,
        representation=Representation.VERBATIM,
    )
    assert r.passed
    assert "정의" in r.detail


# ══════════════════════════════════════════════════════════════════════
# 6단계 · 크기
# ══════════════════════════════════════════════════════════════════════


def test_size_rejects_oversized_structured_payload(conflict_schema):
    payload = {"facts": {"session_binding": "required"}, "pad": "x" * 5000}
    assert not validator.check_size(payload, Representation.STRUCTURED, 2048).passed


def test_text_representation_gets_a_larger_limit():
    """2KB 상한의 근거("자유 텍스트 혼입 신호")가 가명화 본문에는 성립하지 않는다."""
    assert validator.size_limit(Representation.STRUCTURED, 2048) == 2048
    assert validator.size_limit(Representation.PSEUDONYMIZED, 2048) == 2048 * 8


def test_size_passes_typical_pseudonymized_payload(conflict_schema):
    payload = {"excerpts": {"COMP_A": "가" * 3000}}
    assert validator.check_size(payload, Representation.PSEUDONYMIZED, 2048).passed


# ══════════════════════════════════════════════════════════════════════
# 전부 수집 (BR-V-00)
# ══════════════════════════════════════════════════════════════════════


def test_validate_collects_all_failures_not_just_the_first(conflict_schema, vocab, banned):
    """첫 실패에서 멈추면 `PreviewCard` 에 `6/6` 을 표시할 수 없다."""
    payload = {
        "bogus_key": "고객사 H",  # 1단계 + 2단계 + 4단계
        "facts": {"max_session_hours": 99999},  # 3단계
        "pad": "y" * 4000,  # 6단계
    }
    result = validator.validate(
        payload,
        schema=conflict_schema,
        vocab=vocab,
        banned=banned,
        originals=(),
        representation=Representation.STRUCTURED,
    )
    failed = set(validator.failed_stages(result))
    assert {"schema", "vocab", "range", "banned", "size"} <= failed
    assert len(result.checks) == 6


def test_first_failed_stage_reports_earliest(conflict_schema, vocab, banned):
    payload = {"nope": 1}
    result = validator.validate(
        payload,
        schema=conflict_schema,
        vocab=vocab,
        banned=banned,
        originals=(),
        representation=Representation.STRUCTURED,
    )
    assert result.first_failed_stage == "schema"


# ══════════════════════════════════════════════════════════════════════
# 브로커 재검증 (BR-V-07)
# ══════════════════════════════════════════════════════════════════════


def test_broker_revalidation_marks_ngram_as_unverifiable(
    good_payload, conflict_schema, vocab, banned
):
    """원문이 클라우드에 없다는 한계를 숨기지 않는다."""
    result = validator.revalidate_without_originals(
        good_payload,
        schema=conflict_schema,
        vocab=vocab,
        banned=banned,
        representation=Representation.STRUCTURED,
    )
    assert result.passed
    ngram = next(c for c in result.checks if c.stage == "ngram")
    assert "클라우드" in ngram.detail


def test_broker_revalidation_still_catches_banned(conflict_schema, vocab, banned):
    payload = {"entities": [{"ref": "REQ_A", "role": "H社"}]}
    result = validator.revalidate_without_originals(
        payload,
        schema=conflict_schema,
        vocab=vocab,
        banned=banned,
        representation=Representation.STRUCTURED,
    )
    assert not result.passed


# ══════════════════════════════════════════════════════════════════════
# 미리보기 측정값
# ══════════════════════════════════════════════════════════════════════


def test_verbatim_sentence_count_is_zero_for_structured(good_payload):
    assert (
        validator.verbatim_sentence_count(
            good_payload, ORIGINALS, representation=Representation.STRUCTURED
        )
        == 0
    )


def test_verbatim_sentence_count_detects_a_quote():
    original = "우리 SDK 는 토큰 수명 이십사 시간에 무음 갱신을 사용하고 세션 바인딩이 없다"
    payload = {"note": original}
    assert (
        validator.verbatim_sentence_count(
            payload, (original,), representation=Representation.STRUCTURED
        )
        == 1
    )


def test_verbatim_sentence_count_ignores_identifier_free_sentences_for_internal():
    original = "이 파이프라인은 RandomOverSampler 로 소수 클래스를 오버샘플링 한다"
    payload = {"excerpts": {"COMP_A": original}}
    assert (
        validator.verbatim_sentence_count(
            payload,
            (original,),
            representation=Representation.PSEUDONYMIZED,
            identifiers=("atlas_ml",),
        )
        == 0
    )


def test_excluded_categories_is_empty_when_banned_hit(banned):
    """확인하지 않은 것을 "없다"고 쓰면 미리보기가 거짓이 된다."""
    assert validator.excluded_categories({"x": "H社"}, banned) == ()
    assert validator.excluded_categories({"x": "clean"}, banned)


def test_pseudonymized_preview_does_not_claim_verbatim_is_absent(banned):
    """**G4 육안 확인이 찾은 결함의 회귀 방지.**

    가명화(사내) 페이로드는 원문 문장을 유지한다 — 식별자만 바꾼다. 그런데
    구조 페이로드와 같은 목록을 보여줘서, 화면이 "원문 문장·제품명·버전·일정
    없음"이라고 표시하면서 정작 페이로드에는 `SDK v3.2` 와 `2025-12-03` 과
    문서 전문이 들어 있었다.

    유출보다 이게 나쁘다: 사용자는 이 목록을 읽고 [전송] 을 누른다.
    목록이 거짓이면 "사람이 확인한다"는 방어 겹 자체가 무의미해진다.
    """
    payload = {"excerpts": {"COMP_A": "title: SDK v3.2 인증 설계 리뷰\nas_of: 2025-12-03\n"}}
    categories = validator.excluded_categories(payload, banned, Representation.PSEUDONYMIZED)
    for lie in ("원문 문장", "제품명", "버전", "일정"):
        assert lie not in categories, f"가명화 페이로드에 {lie} 이 남는데 없다고 표시한다"
    # 약속할 수 있는 것은 남는다 — 금칙어 검사와 치환이 보장한다
    assert "고객사명" in categories
    assert "담당자" in categories


def test_structured_preview_keeps_the_full_promise(banned):
    """구조 페이로드는 코드가 닫힌 어휘에서 조립한다 — 8개 전부 참이다."""
    categories = validator.excluded_categories(
        {"task": "constraint_conflict_check"}, banned, Representation.STRUCTURED
    )
    assert set(categories) == set(EXCLUDED_CATEGORIES_DEFAULT)


def test_verbatim_preview_promises_nothing(banned):
    """공개 등급은 원문 전송이 등급의 정의다. 없음을 약속할 것이 없다."""
    assert (
        validator.excluded_categories({"text": "공개 문서 전문"}, banned, Representation.VERBATIM)
        == ()
    )


def test_every_representation_has_an_entry():
    """표현이 추가되면 목록도 추가해야 한다 — 빠지면 조용히 기본값을 쓴다."""
    from mesh.schemas import EXCLUDED_CATEGORIES_BY_REPRESENTATION

    for representation in Representation:
        assert representation.value in EXCLUDED_CATEGORIES_BY_REPRESENTATION


def test_pseudonymized_promises_are_a_subset_of_structured():
    """가명화가 구조보다 많은 것을 약속할 수는 없다.

    구조 추출은 코드가 조립하고 가명화는 원문을 유지한다. 가명화 쪽에만
    있는 약속이 생기면 어느 한쪽이 틀린 것이다.
    """
    from mesh.schemas import EXCLUDED_CATEGORIES_BY_REPRESENTATION as BY_REP

    structured = set(BY_REP["structured"])
    pseudonymized = set(BY_REP["pseudonymized"])
    extra = pseudonymized - structured
    # `사내 경로` 는 가명화에만 있는 정당한 약속이다 (PATH 치환이 보장한다).
    assert extra <= {"사내 경로"}, f"근거 없는 약속: {extra}"


# ══════════════════════════════════════════════════════════════════════
# 진단 보조
# ══════════════════════════════════════════════════════════════════════


def test_missing_required_slots(conflict_schema):
    payload = {"facts": {"session_binding": "required"}}
    missing = validator.missing_required_slots(payload, conflict_schema)
    assert "auth_mechanism_class" in missing
    assert "session_binding" not in missing


def test_slot_entries_finds_values_at_any_nesting(conflict_schema):
    assert validator.slot_names_present(
        {"facts": {"REQ_A": {"session_binding": "none"}}}, conflict_schema
    ) == frozenset({"session_binding"})
    assert validator.slot_names_present({"session_binding": "none"}, conflict_schema) == frozenset(
        {"session_binding"}
    )


def test_slot_entries_keeps_conflicting_values_separate(conflict_schema):
    """🔴 이름별 dict 로 뭉치면 두 근거 중 하나만 검사된다."""
    payload = {
        "facts": {
            "REQ_A": {"session_binding": "required"},
            "COMP_A": {"session_binding": "none"},
        }
    }
    entries = validator.slot_entries(payload, conflict_schema)
    assert len(entries) == 2
    assert {v for _, _, v in entries} == {"required", "none"}


def test_range_check_sees_every_ref(conflict_schema):
    """한 근거만 검사하면 다른 근거의 범위 위반이 통과한다."""
    payload = {
        "facts": {
            "REQ_A": {"max_session_hours": 8},
            "COMP_A": {"max_session_hours": 99999},
        }
    }
    r = validator.check_ranges(payload, conflict_schema)
    assert not r.passed
    assert any("COMP_A" in o for o in r.offending)


def test_ref_labels_are_allowed_as_fact_keys(conflict_schema):
    payload = {"facts": {"REQ_A": {"session_binding": "none"}}}
    assert validator.check_schema(payload, conflict_schema, Representation.STRUCTURED).passed


def test_non_ref_fact_keys_are_rejected(conflict_schema):
    payload = {"facts": {"고객사 H 문서": {"session_binding": "none"}}}
    assert not validator.check_schema(payload, conflict_schema, Representation.STRUCTURED).passed


def test_payload_bytes_counts_utf8():
    assert validator.payload_bytes({"k": "가"}) > validator.payload_bytes({"k": "a"})


def test_tier_labels_exist():
    for t in Tier:
        assert t.label_ko

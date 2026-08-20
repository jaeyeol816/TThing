"""가명화 + 재수화 — 시나리오 2 재현, 왕복, 부분 치환 사고 방지.

핵심 둘:
  - 기술 용어는 보존된다 (치환하면 답변 품질이 무너진다)
  - `<SYS_1>` 과 `<SYS_11>` 이 함께 있어도 망가지지 않는다 (긴 키부터)
"""

from __future__ import annotations

import pytest

from mesh import rehydrator, validator
from mesh.pseudonymizer import EXCERPT_MAX_CHARS, apply, merge_mappings
from mesh.rehydrator import (
    answer_to_text,
    rehydrate_response,
    rehydrate_text,
    symbols_in,
    unresolved_warning,
)
from mesh.schemas import Mapping, PseudonymTargets, Representation

# 시나리오 2 의 실제 코퍼스 문장 형태
PARK_TEXT = (
    "atlas_ml 전처리 v3: 라벨 불균형은 RandomOverSampler(sampling_strategy=0.5, "
    "random_state=42) 로 1단계 처리하고 학습 시 class_weight=balanced_subsample 로 "
    "2단계 보정한다. 입력은 data/raw/session_logs/ 이고 담당은 박선영이다."
)
KIM_TEXT = "Nova 게이트웨이는 레거시 SSO 게이트웨이와 claim mapping 을 공유한다. 김철수 확인."


# ══════════════════════════════════════════════════════════════════════
# 치환 대상과 비대상 (BR-P-01)
# ══════════════════════════════════════════════════════════════════════


def test_project_name_is_replaced(pseudonyms):
    r = apply([PARK_TEXT], pseudonyms)
    assert "atlas_ml" not in r.texts[0]
    assert "<PROJ_" in r.texts[0]


@pytest.mark.parametrize(
    "term",
    [
        "RandomOverSampler",
        "sampling_strategy",
        "random_state",
        "class_weight",
        "balanced_subsample",
    ],
)
def test_technical_terms_survive(pseudonyms, term):
    """`<TERM_1>` 이 오버샘플링인지 Claude 가 알 수 없다."""
    r = apply([PARK_TEXT], pseudonyms)
    assert term in r.texts[0]


def test_numeric_parameters_survive(pseudonyms):
    r = apply([PARK_TEXT], pseudonyms)
    assert "0.5" in r.texts[0]
    assert "42" in r.texts[0]


# ══════════════════════════════════════════════════════════════════════
# 보수적 마스킹 — 정규식 휴리스틱 (A + B)
# ══════════════════════════════════════════════════════════════════════


def test_regex_masks_dates_versions_and_units(pseudonyms):
    """목록에 없어도 날짜·버전·분기·단위수치는 모양으로 가린다."""
    text = "2026-08-19 기준 v3.2 릴리스를 3분기에 안정화하고 토큰 수명은 14일이다."
    r = apply([text], pseudonyms)
    out = r.texts[0]
    for token in ("2026-08-19", "v3.2", "3분기", "14일"):
        assert token not in out, token
    # 가역적이어야 한다 — 답변으로 돌아오면 신뢰 구역에서 되돌린다.
    restored, unresolved = rehydrate_text(out, r.mapping)
    assert restored == text
    assert unresolved == ()


def test_regex_keeps_bare_parameters_and_terms(pseudonyms):
    """단위 없는 수치와 기술 용어는 그대로 둔다 (답변이 무너지지 않게)."""
    text = "RandomOverSampler(sampling_strategy=0.5, random_state=42) 로 처리한다"
    r = apply([text], pseudonyms)
    out = r.texts[0]
    assert "0.5" in out
    assert "42" in out
    assert "RandomOverSampler" in out


def test_regex_numbering_is_order_independent(pseudonyms):
    a = "v1.2 는 2025-01-01 에 나왔다"
    b = "v3.4 는 2026-12-31 에 나온다"
    forward = apply([a, b], pseudonyms)
    backward = apply([b, a], pseudonyms)
    assert forward.mapping.table == backward.mapping.table


def test_person_name_is_replaced(pseudonyms):
    r = apply([PARK_TEXT], pseudonyms)
    assert "박선영" not in r.texts[0]
    assert "<PERSON_" in r.texts[0]


def test_system_name_is_replaced(pseudonyms):
    r = apply([KIM_TEXT], pseudonyms)
    assert "Nova 게이트웨이" not in r.texts[0]
    assert "레거시 SSO 게이트웨이" not in r.texts[0]


def test_path_segment_is_replaced(pseudonyms):
    r = apply([PARK_TEXT], pseudonyms)
    assert "data/raw/session_logs" not in r.texts[0]


def test_standards_survive(pseudonyms):
    """`SSO` 는 기술 용어라 남고, `레거시 SSO 게이트웨이` 는 시스템명이라 통째로 치환된다."""
    r = apply(["claim mapping 은 OAuth 와 SAML 을 함께 지원한다"], pseudonyms)
    assert "OAuth" in r.texts[0]
    assert "SAML" in r.texts[0]
    assert "claim mapping" in r.texts[0]


# ══════════════════════════════════════════════════════════════════════
# placeholder 일관성 (BR-P-02, PB-6)
# ══════════════════════════════════════════════════════════════════════


def test_same_target_gets_same_placeholder_across_texts(pseudonyms):
    a = "atlas-ml 첫 번째 문서"
    b = "atlas-ml 두 번째 문서"
    r = apply([a, b], pseudonyms)
    ph_a = symbols_in(r.texts[0])
    ph_b = symbols_in(r.texts[1])
    assert ph_a == ph_b


def test_numbering_is_independent_of_text_order(pseudonyms):
    forward = apply([PARK_TEXT, KIM_TEXT], pseudonyms)
    backward = apply([KIM_TEXT, PARK_TEXT], pseudonyms)
    assert forward.mapping.table == backward.mapping.table


def test_unused_targets_do_not_consume_numbers(pseudonyms):
    r = apply(["atlas-ml 만 등장한다"], pseudonyms)
    assert len(r.mapping.table) == 1
    assert "<PROJ_1>" in r.mapping.table


def test_categories_have_separate_counters(pseudonyms):
    r = apply([PARK_TEXT + " " + KIM_TEXT], pseudonyms)
    prefixes = {k.split("_")[0] for k in r.mapping.table}
    assert "<PROJ" in prefixes
    assert "<PERSON" in prefixes


# ══════════════════════════════════════════════════════════════════════
# 부분 치환 사고 방지 (BR-P-04)
# ══════════════════════════════════════════════════════════════════════


def test_longer_literal_is_replaced_first():
    """`atlas-ml` 을 먼저 치환하면 `atlas-ml-core` 가 `<PROJ_1>-core` 로 망가진다."""
    targets = PseudonymTargets(
        targets={"PROJ": ("atlas-ml", "atlas-ml-core")}, technical_terms=frozenset()
    )
    r = apply(["atlas-ml-core 와 atlas-ml 은 다르다"], targets)
    text = r.texts[0]
    assert "-core" not in text
    assert len(symbols_in(text)) == 2


def test_all_literals_returns_longest_first(pseudonyms):
    lengths = [len(lit) for _, lit in pseudonyms.all_literals()]
    assert lengths == sorted(lengths, reverse=True)


# ══════════════════════════════════════════════════════════════════════
# 왕복 (PB-1)
# ══════════════════════════════════════════════════════════════════════


def test_roundtrip_restores_original(pseudonyms):
    r = apply([PARK_TEXT], pseudonyms)
    restored, unresolved = rehydrate_text(r.texts[0], r.mapping)
    assert restored == PARK_TEXT
    assert unresolved == ()


def test_roundtrip_with_adjacent_numbers():
    """`<SYS_1>` 과 `<SYS_11>` 이 함께 있을 때 짧은 키를 먼저 치환하면 망가진다."""
    mapping = Mapping(table={"<SYS_1>": "알파", "<SYS_11>": "베타"})
    text = "<SYS_11> 는 <SYS_1> 과 다르다"
    out, unresolved = rehydrate_text(text, mapping)
    assert out == "베타 는 알파 과 다르다"
    assert unresolved == ()


def test_truncation_is_reported(pseudonyms):
    r = apply(["가" * (EXCERPT_MAX_CHARS + 100)], pseudonyms)
    assert r.truncated
    assert len(r.texts[0]) == EXCERPT_MAX_CHARS


# ══════════════════════════════════════════════════════════════════════
# 검증 5단계와의 연결 (BR-P-03)
# ══════════════════════════════════════════════════════════════════════


def test_pseudonymized_payload_passes_identifier_ngram_check(pseudonyms):
    r = apply([PARK_TEXT], pseudonyms)
    payload = {"excerpts": {"COMP_A": r.texts[0]}}
    check = validator.check_no_source_ngram(
        payload,
        (PARK_TEXT,),
        representation=Representation.PSEUDONYMIZED,
        identifiers=r.identifiers,
    )
    assert check.passed, check.offending


def test_identifiers_includes_all_targets_not_just_substituted(pseudonyms):
    """치환된 것만 넘기면 **가명화가 놓친 표기 변형**을 검사할 방법이 없어진다."""
    r = apply(["atlas-ml 만 등장"], pseudonyms)
    assert len(r.substituted) == 1
    assert len(r.identifiers) > 10
    assert "김철수" in r.identifiers


def test_missed_identifier_is_caught_by_validator(pseudonyms):
    """가명화를 우회한 텍스트가 있으면 검증이 잡는다."""
    payload = {"excerpts": {"COMP_A": PARK_TEXT}}  # 치환 안 함
    r = apply([PARK_TEXT], pseudonyms)
    check = validator.check_no_source_ngram(
        payload,
        (PARK_TEXT,),
        representation=Representation.PSEUDONYMIZED,
        identifiers=r.identifiers,
    )
    assert not check.passed


# ══════════════════════════════════════════════════════════════════════
# 매핑 합치기
# ══════════════════════════════════════════════════════════════════════


def test_merge_mappings_combines(pseudonyms):
    a = Mapping(table={"COMP_A": "설계 문서"})
    b = Mapping(table={"<PROJ_1>": "atlas-ml"})
    merged = merge_mappings(a, b)
    assert merged.get("COMP_A") == "설계 문서"
    assert merged.get("<PROJ_1>") == "atlas-ml"


def test_merge_mappings_rejects_conflicts():
    """조용히 덮어쓰면 재수화가 틀린 이름을 남긴다."""
    with pytest.raises(ValueError, match="충돌"):
        merge_mappings(Mapping(table={"X_A": "하나"}), Mapping(table={"X_A": "둘"}))


def test_merge_allows_identical_duplicates():
    merged = merge_mappings(Mapping(table={"X_A": "같음"}), Mapping(table={"X_A": "같음"}))
    assert merged.get("X_A") == "같음"


# ══════════════════════════════════════════════════════════════════════
# 재수화 — 매핑에 없는 기호 (BR-G-10)
# ══════════════════════════════════════════════════════════════════════


def test_unmapped_symbol_is_left_intact():
    """프롬프트 인젝션으로 임의 문자열을 치환시키는 것을 막는다."""
    mapping = Mapping(table={"<PROJ_1>": "atlas-ml"})
    out, unresolved = rehydrate_text("<PROJ_1> 과 <SYS_9> 를 비교", mapping)
    assert "atlas-ml" in out
    assert "<SYS_9>" in out
    assert unresolved == ("<SYS_9>",)


def test_unmapped_symbol_is_not_deleted():
    """지우면 사용자가 문장이 불완전해진 것을 알 수 없다."""
    out, _ = rehydrate_text("<SYS_9> 가 원인이다", Mapping.empty())
    assert "<SYS_9>" in out


def test_unresolved_ref_label_is_detected():
    out, unresolved = rehydrate_text("REQ_A 와 COMP_B 를 대조", Mapping(table={"REQ_A": "명세서"}))
    assert "명세서" in out
    assert unresolved == ("COMP_B",)


def test_symbols_in_ignores_ordinary_words():
    assert symbols_in("일반 문장에는 기호가 없다 SDK v3.2 OAuth") == ()


def test_unresolved_warning_text():
    assert unresolved_warning(()) == ""
    assert "BR-G-10" in unresolved_warning(("<SYS_9>",))


# ══════════════════════════════════════════════════════════════════════
# 응답 전체 재수화 (BR-P-04 확장)
# ══════════════════════════════════════════════════════════════════════


def test_rehydrate_response_walks_nested_structures():
    """필드 이름을 하드코딩하면 새 task 를 추가할 때 재수화가 조용히 빠진다."""
    mapping = Mapping(table={"REQ_A": "요구사항명세서", "<SYS_1>": "Nova 게이트웨이"})
    answer = {
        "conflict": True,
        "reason": "REQ_A 는 세션 바인딩을 요구한다",
        "mitigations": ["<SYS_1> 에 바인딩 추가", {"note": "REQ_A 재확인"}],
    }
    out, unresolved = rehydrate_response(answer, mapping)
    assert out["reason"].startswith("요구사항명세서")
    assert "Nova 게이트웨이" in out["mitigations"][0]
    assert out["mitigations"][1]["note"].startswith("요구사항명세서")
    assert unresolved == ()
    assert out["conflict"] is True


def test_rehydrate_response_reports_all_unresolved():
    answer = {"reason": "COMP_Z 참조", "mitigations": ["<SYS_7> 확인"]}
    _, unresolved = rehydrate_response(answer, Mapping.empty())
    assert set(unresolved) == {"COMP_Z", "<SYS_7>"}


def test_rehydrate_obj_stops_at_depth_limit():
    deep: object = "REQ_A"
    for _ in range(20):
        deep = {"n": deep}
    out, _ = rehydrator.rehydrate_obj(deep, Mapping(table={"REQ_A": "X"}))
    assert out is not None


def test_answer_to_text_renders_korean_labels():
    text = answer_to_text(
        {
            "conflict": True,
            "reason": "세션 바인딩 부재",
            "mitigations": ["바인딩 추가", "만료 단축"],
        }
    )
    assert "충돌 여부: 예" in text
    assert "이유: 세션 바인딩 부재" in text
    assert "- 바인딩 추가" in text


def test_answer_to_text_keeps_unknown_keys_visible():
    """숨기면 답변이 사라진 것처럼 보인다."""
    assert "novel_field" in answer_to_text({"novel_field": "값"})


def test_answer_to_text_renders_false_as_no():
    assert "아니오" in answer_to_text({"conflict": False})

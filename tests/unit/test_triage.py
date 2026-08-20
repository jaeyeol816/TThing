"""브로드캐스트 선별 (`mesh.triage`).

세 가지를 고정한다.

  ① **판정이 문서를 보지 않는다.** `Candidate` 에 원문이 들어갈 자리가 없다.
     이것이 성립해야 열 명에게 질문을 뿌려도 새로 노출되는 것이 0 이다.

  ② **모델은 번호와 코드만 고른다.** 화면 문장은 코드가 조립한다.
     자유 문장을 허용하면 판정 결과가 원문 유출 채널이 된다.

  ③ **애매하면 넓게 남긴다** (fail open). 등급 판정과 방향이 반대이고,
     그 이유는 위험이 비대칭이기 때문이다 — 후보를 좁게 잡은 실수는
     "답할 수 있는 사람이 화면에서 사라지는 것" 이고 되돌릴 수 있다.
"""

from __future__ import annotations

import pytest

from mesh.exceptions import ExaoneUnavailable
from mesh.triage import (
    REASON_TEMPLATES,
    VALID_REASON_CODES,
    Candidate,
    build_triage_prompt,
    parse_picks,
    phrase_hits,
    render_reason,
    rule_pass,
    strip_suffix,
    tokens,
    triage,
    validate_topics,
)


def cand(entity_id: str, **kw) -> Candidate:
    kw.setdefault("display_name", entity_id)
    return Candidate(entity_id=entity_id, **kw)


PARK = cand(
    "person:park",
    display_name="박선영 선임",
    expertise="데이터 파이프라인 · 모델 학습",
    topics=("전처리", "라벨 불균형", "오버샘플링"),
    unit_path=("VS본부", "SW플랫폼센터", "데이터플랫폼팀"),
)
KIM = cand(
    "person:kim",
    display_name="김철수 책임",
    expertise="인증 · SSO · SDK 보안",
    topics=("인증", "세션 바인딩", "토큰 갱신"),
    unit_path=("VS본부", "SW플랫폼센터", "인증플랫폼팀"),
)
LEADER = cand(
    "person:jung",
    display_name="정대현 TL",
    expertise="인증 플랫폼 로드맵",
    topics=("로드맵", "우선순위"),
    unit_path=("VS본부", "SW플랫폼센터", "인증플랫폼팀"),
    leads=True,
)


class FakeExaone:
    """번호를 고르는 대역. 실패를 흉내 낼 수 있어야 한다."""

    def __init__(self, picks=None, fail: Exception | None = None) -> None:
        self.picks = picks if picks is not None else []
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system, user, *, name="", max_tokens=0) -> dict:
        self.calls.append((system, user))
        if self.fail:
            raise self.fail
        return {"picks": self.picks}


# ══════════════════════════════════════════════════════════════════════
# ① 판정 재료 — 문서가 들어갈 자리가 없다
# ══════════════════════════════════════════════════════════════════════


def test_candidate_has_no_field_for_document_text() -> None:
    """🔴 이 검사가 브로드캐스트의 안전성 전체를 떠받친다.

    `Candidate` 에 본문·경로·문서 제목을 담을 자리가 있으면, 언젠가 누군가
    채운다. 그 순간 "누가 답할 수 있는가" 라는 결과가 남의 파일에 무엇이
    있는지를 알려주는 채널이 된다.
    """
    fields = set(Candidate.model_fields)
    forbidden = {"text", "chunks", "documents", "open_paths", "internal_path",
                 "knowledge_scope", "titles", "session"}
    assert not (fields & forbidden), fields & forbidden


def test_prompt_carries_only_public_fields() -> None:
    prompt = build_triage_prompt("라벨 불균형?", [PARK])
    assert "박선영" not in prompt, "표시 이름조차 판정에 필요하지 않다"
    assert "라벨 불균형" in prompt
    assert "데이터플랫폼팀" in prompt


# ══════════════════════════════════════════════════════════════════════
# 규칙 판정 — 순수 함수
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("라벨 불균형을 어떤 기법으로 처리했나요?", "person:park"),
        ("왜 세션 바인딩을 넣지 않았나요?", "person:kim"),
        ("전처리 파이프라인은 누가 담당하나요?", "person:park"),
    ],
)
def test_topic_match_finds_the_right_person(question: str, expected: str) -> None:
    verdicts = rule_pass(question, [PARK, KIM])
    relevant = {k for k, v in verdicts.items() if v.relevant}
    assert relevant == {expected}


def test_korean_particles_do_not_break_matching() -> None:
    """`라벨을` 과 `라벨` 이 만나야 한다. 형태소 분석기 없이 꼬리만 자른다."""
    assert strip_suffix("라벨을") == "라벨"
    assert strip_suffix("파이프라인의") == "파이프라인"
    assert strip_suffix("을") == "을", "잘라서 너무 짧아지면 원형을 남긴다"
    assert "라벨" in tokens("라벨을 확인했다")


def test_phrase_hits_matches_across_spacing() -> None:
    assert phrase_hits("라벨불균형이 심합니다", ("라벨 불균형",)) == ("라벨 불균형",)
    assert phrase_hits("전혀 다른 이야기", ("라벨 불균형",)) == ()


def test_no_match_is_not_relevant() -> None:
    verdicts = rule_pass("점심 뭐 먹을까요?", [PARK, KIM])
    assert all(not v.relevant for v in verdicts.values())
    assert all(v.reason_code == "no_match" for v in verdicts.values())


def test_unavailable_person_never_becomes_relevant() -> None:
    busy = PARK.model_copy(update={"available": False})
    verdicts = rule_pass("라벨 불균형 처리 방법", [busy])
    assert verdicts["person:park"].relevant is False
    assert verdicts["person:park"].score > 0, "점수는 남는다 — 이유를 설명할 수 있어야 한다"


def test_team_name_alone_marks_the_leader_as_team_context() -> None:
    verdicts = rule_pass("인증플랫폼팀은 요즘 뭐 하나요?", [LEADER])
    v = verdicts["person:jung"]
    assert v.reason_code in {"team_context", "topic_match", "expertise_match"}


# ══════════════════════════════════════════════════════════════════════
# ② 모델은 번호와 코드만 고른다
# ══════════════════════════════════════════════════════════════════════


def test_parse_picks_drops_out_of_range_indices() -> None:
    """존재하지 않는 사람을 지목하게 두지 않는다 (`select_paths` 와 같은 원칙)."""
    assert parse_picks({"picks": [{"i": 99, "code": "topic_match"}]}, 2) == ()
    assert parse_picks({"picks": [{"i": -1, "code": "topic_match"}]}, 2) == ()
    assert parse_picks({"picks": [{"i": True, "code": "topic_match"}]}, 2) == ()


def test_parse_picks_drops_duplicates_and_junk() -> None:
    picks = parse_picks(
        {"picks": [{"i": 0, "code": "topic_match"}, {"i": 0, "code": "adjacent"}, "nope"]}, 2
    )
    assert picks == ((0, "topic_match"),)


def test_unknown_reason_code_does_not_drop_the_person() -> None:
    """코드를 못 알아들었다고 후보에서 빼면 판정이 아니라 사고다."""
    picks = parse_picks({"picks": [{"i": 0, "code": "made_up_code"}]}, 1)
    assert picks == ((0, "adjacent"),)


def test_parse_picks_survives_garbage() -> None:
    assert parse_picks("문자열", 3) == ()
    assert parse_picks({"picks": "리스트가 아님"}, 3) == ()
    assert parse_picks({}, 3) == ()


def test_reason_sentences_come_from_templates_not_the_model() -> None:
    """🔴 화면 문장은 **코드가 조립한다.** 모델 출력이 그대로 뜨지 않는다."""
    for code in VALID_REASON_CODES:
        text = render_reason(code, PARK, ["전처리"])
        assert text, code
        # 템플릿의 고정 부분이 실제로 문장에 남아 있어야 한다
        skeleton = REASON_TEMPLATES[code].split("{")[0].strip()
        if skeleton:
            assert skeleton in text, (code, text)


async def test_model_pick_can_add_someone_the_rules_missed() -> None:
    """규칙이 모르는 표현(동의어)을 잡으라고 모델이 있는 것이다."""
    exaone = FakeExaone(picks=[{"i": 1, "code": "expertise_match"}])
    out = await triage("클래스 쏠림 문제를 어떻게 다뤘나요?", [KIM, PARK], exaone=exaone)
    by_id = {v.entity_id: v for v in out.verdicts}
    assert by_id["person:park"].relevant
    assert by_id["person:park"].decided_by == "model"
    assert out.model_used is True


async def test_model_failure_falls_back_to_rules() -> None:
    """🔴 선별이 막혔다고 질문 자체가 막히면 안 된다."""
    exaone = FakeExaone(fail=ExaoneUnavailable("타임아웃"))
    out = await triage("라벨 불균형 처리", [PARK, KIM], exaone=exaone)
    assert out.model_used is False
    assert out.model_error
    assert [v.entity_id for v in out.relevant] == ["person:park"]


async def test_rule_reason_wins_over_model_reason() -> None:
    """규칙 사유는 '무엇이 겹쳤는지' 를 가리킨다 — 확인할 수 있는 쪽을 남긴다."""
    exaone = FakeExaone(picks=[{"i": 0, "code": "adjacent"}])
    out = await triage("라벨 불균형 처리", [PARK], exaone=exaone)
    v = out.verdicts[0]
    assert v.reason_code == "topic_match"
    assert v.decided_by == "rule+model"


# ══════════════════════════════════════════════════════════════════════
# ③ 목록에서 지우지 않는다 · 상한
# ══════════════════════════════════════════════════════════════════════


async def test_everyone_stays_in_the_result_even_when_irrelevant() -> None:
    """🔴 지워 버리면 판정이 틀렸을 때 사용자가 되돌릴 대상이 없다."""
    out = await triage("라벨 불균형", [PARK, KIM, LEADER])
    assert len(out.verdicts) == 3
    assert len(out.relevant) == 1


async def test_max_relevant_trims_the_lowest_scores_only() -> None:
    people = [
        cand(f"person:p{i}", topics=("공통주제",), expertise="공통주제 담당")
        for i in range(5)
    ]
    out = await triage("공통주제에 대해 알려주세요", people, max_relevant=2)
    assert len(out.relevant) == 2
    assert len(out.verdicts) == 5, "후보에서 빠져도 목록에는 남는다"


async def test_empty_candidate_list_is_fine() -> None:
    out = await triage("아무거나", [])
    assert out.verdicts == ()
    assert out.model_used is False


# ══════════════════════════════════════════════════════════════════════
# 설정 검증
# ══════════════════════════════════════════════════════════════════════


def test_topics_with_banned_terms_are_reported() -> None:
    """🔴 `topics` 는 인증 없이 보이는 화면에 그대로 실린다."""
    from mesh.schemas import BannedTerms

    banned = BannedTerms(literals=("하나텔",), patterns=())
    problems = validate_topics({"person:x": ["하나텔 인증 연동"]}, banned)
    assert len(problems) == 1
    assert "person:x" in problems[0]


def test_real_config_topics_have_no_banned_terms() -> None:
    from pathlib import Path

    from mesh.config import load_agents
    from mesh.schemas import BannedTerms

    repo = Path(__file__).resolve().parents[2]
    agents = load_agents(repo / "config" / "agents.yaml")
    banned = BannedTerms.load(repo / "agents" / "shared" / "banned.json")
    problems = validate_topics({eid: a.topics for eid, a in agents.items()}, banned)
    assert problems == (), problems

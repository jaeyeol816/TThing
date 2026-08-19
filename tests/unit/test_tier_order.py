"""Tier 순서 — 이걸 틀리면 조용히 유출된다.

StrEnum 의 기본 비교는 알파벳 순이다. Tier.__lt__ 를 구현하지 않으면:

    max(Tier.SECRET, Tier.OPEN)  ->  "secret" vs "open" 알파벳 비교
                                 ->  Tier.SECRET  (우연히 맞다)
    max(Tier.INTERNAL, Tier.OPEN) -> "internal" vs "open"
                                  -> Tier.OPEN   ⚠️ 틀렸다

FR-11(동원된 지식 중 최고 등급이 호출 전체에 걸린다)이 max() 한 줄로
표현되므로, 이 비교가 틀리면 사내 문서가 공개로 판정된다.

실패가 조용하다 — 예외도 없고 로그도 없다. 그래서 테스트가 필수다.
"""

from itertools import combinations, permutations

import pytest

from mesh.schemas import Tier


def test_rank_order():
    assert Tier.OPEN.rank < Tier.INTERNAL.rank < Tier.SECRET.rank


def test_comparison_is_by_sensitivity_not_alphabet():
    assert Tier.OPEN < Tier.INTERNAL
    assert Tier.INTERNAL < Tier.SECRET
    assert Tier.OPEN < Tier.SECRET
    assert Tier.SECRET > Tier.OPEN


def test_alphabet_trap():
    """알파벳 순이면 internal > open 이 되어야 하는데, 그러면 안 된다."""
    assert "internal" < "open"  # 문자열로는 이렇다
    assert Tier.INTERNAL > Tier.OPEN  # 등급으로는 반대다


@pytest.mark.parametrize("pair", list(combinations(Tier, 2)))
def test_max_picks_higher_tier(pair):
    a, b = pair
    expected = a if a.rank > b.rank else b
    assert max(a, b) is expected
    assert max(b, a) is expected


@pytest.mark.parametrize("order", list(permutations(Tier)))
def test_max_over_all_orderings_is_secret(order):
    """어떤 순서로 넣어도 SECRET 이 나와야 한다 — 등급 상향(FR-11)의 핵심."""
    assert max(order) is Tier.SECRET


def test_max_of_realistic_upgrade_case():
    """시나리오 1: 질문은 internal, 파일은 secret + internal -> 전체 secret."""
    question_tier = Tier.INTERNAL
    chunk_tiers = [Tier.SECRET, Tier.INTERNAL]
    assert max([question_tier, *chunk_tiers]) is Tier.SECRET


def test_max_of_internal_and_open_is_internal():
    """알파벳 비교였다면 OPEN 이 나왔을 케이스."""
    assert max([Tier.INTERNAL, Tier.OPEN]) is Tier.INTERNAL


def test_sorted_ascending():
    assert sorted(Tier) == [Tier.OPEN, Tier.INTERNAL, Tier.SECRET]


def test_still_a_str_enum():
    """StrEnum 특성은 유지돼야 한다 (JSON 직렬화·DB 저장에 쓰인다)."""
    assert Tier.SECRET == "secret"
    assert f"{Tier.SECRET}" == "secret"
    assert Tier("secret") is Tier.SECRET


def test_comparison_with_non_tier_is_not_implemented():
    with pytest.raises(TypeError):
        _ = Tier.SECRET < 3


def test_label_ko():
    assert Tier.SECRET.label_ko == "기밀"
    assert Tier.INTERNAL.label_ko == "사내"
    assert Tier.OPEN.label_ko == "공개"

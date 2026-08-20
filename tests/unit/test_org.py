"""조직도 — 트리 조립과 **깊이가 데이터에서 온다**는 성질 (`mesh.org`).

이 파일이 지키는 것은 하나다: **코드가 조직의 층 이름을 알지 못한다.**

`본부 → 센터/연구소 → 팀` 은 이 회사의 현재 모습일 뿐이므로, 층을 하나 더
넣거나 이름을 바꾸는 변경이 `config/org.yaml` 편집으로 끝나야 한다. 그것을
검사하는 방법은 **이 회사와 전혀 다른 조직도를 만들어 돌려 보는 것**이다.
아래 `alien_chart` 가 그 역할을 한다 — 층이 4단계이고 이름이 전부 다르다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mesh.exceptions import ConfigError
from mesh.org import (
    OrgChart,
    OrgRank,
    OrgUnit,
    OrgUnitKind,
    load_org,
    members_of_units,
    sort_members,
    unit_label_path,
)
from mesh.schemas import BannedTerms, OrgPlacement

REPO = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════════════════════
# 픽스처
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def alien_chart() -> OrgChart:
    """이 회사와 **닮지 않은** 조직도. 층이 4개이고 이름이 전부 다르다.

    실물(`config/org.yaml`)로만 테스트하면 코드가 실물의 층 이름을 알고 있어도
    통과한다. 알지 못한다는 것을 보이려면 다른 것을 먹여야 한다.
    """
    return OrgChart(
        version=7,
        unit_kinds=(
            OrgUnitKind(id="guild", label="길드"),
            OrgUnitKind(id="chapter", label="챕터"),
            OrgUnitKind(id="squad", label="스쿼드"),
            OrgUnitKind(id="pod", label="포드"),
        ),
        ranks=(
            OrgRank(id="captain", label="캡틴", short="캡", order=1, leads=True),
            OrgRank(id="crew", label="크루", short="크", order=9),
        ),
        units=(
            OrgUnit(id="g", kind="guild", name="길드A", parent=None),
            OrgUnit(id="c", kind="chapter", name="챕터B", parent="g"),
            OrgUnit(id="s", kind="squad", name="스쿼드C", parent="c"),
            OrgUnit(id="p", kind="pod", name="포드D", parent="s"),
        ),
    )


@pytest.fixture
def real_chart() -> OrgChart:
    """실물 `config/org.yaml`. 손으로 만든 것만 쓰면 실물이 깨져도 통과한다."""
    return load_org(REPO / "config" / "org.yaml")


# ══════════════════════════════════════════════════════════════════════
# 깊이는 데이터가 정한다
# ══════════════════════════════════════════════════════════════════════


def test_depth_comes_from_data_not_code(alien_chart: OrgChart) -> None:
    """4층 조직도가 그대로 4층으로 그려진다."""
    view = alien_chart.to_view({"x": OrgPlacement(unit="p", rank="crew")})
    assert len(view.roots) == 1

    depths = []
    node = view.roots[0]
    while True:
        depths.append(node.depth)
        if not node.children:
            break
        node = node.children[0]
    assert depths == [0, 1, 2, 3]


def test_layer_labels_come_from_the_file(alien_chart: OrgChart) -> None:
    """화면에 뜨는 층 이름이 전부 데이터에서 온다 — 코드에 '팀'이 없다."""
    view = alien_chart.to_view({})
    labels = []
    node = view.roots[0]
    while True:
        labels.append(node.kind_label)
        if not node.children:
            break
        node = node.children[0]
    assert labels == ["길드", "챕터", "스쿼드", "포드"]


def test_member_counts_roll_up(alien_chart: OrgChart) -> None:
    """상위 단위의 인원 수는 자손을 합산한 값이다 (화면이 접을지 정한다)."""
    view = alien_chart.to_view(
        {
            "a": OrgPlacement(unit="p", rank="crew"),
            "b": OrgPlacement(unit="s", rank="captain"),
        }
    )
    guild = view.roots[0]
    assert guild.member_count_total == 2
    assert guild.member_ids == ()  # 본인 소속은 없다 — 합산만


def test_ranks_order_members_inside_a_unit(alien_chart: OrgChart) -> None:
    view = alien_chart.to_view(
        {
            "crew1": OrgPlacement(unit="p", rank="crew"),
            "cap": OrgPlacement(unit="p", rank="captain"),
        }
    )
    pod = view.roots[0].children[0].children[0].children[0]
    assert pod.member_ids == ("cap", "crew1"), "직급 order 가 작을수록 위"


# ══════════════════════════════════════════════════════════════════════
# 오타는 조용히 사라지지 않는다
# ══════════════════════════════════════════════════════════════════════


def test_unknown_unit_becomes_unplaced_not_invisible(alien_chart: OrgChart) -> None:
    """🔴 자리를 못 찾은 사람이 조용히 사라지면 "왜 안 보이지" 가 된다."""
    view = alien_chart.to_view({"ghost": OrgPlacement(unit="없는팀", rank="crew")})
    assert view.unplaced_member_ids == ("ghost",)
    assert all(m.entity_id != "ghost" for m in view.members)


def test_unknown_rank_also_becomes_unplaced(alien_chart: OrgChart) -> None:
    view = alien_chart.to_view({"ghost": OrgPlacement(unit="p", rank="없는직급")})
    assert view.unplaced_member_ids == ("ghost",)


# ══════════════════════════════════════════════════════════════════════
# 로드 시점 검증
# ══════════════════════════════════════════════════════════════════════


def test_missing_file_gives_an_empty_chart_not_an_error(tmp_path: Path) -> None:
    """조직도는 표시용이다. 없다고 앱이 죽으면 안 된다."""
    chart = load_org(tmp_path / "nope.yaml")
    assert chart.units == ()
    assert chart.to_view({}).roots == ()


def test_malformed_yaml_does_raise(tmp_path: Path) -> None:
    """없는 것과 **틀린 것**은 다르다. 틀리면 알려줘야 한다."""
    path = tmp_path / "org.yaml"
    path.write_text("units: [{id: a, kind: x, name: A, parent: nope}]", encoding="utf-8")
    with pytest.raises(ConfigError, match="parent"):
        load_org(path)


def test_cycle_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "org.yaml"
    path.write_text(
        "ranks: [{id: r, label: R}]\n"
        "units:\n"
        "  - {id: a, kind: k, name: A, parent: b}\n"
        "  - {id: b, kind: k, name: B, parent: a}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="순환"):
        load_org(path)


def test_duplicate_unit_id_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "org.yaml"
    path.write_text(
        "ranks: [{id: r, label: R}]\n"
        "units:\n"
        "  - {id: a, kind: k, name: A}\n"
        "  - {id: a, kind: k, name: A2}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="중복"):
        load_org(path)


def test_banned_terms_in_org_names_block_the_load(tmp_path: Path) -> None:
    """🔴 조직도는 **인증 없이 보이는 화면**이다.

    여기 고객사명이 실리면 게이트키퍼를 우회한 유출이 된다 (FR-31 과 같은 이유).
    """
    path = tmp_path / "org.yaml"
    path.write_text(
        "ranks: [{id: r, label: R}]\n"
        "units: [{id: a, kind: k, name: 하나텔전담팀}]\n",
        encoding="utf-8",
    )
    banned = BannedTerms(literals=("하나텔",), patterns=())
    with pytest.raises(ConfigError, match="금칙어"):
        load_org(path, banned=banned)


# ══════════════════════════════════════════════════════════════════════
# 보조 함수
# ══════════════════════════════════════════════════════════════════════


def test_unit_label_path_and_helpers(alien_chart: OrgChart) -> None:
    assert unit_label_path(alien_chart, "p") == "길드A · 챕터B · 스쿼드C · 포드D"
    assert unit_label_path(alien_chart, None) == ""

    places = {
        "a": OrgPlacement(unit="p", rank="crew"),
        "b": OrgPlacement(unit="p", rank="captain"),
        "c": OrgPlacement(unit="s", rank="crew"),
    }
    assert sort_members(["a", "b"], places, alien_chart) == ("b", "a")
    assert set(members_of_units(["p"], places)) == {"a", "b"}


def test_shared_unit_and_common_ancestor(alien_chart: OrgChart) -> None:
    assert alien_chart.shares_unit("p", "p") is True
    assert alien_chart.shares_unit("p", "s") is False
    assert alien_chart.shares_unit(None, None) is False
    assert alien_chart.common_ancestor_depth("p", "s") == 3  # g, c, s
    assert alien_chart.common_ancestor_depth("p", None) == 0


# ══════════════════════════════════════════════════════════════════════
# 실물
# ══════════════════════════════════════════════════════════════════════


def test_real_chart_loads_and_matches_agents_yaml(real_chart: OrgChart) -> None:
    """`agents.yaml` 의 자리가 전부 `org.yaml` 에 있다 — 미배치 0명."""
    from mesh.config import load_agents

    agents = load_agents(REPO / "config" / "agents.yaml")
    placements = {eid: a.org for eid, a in agents.items() if a.org}
    view = real_chart.to_view(placements)

    assert view.unplaced_member_ids == (), "org.yaml 에 없는 자리를 가리키는 사람이 있다"
    assert len(view.members) == len(agents), "조직도에 자리가 없는 사람이 있다"


def test_real_chart_has_no_banned_terms(real_chart: OrgChart) -> None:
    banned = BannedTerms.load(REPO / "agents" / "shared" / "banned.json")
    real_chart.validate_no_banned(banned)  # 예외가 없으면 통과

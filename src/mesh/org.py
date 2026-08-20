"""조직도 — 자리(unit)와 직급(rank)의 트리.

이 모듈이 지키는 것은 **하나뿐**이다: 조직의 층 이름을 코드가 알지 못하게 하는 것.

    본부 → 센터/연구소 → 팀 → 사람

위 네 층은 `config/org.yaml` 에 적힌 이 회사의 현재 모습이고, 여기 코드에는
`division`·`center`·`team` 이라는 문자열이 **하나도 없다.** `units` 는
`parent` 로 이어지는 일반 트리이고 깊이는 데이터가 정한다. 층을 하나 더
넣거나 빼는 변경은 YAML 편집으로 끝난다 (FR-23 과 같은 원칙).

⚠️ 조직도는 **인증 없이 보이는 화면**이다 (FR-31 과 같은 위험).
   `OrgUnit.name`·`description` 에 고객사명이 들어가면 게이트키퍼를 우회한
   유출이다. `validate_no_banned()` 가 로드 시점에 검사한다.

⚠️ 이 모듈은 L1(지원)이다. I/O 는 `load()` 한 번뿐이고 그 외에는 순수 함수다.
   경계 밖 클라이언트를 import 하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mesh.exceptions import ConfigError
from mesh.schemas import BannedTerms, OrgPlacement

#: 트리 깊이 상한. 순환은 `_check_acyclic()` 이 잡지만, 설정 실수로 만들어진
#: 깊은 사슬이 UI 를 무한 들여쓰기시키는 것도 막는다.
MAX_DEPTH = 8


# ══════════════════════════════════════════════════════════════════════
# 모델
# ══════════════════════════════════════════════════════════════════════


class OrgUnitKind(BaseModel):
    """계층 종류 하나. 코드는 `id` 를 해석하지 않고 라벨로만 쓴다."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    icon: str = "users"


class OrgRank(BaseModel):
    """직급 하나. `order` 가 팀 안 정렬 기준이다 (작을수록 위)."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    short: str = ""
    order: int = 100
    #: 팀 대표. UI 가 먼저 그리고, 브로드캐스트에서 팀 문맥 답변의 후보가 된다.
    leads: bool = False

    @property
    def badge(self) -> str:
        return self.short or self.label


class OrgUnit(BaseModel):
    """조직 단위 하나. `parent is None` 이면 최상위."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    name: str
    parent: str | None = None
    description: str = ""


class OrgMemberView(BaseModel):
    """조직도에 그려지는 사람 하나. `AgentCardView` 와 합쳐서 카드가 된다."""

    entity_id: str
    unit_id: str
    rank_id: str
    rank_label: str
    rank_badge: str
    rank_order: int
    title: str = ""
    #: ["VS본부", "SW플랫폼센터", "인증플랫폼팀"] — 화면의 경로 표시용
    unit_path: tuple[str, ...] = ()


class OrgUnitView(BaseModel):
    """트리 노드 하나. 재귀 구조다 — 깊이가 데이터에서 온다."""

    id: str
    kind: str
    kind_label: str
    kind_icon: str
    name: str
    description: str = ""
    depth: int = 0
    children: tuple[OrgUnitView, ...] = ()
    member_ids: tuple[str, ...] = ()
    #: 자기 + 자손의 구성원 수. 0 이면 화면이 접어서 그린다.
    member_count_total: int = 0


class OrgChartView(BaseModel):
    """`GET /api/org` 응답."""

    version: int = 1
    unit_kinds: tuple[OrgUnitKind, ...] = ()
    ranks: tuple[OrgRank, ...] = ()
    roots: tuple[OrgUnitView, ...] = ()
    members: tuple[OrgMemberView, ...] = ()
    #: 자리를 못 찾은 사람. 조용히 사라지면 "왜 안 보이지" 가 된다.
    unplaced_member_ids: tuple[str, ...] = ()


# ══════════════════════════════════════════════════════════════════════
# 차트
# ══════════════════════════════════════════════════════════════════════


class OrgChart(BaseModel):
    """로드된 조직도. 조회는 전부 순수 함수다."""

    model_config = ConfigDict(frozen=True)

    version: int = 1
    unit_kinds: tuple[OrgUnitKind, ...] = ()
    ranks: tuple[OrgRank, ...] = ()
    units: tuple[OrgUnit, ...] = ()

    # ── 조회 ─────────────────────────────────────────────────────────

    @property
    def units_by_id(self) -> dict[str, OrgUnit]:
        return {u.id: u for u in self.units}

    @property
    def ranks_by_id(self) -> dict[str, OrgRank]:
        return {r.id: r for r in self.ranks}

    @property
    def kinds_by_id(self) -> dict[str, OrgUnitKind]:
        return {k.id: k for k in self.unit_kinds}

    def unit(self, unit_id: str) -> OrgUnit | None:
        return self.units_by_id.get(unit_id)

    def rank(self, rank_id: str) -> OrgRank | None:
        return self.ranks_by_id.get(rank_id)

    def kind_label(self, kind_id: str) -> str:
        kind = self.kinds_by_id.get(kind_id)
        return kind.label if kind else kind_id

    def ancestors(self, unit_id: str) -> tuple[OrgUnit, ...]:
        """자기 자신부터 최상위까지. 순서는 **위에서 아래로** 뒤집어 반환한다."""
        by_id = self.units_by_id
        chain: list[OrgUnit] = []
        current = by_id.get(unit_id)
        seen: set[str] = set()
        while current is not None and current.id not in seen and len(chain) < MAX_DEPTH:
            seen.add(current.id)
            chain.append(current)
            current = by_id.get(current.parent) if current.parent else None
        return tuple(reversed(chain))

    def unit_path(self, unit_id: str) -> tuple[str, ...]:
        return tuple(u.name for u in self.ancestors(unit_id))

    def depth_of(self, unit_id: str) -> int:
        return max(0, len(self.ancestors(unit_id)) - 1)

    def children_of(self, unit_id: str | None) -> tuple[OrgUnit, ...]:
        return tuple(u for u in self.units if u.parent == unit_id)

    def descendant_ids(self, unit_id: str) -> tuple[str, ...]:
        """자기 자신 포함. 트리 조회용."""
        out: list[str] = [unit_id]
        stack = [unit_id]
        while stack:
            current = stack.pop()
            for child in self.children_of(current):
                if child.id in out:  # pragma: no cover — _check_acyclic 이 막는다
                    continue
                out.append(child.id)
                stack.append(child.id)
        return tuple(out)

    def shares_unit(self, a: str | None, b: str | None) -> bool:
        """두 사람이 같은 단위에 있는가. 브로드캐스트의 `team_context` 판정에 쓴다."""
        return bool(a) and a == b

    def common_ancestor_depth(self, a: str | None, b: str | None) -> int:
        """두 단위가 공유하는 조상의 깊이. 멀수록 작다 (없으면 0)."""
        if not a or not b:
            return 0
        left = [u.id for u in self.ancestors(a)]
        right = {u.id for u in self.ancestors(b)}
        return sum(1 for uid in left if uid in right)

    # ── 트리 조립 ────────────────────────────────────────────────────

    def to_view(self, placements: dict[str, OrgPlacement]) -> OrgChartView:
        """사람 배치를 얹어 화면이 그릴 트리를 만든다.

        ⚠️ 배치는 **코드가 조립한다.** `placements` 에 있는 unit/rank 가
           이 차트에 없으면 노드를 만들지 않고 `unplaced_member_ids` 로 뺀다.
           설정에 오타가 나면 사람이 조용히 사라지는 대신 눈에 띈다.
        """
        by_unit: dict[str, list[tuple[int, str]]] = {}
        members: list[OrgMemberView] = []
        unplaced: list[str] = []

        for entity_id, place in placements.items():
            unit = self.unit(place.unit)
            rank = self.rank(place.rank)
            if unit is None or rank is None:
                unplaced.append(entity_id)
                continue
            by_unit.setdefault(unit.id, []).append((rank.order, entity_id))
            members.append(
                OrgMemberView(
                    entity_id=entity_id,
                    unit_id=unit.id,
                    rank_id=rank.id,
                    rank_label=rank.label,
                    rank_badge=rank.badge,
                    rank_order=rank.order,
                    title=place.title,
                    unit_path=self.unit_path(unit.id),
                )
            )

        for bucket in by_unit.values():
            bucket.sort()

        def build(unit: OrgUnit, depth: int) -> OrgUnitView:
            children = tuple(build(c, depth + 1) for c in self.children_of(unit.id))
            own = tuple(eid for _, eid in by_unit.get(unit.id, []))
            total = len(own) + sum(c.member_count_total for c in children)
            return OrgUnitView(
                id=unit.id,
                kind=unit.kind,
                kind_label=self.kind_label(unit.kind),
                kind_icon=(self.kinds_by_id.get(unit.kind).icon if unit.kind in self.kinds_by_id else "users"),
                name=unit.name,
                description=unit.description,
                depth=depth,
                children=children,
                member_ids=own,
                member_count_total=total,
            )

        roots = tuple(build(u, 0) for u in self.children_of(None))
        members.sort(key=lambda m: (m.unit_path, m.rank_order, m.entity_id))
        return OrgChartView(
            version=self.version,
            unit_kinds=self.unit_kinds,
            ranks=self.ranks,
            roots=roots,
            members=tuple(members),
            unplaced_member_ids=tuple(sorted(unplaced)),
        )

    # ── 검증 ─────────────────────────────────────────────────────────

    def validate_tree(self) -> None:
        ids = [u.id for u in self.units]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ConfigError(f"org.yaml: 중복 unit id {sorted(dupes)}")
        known = set(ids)
        for unit in self.units:
            if unit.parent is not None and unit.parent not in known:
                raise ConfigError(f"org.yaml: {unit.id!r} 의 parent {unit.parent!r} 가 없다")
        self._check_acyclic()
        if not self.ranks:
            raise ConfigError("org.yaml: ranks 가 비어 있다")

    def _check_acyclic(self) -> None:
        by_id = self.units_by_id
        for unit in self.units:
            seen: set[str] = set()
            current: OrgUnit | None = unit
            while current is not None:
                if current.id in seen:
                    raise ConfigError(f"org.yaml: 순환 참조 — {unit.id!r} 의 조상 사슬")
                seen.add(current.id)
                if len(seen) > MAX_DEPTH:
                    raise ConfigError(
                        f"org.yaml: 계층이 {MAX_DEPTH} 층을 넘는다 ({unit.id!r})"
                    )
                current = by_id.get(current.parent) if current.parent else None

    def validate_no_banned(self, banned: BannedTerms) -> None:
        """조직도 문자열에 금칙어가 있으면 로드에서 막는다.

        조직도는 인증 없이 보이는 화면이라 여기 실린 고객사명은
        게이트키퍼를 우회한 유출이 된다 (FR-31 과 같은 이유).
        """
        for unit in self.units:
            hits = banned.hits(f"{unit.name}\n{unit.description}")
            if hits:
                raise ConfigError(
                    f"org.yaml: 조직 단위 {unit.id!r} 에 금칙어가 있다 {sorted(set(hits))}. "
                    "조직도는 인증 없이 보이는 화면이다"
                )


# ══════════════════════════════════════════════════════════════════════
# 로드
# ══════════════════════════════════════════════════════════════════════

#: 조직도 파일이 없을 때 쓰는 최소 차트. 사람은 전부 `unplaced` 가 되고
#: 화면은 평평한 목록을 그린다 — 조직도가 없다고 앱이 죽지는 않는다.
_EMPTY = OrgChart(
    version=0,
    unit_kinds=(),
    ranks=(OrgRank(id="member", label="구성원", short="", order=100),),
    units=(),
)


def load_org(path: Path, *, banned: BannedTerms | None = None) -> OrgChart:
    """`config/org.yaml` 로드.

    파일이 없으면 빈 차트를 돌려준다 (예외를 올리지 않는다) — 조직도는
    표시용이고, 없다고 질의가 막히면 안 된다. 대신 **형식이 틀리면** 올린다.
    """
    if not path.exists():
        return _EMPTY

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"org.yaml 파싱 실패: {e}") from e

    chart = OrgChart(
        version=int(raw.get("version", 1)),
        unit_kinds=tuple(OrgUnitKind(**k) for k in (raw.get("unit_kinds") or [])),
        ranks=tuple(OrgRank(**r) for r in (raw.get("ranks") or [])),
        units=tuple(OrgUnit(**u) for u in (raw.get("units") or [])),
    )
    chart.validate_tree()
    if banned is not None:
        chart.validate_no_banned(banned)
    return chart


def sort_members(
    entity_ids: Iterable[str],
    placements: dict[str, OrgPlacement],
    chart: OrgChart,
) -> tuple[str, ...]:
    """직급 순 → 이름 순. 화면 여러 곳이 같은 순서를 써야 한다."""

    def key(entity_id: str) -> tuple[int, str]:
        place = placements.get(entity_id)
        rank = chart.rank(place.rank) if place else None
        return (rank.order if rank else 999, entity_id)

    return tuple(sorted(entity_ids, key=key))


def unit_label_path(chart: OrgChart, unit_id: str | None, sep: str = " · ") -> str:
    """화면 한 줄용 경로 문자열. 자리를 못 찾으면 빈 문자열."""
    if not unit_id:
        return ""
    return sep.join(chart.unit_path(unit_id))


def members_of_units(
    unit_ids: Sequence[str],
    placements: dict[str, OrgPlacement],
) -> tuple[str, ...]:
    wanted = set(unit_ids)
    return tuple(eid for eid, p in placements.items() if p.unit in wanted)

"""import 경계 강제 (SECURITY-11, `shared-infrastructure.md` §10).

"다른 파일에서 Claude 클라이언트를 import 하지 않는다"는 규칙이 실제로
지켜지는지 검사한다. **리뷰 매너에 의존하지 않는다** —
5일 동안 3명이 작업하면 반드시 누군가 실수한다.

세 가지 경계를 검사한다:

  1. 경계 밖 클라이언트 (`mesh.llm.broker`, `boto3`)
     -> `mesh.gatekeeper` 와 `mesh.audit` 만 허용

  2. `Chunk` (원문)
     -> 변환·판정·검증·읽기 모듈만 허용. 나머지는 PayloadEnvelope 만 받는다

  3. `Mapping`
     -> `mesh.rehydrator` 와 `mesh.gatekeeper` 만 허용

`TYPE_CHECKING` 블록 안의 import 는 런타임에 실행되지 않으므로 허용한다
(순환 import 를 피하면서 타입 힌트를 쓰기 위한 표준 패턴).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PKG = SRC / "mesh"


# ══════════════════════════════════════════════════════════════════════
# 규칙 정의
# ══════════════════════════════════════════════════════════════════════

#: 경계 밖 클라이언트를 런타임에 import 할 수 있는 모듈.
BOUNDARY_CROSSERS = frozenset(
    {
        "mesh.gatekeeper",  # ask_agent() — 유일한 통로
        "mesh.audit",  # mirror() — 위 페이로드의 사본
        "mesh.llm.broker",  # 구현체 자신
    }
)

#: 런타임 import 가 금지된 모듈·패키지.
FORBIDDEN_RUNTIME_IMPORTS = frozenset({"mesh.llm.broker", "boto3", "botocore"})

#: 원문(`Chunk`)을 받을 수 있는 모듈.
CHUNK_HANDLERS = frozenset(
    {
        "mesh.schemas",  # 타입 정의
        "mesh.classifier",  # 등급 판정
        "mesh.extractor",  # 슬롯 채우기
        "mesh.pseudonymizer",  # 가명화
        "mesh.validator",  # 5-gram 대조 (보지만 내보내지 않는다)
        "mesh.store",  # 읽기
        "mesh.gatekeeper",  # 조율
        "mesh.orchestrator",  # Store -> Gatekeeper 전달
        "mesh.audit",  # sweep_for_leaks 전수 검사
    }
)

#: `Mapping` 을 받을 수 있는 모듈.
MAPPING_HANDLERS = frozenset(
    {
        "mesh.schemas",  # 타입 정의
        "mesh.rehydrator",  # 역치환
        "mesh.gatekeeper",  # 조율 + 캐시
        "mesh.extractor",  # 생성
        "mesh.pseudonymizer",  # 생성
    }
)


# ══════════════════════════════════════════════════════════════════════
# AST 분석
# ══════════════════════════════════════════════════════════════════════


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _type_checking_lines(tree: ast.Module) -> set[int]:
    """`if TYPE_CHECKING:` 블록에 속한 줄 번호. 런타임에 실행되지 않는다."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_tc:
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    out.add(child.lineno)
    return out


def _runtime_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """`(모듈 경로, 줄번호)`. TYPE_CHECKING 블록은 제외한다."""
    skip = _type_checking_lines(tree)
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if node.lineno in skip:
                continue
            out += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.lineno in skip:
                continue
            out.append((node.module, node.lineno))
    return out


def _imported_names(tree: ast.Module) -> list[tuple[str, int]]:
    """`from x import Name` 의 Name 들. TYPE_CHECKING 블록은 제외한다."""
    skip = _type_checking_lines(tree)
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.lineno in skip:
            continue
        out += [(alias.name, node.lineno) for alias in node.names]
    return out


def _matches(imported: str, forbidden: str) -> bool:
    return imported == forbidden or imported.startswith(forbidden + ".")


ALL_MODULES = sorted(PKG.rglob("*.py"))


@pytest.fixture(scope="module")
def parsed() -> dict[str, tuple[ast.Module, Path]]:
    return {_module_name(p): (ast.parse(p.read_text(encoding="utf-8")), p) for p in ALL_MODULES}


# ══════════════════════════════════════════════════════════════════════
# 규칙 1 — 경계 밖 클라이언트
# ══════════════════════════════════════════════════════════════════════


def test_only_gatekeeper_and_audit_cross_the_boundary(parsed):
    """`ask_agent()` 와 `mirror()` 외에는 경계 밖 클라이언트를 만지지 않는다."""
    violations: list[str] = []
    for mod, (tree, path) in parsed.items():
        if mod in BOUNDARY_CROSSERS:
            continue
        for imported, lineno in _runtime_imports(tree):
            for forbidden in FORBIDDEN_RUNTIME_IMPORTS:
                if _matches(imported, forbidden):
                    violations.append(
                        f"{path.relative_to(SRC.parent)}:{lineno} "
                        f"{mod} imports {imported} — 경계는 gatekeeper/audit 만 넘는다"
                    )
    assert not violations, "경계 위반:\n  " + "\n  ".join(violations)


def test_boundary_crossers_are_a_short_list():
    """허용 목록이 늘어나면 단일 통로 규칙이 무의미해진다."""
    assert len(BOUNDARY_CROSSERS) <= 3


def _module_level_imports(tree: ast.Module) -> list[str]:
    """**최상위** import 만. 함수 스코프 import 는 제외한다."""
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_gatekeeper_does_not_import_broker_at_module_level(parsed):
    """`gatekeeper` 는 최상위에서 broker 를 import 하지 않는다.

    두 가지를 지키려는 것이다:
      - 타입 힌트는 `TYPE_CHECKING` 블록으로 (순환 결합 회피)
      - 실제 생성은 `Gatekeeper.build()` 안의 **함수 스코프** import 로

    생성을 gatekeeper 안에 두는 것이 규칙 1 의 핵심이다. `main.py` 가
    `BrokerClient` 를 만들면 경계를 넘는 모듈이 하나 늘어난다 (SECURITY-11).
    그러면 "단일 통로"가 아니게 된다.

    함수 스코프로 두는 이유는 mock 모드에서 `httpx`·`boto3` 경로를 끌고 오지
    않게 하려는 것이다.
    """
    tree, _ = parsed["mesh.gatekeeper"]
    assert "mesh.llm.broker" not in _module_level_imports(tree)


def test_no_module_imports_broker_at_module_level_except_itself(parsed):
    """최상위 import 는 어디에도 없어야 한다 — 생성조차 함수 안에서만 한다."""
    offenders = [
        mod
        for mod, (tree, _) in parsed.items()
        if mod != "mesh.llm.broker" and "mesh.llm.broker" in _module_level_imports(tree)
    ]
    assert not offenders, offenders


# ══════════════════════════════════════════════════════════════════════
# 규칙 2 — 원문 전파 경계
# ══════════════════════════════════════════════════════════════════════


def test_only_designated_modules_handle_chunks(parsed):
    """원문을 받는 모듈이 늘어나면 어디로 흘러가는지 추적할 수 없다."""
    violations: list[str] = []
    for mod, (tree, path) in parsed.items():
        if mod in CHUNK_HANDLERS:
            continue
        for name, lineno in _imported_names(tree):
            if name == "Chunk":
                violations.append(
                    f"{path.relative_to(SRC.parent)}:{lineno} "
                    f"{mod} imports Chunk — PayloadEnvelope 만 받으라"
                )
    assert not violations, "원문 전파 경계 위반:\n  " + "\n  ".join(violations)


# ══════════════════════════════════════════════════════════════════════
# 규칙 3 — 매핑 전파 경계
# ══════════════════════════════════════════════════════════════════════


def test_only_designated_modules_handle_mapping(parsed):
    """매핑이 유출되면 과거의 모든 감사 로그가 복호화된다."""
    violations: list[str] = []
    for mod, (tree, path) in parsed.items():
        if mod in MAPPING_HANDLERS:
            continue
        for name, lineno in _imported_names(tree):
            if name == "Mapping":
                violations.append(f"{path.relative_to(SRC.parent)}:{lineno} {mod} imports Mapping")
    assert not violations, "매핑 전파 경계 위반:\n  " + "\n  ".join(violations)


# ══════════════════════════════════════════════════════════════════════
# 레이어 순서 — 순환 의존 방지 (component-dependency.md §6)
# ══════════════════════════════════════════════════════════════════════

#: 레이어 번호는 **의존 순서**를 나타낸다 (순수성이 아니다).
#: 낮은 번호가 아래이고, 의존은 항상 위 -> 아래 방향이다.
#:
#:   L0  기반      설정 · 타입 · 예외. 서로만 참조
#:   L1  지원      순수 함수(validator, rehydrator) + 타입 계약(api_models) + 픽스처 I/O
#:   L2  모델      LLM 클라이언트
#:   L3  변환      판정 · 추출 · 가명화
#:   L4  경계      gatekeeper · audit  <- 여기만 경계를 넘는다
#:   L5  도메인    store · agent · inbox · api_models
#:   L6  조율      orchestrator · documents
#:   L7  전달      main (FastAPI)
LAYERS: dict[str, int] = {
    "mesh": 0,
    "mesh.exceptions": 0,
    "mesh.config": 0,
    "mesh.schemas": 0,
    "mesh.protocol_schemas": 0,  # 데이터 모델만 (의존 없음)
    "mesh.validator": 1,
    "mesh.rehydrator": 1,
    "mesh.api_models": 1,
    "mesh.llm": 1,
    "mesh.llm.fixtures": 1,
    "mesh.llm.exaone": 2,
    "mesh.llm.broker": 2,
    "mesh.classifier": 3,
    "mesh.extractor": 3,
    "mesh.pseudonymizer": 3,
    "mesh.protocol_store": 0,  # config에서 지연 import — config와 같은 층
    "mesh.gatekeeper": 4,
    "mesh.audit": 4,
    "mesh.store": 5,
    "mesh.agent": 5,
    "mesh.inbox": 5,
    "mesh.orchestrator": 6,
    "mesh.documents": 6,
    "mesh.main": 7,
}


def test_every_module_has_a_layer(parsed):
    """새 모듈을 만들면 레이어를 선언해야 한다 — 순환 의존을 미리 막는다."""
    missing = sorted(set(parsed) - set(LAYERS))
    assert not missing, f"LAYERS 에 레이어를 선언하라: {missing}"


def test_dependencies_flow_downward_only(parsed):
    """의존은 항상 위 -> 아래 방향이다. 같은 레이어끼리도 금지."""
    violations: list[str] = []
    for mod, (tree, path) in parsed.items():
        my_layer = LAYERS[mod]
        for imported, lineno in _runtime_imports(tree):
            if not imported.startswith("mesh"):
                continue
            other = LAYERS.get(imported)
            if other is None or imported == mod:
                continue
            if other >= my_layer and not (other == my_layer == 0):
                violations.append(
                    f"{path.relative_to(SRC.parent)}:{lineno} "
                    f"{mod}(L{my_layer}) -> {imported}(L{other}) — 위 방향 의존"
                )
    assert not violations, "레이어 위반:\n  " + "\n  ".join(violations)


# ══════════════════════════════════════════════════════════════════════
# 검사기 자체 검사 — 아무것도 못 잡는 검사기는 무의미하다
# ══════════════════════════════════════════════════════════════════════


def test_detector_catches_planted_boundary_violation():
    tree = ast.parse("import boto3\n")
    assert any(_matches(m, f) for m, _ in _runtime_imports(tree) for f in FORBIDDEN_RUNTIME_IMPORTS)


def test_detector_catches_planted_broker_import():
    tree = ast.parse("from mesh.llm.broker import BrokerClient\n")
    assert any(_matches(m, f) for m, _ in _runtime_imports(tree) for f in FORBIDDEN_RUNTIME_IMPORTS)


def test_detector_ignores_type_checking_block():
    tree = ast.parse(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from mesh.llm.broker import BrokerClient\n"
    )
    assert not [m for m, _ in _runtime_imports(tree) if _matches(m, "mesh.llm.broker")]


def test_detector_catches_planted_chunk_import():
    tree = ast.parse("from mesh.schemas import Chunk, Tier\n")
    assert any(n == "Chunk" for n, _ in _imported_names(tree))


def test_detector_catches_planted_mapping_import():
    tree = ast.parse("from mesh.schemas import Mapping\n")
    assert any(n == "Mapping" for n, _ in _imported_names(tree))

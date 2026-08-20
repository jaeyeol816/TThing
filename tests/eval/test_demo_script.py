"""`scripts/demo.py` 가 실제로 동작하는지 — LLM 호출 없이 확인한다.

`make demo` 는 live 모드나 **녹화된 픽스처**를 필요로 한다 (픽스처 녹화는
Day 4 작업이다, `logical-components.md` §7). 그렇다고 Day 3 에 이 스크립트를
검증하지 않고 넘기면 시연 당일에 처음 돌려보게 된다.

그래서 여기서 대역을 주입해 4막 전체를 실행한다. 스크립트가 참조하는 필드가
하나라도 바뀌면 이 테스트가 잡는다 — 시연 대본이 코드와 어긋나지 않게 한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def demo():
    """`scripts/demo.py` 를 모듈로 로드한다 (패키지가 아니라 스크립트다)."""
    spec = importlib.util.spec_from_file_location("demo_script", REPO / "scripts" / "demo.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_script"] = module
    spec.loader.exec_module(module)
    return module


def test_four_acts_are_defined(demo):
    """3막 + 후속 질문. 시연 대본이 코드에 있다."""
    assert len(demo.SCENARIOS) == 4
    for spec in demo.SCENARIOS:
        assert spec["question"].strip()
        assert 1 <= len(spec["targets"]) <= 2
        assert spec["watch"], "각 막에서 무엇을 볼지 적혀 있어야 한다"


@pytest.mark.parametrize("index", range(4))
async def test_each_act_runs(demo, wiring, capsys, index):
    spec = demo.SCENARIOS[index]
    if "p99" in spec["question"]:
        # 후속 질문은 구조 추출 실패 경로다
        from mesh.exceptions import ExaoneUnavailable

        wiring.fake_exaone.fail["extract"] = ExaoneUnavailable("슬롯을 채울 수 없다")
    elif index == 1:
        wiring.fake_exaone.slots = [{"sampling_strategy_class": "hybrid"}]
    elif index == 2:
        wiring.fake_exaone.slots = [
            {"session_binding": "none", "renewal_mode": "background_silent"}
        ]

    await demo.run_act(wiring, spec, auto_approve=True, show_payload=True)
    out = capsys.readouterr().out
    assert spec["question"] in out
    assert "prepare" in out


async def test_agent_listing_shows_no_session_text(demo, wiring, capsys):
    await demo.show_agents(wiring)
    out = capsys.readouterr().out
    assert "김철수 책임" in out
    for leak in ("고객사 H", "atlas-ml", "SDK v3.2", "레거시 SSO"):
        assert leak not in out, leak


async def test_leak_sweep_reports_clean(demo, wiring, capsys):
    """1막을 돌린 뒤 전수 검사를 하면 유출 0건이어야 한다."""
    await demo.run_act(wiring, demo.SCENARIOS[0], auto_approve=True, show_payload=False)
    capsys.readouterr()
    await demo.leak_sweep(wiring)
    out = capsys.readouterr().out
    assert "유출 0건" in out
    assert "0건 — 이 문구는 경계를 넘은 적이 없습니다" in out


async def test_cancelling_leaves_no_audit_record(demo, wiring, capsys, monkeypatch):
    """취소하면 감사 레코드가 남지 않는다 (BR-U-03)."""
    monkeypatch.setattr("builtins.input", lambda _: "n")
    before = wiring.audit.count()
    await demo.run_act(wiring, demo.SCENARIOS[0], auto_approve=False, show_payload=False)
    out = capsys.readouterr().out
    assert "취소했습니다" in out
    assert wiring.audit.count() == before
    assert len(wiring.gatekeeper.cache) == 0


def test_demo_does_not_import_boundary_clients(demo):
    """시연 스크립트도 경계 규칙을 지킨다."""
    import ast

    tree = ast.parse((REPO / "scripts" / "demo.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for forbidden in ("boto3", "botocore", "mesh.llm.broker"):
        assert forbidden not in imported, forbidden

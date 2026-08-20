"""게이트 G2 — 등급 판정 정확도 (S-02, S-30, 설계 §7.2).

    make eval-classify

**이 게이트를 통과하지 못하면 Day 3 으로 넘어가지 않는다.**

목표
    기밀 재현율   100%    하나라도 놓치면 실패 (하향 오류 = 유출)
    전체 정확도   >= 90%
    함정 문서     1/1     경로도 헤더도 단서가 없는 문서

──────────────────────────────────────────────────────────────────────
왜 규칙만으로 측정하는가
──────────────────────────────────────────────────────────────────────

프로덕션 판정은 `max(규칙, EXAONE)` 이다. `max` 는 **등급을 올릴 수만 있다.**
따라서

    기밀 재현율(프로덕션) >= 기밀 재현율(규칙)

이 항상 성립한다. 규칙만으로 100% 가 나오면 EXAONE 을 더해도 100% 다.
반대로 EXAONE 이 추가로 올리는 것은 상향 오류(불편)이고 유출이 아니다.

그래서 게이트는 **규칙만** 측정한다. 이점 셋:
  - 결정적이다 (LLM 호출 0회, 재현 가능, CI 에서 무료)
  - 하한선을 측정한다 (모델 없이도 이 정확도가 보장된다)
  - 모델 가용성에 게이트가 흔들리지 않는다

EXAONE 을 포함한 실측이 필요하면 `MESH_EVAL_WITH_EXAONE=1` 로 실행한다
(문서 11건 x 1회 = 11회 호출).

──────────────────────────────────────────────────────────────────────
오분류의 비대칭 (labels.json `_asymmetry`)
──────────────────────────────────────────────────────────────────────

    상향  internal -> secret    불편. 답변이 무뎌진다      경고
    하향  secret -> internal    *** 유출 ***               blocking
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from mesh.classifier import Classifier, rule_tier
from mesh.schemas import BannedTerms, ClassificationRules, Tier

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"


# ══════════════════════════════════════════════════════════════════════
# 측정
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Outcome:
    path: str
    expected: Tier
    got: Tier
    rule: int
    reason: str
    trap: bool

    @property
    def correct(self) -> bool:
        return self.got is self.expected

    @property
    def direction(self) -> str:
        if self.correct:
            return "-"
        return "상향" if self.got > self.expected else "하향"


def _load_labels() -> list[dict]:
    raw = json.loads((DATA / "labels.json").read_text(encoding="utf-8"))
    return raw["labels"]


def _load_targets() -> dict:
    raw = json.loads((DATA / "labels.json").read_text(encoding="utf-8"))
    return raw["_targets"]


def _rules() -> ClassificationRules:
    return ClassificationRules(banned=BannedTerms.load(DATA / "banned.json"))


def measure_rules() -> list[Outcome]:
    rules = _rules()
    out: list[Outcome] = []
    for label in _load_labels():
        path = label["path"]
        text = (DATA / path).read_text(encoding="utf-8")
        verdict = rule_tier(text, path, rules)
        out.append(
            Outcome(
                path=path,
                expected=Tier(label["tier"]),
                got=verdict.tier,
                rule=verdict.rule,
                reason=verdict.reasons[0] if verdict.reasons else "",
                trap=bool(label.get("trap")),
            )
        )
    return out


async def measure_with_exaone() -> list[Outcome]:
    """실측용. `MESH_EVAL_WITH_EXAONE=1` 일 때만 호출된다 (LLM 11회)."""
    from mesh.config import Config
    from mesh.llm.exaone import ExaoneClient

    cfg = Config.load(strict=False)
    exaone = ExaoneClient(cfg)
    classifier = Classifier(_rules(), exaone)
    out: list[Outcome] = []
    try:
        for label in _load_labels():
            path = label["path"]
            text = (DATA / path).read_text(encoding="utf-8")
            decision = await classifier.classify(text, path)
            out.append(
                Outcome(
                    path=path,
                    expected=Tier(label["tier"]),
                    got=decision.tier,
                    rule=0,
                    reason=(
                        f"rule={decision.rule_tier.value} "
                        f"exaone={decision.exaone_tier.value if decision.exaone_tier else '-'}"
                    ),
                    trap=bool(label.get("trap")),
                )
            )
    finally:
        await exaone.aclose()
    return out


# ══════════════════════════════════════════════════════════════════════
# 지표
# ══════════════════════════════════════════════════════════════════════


def metrics(outcomes: list[Outcome]) -> dict[str, float | int]:
    total = len(outcomes)
    secrets = [o for o in outcomes if o.expected is Tier.SECRET]
    traps = [o for o in outcomes if o.trap]
    downgrades = [o for o in outcomes if o.direction == "하향"]
    upgrades = [o for o in outcomes if o.direction == "상향"]
    return {
        "total": total,
        "correct": sum(o.correct for o in outcomes),
        "accuracy": sum(o.correct for o in outcomes) / total if total else 0.0,
        "secret_total": len(secrets),
        "secret_recall": (
            sum(o.got is Tier.SECRET for o in secrets) / len(secrets) if secrets else 1.0
        ),
        "trap_total": len(traps),
        "trap_recall": (sum(o.got is Tier.SECRET for o in traps) / len(traps) if traps else 1.0),
        "downgrades": len(downgrades),
        "upgrades": len(upgrades),
    }


def render_report(outcomes: list[Outcome], m: dict, *, mode: str) -> str:
    """사람이 읽을 리포트.

    ⚠️ **assert 보다 먼저 출력한다.** 실패했을 때 무엇이 왜 틀렸는지 보이지
       않으면 게이트가 진단 도구가 되지 못한다.
    """
    lines: list[str] = []
    add = lines.append
    add("")
    add("═" * 92)
    add(f"  게이트 G2 · 등급 판정 정확도   (판정 모드: {mode})")
    add("═" * 92)
    add(f"  {'판정':6s} {'정답':8s} {'결과':8s} {'규칙':4s}  {'문서'}")
    add("─" * 92)
    for o in sorted(outcomes, key=lambda x: (x.correct, x.path)):
        mark = "OK" if o.correct else o.direction
        flag = " [함정]" if o.trap else ""
        add(f"  {mark:6s} {o.expected.value:8s} {o.got.value:8s} {o.rule:<4d}  {o.path}{flag}")
        if not o.correct:
            add(f"  {'':22s}└ 근거: {o.reason}")
    add("─" * 92)
    add(f"  정확도        {m['correct']}/{m['total']} = {m['accuracy']:.1%}   (목표 >= 90%)")
    add(
        f"  기밀 재현율   {m['secret_total'] - m['downgrades']}/{m['secret_total']}"
        f" = {m['secret_recall']:.1%}   (목표 100%)"
    )
    add(f"  함정 탐지     {m['trap_recall']:.0%}   ({m['trap_total']}건)")
    add("")
    add(f"  상향 오류     {m['upgrades']}건   불편 — 답변이 무뎌진다")
    add(f"  하향 오류     {m['downgrades']}건   *** 유출 *** — blocking")
    add("═" * 92)
    if m["downgrades"]:
        add("  🔴 하향 오류가 있다. 기밀 문서가 낮은 등급으로 판정되면 원문이 나간다.")
        add("     classifier.rule_tier 의 규칙 또는 banned.json 을 보강하라.")
    add("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 게이트
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def outcomes() -> list[Outcome]:
    return measure_rules()


@pytest.fixture(scope="module")
def report(outcomes) -> dict:
    m = metrics(outcomes)
    # ⚠️ assert 앞에서 출력한다. `make eval-classify` 가 `-s` 로 실행한다.
    print(render_report(outcomes, m, mode="규칙만 (LLM 호출 0회)"))
    return m


def test_no_downgrade_errors(report, outcomes):
    """🔴 blocking — 하향 오류는 유출이다."""
    bad = [o for o in outcomes if o.direction == "하향"]
    assert not bad, "하향 오분류: " + ", ".join(
        f"{o.path} ({o.expected.value} -> {o.got.value})" for o in bad
    )


def test_secret_recall_is_perfect(report):
    """🔴 blocking — 미달이면 Step 9~10 으로 돌아간다 (설계 §7.2)."""
    target = _load_targets()["secret_recall"]
    assert report["secret_recall"] >= target, (
        f"기밀 재현율 {report['secret_recall']:.1%} < 목표 {target:.0%}"
    )


def test_trap_document_is_detected(report):
    """🔴 blocking — 경로도 헤더도 단서가 없고 본문 금액만이 신호다 (FR-52)."""
    target = _load_targets()["trap_recall"]
    assert report["trap_total"] >= 1, "labels.json 에 함정 문서가 없다"
    assert report["trap_recall"] >= target, (
        f"함정 문서 탐지 {report['trap_recall']:.0%} < 목표 {target:.0%}"
    )


def test_accuracy_meets_target(report):
    target = _load_targets()["accuracy"]
    assert report["accuracy"] >= target, f"정확도 {report['accuracy']:.1%} < 목표 {target:.0%}"


def test_open_tier_requires_explicit_marking(outcomes):
    """`OPEN` 으로 판정된 문서는 정답도 `OPEN` 이어야 한다.

    상향 오류는 불편이지만 `OPEN` 오판정은 원문이 그대로 나가는 유출이다.
    이 검사는 위 재현율과 별도로 `OPEN` 쪽 정밀도를 본다.
    """
    wrong = [o for o in outcomes if o.got is Tier.OPEN and o.expected is not Tier.OPEN]
    assert not wrong, [o.path for o in wrong]


def test_every_corpus_document_is_labelled():
    """라벨 없는 문서가 있으면 정확도가 실제보다 좋게 보인다."""
    labelled = {label["path"] for label in _load_labels()}
    found = {
        p.relative_to(DATA).as_posix()
        for p in (DATA / "corpus").rglob("*")
        if p.is_file() and not p.name.startswith(".")
    }
    assert found == labelled, {
        "라벨 없음": sorted(found - labelled),
        "파일 없음": sorted(labelled - found),
    }


def test_labels_cover_all_three_tiers(outcomes):
    """한 등급만 있는 데이터셋에서는 정확도가 무의미하다."""
    assert {o.expected for o in outcomes} == set(Tier)


def test_rule_only_classification_makes_no_llm_calls():
    """게이트가 결정적이어야 CI 에서 재현된다."""
    a = measure_rules()
    b = measure_rules()
    assert [o.got for o in a] == [o.got for o in b]


# ══════════════════════════════════════════════════════════════════════
# 실측 (선택) — MESH_EVAL_WITH_EXAONE=1
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    os.getenv("MESH_EVAL_WITH_EXAONE") != "1",
    reason="LLM 호출 11회. MESH_EVAL_WITH_EXAONE=1 로 명시할 때만 실행한다",
)
async def test_production_classification_is_never_worse():
    """`max(규칙, EXAONE)` 이 규칙보다 낮아지지 않음을 실측으로 확인한다."""
    rules_only = {o.path: o.got for o in measure_rules()}
    with_model = await measure_with_exaone()
    m = metrics(with_model)
    print(render_report(with_model, m, mode="max(규칙, EXAONE) — 실측"))

    for o in with_model:
        assert o.got >= rules_only[o.path], f"{o.path}: max() 가 등급을 낮췄다"
    assert m["downgrades"] == 0
    assert m["secret_recall"] == 1.0

"""schemas.py — 동결된 타입 계약의 불변식."""

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from mesh.schemas import (
    STRUCTURAL_KEYS,
    AgentCall,
    BannedTerms,
    Chunk,
    Citation,
    Disclose,
    PayloadEnvelope,
    Representation,
    SlotDef,
    TaskSchema,
    Tier,
    ValidationResult,
    Vocabulary,
)

DATA = Path(__file__).resolve().parents[2] / "data"


# ── SlotDef: 자유 문자열 슬롯 금지 ────────────────────────────────────


def test_slot_kind_is_limited_to_three():
    """kind 에 str 이 없어야 한다 — 원문이 새어나갈 채널을 만들지 않는다."""
    with pytest.raises(ValidationError):
        SlotDef(name="free_text", kind="str")  # type: ignore[arg-type]


def test_enum_slot_requires_allowed():
    with pytest.raises(ValidationError, match="allowed"):
        SlotDef(name="x", kind="enum")


def test_int_slot_requires_range():
    with pytest.raises(ValidationError, match="min/max"):
        SlotDef(name="x", kind="int")


def test_bool_slot_rejects_allowed():
    with pytest.raises(ValidationError, match="무의미"):
        SlotDef(name="x", kind="bool", allowed=("true", "false"))


def test_valid_slots():
    SlotDef(name="a", kind="enum", allowed=("p", "q"))
    SlotDef(name="b", kind="int", min=0, max=10)
    SlotDef(name="c", kind="bool")


# ── Vocabulary 로드 ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def vocab() -> Vocabulary:
    return Vocabulary.load(DATA / "vocab.json")


def test_vocab_loads(vocab):
    assert vocab.version == "1.0.0"
    assert len(vocab.slots) == 8
    assert set(vocab.tasks) == {
        "constraint_conflict_check",
        "technique_lookup",
        "rationale_lookup",
    }
    assert "external_requirement" in vocab.entity_roles


def test_role_is_structural_not_a_slot():
    """role 은 페이로드의 구조 키다. 허용값은 task_schema.entity_roles 에서 온다 —
    전역 목록보다 정확하다 (constraint_conflict_check 에 'goal' 이 오면 잡힌다)."""
    assert "role" in STRUCTURAL_KEYS


def test_vocab_rejects_slot_named_like_structural_key(tmp_path):
    bad = tmp_path / "vocab.json"
    bad.write_text(
        """{"version":"x","slots":{"role":{"kind":"bool"}},
            "tasks":[],"domains":[],"question_templates":[],
            "entity_roles":[],"task_schemas":{}}""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="구조 키와 겹친다"):
        Vocabulary.load(bad)


def test_no_free_text_slot_in_vocab(vocab):
    assert all(s.kind in {"enum", "int", "bool"} for s in vocab.slots.values())


def test_performance_slots_intentionally_absent(vocab):
    """시나리오 3 폴백의 전제 (FR-54). 이 슬롯이 생기면 폴백이 사라진다."""
    for forbidden in ("p99_latency_ms", "throughput_tps", "amount", "contract_no", "price"):
        assert forbidden not in vocab.slots, f"{forbidden} 슬롯이 있으면 시나리오 3이 깨진다"


def test_task_schema_slot_names(vocab):
    ts = vocab.task_schemas["constraint_conflict_check"]
    assert "auth_mechanism_class" in ts.slot_names
    assert "p99_latency_ms" not in ts.slot_names
    assert ts.required_slots <= ts.slot_names


def test_task_schema_rejects_undefined_slot(tmp_path):
    bad = tmp_path / "vocab.json"
    bad.write_text(
        """{"version":"x","slots":{"a":{"kind":"bool"}},
            "tasks":["t"],"domains":["d"],"question_templates":["q"],
            "entity_roles":["r"],
            "task_schemas":{"t":{"domain":"d","question_template":"q",
              "entity_roles":["r"],"slots":["a","ghost"],"answer_format":{}}}}""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ghost"):
        Vocabulary.load(bad)


def test_task_schema_rejects_unregistered_role(tmp_path):
    bad = tmp_path / "vocab.json"
    bad.write_text(
        """{"version":"x","slots":{"a":{"kind":"bool"}},
            "tasks":["t"],"domains":["d"],"question_templates":["q"],
            "entity_roles":["our_component"],
            "task_schemas":{"t":{"domain":"d","question_template":"q",
              "entity_roles":["ghost_role"],"slots":["a"],"answer_format":{}}}}""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ghost_role"):
        Vocabulary.load(bad)


def test_structural_keys_disjoint_from_slots(vocab):
    """구조 키와 슬롯 이름이 겹치면 검증 1단계가 모호해진다."""
    assert not (STRUCTURAL_KEYS & set(vocab.slots))


def test_enum_values_helper(vocab):
    assert "challenge_response" in vocab.enum_values("auth_mechanism_class")
    assert vocab.enum_values("nonexistent") == frozenset()


# ── BannedTerms ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def banned() -> BannedTerms:
    return BannedTerms.load(DATA / "banned.json")


def test_banned_patterns_compile(banned):
    assert len(banned.compiled()) == len(banned.patterns)


@pytest.mark.parametrize(
    "text",
    [
        "H社 라이선스",
        "하나텔 계약",
        "REQ-4412 요구사항",
        "CTR-204817",
        "12억원",
        "3천만원",
        "1,200,000원",
        "USD 50,000",
        "EAP-AKA 방식",
    ],
)
def test_banned_hits(banned, text):
    assert banned.hits(text), f"금칙어를 놓쳤다: {text}"


@pytest.mark.parametrize(
    "text",
    ["3천 TPS 부하 테스트", "p99 840ms", "sampling_strategy=0.5", "세션 8시간", "토큰 24시간"],
)
def test_banned_no_false_positive(banned, text):
    assert not banned.hits(text), f"오탐: {text}"


def test_banned_is_case_insensitive(banned):
    assert banned.hits("hanatel")
    assert banned.hits("HANATEL")
    assert banned.hits("customer h")


# ── PayloadEnvelope ──────────────────────────────────────────────────


def _envelope(**kw) -> PayloadEnvelope:
    base = dict(
        envelope_id="env_" + "A" * 22,
        tier=Tier.SECRET,
        task_schema_id="constraint_conflict_check",
        payload={"task": "constraint_conflict_check"},
        representation=Representation.STRUCTURED,
        payload_sha256="0" * 64,
        size_bytes=42,
    )
    return PayloadEnvelope(**{**base, **kw})


def test_envelope_id_format_enforced():
    with pytest.raises(ValidationError, match="envelope_id"):
        _envelope(envelope_id="bad-id")


def test_envelope_tier_is_single_value():
    """등급 혼합 페이로드는 타입 수준에서 생성되지 않는다 (BR-G-08)."""
    with pytest.raises(ValidationError):
        _envelope(tier=[Tier.SECRET, Tier.INTERNAL])  # type: ignore[arg-type]


def test_envelope_roundtrip_is_identity():
    """PB-2 왕복 속성의 예제 버전."""
    e = _envelope(validation=ValidationResult(checks=()))
    assert PayloadEnvelope.model_validate_json(e.model_dump_json()) == e


def test_envelope_is_frozen():
    e = _envelope()
    with pytest.raises(ValidationError):
        e.tier = Tier.OPEN  # type: ignore[misc]


# ── AgentCall ────────────────────────────────────────────────────────


def test_agent_call_tier_is_single_value():
    with pytest.raises(ValidationError):
        AgentCall(
            call_id="c1",
            entity_id="person:kim",
            tier=[Tier.SECRET],  # type: ignore[arg-type]
            task_schema_id="t",
        )


# ── Citation: 경로 필드 부재 (FR-43) ─────────────────────────────────


def test_citation_has_no_internal_path():
    fields = set(Citation.model_fields)
    for forbidden in ("internal_path", "path", "file", "abspath"):
        assert forbidden not in fields, f"Citation 에 {forbidden} 가 있으면 권한 우회가 된다"
    assert {"ref", "display_title", "section", "tier", "as_of", "formality"} <= fields


def test_citation_serialized_has_no_path():
    c = Citation(ref="REQ_A", display_title="고객사 H 요구사항명세서", tier=Tier.SECRET)
    dumped = c.model_dump_json()
    assert "internal_path" not in dumped
    assert "corpus/" not in dumped


# ── Chunk ────────────────────────────────────────────────────────────


def test_chunk_separates_display_from_path():
    c = Chunk(
        chunk_id="ck1",
        entity_id="person:kim",
        text="원문",
        display_title="고객사 H 요구사항명세서",
        internal_path="corpus/customer-H/req-spec-2026H.md",
        as_of=date(2026, 7, 15),
    )
    assert c.display_title != c.internal_path
    assert c.tier is None  # Store 는 등급을 채우지 않는다 — Gatekeeper 의 일


# ── Disclose: expertise 는 끌 수 없다 ───────────────────────────────


def test_expertise_cannot_be_disabled():
    """담당 영역을 끄면 지목이 불가능해진다. 타입으로 막았다."""
    with pytest.raises(ValidationError):
        Disclose(expertise=False)  # type: ignore[arg-type]


def test_disclose_defaults_are_off():
    """opt-in 이 기본이다 — 감시 도구가 되지 않게."""
    d = Disclose()
    assert d.expertise is True
    assert d.activity_status is False
    assert d.question_count_today is False
    assert d.current_focus is False


# ── ValidationResult ────────────────────────────────────────────────


def test_validation_summary_format():
    from mesh.schemas import CheckResult

    vr = ValidationResult(
        checks=(
            CheckResult(stage="schema", passed=True),
            CheckResult(stage="vocab", passed=True),
            CheckResult(stage="ngram", passed=False, detail="hit"),
        )
    )
    assert vr.summary == "2/3"
    assert vr.passed is False
    assert vr.first_failed_stage == "ngram"


def test_validation_all_passed():
    from mesh.schemas import CheckResult

    vr = ValidationResult(
        checks=tuple(CheckResult(stage=s, passed=True) for s in ("schema", "vocab"))
    )
    assert vr.passed is True
    assert vr.first_failed_stage is None


# ── TaskSchema 헬퍼 ─────────────────────────────────────────────────


def test_task_schema_slot_lookup(vocab):
    ts: TaskSchema = vocab.task_schemas["technique_lookup"]
    assert ts.slot("sampling_strategy_class") is not None
    assert ts.slot("ghost") is None


# ══════════════════════════════════════════════════════════════════════
# PseudonymTargets — banned.json 과 정반대 성격
#
# v1.0.0 에서 두 목록을 섞어 사내 문서 5건이 전부 SECRET 으로 오분류됐다.
# 정확도가 55% 로 떨어지고 시나리오 2의 가명화 경로가 실행되지 않았다.
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def pseudonyms():
    from mesh.schemas import PseudonymTargets

    return PseudonymTargets.load(DATA / "pseudonyms.json")


def test_pseudonyms_load(pseudonyms):
    assert set(pseudonyms.targets) == {"PROJ", "SYS", "PERSON", "PATH"}
    assert "atlas-ml" in pseudonyms.targets["PROJ"]
    assert "Nova 게이트웨이" in pseudonyms.targets["SYS"]


def test_pseudonym_and_banned_lists_are_disjoint(pseudonyms, banned):
    """겹치면 SECRET 상향을 유발해 가명화 경로가 실행되지 않는다."""
    banned_low = {lit.lower() for lit in banned.literals}
    overlap = [lit for _, lit in pseudonyms.all_literals() if lit.lower() in banned_low]
    assert not overlap, f"두 목록이 겹친다: {overlap}"


def test_internal_project_names_are_not_banned(banned):
    """사내 프로젝트·시스템명은 치환 대상이지 차단 대상이 아니다."""
    for name in ("atlas-ml", "atlas_ml", "Nova 게이트웨이", "sdk-core"):
        assert not banned.hits(name), f"{name} 이 차단 목록에 있으면 사내 문서가 SECRET 이 된다"


def test_customer_identifiers_are_banned(banned):
    """고객사명·계약번호·금액은 그 자체로 기밀이다."""
    for name in ("H社", "하나텔", "REQ-4412", "CTR-204817", "12억원"):
        assert banned.hits(name), f"{name} 을 차단하지 않으면 유출된다"


def test_all_literals_sorted_longest_first(pseudonyms):
    """짧은 리터럴을 먼저 치환하면 긴 것이 망가진다."""
    lengths = [len(lit) for _, lit in pseudonyms.all_literals()]
    assert lengths == sorted(lengths, reverse=True)


def test_technical_terms_are_never_substituted(pseudonyms):
    """치환하면 답변 품질이 무너진다 (BR-P-01)."""
    for term in ("RandomOverSampler", "balanced_subsample", "SSO", "JWT", "p99", "TPS"):
        assert pseudonyms.is_technical(term), f"{term} 은 치환하면 안 된다"


def test_technical_terms_do_not_include_identifiers(pseudonyms):
    """반대로 식별자가 기술 용어에 들어가면 치환되지 않아 유출된다."""
    for ident in ("atlas-ml", "Nova 게이트웨이", "김철수", "하나텔"):
        assert not pseudonyms.is_technical(ident)


def test_data_bundle_rejects_overlapping_lists(mock_env):
    """로드 시점에 겹침을 잡는다. v1.0.0 결함의 재발 방지."""
    from mesh.config import Config, DataBundle
    from mesh.exceptions import ConfigError

    bad = json.loads((mock_env / "pseudonyms.json").read_text(encoding="utf-8"))
    bad["targets"]["PROJ"]["literals"].append("H社")  # 차단 목록과 겹치게
    (mock_env / "pseudonyms.json").write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ConfigError, match="겹친다"):
        DataBundle(Config.load(strict=False), load_agent_configs=False)

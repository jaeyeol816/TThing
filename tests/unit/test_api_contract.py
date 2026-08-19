"""API 계약 — Day 1 동결. C(U4)가 이 스키마로 화면을 선행 개발한다.

두 가지를 검사한다:

  1. **계약 불변식** — 타입으로 못 박은 약속이 유지되는지
     (`agents_notified: Literal[False]`, `divergent` 아닌 `conflict` 부재,
      `internal_path` 부재)

  2. **픽스처 정합성** — `data/fixtures/api/*.json` 이 실제 모델로 역파싱되는지
     손으로 쓴 JSON 이 아니라 모델로 생성했으므로 형태가 어긋날 수 없다.
     이 테스트가 그걸 보증한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from mesh.api_models import (
    MAX_QUESTION_CHARS,
    AgentCardView,
    AskRequest,
    AskResult,
    AuditSearchResult,
    HealthStatus,
    InboxItem,
    MergedAnswer,
    PreparedCall,
    PrepareResult,
    ResolveRequest,
    SendRequest,
)
from mesh.schemas import Disposition, Tier

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "data" / "fixtures" / "api"


# ══════════════════════════════════════════════════════════════════════
# AskRequest — 입력 검증 (NFR-S-05)
# ══════════════════════════════════════════════════════════════════════


def test_valid_ask_request():
    r = AskRequest(question="충돌하나요?", asker="person:choi", targets=["person:kim"])
    assert r.targets == ["person:kim"]


def test_question_length_limit():
    with pytest.raises(ValidationError):
        AskRequest(question="x" * (MAX_QUESTION_CHARS + 1), asker="person:a", targets=["person:b"])


@pytest.mark.parametrize("q", ["", "   ", "\n\t"])
def test_blank_question_rejected(q):
    with pytest.raises(ValidationError):
        AskRequest(question=q, asker="person:a", targets=["person:b"])


def test_max_two_targets():
    """상한 2개. 그 이상은 답변이 길어지고 비용만 늘어난다 (BR-O-02)."""
    with pytest.raises(ValidationError):
        AskRequest(question="q", asker="person:a", targets=["person:b", "person:c", "person:d"])


def test_zero_targets_rejected():
    with pytest.raises(ValidationError):
        AskRequest(question="q", asker="person:a", targets=[])


def test_duplicate_targets_rejected():
    with pytest.raises(ValidationError, match="중복"):
        AskRequest(question="q", asker="person:a", targets=["person:b", "person:b"])


@pytest.mark.parametrize(
    "bad", ["kim", "person:", "person:KIM", "person:김철수", "PERSON:kim", "person:kim!"]
)
def test_malformed_entity_id_rejected(bad):
    with pytest.raises(ValidationError):
        AskRequest(question="q", asker=bad, targets=["person:b"])


def test_malformed_target_rejected():
    with pytest.raises(ValidationError, match="entity_id"):
        AskRequest(question="q", asker="person:a", targets=["not-an-id"])


# ══════════════════════════════════════════════════════════════════════
# PrepareResult — 타입으로 못 박은 약속
# ══════════════════════════════════════════════════════════════════════


def _ready_call(**kw) -> PreparedCall:
    from mesh.schemas import CheckResult, PreviewCard, Representation

    base = dict(
        envelope_id="env_" + "A" * 22,
        target_entity_id="person:kim",
        agent_label="김철수 책임의 Agent",
        tier=Tier.SECRET,
        disposition="ready",
        preview=PreviewCard(
            envelope_id="env_" + "A" * 22,
            tier=Tier.SECRET,
            representation=Representation.STRUCTURED,
            payload_pretty="{}",
            size_bytes=2,
            validation_summary="6/6",
            checks=(CheckResult(stage="schema", passed=True),),
            excluded_categories=("고객사명",),
            verbatim_sentence_count=0,
        ),
    )
    return PreparedCall(**{**base, **kw})


def test_agents_notified_cannot_be_true():
    """prepare 단계에서 담당자에게 알림이 가면 P1 이 무너진다 (BR-O-03).
    타입이 False 만 허용하므로 코드가 True 를 넣을 수 없다."""
    with pytest.raises(ValidationError):
        PrepareResult(request_id="r", calls=(), agents_notified=True)  # type: ignore[arg-type]


def test_agents_notified_annotation_is_literal_false():
    assert PrepareResult.model_fields["agents_notified"].annotation == Literal[False]


def test_prepare_default_does_not_notify():
    assert PrepareResult(request_id="r", calls=()).agents_notified is False


def test_ready_call_requires_envelope_and_preview():
    with pytest.raises(ValidationError, match="envelope_id/preview"):
        PreparedCall(
            target_entity_id="person:kim",
            agent_label="L",
            tier=Tier.SECRET,
            disposition="ready",
        )


def test_blocked_call_requires_fallback():
    """차단만 하고 답을 안 주면 안 된다 — 한 왕복에 끝나야 한다 (시나리오 3)."""
    with pytest.raises(ValidationError, match="fallback"):
        PreparedCall(
            target_entity_id="person:kim",
            agent_label="L",
            tier=Tier.SECRET,
            disposition="blocked",
        )


def test_ready_envelope_ids_helper():
    r = PrepareResult(request_id="r", calls=(_ready_call(),))
    assert r.ready_envelope_ids == ("env_" + "A" * 22,)


# ══════════════════════════════════════════════════════════════════════
# SendRequest — 승인 없는 전송 불가
# ══════════════════════════════════════════════════════════════════════


def test_send_requires_approved_by():
    with pytest.raises(ValidationError):
        SendRequest(request_id="r", envelope_ids=["env_" + "A" * 22])  # type: ignore[call-arg]


def test_send_rejects_blank_approver():
    with pytest.raises(ValidationError):
        SendRequest(request_id="r", envelope_ids=["env_" + "A" * 22], approved_by="")


def test_send_rejects_malformed_envelope_id():
    with pytest.raises(ValidationError, match="envelope_id"):
        SendRequest(request_id="r", envelope_ids=["bad-id"], approved_by="person:a")


def test_send_rejects_duplicate_envelope_ids():
    e = "env_" + "A" * 22
    with pytest.raises(ValidationError, match="중복"):
        SendRequest(request_id="r", envelope_ids=[e, e], approved_by="person:a")


def test_send_max_two_envelopes():
    with pytest.raises(ValidationError):
        SendRequest(
            request_id="r",
            envelope_ids=["env_" + c * 22 for c in "ABC"],
            approved_by="person:a",
        )


# ══════════════════════════════════════════════════════════════════════
# MergedAnswer — divergent, not conflict (Round 2 Q11)
# ══════════════════════════════════════════════════════════════════════


def test_field_is_divergent_not_conflict():
    """`conflict: true` 는 단정이고 `divergent: true` 는 관찰이다.
    둘 다 맞을 수 있으므로 판단을 사람에게 남긴다."""
    fields = set(MergedAnswer.model_fields)
    assert "divergent" in fields
    assert "conflict" not in fields


def test_divergent_defaults_false():
    m = MergedAnswer(answers=(), disposition=Disposition.AUTO)
    assert m.divergent is False
    assert m.divergence_note is None


# ══════════════════════════════════════════════════════════════════════
# ResolveRequest — 3버튼 (BR-I-01)
# ══════════════════════════════════════════════════════════════════════


def test_approve_needs_nothing_extra():
    assert ResolveRequest(action="approve").edited_text is None


@pytest.mark.parametrize("text", [None, "", "   "])
def test_approve_with_edit_requires_text(text):
    with pytest.raises(ValidationError, match="edited_text"):
        ResolveRequest(action="approve_with_edit", edited_text=text)


def test_approve_with_edit_ok():
    r = ResolveRequest(
        action="approve_with_edit", edited_text="13:47에 고쳤으니 그 부분 먼저 보세요"
    )
    assert r.edited_text


def test_not_me_requires_redirect_target():
    with pytest.raises(ValidationError, match="redirect_to"):
        ResolveRequest(action="not_me")


def test_not_me_validates_redirect_format():
    with pytest.raises(ValidationError, match="redirect_to"):
        ResolveRequest(action="not_me", redirect_to="박선임")


def test_not_me_ok():
    assert ResolveRequest(action="not_me", redirect_to="person:park").redirect_to == "person:park"


# ══════════════════════════════════════════════════════════════════════
# AuditSearchResult — 1막 결정적 장면 ② (FR-42)
# ══════════════════════════════════════════════════════════════════════


def test_zero_hit_requires_a_query():
    """검색 전에는 0건 배너를 띄우지 않는다."""
    assert AuditSearchResult(rows=()).zero_hit is False


def test_zero_hit_when_query_yields_nothing():
    r = AuditSearchResult(query="REQ-4412", rows=(), total_records=24)
    assert r.zero_hit is True


def test_no_zero_hit_when_rows_exist():
    from mesh.api_models import AuditRowView

    row = json.loads((FIXTURES / "GET_api_audit.json").read_text(encoding="utf-8"))["rows"][0]
    r = AuditSearchResult(query="constraint", rows=(AuditRowView(**row),))
    assert r.zero_hit is False


# ══════════════════════════════════════════════════════════════════════
# HealthStatus — 정직성 표시 (Round 2 Q15)
# ══════════════════════════════════════════════════════════════════════


def test_health_exposes_trust_boundary_simulated():
    """숨기면 심사자를 속이는 것이다. UI 헤더가 상시 표시한다."""
    assert "trust_boundary_simulated" in HealthStatus.model_fields


def test_auto_answer_rate():
    h = HealthStatus(
        exaone_mode="mock",
        agent_transport="mock",
        trusted_zone_llm_base_url="x",
        trust_boundary_simulated=True,
        agent_model_id="m",
        draft_model_id="d",
        vocab_version="1",
        vocab_sha256="s",
        disposition_counts={"auto": 14, "unverified": 5, "escalate": 4, "blocked": 1},
    )
    assert round(h.auto_answer_rate, 3) == 0.583  # 목표 >= 50%


def test_auto_answer_rate_is_none_without_data():
    h = HealthStatus(
        exaone_mode="mock",
        agent_transport="mock",
        trusted_zone_llm_base_url="x",
        trust_boundary_simulated=False,
        agent_model_id="m",
        draft_model_id="d",
        vocab_version="1",
        vocab_sha256="s",
    )
    assert h.auto_answer_rate is None


# ══════════════════════════════════════════════════════════════════════
# 픽스처 정합성 — 실제 모델로 역파싱된다
# ══════════════════════════════════════════════════════════════════════

FIXTURE_MODELS = {
    "GET_api_health": HealthStatus,
    "POST_api_ask_prepare_ready": PrepareResult,
    "POST_api_ask_prepare_blocked": PrepareResult,
    "POST_api_ask_prepare_decomposed": PrepareResult,
    "POST_api_ask_send_auto": AskResult,
    "POST_api_ask_send_divergent": AskResult,
    "POST_api_ask_send_escalate": AskResult,
    "GET_api_audit": AuditSearchResult,
    "GET_api_audit_zero": AuditSearchResult,
}


def test_all_expected_fixtures_exist():
    expected = set(FIXTURE_MODELS) | {"GET_api_agents", "GET_api_inbox"}
    actual = {p.stem for p in FIXTURES.glob("*.json")}
    assert expected <= actual, f"누락된 픽스처: {sorted(expected - actual)}"


@pytest.mark.parametrize("name,model", sorted(FIXTURE_MODELS.items()))
def test_fixture_roundtrips_through_model(name, model):
    """손으로 쓴 JSON 이 아니라 모델로 생성했으므로 형태가 어긋날 수 없다.
    이 테스트가 그걸 보증한다 — C 가 Day 4 에 UI 를 다시 만들지 않게."""
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    parsed = model.model_validate(raw)
    assert json.loads(parsed.model_dump_json()) == raw


def test_agents_fixture_roundtrips():
    raw = json.loads((FIXTURES / "GET_api_agents.json").read_text(encoding="utf-8"))
    cards = [AgentCardView.model_validate(a) for a in raw["agents"]]
    assert len(cards) == 3
    assert {c.entity_id for c in cards} == {"person:kim", "person:park", "person:choi"}


def test_inbox_fixture_roundtrips():
    raw = json.loads((FIXTURES / "GET_api_inbox.json").read_text(encoding="utf-8"))
    items = [InboxItem.model_validate(i) for i in raw["items"]]
    assert len(items) == 1
    assert items[0].draft.already_answered  # "Agent가 이미 답변함" 표시용


# ══════════════════════════════════════════════════════════════════════
# 유출 방어 — 응답에 원문·경로가 없다
# ══════════════════════════════════════════════════════════════════════

# "유출"의 의미가 위치에 따라 다르다. 여기를 혼동하면 테스트가 거짓이 된다.
#
#   페이로드 (경계를 넘는 것)          -> 원문·식별자가 **하나도** 없어야 한다
#   재수화된 답변 (신뢰 구역에 남는 것) -> 실제 이름이 **있어야** 한다 (FR-13, 재수화의 목적)
#   감사 검색어                        -> 사용자가 원문 문구를 입력한다. 있는 게 정상
#
# 어디에도 있어서는 안 되는 것: 경로 · 매핑 테이블 · EXAONE thinking

#: 응답 어디에도 등장해서는 안 되는 것.
NEVER_ANYWHERE = ("internal_path", "corpus/", "reasoning", '"mapping"', '"table"')

#: 경계를 넘는 페이로드에 등장해서는 안 되는 것 (원문·식별자).
NEVER_IN_PAYLOAD = ("REQ-4412", "EAP-AKA", "H社", "하나텔", "CTR-204817", "12억", "SDK v3.2")


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_no_fixture_contains_paths_or_mapping(path):
    """경로·매핑·thinking 은 응답 어디에도 없다.

    `internal_path` 와 `corpus/` — `Citation` 에 경로 필드가 없으므로
    표시할 방법이 구조적으로 없다 (FR-43).
    """
    text = path.read_text(encoding="utf-8")
    hits = [p for p in NEVER_ANYWHERE if p in text]
    assert not hits, f"{path.name} 에 있어서는 안 되는 것: {hits}"


def _payloads_in(raw: object) -> list[dict]:
    """픽스처에서 경계를 넘는 페이로드만 추출한다.

    `preview.payload_pretty` (미리보기에 표시되는 전문) 와
    `rows[].payload` (감사 로그에 기록된 전문) 가 대상이다.
    """
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "payload_pretty" in node:
                found.append(json.loads(node["payload_pretty"]))
            if "payload" in node and isinstance(node["payload"], dict):
                found.append(node["payload"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(raw)
    return found


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_payloads_contain_no_originals(path):
    """경계를 넘는 페이로드에는 원문·식별자가 하나도 없어야 한다 (FR-03).

    ⚠️ 재수화된 답변은 검사하지 않는다 — 거기엔 실제 이름이 **있어야** 한다.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    for payload in _payloads_in(raw):
        text = json.dumps(payload, ensure_ascii=False)
        hits = [p for p in NEVER_IN_PAYLOAD if p in text]
        assert not hits, f"{path.name} 의 페이로드에 원문: {hits}"


def test_payload_extractor_finds_something():
    """추출기 자체를 검사한다. 아무것도 못 찾는 추출기는 무의미하다."""
    raw = json.loads((FIXTURES / "POST_api_ask_prepare_ready.json").read_text(encoding="utf-8"))
    payloads = _payloads_in(raw)
    assert payloads
    assert payloads[0]["task"] == "constraint_conflict_check"


def test_payload_extractor_catches_planted_leak():
    planted = {"preview": {"payload_pretty": json.dumps({"reason": "H社 REQ-4412 관련"})}}
    text = json.dumps(_payloads_in(planted)[0], ensure_ascii=False)
    assert [p for p in NEVER_IN_PAYLOAD if p in text]


def test_rehydrated_answer_does_contain_real_names():
    """재수화의 목적이 실제 이름을 되돌리는 것이다 (FR-13).

    최종 답변에 `REQ_A` 같은 기호가 남아 있으면 치환이 실패한 것이다.
    """
    raw = json.loads((FIXTURES / "POST_api_ask_send_auto.json").read_text(encoding="utf-8"))
    result = AskResult.model_validate(raw)
    text = result.merged.answers[0].text

    assert "REQ-4412" in text, "재수화가 안 됐다 — 실제 이름이 있어야 한다"
    assert "SDK v3.2" in text
    for symbol in ("REQ_A", "COMP_B"):
        assert symbol not in text, f"치환되지 않은 기호가 남았다: {symbol}"


def test_audit_search_query_may_contain_originals():
    """감사 검색어는 사용자가 원문 문구를 입력한 것이다. 있는 게 정상이다."""
    raw = json.loads((FIXTURES / "GET_api_audit_zero.json").read_text(encoding="utf-8"))
    result = AuditSearchResult.model_validate(raw)
    assert result.query == "REQ-4412"
    assert result.zero_hit is True  # 검색어는 있고 결과는 0건 — 그게 증거다


def test_audit_zero_fixture_is_the_decisive_scene():
    """1막 결정적 장면 ② — 원문 문구 검색 0건."""
    raw = json.loads((FIXTURES / "GET_api_audit_zero.json").read_text(encoding="utf-8"))
    result = AuditSearchResult.model_validate(raw)
    assert result.zero_hit is True
    assert result.total_records > 0  # 로그가 비어서 0건인 게 아니다


def test_blocked_fixture_has_fallback_and_no_external_agent():
    """시나리오 3 후속 — 차단됐지만 답은 나오고, 경계를 넘지 않았다."""
    raw = json.loads((FIXTURES / "POST_api_ask_prepare_blocked.json").read_text(encoding="utf-8"))
    result = PrepareResult.model_validate(raw)
    call = result.calls[0]
    assert call.disposition == "blocked"
    assert call.fallback is not None
    assert call.fallback.used_external_agent is False  # -> [사내망 밖으로 나간 것 없음]
    assert call.envelope_id is None  # 전송할 envelope 이 없다


def test_divergent_fixture_preserves_request_order():
    """신뢰도로 정렬하지 않는다. 위쪽 답을 정답으로 읽게 만들지 않는다 (BR-O-07)."""
    raw = json.loads((FIXTURES / "POST_api_ask_send_divergent.json").read_text(encoding="utf-8"))
    result = AskResult.model_validate(raw)
    answers = result.merged.answers
    assert result.merged.divergent is True
    assert "둘 다 사실일 수 있습니다" in (result.merged.divergence_note or "")
    assert [a.entity_id for a in answers] == ["person:kim", "person:choi"]
    # 신뢰도 순이면 kim(0.71) > choi(0.624) 인데, 우연히 같으므로 값으로도 확인
    assert answers[0].confidence > answers[1].confidence
    # STALE 보정이 적용된 값
    assert round(answers[1].confidence, 3) == 0.624


def test_auto_fixture_has_citations():
    """인용 0개면 자동 응답이 되지 않는다 (BR-O-04)."""
    raw = json.loads((FIXTURES / "POST_api_ask_send_auto.json").read_text(encoding="utf-8"))
    result = AskResult.model_validate(raw)
    assert result.merged.disposition is Disposition.AUTO
    for a in result.merged.answers:
        assert a.citations, "AUTO 인데 인용이 없다"


def test_preview_reports_zero_verbatim_sentences():
    """ "원문 0개"가 주장이 아니라 측정값이다."""
    raw = json.loads((FIXTURES / "POST_api_ask_prepare_ready.json").read_text(encoding="utf-8"))
    result = PrepareResult.model_validate(raw)
    preview = result.calls[0].preview
    assert preview is not None
    assert preview.verbatim_sentence_count == 0
    assert preview.validation_summary == "6/6"
    assert "고객사명" in preview.excluded_categories

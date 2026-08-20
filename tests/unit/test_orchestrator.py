"""Orchestrator — 순수 함수(branch/merge)와 조율(prepare/send).

가장 중요한 넷:
  - 인용 0개면 신뢰도와 무관하게 `ESCALATE` (BR-O-04)
  - `merge()` 가 답을 하나도 버리지 않는다 (BR-O-06)
  - `prepare` 가 사람에게 알리지 않는다 (BR-O-03)
  - 이 파일에 모델 호출 코드가 없다 (BR-O 전제)
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from mesh import orchestrator as orch_mod
from mesh.api_models import AskRequest
from mesh.exceptions import BrokerError, ExaoneUnavailable, GatekeeperError
from mesh.orchestrator import (
    MINUTES_PER_INTERRUPT,
    branch,
    is_divergent,
    merge,
    session_facts,
    time_gap_label,
)
from mesh.schemas import Citation, Disposition, Freshness, RehydratedAnswer, Tier
from tests.fakes import (
    agent_reply,
)

AUTO, ESCALATE = 0.75, 0.45


def answer(
    *,
    entity_id: str = "person:kim",
    confidence: float = 0.83,
    citations: int = 1,
    text: str = "세션 바인딩이 필요합니다",
    title: str = "인증 설계 문서",
    as_of: date | None = None,
    tier: Tier = Tier.INTERNAL,
) -> RehydratedAnswer:
    return RehydratedAnswer(
        entity_id=entity_id,
        agent_label=f"{entity_id} 의 Agent",
        text=text,
        confidence=confidence,
        citations=tuple(
            Citation(
                ref=f"COMP_{chr(65 + i)}",
                display_title=f"{title} {i}" if i else title,
                tier=tier,
                as_of=as_of,
            )
            for i in range(citations)
        ),
        tier=tier,
        used_external_agent=True,
    )


# ══════════════════════════════════════════════════════════════════════
# branch — 인용이 신뢰도보다 먼저 (BR-O-04)
# ══════════════════════════════════════════════════════════════════════


def test_zero_citations_escalates_regardless_of_confidence():
    """🔴 근거 없는 생성은 사용자에게 도달하지 않는다.

    이 순서가 자동 응답의 인용 준수율을 **구조적으로** 100% 로 만든다.
    """
    for conf in (0.0, 0.5, 0.99, 1.0):
        got = branch([answer(confidence=conf, citations=0)], auto=AUTO, escalate=ESCALATE)
        assert got is Disposition.ESCALATE, conf


def test_empty_answers_escalates():
    assert branch([], auto=AUTO, escalate=ESCALATE) is Disposition.ESCALATE


@pytest.mark.parametrize(
    "conf,expected",
    [
        (1.00, Disposition.AUTO),
        (0.75, Disposition.AUTO),
        (0.7499, Disposition.UNVERIFIED),
        (0.45, Disposition.UNVERIFIED),
        (0.4499, Disposition.ESCALATE),
        (0.0, Disposition.ESCALATE),
    ],
)
def test_confidence_boundaries(conf, expected):
    assert branch([answer(confidence=conf)], auto=AUTO, escalate=ESCALATE) is expected


def test_two_answers_use_the_lower_confidence():
    """약한 답이 강한 답에 편승하지 않게 (BR-O-05)."""
    got = branch(
        [answer(confidence=0.95), answer(entity_id="person:choi", confidence=0.50)],
        auto=AUTO,
        escalate=ESCALATE,
    )
    assert got is Disposition.UNVERIFIED


def test_one_missing_citation_escalates_both():
    got = branch(
        [answer(confidence=0.95), answer(entity_id="person:choi", confidence=0.95, citations=0)],
        auto=AUTO,
        escalate=ESCALATE,
    )
    assert got is Disposition.ESCALATE


# ══════════════════════════════════════════════════════════════════════
# merge — 답을 버리지 않는다 (BR-O-06, BR-O-07)
# ══════════════════════════════════════════════════════════════════════


def test_merge_keeps_request_order():
    """신뢰도로 정렬하면 사용자가 위쪽 답을 정답으로 읽는다."""
    a = answer(entity_id="person:kim", confidence=0.50)
    b = answer(entity_id="person:choi", confidence=0.95)
    merged = merge([b, a], order=["person:kim", "person:choi"], auto=AUTO, escalate=ESCALATE)
    assert [x.entity_id for x in merged.answers] == ["person:kim", "person:choi"]


def test_merge_never_drops_an_answer():
    """🔴 하나를 조용히 고르면 나머지 하나는 영원히 묻힌다."""
    answers = [answer(entity_id="person:kim"), answer(entity_id="person:choi", confidence=0.1)]
    merged = merge(answers, order=["person:kim", "person:choi"], auto=AUTO, escalate=ESCALATE)
    assert len(merged.answers) == 2


def test_merge_preserves_count_for_every_shape():
    """입력 개수 = 출력 개수. 미래의 "정리"가 답을 지우지 못하게 한다.

    소스 grep 으로 검사하려 했으나 `ordered[0]` 같은 정상 인덱싱까지 걸려
    무의미해졌다. 결과를 직접 세는 것이 정확하다 (전수 검사는 PB-O3).
    """
    shapes = [
        [answer()],
        [answer(entity_id="person:kim"), answer(entity_id="person:choi")],
        [answer(entity_id="person:kim", confidence=0.01), answer(entity_id="person:choi")],
        [answer(entity_id="person:kim", citations=0), answer(entity_id="person:choi")],
    ]
    for answers in shapes:
        merged = merge(answers, order=["person:kim", "person:choi"], auto=AUTO, escalate=ESCALATE)
        assert len(merged.answers) == len(answers)
        assert {a.entity_id for a in merged.answers} == {a.entity_id for a in answers}


def test_merge_source_has_no_filtering_construct():
    """`filter`·조건부 제외·슬라이스가 없다."""
    src = inspect.getsource(merge)
    for banned in ("filter(", "[:1]", "if a.confidence", "continue"):
        assert banned not in src, banned


def test_divergent_requires_both_conditions():
    """텍스트가 다르고 **근거 문서도 다를** 때만 관찰로 인정한다."""
    same_source = [
        answer(entity_id="person:kim", text="A 다", title="같은 문서"),
        answer(entity_id="person:choi", text="B 다", title="같은 문서"),
    ]
    assert not is_divergent(same_source)

    diff = [
        answer(entity_id="person:kim", text="A 다", title="메모"),
        answer(entity_id="person:choi", text="B 다", title="리뷰"),
    ]
    assert is_divergent(diff)


def test_same_text_is_not_divergent():
    same = [
        answer(entity_id="person:kim", text="같은 결론", title="메모"),
        answer(entity_id="person:choi", text="같은 결론", title="리뷰"),
    ]
    assert not is_divergent(same)


def test_whitespace_difference_is_not_divergent():
    pair = [
        answer(entity_id="person:kim", text="세션  바인딩이   필요", title="메모"),
        answer(entity_id="person:choi", text="세션 바인딩이 필요", title="리뷰"),
    ]
    assert not is_divergent(pair)


def test_single_answer_is_never_divergent():
    assert not is_divergent([answer()])


def test_divergence_note_is_a_fixed_template():
    """🔴 상충을 LLM 으로 판정하지 않는다. `divergent` 는 관찰이다."""
    merged = merge(
        [
            answer(entity_id="person:kim", text="성능 때문", title="메모", as_of=date(2025, 11, 5)),
            answer(
                entity_id="person:choi", text="호환 때문", title="리뷰", as_of=date(2025, 12, 10)
            ),
        ],
        order=["person:kim", "person:choi"],
        auto=AUTO,
        escalate=ESCALATE,
    )
    assert merged.divergent
    assert "둘 다 사실일 수 있습니다" in merged.divergence_note
    assert "약 1개월" in merged.divergence_note


def test_no_llm_in_divergence_detection():
    src = inspect.getsource(is_divergent)
    for banned in ("await", "exaone", "complete_"):
        assert banned not in src


@pytest.mark.parametrize(
    "days,fragment",
    [(3, "3일"), (21, "약 3주"), (35, "약 1개월"), (400, "약 1년")],
)
def test_time_gap_labels(days, fragment):
    from datetime import timedelta

    base = date(2026, 1, 1)
    a = answer(entity_id="person:kim", as_of=base)
    b = answer(entity_id="person:choi", as_of=base + timedelta(days=days))
    assert fragment in time_gap_label(a, b)


def test_time_gap_without_dates():
    assert "알 수 없는" in time_gap_label(answer(), answer(entity_id="person:choi"))


# ══════════════════════════════════════════════════════════════════════
# 세션 사실 (BR-S-04)
# ══════════════════════════════════════════════════════════════════════


def test_expired_session_contributes_no_realtime_facts(full_cfg):
    """24시간 전 세션으로 "지금 학습 실행 중"이라고 말하면 틀린 실시간 정보다."""
    from mesh.config import DataBundle
    from mesh.store import KnowledgeStore

    store = KnowledgeStore(full_cfg, DataBundle(full_cfg))
    session = store.load_session("person:park")
    assert session_facts(session, Freshness.LIVE)
    assert session_facts(session, Freshness.EXPIRED) == ()


def test_live_session_reports_running_job(full_cfg):
    from mesh.config import DataBundle
    from mesh.store import KnowledgeStore

    store = KnowledgeStore(full_cfg, DataBundle(full_cfg))
    facts = session_facts(store.load_session("person:park"), Freshness.LIVE)
    joined = " ".join(facts)
    assert "실행 중" in joined
    assert "기밀 등급" in joined  # 데이터셋이 secret


# ══════════════════════════════════════════════════════════════════════
# 모델 호출이 없다
# ══════════════════════════════════════════════════════════════════════


def test_orchestrator_does_not_call_models():
    """🔴 Orchestrator 는 앱 코드일 뿐이다 (M-03).

    설계의 완료 기준은 `grep -c "exaone\\|bedrock\\|broker" == 0` 이었다.
    문자열 검사는 `reason="broker_unavailable"` 같은 **이유 코드**까지 잡으므로
    ast 로 바꿨다 — import 와 모델 메서드 호출을 정확히 본다.
    """
    tree = ast.parse(Path(inspect.getfile(orch_mod)).read_text(encoding="utf-8"))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for forbidden in ("boto3", "botocore", "httpx", "mesh.llm.broker", "mesh.llm.exaone"):
        assert forbidden not in imported, forbidden

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("complete_json", "complete_text", "invoke", "converse"):
        assert forbidden not in called, forbidden


# ══════════════════════════════════════════════════════════════════════
# prepare (BR-O-03)
# ══════════════════════════════════════════════════════════════════════

QUESTION = "고객사 요구와 우리 SDK 갱신 방식이 충돌하나요?"


def ask(targets=("person:kim",), question=QUESTION) -> AskRequest:
    return AskRequest(question=question, asker="person:lee", targets=list(targets))


async def test_prepare_does_not_call_the_agent(wiring):
    result = await wiring.orchestrator.prepare(ask())
    assert result.agents_notified is False
    assert wiring.fake_broker.calls == []  # Agent 호출 없음
    assert wiring.audit.count() == 0  # 감사 레코드 없음
    assert wiring.inbox.count_open("person:kim") == 0  # 인박스도 비어 있다


async def test_prepare_produces_a_validated_preview(wiring):
    result = await wiring.orchestrator.prepare(ask())
    call = result.calls[0]
    assert call.disposition == "ready"
    assert call.preview.validation_summary == "6/6"
    assert call.preview.verbatim_sentence_count == 0
    assert call.tier is Tier.SECRET  # 상향됐다


async def test_prepare_reports_tier_upgrade(wiring):
    result = await wiring.orchestrator.prepare(ask())
    assert result.upgraded_tier is Tier.SECRET
    assert "근거" in result.upgrade_reason


async def test_prepare_payload_has_no_original_text(wiring):
    result = await wiring.orchestrator.prepare(ask())
    pretty = result.calls[0].preview.payload_pretty
    for leak in ("H社", "REQ-4412", "CTR-204817", "EAP-AKA", "12억", "김철수"):
        assert leak not in pretty, leak


async def test_prepare_blocked_call_carries_a_fallback(wiring):
    """차단만 하고 답을 안 주면 안 된다 — 타입이 막는다 (`PreparedCall` validator)."""
    wiring.fake_exaone.fail["extract"] = ExaoneUnavailable("타임아웃")
    result = await wiring.orchestrator.prepare(ask())
    call = result.calls[0]
    assert call.disposition == "blocked"
    assert call.fallback is not None
    assert call.fallback.used_external_agent is False
    assert call.envelope_id is None
    assert "사내망 밖으로 나간 것 없음" in call.fallback.text
    assert wiring.audit.count() == 0  # 감사 레코드 없음
    assert wiring.audit.local_count() == 1  # local_queries 에만


async def test_prepare_two_targets_runs_in_parallel(wiring):
    result = await wiring.orchestrator.prepare(ask(("person:kim", "person:choi")))
    assert len(result.calls) == 2
    assert [c.target_entity_id for c in result.calls] == ["person:kim", "person:choi"]


async def test_one_target_failing_does_not_kill_the_other(wiring, full_data_root):
    """2명 중 1명 실패 시 나머지는 반환한다 (R-02)."""
    (full_data_root / "sessions" / "person_choi.json").unlink()
    result = await wiring.orchestrator.prepare(ask(("person:kim", "person:choi")))
    assert len(result.calls) == 2
    kim, choi = result.calls
    assert kim.disposition == "ready"
    assert choi.disposition == "blocked"
    assert choi.fallback is not None


async def test_daily_limit_blocks_a_target(wiring):
    for i in range(60):
        wiring.audit.record_local(
            actor="person:lee",
            target_entity_id="person:kim",
            tier=Tier.INTERNAL,
            reason_code="extraction_failed",
            question_sha256=f"h{i}",
        )
    result = await wiring.orchestrator.prepare(ask())
    assert result.calls[0].disposition == "blocked"
    assert "상한" in result.calls[0].blocked_reason


# ══════════════════════════════════════════════════════════════════════
# send
# ══════════════════════════════════════════════════════════════════════


async def test_send_returns_rehydrated_answer(wiring):
    prepared = await wiring.orchestrator.prepare(ask())
    env_id = prepared.calls[0].envelope_id
    result = await wiring.orchestrator.send(prepared.request_id, [env_id], "person:lee")

    assert result.merged.disposition is Disposition.AUTO
    assert len(result.merged.answers) == 1
    text = result.merged.answers[0].text
    assert "REQ_A" not in text  # 재수화됐다
    assert "요구사항명세서" in text
    assert result.interrupts_avoided == 1
    assert result.minutes_saved_estimate == MINUTES_PER_INTERRUPT


async def test_send_records_audit_before_calling(wiring):
    prepared = await wiring.orchestrator.prepare(ask())
    await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    kinds = [r.kind for r in wiring.audit.recent()]
    assert "request" in kinds and "result" in kinds


async def test_send_is_single_use(wiring):
    prepared = await wiring.orchestrator.prepare(ask())
    env_id = prepared.calls[0].envelope_id
    await wiring.orchestrator.send(prepared.request_id, [env_id], "person:lee")
    with pytest.raises(GatekeeperError, match="찾을 수 없다"):
        await wiring.orchestrator.send(prepared.request_id, [env_id], "person:lee")


async def test_unknown_envelope_is_rejected(wiring):
    prepared = await wiring.orchestrator.prepare(ask())
    with pytest.raises(GatekeeperError, match="준비 결과에 없다"):
        await wiring.orchestrator.send(
            prepared.request_id, ["env_AAAAAAAAAAAAAAAAAAAAAA"], "person:lee"
        )


async def test_mapping_is_discarded_after_send(wiring):
    prepared = await wiring.orchestrator.prepare(ask())
    await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    assert len(wiring.gatekeeper.cache) == 0


async def test_broker_failure_falls_back_in_zone(wiring):
    wiring.fake_broker.response = BrokerError("Bedrock 오류")
    prepared = await wiring.orchestrator.prepare(ask())
    result = await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    assert result.merged.answers[0].used_external_agent is False
    assert wiring.audit.count() == 1  # request 는 남았다 (BR-A-01)


async def test_low_confidence_escalates_to_inbox(wiring):
    wiring.fake_broker.response = agent_reply(confidence=0.20)
    prepared = await wiring.orchestrator.prepare(ask())
    result = await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    assert result.merged.disposition is Disposition.ESCALATE
    assert len(result.escalations) == 1
    items = wiring.inbox.list_for("person:kim")
    assert len(items) == 1
    assert items[0].thread_id == prepared.request_id
    assert items[0].draft.draft_answer


async def test_zero_citation_answer_escalates(wiring):
    wiring.fake_broker.response = agent_reply(confidence=0.99, citations=())
    prepared = await wiring.orchestrator.prepare(ask())
    result = await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    assert result.merged.disposition is Disposition.ESCALATE
    assert result.escalations


async def test_escalation_draft_does_not_cross_with_titles(wiring):
    """🔴 근거 제목·시점은 경계를 넘지 않는다. 초안 프롬프트를 확인한다."""
    wiring.fake_broker.response = agent_reply(confidence=0.20)
    prepared = await wiring.orchestrator.prepare(ask())
    await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    draft_prompts = [
        prompt for model, prompt in wiring.fake_broker.calls if model == wiring.cfg.draft_model_id
    ]
    assert draft_prompts
    for leak in ("요구사항명세서", "고객사 H", "2026-07-15", "고객사 H 인증 요구사항 검토"):
        assert leak not in draft_prompts[0], leak


async def test_escalation_situation_is_built_locally(wiring):
    """제목·시점은 응답이 돌아온 뒤 신뢰 구역 안에서 덧붙인다."""
    wiring.fake_broker.response = agent_reply(confidence=0.20)
    prepared = await wiring.orchestrator.prepare(ask())
    await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    item = wiring.inbox.list_for("person:kim")[0]
    joined = " ".join(item.draft.situation)
    assert "근거:" in joined
    assert "원문은 경계를 넘지 않았습니다" in joined  # secret 등급


async def test_stale_session_lowers_confidence(wiring):
    """실측 효과: 0.78 x 0.8 = 0.62 -> UNVERIFIED 배지."""
    wiring.fake_broker.response = agent_reply(confidence=0.78)
    prepared = await wiring.orchestrator.prepare(ask(("person:choi",)))
    result = await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    got = result.merged.answers[0]
    assert got.freshness is Freshness.STALE
    assert got.confidence == pytest.approx(0.624)
    assert result.merged.disposition is Disposition.UNVERIFIED


async def test_two_answers_are_both_returned(wiring):
    prepared = await wiring.orchestrator.prepare(ask(("person:kim", "person:choi")))
    ids = [c.envelope_id for c in prepared.calls if c.envelope_id]
    result = await wiring.orchestrator.send(prepared.request_id, ids, "person:lee")
    assert len(result.merged.answers) == 2
    assert [a.entity_id for a in result.merged.answers] == ["person:kim", "person:choi"]


async def test_expired_request_id_is_rejected(wiring):
    with pytest.raises(GatekeeperError, match="찾을 수 없다"):
        await wiring.orchestrator.send("req_nope", ["env_AAAAAAAAAAAAAAAAAAAAAA"], "person:lee")


async def test_outcome_is_recorded_for_health(wiring):
    prepared = await wiring.orchestrator.prepare(ask())
    await wiring.orchestrator.send(
        prepared.request_id, [prepared.calls[0].envelope_id], "person:lee"
    )
    assert wiring.audit.disposition_counts() == {"auto": 1}


# ══════════════════════════════════════════════════════════════════════
# 시나리오 2 — 기법 질문 (사내 등급, 가명화)
# ══════════════════════════════════════════════════════════════════════


async def test_internal_tier_question_is_pseudonymized(wiring):
    wiring.fake_exaone.slots = [{"sampling_strategy_class": "hybrid"}]
    prepared = await wiring.orchestrator.prepare(
        ask(("person:park",), "라벨 불균형을 어떤 기법으로 처리했나요?")
    )
    call = prepared.calls[0]
    assert call.tier is Tier.INTERNAL
    assert call.preview.representation.value == "pseudonymized"
    pretty = call.preview.payload_pretty
    assert "atlas_ml" not in pretty
    assert "박선영" not in pretty
    assert "RandomOverSampler" in pretty  # 기술 용어는 보존
    assert call.preview.validation_summary == "6/6"
    assert call.preview.verbatim_sentence_count == 0

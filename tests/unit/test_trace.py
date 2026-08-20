"""게이트키퍼 트레이스 (`mesh.trace`).

트레이스는 화면에 자료를 **더 많이** 보여주는 기능이다. 그래서 위험이
반대 방향이다 — 다른 모듈은 "안 나가게" 를 지키고, 여기는 "보여주되
보여주면 안 되는 것은 애초에 담지 않기" 를 지킨다.

이 파일이 고정하는 것은 그 세 규칙이다 (`trace.py` 머리말).

  ① 원문을 담지 않는다
  ② `CheckResult.offending` 을 담지 않는다 (건수만)
  ③ 매핑표는 **답변에 등장한 기호만** 공개한다
"""

from __future__ import annotations

import json

import pytest

from mesh.schemas import (
    CheckResult,
    Citation,
    PayloadEnvelope,
    Representation,
    Tier,
    TierDecision,
    ValidationResult,
)
from mesh.trace import (
    TraceEvidence,
    TraceRecorder,
    TraceStore,
    mapping_rows,
    redact_checks,
)


def env(payload: dict | None = None, rep: Representation = Representation.STRUCTURED):
    payload = payload if payload is not None else {"task": "x", "constraint_a": "token_refresh"}
    return PayloadEnvelope(
        envelope_id="env_AAAAAAAAAAAAAAAAAAAAAA",
        tier=Tier.SECRET,
        task_schema_id="constraint_conflict_check",
        payload=payload,
        representation=rep,
        payload_sha256="a" * 64,
        size_bytes=len(json.dumps(payload)),
    )


def recorder() -> TraceRecorder:
    return TraceRecorder(request_id="req_1", entity_id="person:kim", question="왜 그렇게 했나요?")


DECISION = TierDecision(tier=Tier.SECRET, rule_tier=Tier.SECRET, reasons=("금칙어 리터럴",))


# ══════════════════════════════════════════════════════════════════════
# ① 원문을 담지 않는다
# ══════════════════════════════════════════════════════════════════════


def test_evidence_has_no_place_for_raw_text_or_paths() -> None:
    """🔴 `TraceEvidence` 는 `Chunk` 의 **투영**이다.

    본문(`text`)과 내부 경로(`internal_path`)를 담을 자리가 있으면 언젠가
    채워진다. 자리를 두지 않는 것이 "안 쓰기" 보다 강하다.
    """
    fields = set(TraceEvidence.model_fields)
    assert "text" not in fields
    assert "internal_path" not in fields
    assert "chars" in fields, "분량은 숫자로만 남긴다"


def test_trace_module_does_not_import_chunk_or_mapping() -> None:
    """import 자체가 없어야 규칙이 코드로 강제된다 (test_import_boundary 와 짝)."""
    import ast
    import inspect
    from pathlib import Path

    from mesh import trace as trace_mod

    tree = ast.parse(Path(inspect.getfile(trace_mod)).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "Chunk" not in imported
    assert "Mapping" not in imported


def test_evidence_stages_show_titles_and_sizes_not_bodies() -> None:
    """제목·등급·분량은 보여주고 본문은 보여줄 방법이 없다."""
    evidence = (
        TraceEvidence(
            title="SDK 인증 설계 문서",
            tier=Tier.SECRET,
            source_kind="design_doc",
            as_of="2026-08-19",
            chars=4200,
            rule_tier=Tier.SECRET,
            reasons=("EXAONE 보조 판정 비활성",),
        ),
    )
    rec = recorder()
    rec.add_classify(question_decision=DECISION, evidence=evidence, effective=Tier.SECRET)
    rec.add_select(candidate_count=3, evidence=evidence, selected_by_model=True)
    blob = rec.build().model_dump_json()

    assert "SDK 인증 설계 문서" in blob
    assert "EXAONE 보조 판정 비활성" in blob
    assert "4,200자" in blob, "분량은 숫자로만 남는다"


# ══════════════════════════════════════════════════════════════════════
# ② offending 은 건수만
# ══════════════════════════════════════════════════════════════════════


def test_offending_values_never_reach_the_trace() -> None:
    """🔴 검증이 막은 내용을 "막았다" 는 화면에서 보여주면 자기모순이다."""
    leaked = "고객사 요구사항 번호는 REQ-4412 이다"
    result = ValidationResult(
        checks=(
            CheckResult(stage="ngram", passed=False, detail="원문 5-gram 3건", offending=(leaked,)),
            CheckResult(stage="size", passed=True, detail="754/2048 bytes"),
        )
    )
    rec = recorder()
    rec.add_validate(result=result, verbatim_count=3)
    blob = rec.build().model_dump_json()

    assert leaked not in blob
    assert "REQ-4412" not in blob
    assert "1건 비공개" in blob, "숨겼다는 사실 자체는 숨기지 않는다"


# ── 판정 사유의 값도 담지 않는다 ──────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "must_not_contain"),
    [
        ("경로 규칙 'person_kim/data/customer-H/**' 매치", "customer-H"),
        ("사내 경로 규칙 'person_park/data/**' 매치", "person_park"),
        ("금칙어 '하나텔'", "하나텔"),
        ("금칙어 패턴 /REQ-\\d{4}/", "REQ-"),
        ("헤더 표기 '기밀 — 고객사 H 전용'", "고객사 H"),
    ],
)
def test_rule_reasons_never_carry_the_matched_value(reason: str, must_not_contain: str) -> None:
    """🔴 `classifier` 의 사유 문자열은 **서버 로그용**이다.

    매치된 값이 그대로 들어 있다 — 내부 경로(FR-43 이 금지)와 금칙어 리터럴
    (애초에 막으려던 그 단어). 그것이 화면에 뜨면 트레이스가 유출 채널이 된다.
    """
    from mesh.trace import redact_reasons

    out = redact_reasons([reason])
    assert must_not_contain not in " ".join(out), out
    assert out and out[0], "가리되 '무슨 규칙이 걸렸는지' 는 남아야 한다"


def test_unknown_reason_with_a_value_is_hidden_whole() -> None:
    """새 규칙이 생겨도 검사 없이 화면에 뜨지 않는다 (fail closed)."""
    from mesh.trace import redact_reasons

    out = redact_reasons(["새규칙 '비밀값'"])
    assert "비밀값" not in out[0]


def test_value_free_reasons_pass_through() -> None:
    from mesh.trace import redact_reasons

    assert redact_reasons(["EXAONE 보조 판정 비활성"]) == ("EXAONE 보조 판정 비활성",)


def test_classify_stage_redacts_reasons_even_if_the_caller_forgot() -> None:
    """호출자가 원본을 그대로 넘겨도 마지막 관문이 막는다."""
    rec = recorder()
    rec.add_classify(
        question_decision=TierDecision(
            tier=Tier.SECRET, rule_tier=Tier.SECRET, reasons=("금칙어 '하나텔'",)
        ),
        evidence=(
            TraceEvidence(
                title="문서",
                tier=Tier.SECRET,
                reasons=("경로 규칙 'person_kim/data/customer-H/**' 매치",),
            ),
        ),
        effective=Tier.SECRET,
    )
    blob = rec.build().model_dump_json()
    assert "하나텔" not in blob
    assert "customer-H" not in blob
    assert "person_kim" not in blob


def test_redact_checks_counts_without_values() -> None:
    result = ValidationResult(
        checks=(CheckResult(stage="banned", passed=False, offending=("a", "b")),)
    )
    pairs = redact_checks(result)
    assert pairs[0][1] == 2
    assert redact_checks(None) == ()


# ══════════════════════════════════════════════════════════════════════
# ③ 매핑표는 답변에 등장한 기호만
# ══════════════════════════════════════════════════════════════════════


def test_mapping_rows_hide_values_that_never_appeared() -> None:
    rows = mapping_rows({"<PERSON_1>": "김철수", "<PERSON_2>": "박선영"},
                        visible_in="<PERSON_1> 이 결정했습니다")
    shown = {sym: val for sym, val, ok in rows if ok}
    hidden = [sym for sym, _v, ok in rows if not ok]
    assert shown == {"<PERSON_1>": "김철수"}
    assert hidden == ["<PERSON_2>"]


def test_transform_stage_never_publishes_mapping_values() -> None:
    """변환 단계에서는 **기호만** 보인다. 값은 재수화 단계에서 조건부로 열린다."""
    rec = recorder()
    rec.add_transform(env=env(), mapping_table={"<PERSON_1>": "김철수"})
    blob = rec.build().model_dump_json()
    assert "<PERSON_1>" in blob
    assert "김철수" not in blob


def test_rehydrate_stage_publishes_only_symbols_in_the_answer() -> None:
    rec = recorder()
    rec.add_rehydrate(
        masked_text="<PERSON_1> 이 결정했습니다",
        rehydrated_text="김철수 책임이 결정했습니다",
        mapping_table={"<PERSON_1>": "김철수 책임", "<PERSON_9>": "박선영 선임"},
        citations=(Citation(ref="DOC_A", display_title="설계 문서", tier=Tier.INTERNAL),),
        confidence=0.83,
    )
    blob = rec.build().model_dump_json()
    assert "김철수 책임" in blob, "답변에 등장한 기호는 이미 사용자가 읽은 이름이다"
    assert "박선영" not in blob, "등장하지 않은 기호의 값은 새 정보다"


def test_compare_panel_shows_both_sides() -> None:
    """암호화된(기호) 답변과 복원된 답변을 나란히 볼 수 있어야 한다."""
    rec = recorder()
    rec.add_rehydrate(
        masked_text="<PERSON_1> 이 결정",
        rehydrated_text="김철수 책임이 결정",
        mapping_table={"<PERSON_1>": "김철수 책임"},
    )
    stage = {s.stage_id: s for s in rec.build().stages}["rehydrate"]
    compare = [p for p in stage.panels if p.kind == "compare"]
    assert len(compare) == 1
    assert compare[0].before_text == "<PERSON_1> 이 결정"
    assert compare[0].after_text == "김철수 책임이 결정"


# ══════════════════════════════════════════════════════════════════════
# 표현별 설명 — "JSON 인가 자연어인가" 에 답해야 한다
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("rep", "must_say"),
    [
        (Representation.STRUCTURED, "JSON"),
        (Representation.PSEUDONYMIZED, "자연어"),
        (Representation.VERBATIM, "원문"),
    ],
)
def test_transform_caption_answers_what_shape_the_payload_is(rep, must_say: str) -> None:
    rec = recorder()
    rec.add_transform(env=env(rep=rep), mapping_table={})
    stage = {s.stage_id: s for s in rec.build().stages}["transform"]
    payload_panel = next(p for p in stage.panels if p.kind == "json")
    assert must_say in payload_panel.caption


def test_payload_is_shown_in_full() -> None:
    """전문이다. 자르지 않는다 (BR-U-01)."""
    payload = {"task": "x", "items": [f"v{i}" for i in range(50)]}
    rec = recorder()
    rec.add_transform(env=env(payload=payload), mapping_table={})
    stage = {s.stage_id: s for s in rec.build().stages}["transform"]
    panel = next(p for p in stage.panels if p.kind == "json")
    assert json.loads(panel.json_text) == payload


# ══════════════════════════════════════════════════════════════════════
# 단계 조립 · 차단 경로
# ══════════════════════════════════════════════════════════════════════


def test_stages_come_out_in_pipeline_order_regardless_of_call_order() -> None:
    rec = recorder()
    rec.add_validate(result=ValidationResult(checks=()), verbatim_count=0)
    rec.add_classify(question_decision=DECISION, evidence=(), effective=Tier.SECRET)
    assert [s.stage_id for s in rec.build().stages] == ["classify", "validate"]


def test_blocked_marks_the_trace_as_not_crossing() -> None:
    rec = recorder()
    rec.add_dispatch(
        env=env(), transport="mock", model_id="m", approved_by="person:lee", sent=True
    )
    assert rec.crossed_boundary is True
    rec.add_blocked(stage_id="dispatch", reason="검증 6/5 — 전송하지 않았다")
    assert rec.crossed_boundary is False
    assert rec.has_blocked is True


def test_dispatch_stage_marks_the_boundary() -> None:
    rec = recorder()
    rec.add_dispatch(
        env=env(), transport="broker", model_id="claude", approved_by="person:lee",
        endpoint="https://example.invalid/x",
    )
    stage = {s.stage_id: s for s in rec.build().stages}["dispatch"]
    assert stage.crosses_boundary is True
    assert stage.status == "warn", "경계를 넘은 것은 통과가 아니라 주의다"


def test_recorder_never_raises_on_bad_input() -> None:
    """🔴 기록 실패가 질의를 죽이면 안 된다 — 트레이스는 설명이지 기능이 아니다."""
    rec = recorder()
    rec.put("classify", status="이런상태는없다")  # type: ignore[arg-type]
    assert rec.build().stages == ()


# ══════════════════════════════════════════════════════════════════════
# 보관소
# ══════════════════════════════════════════════════════════════════════


def test_store_round_trip_and_miss() -> None:
    store = TraceStore()
    rec = recorder()
    rec.add_classify(question_decision=DECISION, evidence=(), effective=Tier.SECRET)
    trace_id = store.put(rec.build())
    assert store.get(trace_id) is not None
    assert store.get("tr_nope") is None


def test_store_expires_by_ttl() -> None:
    """감사 로그와 달리 트레이스는 **사라져야 한다** (매핑 일부를 품고 있다)."""
    store = TraceStore(ttl_seconds=0)
    trace_id = store.put(recorder().build())
    assert store.get(trace_id) is None
    assert len(store) == 0


def test_store_evicts_the_oldest_over_capacity() -> None:
    store = TraceStore(max_items=2)
    ids = [store.put(recorder().build()) for _ in range(3)]
    assert store.get(ids[0]) is None
    assert store.get(ids[-1]) is not None


def test_store_is_memory_only() -> None:
    """파일로 쓰지 않는다 — `Mapping` 이 직렬화 불가인 이유를 우회하지 않게."""
    import ast
    import inspect
    from pathlib import Path

    from mesh import trace as trace_mod

    src = Path(inspect.getfile(trace_mod)).read_text(encoding="utf-8")
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("write_text", "write_bytes", "open", "dump"):
        assert forbidden not in called, forbidden

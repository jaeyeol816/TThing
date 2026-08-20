"""게이트 G3 — 시나리오 종단 (설계 §7.2, U3 Step 7).

    make eval

**이 게이트를 통과하지 못하면 Day 4 로 넘어가지 않는다.**

세 시나리오를 HTTP 표면으로 끝까지 돌린다. 대역은 LLM 응답만 흉내 내고
조립·검증·감사·재수화는 실제 코드가 돈다 — 그러지 않으면 테스트가 통과하는데
실물이 새는 상태가 된다.

`make eval-classify` 가 판정 정확도(G2)를 보고, 이 파일이 **경로 전체**를 본다.
"""

from __future__ import annotations

import json

import pytest

from tests.fakes import agent_reply

# ══════════════════════════════════════════════════════════════════════
# "유출"의 의미는 위치에 따라 다르다 (Day 2 에 배운 것)
# ══════════════════════════════════════════════════════════════════════
#
#   | 위치                     | 고객사명·계약번호 | 지목한 사람 이름 |
#   |--------------------------|:-----------------:|:----------------:|
#   | 페이로드 (경계를 넘는 것) | ✕                 | ✕                |
#   | 감사 로그 (페이로드 사본) | ✕                 | ✕                |
#   | API 응답 (신뢰 구역)      | ✕                 | **○**            |
#
# `agent_label` 이 `"김철수 책임의 Agent"` 인 것이 정상이다 — 사용자가 직접
# 지목한 사람이고, 그 이름은 신뢰 구역 안에 머문다. 반면 고객사명은 사용자가
# 알려준 것이 아니라 문서에서 온 것이므로 어디에도 나와서는 안 된다.

#: 어디에도 없어야 하는 것 (문서에서만 온 정보)
NEVER_ANYWHERE = ("H社", "하나텔", "REQ-4412", "CTR-204817", "EAP-AKA", "12억")

#: 페이로드·감사에는 없어야 하지만 신뢰 구역 응답에는 있어도 되는 것
NEVER_IN_PAYLOAD = (*NEVER_ANYWHERE, "김철수", "박선영", "최민수")

Q1 = "고객사 요구사항과 우리 SDK 토큰 갱신 방식이 충돌하나요?"
Q2 = "라벨 불균형을 어떤 기법으로 처리했나요?"
Q3 = "왜 세션 바인딩을 넣지 않았나요? 그 결정 배경을 알고 싶습니다"
Q3_FOLLOWUP = "그때 p99 지연이 얼마였나요?"


def _prepare(client, question, targets):
    r = client.post(
        "/api/ask/prepare",
        json={"question": question, "asker": "person:lee", "targets": list(targets)},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _send(client, prepared, approved_by="person:lee"):
    ids = [c["envelope_id"] for c in prepared["calls"] if c["envelope_id"]]
    r = client.post(
        "/api/ask/send",
        json={
            "request_id": prepared["request_id"],
            "envelope_ids": ids,
            "approved_by": approved_by,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# ══════════════════════════════════════════════════════════════════════
# 시나리오 1 — 자동 응답 (1명, 기밀 상향)
# ══════════════════════════════════════════════════════════════════════


def test_scenario_1_end_to_end(client, wiring):
    """기밀 문서가 동원되지만 원문 0개로 유용한 답이 나오고, 김책임은 방해받지 않는다."""
    prepared = _prepare(client, Q1, ["person:kim"])
    call = prepared["calls"][0]

    # ① 상향: 질문은 사내인데 근거에 기밀이 있다
    assert call["tier"] == "secret"
    assert prepared["upgraded_tier"] == "secret"

    # ② 검증 6/6 + 측정된 원문 0개
    assert call["preview"]["validation_summary"] == "6/6"
    assert call["preview"]["verbatim_sentence_count"] == 0
    assert call["preview"]["excluded_categories"]

    # ③ prepare 는 사람을 깨우지 않는다
    assert prepared["agents_notified"] is False
    assert client.get("/api/inbox", params={"owner": "person:kim"}).json() == []
    assert wiring.audit.count() == 0

    result = _send(client, prepared)

    # ④ 자동 응답 + 재수화된 실제 이름 + 인용
    assert result["merged"]["disposition"] == "auto"
    answer = result["merged"]["answers"][0]
    assert "REQ_A" not in answer["text"]
    assert len(answer["citations"]) >= 1
    assert answer["used_external_agent"] is True
    assert result["interrupts_avoided"] == 1

    # ⑤ 문서에서 온 정보는 응답 어디에도 없다
    blob = json.dumps(prepared, ensure_ascii=False) + json.dumps(result, ensure_ascii=False)
    for leak in NEVER_ANYWHERE:
        assert leak not in blob, leak

    # ⑥ 경계를 넘은 것(페이로드 = 감사 로그)에는 인명도 없다
    for leak in NEVER_IN_PAYLOAD:
        assert leak not in call["preview"]["payload_pretty"], leak
        assert client.get("/api/audit", params={"q": leak}).json()["rows"] == [], leak

    # ⑦ 지목한 사람 이름은 신뢰 구역 응답에 남는다 — 재수화의 목적이다 (FR-13)
    assert "김철수" in answer["agent_label"]

    # ⑧ 김책임은 여전히 방해받지 않았다
    assert client.get("/api/inbox", params={"owner": "person:kim"}).json() == []


def test_scenario_1_payload_is_structured_with_conflict_preserved(client):
    """구조 페이로드에 **충돌이 보존**돼야 유용한 답이 나온다 (발견 14)."""
    prepared = _prepare(client, Q1, ["person:kim"])
    payload = json.loads(prepared["calls"][0]["preview"]["payload_pretty"])
    facts = payload["facts"]
    assert set(facts) >= {"REQ_A", "COMP_A"}
    assert facts["REQ_A"]["session_binding"] == "required"
    assert facts["COMP_A"]["session_binding"] == "none"
    assert "excerpts" not in payload  # 기밀 등급에는 텍스트 키가 없다


# ══════════════════════════════════════════════════════════════════════
# 시나리오 2 — 사내 등급 가명화 + 부분 에스컬레이션
# ══════════════════════════════════════════════════════════════════════


def test_scenario_2_pseudonymized_and_technical_terms_survive(client, wiring):
    wiring.fake_exaone.slots = [{"sampling_strategy_class": "hybrid"}]
    prepared = _prepare(client, Q2, ["person:park"])
    call = prepared["calls"][0]

    assert call["tier"] == "internal"
    assert call["preview"]["representation"] == "pseudonymized"
    assert call["preview"]["validation_summary"] == "6/6"
    assert call["preview"]["verbatim_sentence_count"] == 0

    pretty = call["preview"]["payload_pretty"]
    # 식별자는 치환된다
    for identifier in ("atlas_ml", "atlas-ml", "박선영"):
        assert identifier not in pretty, identifier
    # 기술 용어는 보존된다 — 치환하면 답변 품질이 무너진다
    for term in ("RandomOverSampler", "sampling_strategy", "balanced_subsample"):
        assert term in pretty, term


def test_scenario_2_low_confidence_escalates_with_a_usable_draft(client, wiring):
    """담당자에게 질문 원문만 던지면 알림이 하나 늘 뿐이다."""
    wiring.fake_exaone.slots = [{"sampling_strategy_class": "hybrid"}]
    wiring.fake_broker.response = agent_reply(confidence=0.30, citations=("COMP_A",))
    prepared = _prepare(client, Q2, ["person:park"])
    result = _send(client, prepared)

    assert result["merged"]["disposition"] == "escalate"
    assert result["escalations"]

    items = client.get("/api/inbox", params={"owner": "person:park"}).json()
    assert len(items) == 1
    draft = items[0]["draft"]
    assert draft["summary"]
    assert draft["situation"]  # 근거
    assert draft["draft_answer"]  # 그대로 승인 가능한 문장
    assert items[0]["thread_id"] == prepared["request_id"]


def test_scenario_2_approval_feeds_back_preserving_tier(client, wiring):
    """🔴 승인은 답변의 정확성을 검증한 것이고 등급을 낮춘 것이 아니다 (BR-I-02)."""
    wiring.fake_exaone.slots = [{"sampling_strategy_class": "hybrid"}]
    wiring.fake_broker.response = agent_reply(confidence=0.30, citations=("COMP_A",))
    prepared = _prepare(client, Q2, ["person:park"])
    _send(client, prepared)

    item_id = client.get("/api/inbox", params={"owner": "person:park"}).json()[0]["item_id"]
    r = client.post(
        f"/api/inbox/{item_id}/resolve",
        json={"action": "approve_with_edit", "edited_text": "하이브리드로 처리했습니다"},
    )
    assert r.status_code == 200

    verified = wiring.store.load_session("person:park").verified_qa
    assert len(verified) == 1
    assert verified[0].answer == "하이브리드로 처리했습니다"
    assert verified[0].tier.value == "internal"


def test_scenario_2_session_facts_reach_the_draft_but_not_the_boundary(client, wiring):
    """세션 사실은 초안에 들어가고 경계는 넘지 않는다."""
    wiring.fake_exaone.slots = [{"sampling_strategy_class": "hybrid"}]
    wiring.fake_broker.response = agent_reply(confidence=0.30, citations=("COMP_A",))
    prepared = _prepare(client, Q2, ["person:park"])
    _send(client, prepared)

    item = client.get("/api/inbox", params={"owner": "person:park"}).json()[0]
    joined = " ".join(item["draft"]["situation"])
    assert "실행 중" in joined  # 세션 사실이 초안에 있다

    draft_prompts = [p for m, p in wiring.fake_broker.calls if m == wiring.cfg.draft_model_id]
    assert draft_prompts
    assert "실행 중" not in draft_prompts[0]  # 경계는 넘지 않았다


# ══════════════════════════════════════════════════════════════════════
# 시나리오 3 — 2명 병기 + 폴백
# ══════════════════════════════════════════════════════════════════════


def test_scenario_3_two_answers_are_both_shown(client, wiring):
    """🔴 하나를 조용히 고르면 나머지 하나는 영원히 묻힌다 (BR-O-06)."""
    wiring.fake_exaone.slots = [{"session_binding": "none", "renewal_mode": "background_silent"}]
    prepared = _prepare(client, Q3, ["person:kim", "person:choi"])
    assert len(prepared["calls"]) == 2

    result = _send(client, prepared)
    answers = result["merged"]["answers"]
    assert len(answers) == 2
    # 요청 순서를 유지한다 — 신뢰도로 정렬하지 않는다
    assert [a["entity_id"] for a in answers] == ["person:kim", "person:choi"]


def test_scenario_3_stale_session_lowers_confidence(client, wiring):
    """최민수는 2시간 전 세션이다. 0.78 x 0.8 = 0.62 -> 미검증 배지."""
    wiring.fake_exaone.slots = [{"session_binding": "none", "renewal_mode": "background_silent"}]
    wiring.fake_broker.response = agent_reply(confidence=0.78, citations=("COMP_A",))
    prepared = _prepare(client, Q3, ["person:choi"])
    result = _send(client, prepared)

    answer = result["merged"]["answers"][0]
    assert answer["freshness"] == "stale"
    assert answer["confidence"] == pytest.approx(0.624)
    assert result["merged"]["disposition"] == "unverified"


def test_scenario_3_followup_is_blocked_with_a_fallback_and_no_audit(client, wiring):
    """🔴 결정적 장면 — 감사 로그에 **레코드가 없는 것**이 증거가 된다.

    성능 수치 슬롯이 어휘 사전에 없으므로 채울 수 없고, `prepare` 가
    `blocked` + `fallback` 을 함께 반환하므로 `send` 를 부를 필요조차 없다.
    """
    # 대역으로 실패를 강제하지 않는다 — **실제 경로**가 막아야 한다.
    # 성능 수치를 묻는 질문에 해당하는 task 가 어휘 사전에 없으므로
    # `choose_schema()` 가 거부하고 구조 페이로드가 만들어지지 않는다.
    prepared = _prepare(client, Q3_FOLLOWUP, ["person:kim"])
    call = prepared["calls"][0]

    assert call["disposition"] == "blocked"
    assert call["envelope_id"] is None
    assert call["fallback"] is not None
    assert call["fallback"]["used_external_agent"] is False
    assert "사내망 밖으로 나간 것 없음" in call["fallback"]["text"]

    # 감사 로그가 비어 있다 (BR-A-03)
    audit = client.get("/api/audit").json()
    assert audit["rows"] == []
    assert audit["total_records"] == 0
    # 신뢰 구역 내 처리는 별도 테이블에만
    assert wiring.audit.local_count() == 1


def test_scenario_3_vocabulary_has_no_performance_slot():
    """폴백이 우연이 아님을 확인한다 — 어휘 사전에 성능 슬롯이 **의도적으로** 없다."""
    from pathlib import Path

    vocab = json.loads(Path("data/vocab.json").read_text(encoding="utf-8"))
    for absent in ("p99_latency_ms", "throughput_tps", "latency_ms"):
        assert absent not in vocab["slots"], absent
    assert any("성능 수치" in line for line in vocab["_intentionally_absent"])


# ══════════════════════════════════════════════════════════════════════
# 게이트 조건 (U3 Step 7)
# ══════════════════════════════════════════════════════════════════════


def test_send_requires_approval(client):
    prepared = _prepare(client, Q1, ["person:kim"])
    r = client.post(
        "/api/ask/send",
        json={
            "request_id": prepared["request_id"],
            "envelope_ids": [prepared["calls"][0]["envelope_id"]],
        },
    )
    assert r.status_code == 422


def test_envelope_is_single_use(client):
    prepared = _prepare(client, Q1, ["person:kim"])
    payload = {
        "request_id": prepared["request_id"],
        "envelope_ids": [prepared["calls"][0]["envelope_id"]],
        "approved_by": "person:lee",
    }
    assert client.post("/api/ask/send", json=payload).status_code == 200
    assert client.post("/api/ask/send", json=payload).status_code == 410


def test_zero_citations_blocks_even_at_high_confidence(client, wiring):
    """🔴 신뢰도 0.99 라도 근거가 없으면 에스컬레이션이다."""
    wiring.fake_broker.response = agent_reply(confidence=0.99, citations=())
    prepared = _prepare(client, Q1, ["person:kim"])
    result = _send(client, prepared)
    assert result["merged"]["disposition"] == "escalate"


def test_one_of_two_failing_still_returns_the_other(client, wiring, full_data_root):
    """2명 중 1명이 실패해도 나머지 답변은 온다 (R-02).

    ⚠️ 세션 경로를 손으로 적지 않는다. `agents/{id}/gatekeeper/session.json` 로
       레이아웃이 바뀌었을 때 이 테스트가 옛 경로(`sessions/person_choi.json`)를
       지우려다 **테스트 자체가 실패**했다 — R-02 를 검사하지 못하는 상태로
       한동안 빨간불이었다. 경로는 `Config` 에게 묻는다.
    """
    wiring.cfg.agent_session_path("person:choi").unlink()
    prepared = _prepare(client, Q3, ["person:kim", "person:choi"])
    ready = [c for c in prepared["calls"] if c["disposition"] == "ready"]
    blocked = [c for c in prepared["calls"] if c["disposition"] == "blocked"]
    assert len(ready) == 1
    assert len(blocked) == 1
    assert blocked[0]["fallback"] is not None

    result = _send(client, prepared)
    assert len(result["merged"]["answers"]) == 1


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_closed(client, path):
    assert client.get(path).status_code == 404


def test_security_headers_present(client):
    from mesh.main import SECURITY_HEADERS

    headers = client.get("/api/health").headers
    for key, value in SECURITY_HEADERS.items():
        assert headers.get(key) == value


def test_adding_one_more_agent_needs_no_code_change(client, wiring):
    """`agents.yaml` 에 항목 하나 더하는 것으로 끝난다 (FR-23).

    ⚠️ 여기 쓰는 `entity_id` 는 **실제 설정에 없는 것**이어야 한다. 예전에는
       `person:han` 이었는데 그 사람이 진짜로 조직도에 들어오면서 "추가했지만
       인원이 그대로" 가 되어 테스트가 깨졌다. 실재하지 않을 이름을 쓴다.
    """
    from mesh.schemas import AgentConfig

    before = len(client.get("/api/agents").json())
    assert "person:testonly" not in wiring.data.agents
    wiring.data.agents["person:testonly"] = AgentConfig(
        entity_id="person:testonly",
        display_name="테스트 연구원",
        expertise="모델 평가",
        persona_prompt="평가 담당입니다.",
        knowledge_scope=("corpus/public/**",),
        escalation_inbox="person:testonly",
    )
    after = client.get("/api/agents").json()
    assert len(after) == before + 1
    assert "person:testonly" in {c["entity_id"] for c in after}


def test_health_exposes_the_simulated_boundary(client):
    body = client.get("/api/health").json()
    assert "trust_boundary_simulated" in body
    assert body["vocab_version"]


def test_full_sweep_finds_no_leak(client, wiring):
    """전수 유출 검사 — 저장된 **모든** 페이로드 × **모든** 코퍼스 문서 (FR-16, S-05)."""
    from pathlib import Path

    prepared = _prepare(client, Q1, ["person:kim"])
    _send(client, prepared)

    # ⚠️ 스캔 범위를 `corpus/` 로 못 박지 않는다. 지식 파일이
    #    `agents/{id}/data/**` 로 옮겨졌을 때 이 목록이 **0건**이 됐고,
    #    "전수 검사 통과" 가 아무것도 검사하지 않은 통과였다.
    #    아래 `documents_scanned` 하한이 그 실패를 잡는 장치다.
    root = Path(wiring.cfg.data_root)
    documents = [
        (p.relative_to(root).as_posix(), p.read_text(encoding="utf-8", errors="replace"))
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in {".md", ".txt", ".yaml", ".yml", ".py", ".log"}
        and "fixtures" not in p.parts
    ]
    report = wiring.audit.sweep_for_leaks(
        documents,
        identifiers=[lit for _, lit in wiring.data.pseudonyms.all_literals()],
        banned_literals=wiring.data.banned.literals,
        banned_patterns=wiring.data.banned.patterns,
    )
    assert report.payloads_scanned >= 2
    assert report.documents_scanned >= 11
    assert report.clean, (report.hits[:3], report.banned_hits[:3])


# ══════════════════════════════════════════════════════════════════════
# 시나리오 4 — 내가 방금 만든 기밀 문서에 남이 질문한다 (Day 4)
# ══════════════════════════════════════════════════════════════════════
#
# 시나리오 1~3 은 저장소에 심어둔 샘플 코퍼스를 쓴다. 그건 우리가 만든
# 문서이므로 "잘 되게 맞춰 놨을 수도" 있다.
#
# 여기서는 **테스트가 문서를 새로 만들어 업로드한다.** 코퍼스에 없던
# 표현·금액·날짜가 들어간 문서다. 그리고 **다른 사람**이 그 문서에 대해
# 질문한다. 이것이 이 도구의 실사용 형태이고, 데모의 1막이다.
#
# 새 입구는 새 위험이다. Day 4 에 업로드 경로가 생겼으므로,
# 그 경로로 들어온 원문도 같은 보장을 받는지 종단으로 확인한다.

UPLOAD_OWNER = "person:kim"
UPLOAD_ASKER = "person:park"

#: 코퍼스에 **없는** 표현을 쓴다. 샘플에 맞춘 통과를 배제한다.
FRESH_SECRET = """# title: 방금 만든 재계약 메모
# as_of: 2026-08-19

고객사 H 는 요구사항 REQ-9931 에서 15분 주기 재인증을 강제한다.
재계약 총액은 37억 원 규모이고 납기는 2027-03-15 이다.
위약금 조항이 붙어 있어 일정 협상 여지가 없다.
우리 SDK 는 무상태 토큰 갱신을 쓰므로 이 제약과 정면으로 충돌한다.
"""

#: 이 문서에만 있는 문자열. 경계를 넘는 어디에도 없어야 한다.
FRESH_SECRETS_ONLY = ("REQ-9931", "37억", "2027-03-15", "위약금", "15분 주기")


def _upload(client, content=FRESH_SECRET, filename="fresh-recontract.md"):
    r = client.post(
        "/api/documents",
        json={
            "owner": UPLOAD_OWNER,
            "filename": filename,
            "content": content,
            "attach_to_session": True,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_scenario_4_upload_then_someone_else_asks(client, wiring):
    """**업로드 → 다른 사람의 질문 → 원문 0개.** 이 도구의 실사용 형태다."""
    uploaded = _upload(client)
    doc = uploaded["document"]

    # ① 올리는 즉시 기밀로 판정되고 **근거가 함께** 온다
    assert doc["tier"] == "secret"
    assert doc["tier_evidence"], "근거 없는 판정은 블랙박스다"
    assert uploaded["in_scope"] is True
    assert doc["attached"] is True
    assert any("기밀" in w for w in uploaded["warnings"])

    # ② 업로드만으로는 아무것도 경계를 넘지 않는다
    assert wiring.audit.count() == 0

    # ③ 다른 사람이 그 문서에 대해 묻는다
    prepared = _prepare(client, Q1, [UPLOAD_OWNER])
    call = prepared["calls"][0]
    assert call["tier"] == "secret"
    assert call["preview"]["verbatim_sentence_count"] == 0

    # ④ 방금 만든 문서의 표현이 경계를 넘는 페이로드에 없다
    payload = call["preview"]["payload_pretty"]
    for leak in (*FRESH_SECRETS_ONLY, *NEVER_IN_PAYLOAD):
        assert leak not in payload, f"업로드한 원문이 경계를 넘었다: {leak}"

    result = _send(client, prepared)

    # ⑤ 감사 로그(페이로드 사본)에도 없다
    for leak in FRESH_SECRETS_ONLY:
        assert wiring.audit.search(leak) == (), f"감사 로그에 {leak} 이 남았다"

    # ⑥ 그래도 쓸 만한 답이 나온다 — 막기만 하는 도구가 아니다
    answer = result["merged"]["answers"][0]
    assert answer["text"].strip()
    assert answer["used_external_agent"] is True

    # ⑦ 문서를 올린 사람은 방해받지 않았다
    assert client.get("/api/inbox", params={"owner": UPLOAD_OWNER}).json() == []


def test_scenario_4_upload_does_not_revive_session_freshness(client, wiring):
    """파일을 올린 것은 "그 사람이 지금 그 일을 하고 있다"가 아니다 (BR-S-04).

    되살리면 오래된 세션이 업로드 한 번으로 LIVE 가 되고, STALE 신뢰도
    보정이 통째로 무력화된다 — 자신 없어야 할 답이 자신 있게 나온다.
    """
    before = wiring.store.load_session(UPLOAD_OWNER).updated_at
    _upload(client, filename="freshness-probe.md")
    assert wiring.store.load_session(UPLOAD_OWNER).updated_at == before


def test_scenario_4_uploaded_document_can_be_withdrawn(client, wiring):
    """올린 사람이 되돌릴 수 있어야 한다 — 지우면 질의 후보에서도 빠진다."""
    doc = _upload(client, filename="withdraw-me.md")["document"]
    resolved = wiring.store.resolve(doc["internal_path"])
    assert resolved.is_file()

    r = client.delete(f"/api/documents/{doc['document_id']}", params={"owner": UPLOAD_OWNER})
    assert r.status_code == 200
    assert not resolved.exists()
    assert doc["internal_path"] not in wiring.store.load_session(UPLOAD_OWNER).open_paths


def test_scenario_4_others_cannot_see_the_uploaded_document(client):
    """지식 격리 (BR-S-03). 질문자는 문서 목록을 볼 수 없다."""
    _upload(client, filename="kim-private.md")
    others = client.get("/api/documents", params={"owner": UPLOAD_ASKER}).json()
    assert "kim-private.md" not in {d["filename"] for d in others["documents"]}


def test_scenario_4_upload_cannot_declare_itself_public(client):
    """헤더 한 줄로 등급을 낮출 수 없다.

    낮출 수 있다면 기밀 문서 맨 위에 `보안 등급: 공개` 를 적는 것만으로
    게이트키퍼를 통째로 우회한다. 하향 권한은 작성자가 아니라 배치 경로에 있다.
    """
    doc = _upload(
        client,
        content="# title: 공개 주장\n# 보안 등급: 공개\n\n" + FRESH_SECRET,
        filename="claims-public.md",
    )["document"]
    # 금칙어가 헤더 주장보다 앞선다 (classifier 규칙 ②③ > ④)
    assert doc["tier"] == "secret"

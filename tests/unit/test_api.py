"""HTTP 표면 — 보안 헤더, 상태 코드, 응답 형태 (BR-M-*).

가장 중요한 넷:
  - `/docs` `/redoc` `/openapi.json` 이 404 다
  - 보안 헤더 4개가 **모든** 응답에 붙는다
  - 승인 없는 `send` 는 422, 만료된 envelope 은 410
  - 응답 JSON 에 `internal_path` 가 없다
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from mesh import main as main_mod
from mesh.exceptions import MeshError
from mesh.main import SECURITY_HEADERS, check_bind_host
from tests.fakes import agent_reply

QUESTION = "고객사 요구와 우리 SDK 갱신 방식이 충돌하나요?"
NEVER_IN_RESPONSE = ("internal_path", "corpus/", "REQ-4412", "CTR-204817", "H社", "12억")


def prepare(client, targets=("person:kim",), question=QUESTION):
    return client.post(
        "/api/ask/prepare",
        json={"question": question, "asker": "person:lee", "targets": list(targets)},
    )


# ══════════════════════════════════════════════════════════════════════
# 노출 표면 (SECURITY-09)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_exposed(client, path):
    """OpenAPI 스키마는 내부 필드 이름과 구조를 전부 노출한다."""
    assert client.get(path).status_code == 404


def test_docs_open_in_dev_mode(monkeypatch, wiring):
    from fastapi.testclient import TestClient

    from mesh.config import Config
    from mesh.main import create_app

    monkeypatch.setenv("MESH_DEV", "1")
    cfg = Config.load()
    with TestClient(create_app(cfg, services=wiring)) as c:
        assert c.get("/openapi.json").status_code == 200


def test_no_cors_header(client):
    """와일드카드 CORS 는 이 서비스에서 곧 데이터 유출이다 (BR-M-04)."""
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_no_cors_middleware_in_source():
    src = Path(inspect.getfile(main_mod)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not any("cors" in (m or "").lower() for m in imported)


def test_no_static_directory_mount():
    """디렉터리 리스팅을 원천 차단한다 (BR-M-08).

    ast 로 검사한다 — 문자열 검사는 "StaticFiles 를 쓰지 않는다"는 주석까지 잡는다.
    """
    tree = ast.parse(Path(inspect.getfile(main_mod)).read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "StaticFiles" not in called
    assert "mount" not in called


# ══════════════════════════════════════════════════════════════════════
# 보안 헤더 (BR-M-03)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("header", sorted(SECURITY_HEADERS))
def test_security_headers_on_api(client, header):
    assert client.get("/api/health").headers.get(header) == SECURITY_HEADERS[header]


@pytest.mark.parametrize("path", ["/", "/app.js", "/style.css", "/api/health", "/nope"])
def test_security_headers_on_every_response(client, path):
    headers = client.get(path).headers
    for key in SECURITY_HEADERS:
        assert key in headers, (path, key)


def test_csp_has_no_unsafe_inline():
    """`unsafe-inline` 을 쓰지 않는다 -> U4 의 JS·CSS 는 별도 파일이어야 한다."""
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "default-src 'self'" in csp


def test_four_headers_exactly():
    assert set(SECURITY_HEADERS) == {
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
    }


def test_correlation_id_is_returned(client):
    r = client.get("/api/health")
    assert r.headers["X-Correlation-Id"]


def test_correlation_id_is_echoed(client):
    r = client.get("/api/health", headers={"X-Correlation-Id": "req_mine"})
    assert r.headers["X-Correlation-Id"] == "req_mine"


# ══════════════════════════════════════════════════════════════════════
# 바인딩 (BR-M-01)
# ══════════════════════════════════════════════════════════════════════


def test_non_localhost_bind_is_refused(full_cfg):
    """경고만 하면 아무도 읽지 않는다. 시작을 막는다."""
    cfg = dataclasses.replace(full_cfg, bind_host="0.0.0.0")  # noqa: S104
    with pytest.raises(MeshError, match="MESH_ALLOW_NETWORK_BIND"):
        check_bind_host(cfg, allow_network=False)


def test_non_localhost_bind_allowed_with_explicit_flag(full_cfg):
    cfg = dataclasses.replace(full_cfg, bind_host="0.0.0.0")  # noqa: S104
    check_bind_host(cfg, allow_network=True)  # 예외 없음


def test_localhost_is_fine(full_cfg):
    check_bind_host(full_cfg, allow_network=False)


# ══════════════════════════════════════════════════════════════════════
# /api/health
# ══════════════════════════════════════════════════════════════════════


def test_health_exposes_trust_boundary_simulation(client):
    """🔴 숨기면 심사자를 속이는 것이다. 먼저 밝히는 것이 낫다."""
    body = client.get("/api/health").json()
    assert "trust_boundary_simulated" in body
    assert body["trusted_zone_llm_base_url"]
    assert body["vocab_version"]
    assert body["exaone_mode"] == "mock"


def test_health_reports_disposition_counts(client):
    body = client.get("/api/health").json()
    assert body["disposition_counts"] == {}
    r = prepare(client).json()
    client.post(
        "/api/ask/send",
        json={
            "request_id": r["request_id"],
            "envelope_ids": [r["calls"][0]["envelope_id"]],
            "approved_by": "person:lee",
        },
    )
    assert client.get("/api/health").json()["disposition_counts"] == {"auto": 1}


def test_health_reports_demo_now_override(client):
    assert client.get("/api/health").json()["demo_now_override"] is not None


# ══════════════════════════════════════════════════════════════════════
# /api/agents
# ══════════════════════════════════════════════════════════════════════


def test_agents_lists_three(client):
    cards = client.get("/api/agents").json()
    assert {c["entity_id"] for c in cards} == {"person:kim", "person:park", "person:choi"}
    assert all(c["expertise"] for c in cards)


def test_agents_response_has_no_session_text(client):
    """이 화면은 인증 없이 보인다 (FR-31)."""
    blob = client.get("/api/agents").text
    for leak in ("고객사 H", "SDK v3.2", "atlas-ml", "레거시 SSO", "재학습"):
        assert leak not in blob, leak


def test_adding_an_agent_needs_no_code_change(client, wiring):
    """`agents.yaml` 에 항목 하나 더하는 것으로 끝난다 (FR-23)."""
    from mesh.schemas import AgentConfig

    wiring.data.agents["person:new"] = AgentConfig(
        entity_id="person:new",
        display_name="신규 담당",
        expertise="테스트 영역",
        persona_prompt="테스트",
        knowledge_scope=("corpus/public/**",),
        escalation_inbox="person:new",
    )
    cards = client.get("/api/agents").json()
    assert "person:new" in {c["entity_id"] for c in cards}


# ══════════════════════════════════════════════════════════════════════
# prepare / send (BR-M-02)
# ══════════════════════════════════════════════════════════════════════


def test_prepare_returns_preview_without_calling_agent(client, wiring):
    body = prepare(client).json()
    assert body["agents_notified"] is False
    assert body["calls"][0]["preview"]["validation_summary"] == "6/6"
    assert wiring.fake_broker.calls == []


def test_prepare_rejects_three_targets(client):
    r = client.post(
        "/api/ask/prepare",
        json={
            "question": QUESTION,
            "asker": "person:lee",
            "targets": ["person:kim", "person:park", "person:choi"],
        },
    )
    assert r.status_code == 422


def test_prepare_rejects_duplicate_targets(client):
    r = client.post(
        "/api/ask/prepare",
        json={
            "question": QUESTION,
            "asker": "person:lee",
            "targets": ["person:kim", "person:kim"],
        },
    )
    assert r.status_code == 422


def test_prepare_rejects_blank_question(client):
    r = client.post(
        "/api/ask/prepare",
        json={"question": "   ", "asker": "person:lee", "targets": ["person:kim"]},
    )
    assert r.status_code == 422


def test_prepare_rejects_malformed_entity_id(client):
    r = client.post(
        "/api/ask/prepare",
        json={"question": QUESTION, "asker": "김대리", "targets": ["person:kim"]},
    )
    assert r.status_code == 422


def test_send_without_approval_is_422(client):
    """🔴 승인 없는 전송이 여기까지 오지도 못한다."""
    body = prepare(client).json()
    r = client.post(
        "/api/ask/send",
        json={"request_id": body["request_id"], "envelope_ids": [body["calls"][0]["envelope_id"]]},
    )
    assert r.status_code == 422


def test_send_succeeds_and_rehydrates(client):
    body = prepare(client).json()
    r = client.post(
        "/api/ask/send",
        json={
            "request_id": body["request_id"],
            "envelope_ids": [body["calls"][0]["envelope_id"]],
            "approved_by": "person:lee",
        },
    )
    assert r.status_code == 200
    result = r.json()
    assert result["merged"]["disposition"] == "auto"
    assert "REQ_A" not in result["merged"]["answers"][0]["text"]
    assert result["merged"]["answers"][0]["citations"]


def test_send_twice_is_410(client):
    """envelope 은 일회용이다 (중복 전송·중복 과금 방지)."""
    body = prepare(client).json()
    payload = {
        "request_id": body["request_id"],
        "envelope_ids": [body["calls"][0]["envelope_id"]],
        "approved_by": "person:lee",
    }
    assert client.post("/api/ask/send", json=payload).status_code == 200
    second = client.post("/api/ask/send", json=payload)
    assert second.status_code == 410
    assert second.json()["error"] == "gone"


def test_send_with_unknown_request_id_is_410(client):
    r = client.post(
        "/api/ask/send",
        json={
            "request_id": "req_nope",
            "envelope_ids": ["env_AAAAAAAAAAAAAAAAAAAAAA"],
            "approved_by": "person:lee",
        },
    )
    assert r.status_code == 410


def test_send_with_malformed_envelope_id_is_422(client):
    r = client.post(
        "/api/ask/send",
        json={"request_id": "req_x", "envelope_ids": ["nope"], "approved_by": "person:lee"},
    )
    assert r.status_code == 422


def test_response_never_contains_internal_paths(client):
    body = prepare(client).json()
    r = client.post(
        "/api/ask/send",
        json={
            "request_id": body["request_id"],
            "envelope_ids": [body["calls"][0]["envelope_id"]],
            "approved_by": "person:lee",
        },
    )
    blob = json.dumps(body, ensure_ascii=False) + r.text
    for leak in NEVER_IN_RESPONSE:
        assert leak not in blob, leak


# ══════════════════════════════════════════════════════════════════════
# 인박스
# ══════════════════════════════════════════════════════════════════════


def test_inbox_flow_end_to_end(client, wiring):
    wiring.fake_broker.response = agent_reply(confidence=0.20)
    body = prepare(client).json()
    client.post(
        "/api/ask/send",
        json={
            "request_id": body["request_id"],
            "envelope_ids": [body["calls"][0]["envelope_id"]],
            "approved_by": "person:lee",
        },
    )

    items = client.get("/api/inbox", params={"owner": "person:kim"}).json()
    assert len(items) == 1
    item_id = items[0]["item_id"]

    resolved = client.post(
        f"/api/inbox/{item_id}/resolve",
        json={"action": "approve_with_edit", "edited_text": "담당자가 확인한 답변입니다"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "approved_with_edit"

    # 환류 확인 (BR-I-02)
    verified = wiring.store.load_session("person:kim").verified_qa
    assert len(verified) == 1
    assert verified[0].answer == "담당자가 확인한 답변입니다"
    assert verified[0].tier.value == "secret"  # 등급 보존


def test_resolve_unknown_item_is_404(client):
    r = client.post("/api/inbox/inb_nope/resolve", json={"action": "approve"})
    assert r.status_code == 404


def test_double_resolve_is_409(client, wiring):
    item = wiring.inbox.add(
        owner_entity_id="person:kim",
        asker="person:lee",
        thread_id="req_1",
        question_summary="q",
        draft=__import__("mesh.schemas", fromlist=["EscalationDraft"]).EscalationDraft(
            summary="s", situation=["a"], draft_answer="d"
        ),
        tier=__import__("mesh.schemas", fromlist=["Tier"]).Tier.INTERNAL,
    )
    assert (
        client.post(f"/api/inbox/{item.item_id}/resolve", json={"action": "approve"}).status_code
        == 200
    )
    assert (
        client.post(f"/api/inbox/{item.item_id}/resolve", json={"action": "approve"}).status_code
        == 409
    )


def test_inbox_requires_owner(client):
    assert client.get("/api/inbox").status_code == 422


# ══════════════════════════════════════════════════════════════════════
# 감사 로그 (FR-42)
# ══════════════════════════════════════════════════════════════════════


def test_audit_search_zero_hit_for_original_text(client):
    body = prepare(client).json()
    client.post(
        "/api/ask/send",
        json={
            "request_id": body["request_id"],
            "envelope_ids": [body["calls"][0]["envelope_id"]],
            "approved_by": "person:lee",
        },
    )
    for term in ("REQ-4412", "H社", "12억원", "EAP-AKA"):
        result = client.get("/api/audit", params={"q": term}).json()
        assert result["rows"] == [], term
        assert result["query"] == term


def test_audit_search_finds_structured_values(client):
    body = prepare(client).json()
    client.post(
        "/api/ask/send",
        json={
            "request_id": body["request_id"],
            "envelope_ids": [body["calls"][0]["envelope_id"]],
            "approved_by": "person:lee",
        },
    )
    result = client.get("/api/audit", params={"q": "session_binding"}).json()
    assert result["rows"]
    assert result["total_records"] >= 2


def test_audit_search_term_is_length_limited(client):
    assert client.get("/api/audit", params={"q": "x" * 500}).status_code == 422


@pytest.mark.parametrize("evil", ["' OR 1=1 --", "%", "'; DROP TABLE audit; --"])
def test_audit_search_is_injection_safe(client, evil):
    assert client.get("/api/audit", params={"q": evil}).status_code == 200
    assert client.get("/api/health").status_code == 200  # 테이블이 살아 있다


def test_audit_row_has_no_internal_path(client):
    body = prepare(client).json()
    client.post(
        "/api/ask/send",
        json={
            "request_id": body["request_id"],
            "envelope_ids": [body["calls"][0]["envelope_id"]],
            "approved_by": "person:lee",
        },
    )
    blob = client.get("/api/audit").text
    assert "internal_path" not in blob
    assert "corpus/" not in blob


def test_audit_excludes_local_queries(client, wiring):
    """🔴 "레코드가 없다"가 증거가 되려면 섞이면 안 된다 (BR-U-11)."""
    wiring.fake_exaone.fail["extract"] = __import__(
        "mesh.exceptions", fromlist=["ExaoneUnavailable"]
    ).ExaoneUnavailable("타임아웃")
    prepare(client)
    assert wiring.audit.local_count() == 1
    assert client.get("/api/audit").json()["rows"] == []
    assert client.get("/api/audit").json()["total_records"] == 0


# ══════════════════════════════════════════════════════════════════════
# 오류 응답 (BR-M-05)
# ══════════════════════════════════════════════════════════════════════


def test_error_response_has_no_stack_trace(client):
    r = client.post(
        "/api/ask/send",
        json={
            "request_id": "req_nope",
            "envelope_ids": ["env_AAAAAAAAAAAAAAAAAAAAAA"],
            "approved_by": "person:lee",
        },
    )
    body = r.json()
    assert set(body) <= {"error", "correlation_id", "detail"}
    assert "Traceback" not in r.text
    assert "/Users/" not in r.text
    assert "site-packages" not in r.text
    assert body["correlation_id"]


def test_unhandled_error_hides_details(wiring, monkeypatch):
    """⚠️ `raise_server_exceptions=False` 로 **실제 500 응답**을 본다.

    기본값(`True`)이면 TestClient 가 예외를 그대로 올려서 전역 핸들러가
    만드는 응답을 검사할 수 없다 — 즉 이 테스트가 아무것도 확인하지 못한다.
    """
    from fastapi.testclient import TestClient

    from mesh.main import create_app

    async def boom(*a, **kw):
        raise RuntimeError("내부 경로 /Users/secret/thing.py 가 노출되면 안 된다")

    monkeypatch.setattr(wiring.orchestrator, "agent_cards", boom)
    app = create_app(wiring.cfg, services=wiring)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/agents")
    assert r.status_code == 500
    assert "internal_error" == r.json()["error"]
    assert "/Users/" not in r.text
    assert "RuntimeError" not in r.text


# ══════════════════════════════════════════════════════════════════════
# 정적 파일
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", ["/", "/app.js", "/style.css"])
def test_static_paths_respond(client, path):
    """U4 가 아직 파일을 만들지 않아도 앱이 뜬다."""
    assert client.get(path).status_code == 200


def test_unmapped_path_is_404(client):
    assert client.get("/../../etc/passwd").status_code in {404, 400}
    assert client.get("/web/index.html").status_code == 404


# ══════════════════════════════════════════════════════════════════════
# 문서 업로드 (Day 4) — HTTP 계층
# ══════════════════════════════════════════════════════════════════════
#
# `test_documents.py` 가 서비스 계층을 덮는다. 여기서 보는 것은 **HTTP 로
# 올라올 때 무엇이 달라지는가**다: 상태 코드, 오류 응답 형태, 그리고
# 프런트가 실제로 쓰는 필드가 응답에 있는지.

UPLOAD_SECRET = "# title: HTTP 계약 메모\n\n고객사 H 와 총액 12억 원 규모로 합의했다.\n"
UPLOAD_PLAIN = "# title: 재시도 지침\n\n지수 백오프로 3회 재시도한다.\n"


def _upload(client, **overrides):
    body = {
        "owner": "person:kim",
        "filename": "http-memo.md",
        "content": UPLOAD_PLAIN,
        "attach_to_session": False,
    }
    body.update(overrides)
    return client.post("/api/documents", json=body)


def test_upload_returns_tier_with_evidence(client):
    """업로드 응답이 곧 첫 데모 장면이다 — 등급과 **근거**를 함께 준다.

    근거가 없으면 "왜 기밀인가"에 답할 수 없고, 등급 판정이 블랙박스로 보인다.
    """
    r = _upload(client, filename="contract.md", content=UPLOAD_SECRET)
    assert r.status_code == 200
    doc = r.json()["document"]
    assert doc["tier"] == "secret"
    assert doc["tier_evidence"], "근거 없는 판정은 블랙박스다"
    assert all({"rule", "reason"} <= set(e) for e in doc["tier_evidence"])
    assert r.json()["in_scope"] is True


def test_upload_response_has_the_fields_the_ui_uses(client):
    """화면이 참조하는 필드를 계약으로 고정한다.

    하나라도 이름이 바뀌면 화면이 `undefined` 를 그린다 — 조용한 실패다.
    """
    doc = _upload(client).json()["document"]
    assert {
        "document_id",
        "owner",
        "filename",
        "size_bytes",
        "uploaded_at",
        "tier",
        "tier_evidence",
        "attached",
        "seeded",
    } <= set(doc)


def test_upload_rejects_path_traversal_with_422(client):
    """경로 탈출은 `api_models` 가 먼저 막는다 → 422 (요청 형식 오류)."""
    r = _upload(client, filename="../escape.md")
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_upload_rejects_unknown_owner_with_400(client):
    """미등록 사용자는 형식은 맞고 내용이 틀렸다 → 400 (MeshError)."""
    r = _upload(client, owner="person:nobody")
    assert r.status_code == 400
    assert r.json()["error"] == "UploadRejected"


def test_upload_rejects_oversized_content(client):
    from mesh.api_models import MAX_UPLOAD_CHARS

    r = _upload(client, content="가" * (MAX_UPLOAD_CHARS + 1))
    assert r.status_code == 422


def test_validation_error_does_not_echo_the_document(client):
    """**422 응답이 요청 본문을 되비추지 않는다.**

    FastAPI 기본 핸들러는 오류마다 `input` 을 담는다 — 업로드가 상한을
    넘겼을 때 방금 올린 기밀 문서 전문이 오류 응답에 실렸다 (실측).
    요청자 자신에게 돌아가므로 유출은 아니지만, 원문을 담는 오류 응답은
    로그·프록시·브라우저 히스토리로 원문을 퍼뜨린다.
    """
    from mesh.api_models import MAX_UPLOAD_CHARS

    secret = "고객사 H 총액 12억 원 REQ-4412 기밀 본문"
    r = _upload(client, content=secret + "가" * MAX_UPLOAD_CHARS)

    assert r.status_code == 422
    assert "고객사 H" not in r.text
    assert "REQ-4412" not in r.text
    assert "12억" not in r.text
    # 응답 크기가 요청 크기에 비례해 커지지 않는다
    assert len(r.content) < 1000, "오류 응답이 본문 크기를 따라 커진다"


def test_validation_error_keeps_the_error_contract(client):
    """모든 오류가 같은 형태여야 화면이 오류를 표시할 수 있다."""
    r = _upload(client, filename="../escape.md")
    body = r.json()
    assert r.status_code == 422
    assert body["error"] == "validation_error"
    assert body["correlation_id"]
    # 어느 필드가 틀렸는지는 알려준다 — 고칠 수 있어야 한다
    assert "filename" in body["detail"]


def test_validation_error_names_the_field_without_the_value(client):
    r = _upload(client, owner="not-an-entity-id")
    body = r.json()
    assert r.status_code == 422
    assert "owner" in body["detail"]
    assert "not-an-entity-id" not in r.text, "틀린 값을 되비추지 않는다"


def test_upload_error_response_has_no_internal_path(client):
    """오류 메시지가 파일시스템 구조를 알려주면 그 자체가 정보다."""
    r = _upload(client, filename="../escape.md")
    assert "/Users/" not in r.text
    assert "corpus/" not in r.text


def test_upload_does_not_attach_by_default_flag(client, wiring):
    _upload(client, filename="not-attached.md")
    session = wiring.store.load_session("person:kim")
    assert not any("not-attached.md" in p for p in session.open_paths)


def test_upload_attaches_when_asked(client, wiring):
    r = _upload(client, filename="attached.md", attach_to_session=True)
    rel = r.json()["document"]["internal_path"]
    assert rel in wiring.store.load_session("person:kim").open_paths


def test_document_list_separates_uploads_from_seeded(client):
    _upload(client, filename="mine.md")
    r = client.get("/api/documents", params={"owner": "person:kim"})
    assert r.status_code == 200
    body = r.json()
    assert body["owner"] == "person:kim"
    by_name = {d["filename"]: d for d in body["documents"]}
    assert by_name["mine.md"]["seeded"] is False
    assert any(d["seeded"] for d in body["documents"])


def test_document_list_requires_owner(client):
    assert client.get("/api/documents").status_code == 422


def test_document_delete_roundtrip(client, wiring):
    r = _upload(client, filename="temp.md", attach_to_session=True)
    doc = r.json()["document"]
    resolved = wiring.store.resolve(doc["internal_path"])

    d = client.delete(f"/api/documents/{doc['document_id']}", params={"owner": "person:kim"})
    assert d.status_code == 200
    assert d.json() == {"deleted": True}
    assert not resolved.exists()


def test_document_delete_unknown_is_404(client):
    r = client.delete("/api/documents/doc_deadbeef1234", params={"owner": "person:kim"})
    assert r.status_code == 404


def test_document_delete_by_other_owner_is_404(client, wiring):
    """남의 문서 id 로 지울 수 없다. 목록에 없으면 존재하지 않는 것과 같다."""
    doc = _upload(client, filename="kims.md").json()["document"]
    r = client.delete(f"/api/documents/{doc['document_id']}", params={"owner": "person:park"})
    assert r.status_code == 404
    assert wiring.store.resolve(doc["internal_path"]).is_file()


def test_uploaded_secret_never_reaches_the_payload(client):
    """**업로드 → 질문 종단.** 올린 기밀이 경계를 넘는 페이로드에 없다.

    이게 이 프로젝트의 주장 그 자체다. 업로드 경로가 생겼으니
    그 경로로 들어온 원문도 같은 보장을 받아야 한다.
    """
    client.post(
        "/api/documents",
        json={
            "owner": "person:kim",
            "filename": "fresh-secret.md",
            "content": (
                "# title: 갓 올린 기밀\n\n"
                "고객사 H 요구사항 REQ-4412 는 30분 주기 재인증을 강제한다.\n"
                "총액 12억 원 규모이며 납기는 2026-11-30 이다.\n"
            ),
            "attach_to_session": True,
        },
    ).raise_for_status()

    r = client.post(
        "/api/ask/prepare",
        json={
            "asker": "person:park",
            "question": "고객사 요구사항과 우리 SDK 토큰 갱신 방식이 충돌하나요?",
            "targets": ["person:kim"],
        },
    )
    assert r.status_code == 200
    previews = [c["preview"] for c in r.json()["calls"] if c["preview"]]
    assert previews, "미리보기가 없으면 검사할 것이 없다"
    for preview in previews:
        payload = preview["payload_pretty"]
        for forbidden in ("고객사 H", "REQ-4412", "12억", "2026-11-30"):
            assert forbidden not in payload, f"경계를 넘는 페이로드에 {forbidden} 이 있다"
        assert preview["verbatim_sentence_count"] == 0


# ══════════════════════════════════════════════════════════════════════
# 사용자 · 질문 프리셋 (Day 4)
# ══════════════════════════════════════════════════════════════════════


def test_users_lists_switchable_people(client):
    r = client.get("/api/users")
    assert r.status_code == 200
    users = r.json()
    assert len(users) == 3
    assert {u["entity_id"] for u in users} == {"person:kim", "person:park", "person:choi"}
    for u in users:
        assert u["display_name"]


def test_users_response_has_no_session_text(client):
    """사용자 목록은 인증 없이 보인다. 세션 원문이 섞이면 게이트키퍼 우회다."""
    body = client.get("/api/users").text
    for leak in ("고객사 H", "REQ-4412", "corpus/"):
        assert leak not in body


def test_questions_presets_are_usable(client):
    """프리셋은 서버가 준다 — 대본을 고칠 때 JS 를 건드리지 않는다."""
    r = client.get("/api/questions")
    assert r.status_code == 200
    presets = r.json()
    assert presets, "프리셋이 비면 화면 드롭다운이 빈다"
    for p in presets:
        assert p["label"] and p["question"]
        assert 1 <= len(p["targets"]) <= 2


def test_question_presets_target_real_agents(client):
    """대본의 지목 대상이 실제로 존재해야 한다. 아니면 시연 중에 발견한다."""
    known = {u["entity_id"] for u in client.get("/api/users").json()}
    for p in client.get("/api/questions").json():
        assert set(p["targets"]) <= known, f"{p['label']} 의 지목 대상이 없다"

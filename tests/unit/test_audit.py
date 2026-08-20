"""감사 로그 — 추가 전용, 금지 필드 거부, 원문 검색, 전수 유출 검사.

가장 중요한 셋:
  - `audit` 테이블에 `DELETE`/`UPDATE` 문이 앱 코드에 없다
  - 금지 필드가 있으면 **기록을 거부한다** (그러면 전송도 일어나지 않는다)
  - `local_queries` 에 질문 원문이 없다
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

import pytest

from mesh import audit as audit_mod
from mesh.audit import LOCAL_REASON_CODES, AuditLog
from mesh.config import sha256_canonical
from mesh.exceptions import GatekeeperError
from mesh.schemas import AuditRecord, Representation, Tier, Transport

QUESTION = "H社 요구사항 REQ-4412 와 우리 SDK 갱신 방식이 충돌하나요?"


@pytest.fixture
def store(cfg):
    log = AuditLog(cfg)
    yield log
    log.close()


def make_record(**over) -> AuditRecord:
    base = {
        "record_id": "aud_test0000000000000001",
        "at": datetime(2026, 8, 19, 14, 31, tzinfo=None),
        "kind": "request",
        "actor": "person:lee",
        "target_entity_id": "person:kim",
        "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "transport": Transport.DIRECT,
        "trusted_zone_llm_base_url": "https://api.friendli.ai/dedicated/v1",
        "tier": Tier.SECRET,
        "representation": Representation.STRUCTURED,
        "payload": {
            "task": "constraint_conflict_check",
            "facts": {"session_binding": "required", "max_session_hours": 8},
        },
        "payload_sha256": "deadbeef" * 8,
        "size_bytes": 128,
        "validation_summary": "6/6",
        "approved_by": "person:lee",
        "envelope_id": "env_AAAAAAAAAAAAAAAAAAAAAA",
    }
    return AuditRecord(**{**base, **over})


# ══════════════════════════════════════════════════════════════════════
# 추가 전용 (NFR-S-13)
# ══════════════════════════════════════════════════════════════════════


def test_no_delete_or_update_on_audit_table():
    """🔴 감사 로그는 추가 전용이다. grep 으로 확인한다 (설계 §8)."""
    src = Path(inspect.getfile(audit_mod)).read_text(encoding="utf-8")
    # 주석·docstring 을 포함해도 SQL 문 형태가 없어야 한다
    assert not re.search(r"\bDELETE\s+FROM\s+audit\b", src, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\s+audit\b", src, re.IGNORECASE)
    assert not re.search(r"\bDROP\s+TABLE\s+audit\b", src, re.IGNORECASE)


def test_no_delete_or_update_anywhere_in_src():
    """앱 전체에서 감사 테이블을 수정하는 문이 없다."""
    root = Path(inspect.getfile(audit_mod)).parent
    for path in root.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"\bDELETE\s+FROM\s+audit\b", src, re.IGNORECASE), path
        assert not re.search(r"\bUPDATE\s+audit\b", src, re.IGNORECASE), path


def test_record_ids_are_unique(store):
    store.record(make_record())
    with pytest.raises(sqlite3.IntegrityError):
        store.record(make_record())


# ══════════════════════════════════════════════════════════════════════
# 파일 권한 (NFR-S-01)
# ══════════════════════════════════════════════════════════════════════


def test_db_file_is_owner_only(store):
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0, oct(mode)


# ══════════════════════════════════════════════════════════════════════
# 금지 필드 (BR-A-02)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"text": "원문 문장"},
        {"facts": {"chunk_text": "원문"}},
        {"mapping": {"REQ_A": "명세서"}},
        {"nested": [{"reasoning": "문서에 …라고 나와 있으므로"}]},
        {"deep": {"more": {"friendli_token": "flp_xxx"}}},
    ],
)
def test_record_rejects_forbidden_fields(store, bad_payload):
    """기록을 거부하면 `ask_agent` 가 예외를 받고 전송도 일어나지 않는다 (fail closed)."""
    with pytest.raises(GatekeeperError, match="금지 필드"):
        store.record(make_record(payload=bad_payload))
    assert store.count() == 0


def test_reject_forbidden_allows_clean_payload():
    AuditLog.reject_forbidden({"task": "x", "facts": {"session_binding": "required"}})


def test_audit_columns_have_no_forbidden_names():
    """스키마 자체에 원문·매핑을 담을 칸이 없다."""
    from mesh.audit import _AUDIT_COLUMNS
    from mesh.config import FORBIDDEN_LOG_KEYS

    for col in _AUDIT_COLUMNS:
        assert col.lower() not in FORBIDDEN_LOG_KEYS, col


def test_reasoning_never_reaches_the_db(store):
    """실측 유출 채널 (`preflight-findings.md` 발견 1)."""
    with pytest.raises(GatekeeperError):
        store.record(make_record(payload={"reasoning_content": "원문 재인용"}))


# ══════════════════════════════════════════════════════════════════════
# 왕복
# ══════════════════════════════════════════════════════════════════════


def test_record_and_read_back(store):
    rec = make_record()
    store.record(rec)
    got = store.recent()[0]
    assert got.record_id == rec.record_id
    assert got.payload == rec.payload
    assert got.tier is Tier.SECRET
    assert got.transport is Transport.DIRECT
    assert got.trusted_zone_llm_base_url == rec.trusted_zone_llm_base_url


def test_trusted_zone_url_is_recorded_every_time(store):
    """이 프로젝트의 신뢰 경계는 설정값이다. 설정이 경계를 정하면 그 설정도 감사 대상이다."""
    store.record(make_record())
    store.record(
        make_record(
            record_id="aud_test0000000000000002",
            trusted_zone_llm_base_url="http://10.0.0.5:8000/v1",
        )
    )
    urls = {r.trusted_zone_llm_base_url for r in store.recent()}
    assert len(urls) == 2


def test_by_envelope(store):
    store.record(make_record())
    store.record(make_record(record_id="aud_test0000000000000002", kind="result"))
    assert len(store.by_envelope("env_AAAAAAAAAAAAAAAAAAAAAA")) == 2
    assert store.by_envelope("env_BBBBBBBBBBBBBBBBBBBBBB") == ()


def test_usage_roundtrip(store):
    store.record(make_record(usage={"inputTokens": 460, "outputTokens": 513}))
    assert store.recent()[0].usage["outputTokens"] == 513


# ══════════════════════════════════════════════════════════════════════
# 원문 검색 (BR-A-04, FR-42)
# ══════════════════════════════════════════════════════════════════════


def test_search_finds_a_value_in_the_payload(store):
    store.record(make_record())
    assert len(store.search("session_binding")) == 1
    assert len(store.search("SESSION_BINDING")) == 1


def test_search_returns_zero_for_never_sent_text(store):
    """0건이 이 화면의 핵심 기능이다 — "경계를 넘은 적이 없습니다"의 근거."""
    store.record(make_record())
    assert store.search("REQ-4412") == ()
    assert store.search("H社") == ()
    assert store.search("12억원") == ()


@pytest.mark.parametrize("evil", ["' OR 1=1 --", "%", "_", "'; DROP TABLE audit; --"])
def test_search_is_injection_safe(store, evil):
    store.record(make_record())
    store.search(evil)  # 예외 없이 동작해야 한다
    assert store.count() == 1  # 테이블이 살아 있다


def test_search_ignores_blank(store):
    store.record(make_record())
    assert store.search("   ") == ()


# ══════════════════════════════════════════════════════════════════════
# local_queries (BR-A-03) — 레코드가 없어야 하는 경우
# ══════════════════════════════════════════════════════════════════════


def test_local_query_leaves_no_audit_record(store):
    """🔴 시나리오 3의 결정적 장면 — 감사 로그에 없다는 것이 증거다."""
    store.record_local(
        actor="local",
        target_entity_id="person:kim",
        tier=Tier.SECRET,
        reason_code="extraction_failed",
        question_sha256=sha256_canonical(QUESTION),
    )
    assert store.count() == 0
    assert store.local_count() == 1


def test_local_query_does_not_store_the_question(store):
    store.record_local(
        actor="local",
        target_entity_id="person:kim",
        tier=Tier.SECRET,
        reason_code="extraction_failed",
        question_sha256=sha256_canonical(QUESTION),
    )
    dump = store.path.read_bytes().decode("utf-8", errors="ignore")
    assert "REQ-4412" not in dump
    assert "H社" not in dump


def test_local_reason_code_must_be_enumerated(store):
    """자유 문자열을 받으면 이유에 질문 원문이 섞여 들어간다."""
    with pytest.raises(GatekeeperError, match="미등록 local reason_code"):
        store.record_local(
            actor="local",
            target_entity_id="person:kim",
            tier=Tier.SECRET,
            reason_code=f"실패: {QUESTION}",
            question_sha256="x",
        )


def test_all_local_reason_codes_are_accepted(store):
    for i, code in enumerate(LOCAL_REASON_CODES):
        store.record_local(
            actor="local",
            target_entity_id="person:kim",
            tier=Tier.INTERNAL,
            reason_code=code,
            question_sha256=f"h{i}",
        )
    assert store.local_count() == len(LOCAL_REASON_CODES)


# ══════════════════════════════════════════════════════════════════════
# 인박스
# ══════════════════════════════════════════════════════════════════════


def test_inbox_roundtrip(store):
    store.add_inbox(
        to_entity_id="person:park",
        summary="GPU 점유 중 실행 허락 요청",
        situation=["학습 작업 실행 중", "GPU 0 점유"],
        draft_answer="지금은 피하고 30분 후 재확인을 권합니다",
        already_answered=["기법 질문은 자동 응답됨"],
    )
    items = store.list_inbox("person:park")
    assert len(items) == 1
    assert items[0]["situation"] == ["학습 작업 실행 중", "GPU 0 점유"]
    assert items[0]["status"] == "open"
    assert store.list_inbox("person:kim") == ()


# ══════════════════════════════════════════════════════════════════════
# 전수 유출 검사 (FR-16, S-05)
# ══════════════════════════════════════════════════════════════════════


def test_sweep_is_clean_for_structured_payloads(store, banned):
    store.record(make_record())
    report = store.sweep_for_leaks(
        [("corpus/customer-H/req-spec.md", "H社 요구사항 REQ-4412 는 세션 바인딩을 요구한다")],
        banned_literals=banned.literals,
        banned_patterns=banned.patterns,
    )
    assert report.clean
    assert report.payloads_scanned == 1
    assert report.documents_scanned == 1


def test_sweep_detects_a_planted_leak(store):
    """검사기가 아무것도 못 잡으면 무의미하다 — 심은 유출을 잡는지 확인한다."""
    original = "인증은 세션에 바인딩된 방식이어야 하며 최대 유지시간은 여덟 시간이다"
    store.record(make_record(payload={"answer_format": {"conflict": original}}))
    report = store.sweep_for_leaks([("corpus/x.md", original)])
    assert not report.clean
    assert report.hits[0].kind == "ngram"


def test_sweep_detects_banned_literal(store, banned):
    store.record(make_record(payload={"answer_format": {"reason": "H社 요구"}}))
    report = store.sweep_for_leaks(
        [], banned_literals=banned.literals, banned_patterns=banned.patterns
    )
    assert not report.clean
    assert any(h.kind == "banned_literal" for h in report.banned_hits)


def test_sweep_detects_banned_pattern(store, banned):
    store.record(make_record(payload={"answer_format": {"reason": "REQ-4412"}}))
    report = store.sweep_for_leaks([], banned_literals=(), banned_patterns=banned.patterns)
    assert any(h.kind == "banned_pattern" for h in report.banned_hits)


def test_sweep_defeats_newline_escaping(store):
    """🔴 저장된 JSON 의 `\\n` 이스케이프가 공백 정규화를 빠져나가면 대조가 헐거워진다."""
    original = "세션 최대 유지시간은 여덟 시간으로 제한한다"
    store.record(
        make_record(
            payload={"answer_format": {"reason": "세션    최대\n유지시간은 여덟 시간으로 제한한다"}}
        )
    )
    report = store.sweep_for_leaks([("corpus/x.md", original)])
    assert not report.clean


def test_sweep_records_timing(store):
    store.record(make_record())
    report = store.sweep_for_leaks([("a", "가 나 다 라 마 바")])
    assert report.elapsed_seconds >= 0
    assert report.ngram_size == 5


# ══════════════════════════════════════════════════════════════════════
# 미러링 — 유일한 fail-open (BR-A-05)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mirror_is_skipped_outside_broker_mode(store):
    assert await store.mirror(make_record()) is False
    assert store.mirror_failures == 0


@pytest.mark.asyncio
async def test_mirror_failure_does_not_raise(monkeypatch, cfg):
    """미러링 실패가 질의를 죽이면 데모가 멈춘다. 로컬 SQLite 가 원본이다."""
    monkeypatch.setenv("AGENT_TRANSPORT", "broker")
    monkeypatch.setenv("BROKER_API_URL", "https://example.invalid")
    monkeypatch.setenv("BROKER_API_KEY", "k")
    from mesh.config import Config

    log = AuditLog(Config.load())
    try:
        assert await log.mirror(make_record()) is False
        assert log.mirror_failures == 1
    finally:
        log.close()


# ══════════════════════════════════════════════════════════════════════
# 보조
# ══════════════════════════════════════════════════════════════════════


def test_payload_preview_truncates():
    long_payload = {"k": "x" * 1000}
    out = audit_mod.payload_preview(long_payload, limit=50)
    assert len(out) < 100
    assert out.endswith("…")


def test_payloads_returns_raw_json(store):
    store.record(make_record())
    rid, blob = store.payloads()[0]
    assert rid == "aud_test0000000000000001"
    assert json.loads(blob)["task"] == "constraint_conflict_check"

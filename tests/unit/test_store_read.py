"""Store 파일 읽기 · 경로 선택 · 지목 목록 (U2 Day 3).

가장 중요한 셋:
  - 경로 탈출과 scope 위반을 막는다 (2중 검사)
  - `select_paths()` 프롬프트에 **파일 본문이 없다**
  - `list_agents()` 결과에 `Session.focus` 원문이 **없다**
"""

from __future__ import annotations

from datetime import date

import pytest

from mesh.exceptions import ExaoneUnavailable, ScopeViolationError
from mesh.schemas import Freshness, Tier, VerifiedQA
from mesh.store import (
    HEADER_SCAN_LINES,
    MAX_FILE_BYTES,
    RUN_LOG_TAIL_LINES,
    KnowledgeStore,
    chunk_id_for,
    parse_date,
    parse_header,
    read_body,
    source_kind_of,
)
from tests.fakes import FakeExaone


@pytest.fixture
def store(full_cfg):
    from mesh.config import DataBundle

    return KnowledgeStore(full_cfg, DataBundle(full_cfg))


# ══════════════════════════════════════════════════════════════════════
# 문서 종류 판정 (BR-S-09)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "rel,kind,formality",
    [
        ("corpus/kim/docs/auth-design.md", "design_doc", "official"),
        ("corpus/kim/notes/2025-11-auth.md", "note", "informal"),
        ("corpus/park/scripts/preprocess_v3.py", "script", "official"),
        ("corpus/park/configs/v3.yaml", "config", "official"),
        ("corpus/park/runs/2026-08-19/train.log", "run_log", "official"),
        ("corpus/customer-H/req-spec-2026H.md", "spec", "official"),
    ],
)
def test_source_kind_from_path(rel, kind, formality):
    assert source_kind_of(rel) == (kind, formality)


def test_notes_are_informal():
    """🔴 시나리오 3 의 핵심 — 개인 메모(비공식) vs 설계 리뷰(공식)."""
    assert source_kind_of("corpus/kim/notes/x.md")[1] == "informal"
    assert source_kind_of("corpus/choi/docs/auth-review.md")[1] == "official"


def test_unknown_path_falls_back_by_extension():
    assert source_kind_of("weird/place/thing.log")[0] == "run_log"
    assert source_kind_of("weird/place/thing.py")[0] == "script"
    assert source_kind_of("weird/place/thing.yaml")[0] == "config"
    assert source_kind_of("weird/place/thing.md")[0] == "design_doc"


def test_chunk_id_is_deterministic():
    """`prepare` 와 `send` 사이를 건너가므로 매번 같아야 한다."""
    assert chunk_id_for("corpus/a.md") == chunk_id_for("corpus/a.md")
    assert chunk_id_for("corpus/a.md") != chunk_id_for("corpus/b.md")


# ══════════════════════════════════════════════════════════════════════
# 헤더 파싱
# ══════════════════════════════════════════════════════════════════════


def test_parse_markdown_front_matter():
    meta = parse_header("---\ntitle: 인증 설계\nas_of: 2026-06-01\nformality: informal\n---\n본문")
    assert meta["title"] == "인증 설계"
    assert meta["as_of"] == "2026-06-01"
    assert meta["formality"] == "informal"


def test_parse_comment_header():
    meta = parse_header("#!/usr/bin/env python3\n# title: 전처리 v3\n# as_of: 2026-08-19\n")
    assert meta["title"] == "전처리 v3"


def test_header_ignores_security_level():
    """등급 판정은 `classifier.py` 의 일이다. 두 곳에서 해석하면 한쪽이 느슨해진다."""
    meta = parse_header("---\n보안등급: 기밀\ntitle: x\n---")
    assert "보안등급" not in meta
    assert "tier" not in meta


def test_header_is_only_read_near_the_top():
    body = "\n".join(["줄"] * (HEADER_SCAN_LINES + 5)) + "\ntitle: 가짜 제목"
    assert "title" not in parse_header(body)


def test_invalid_formality_is_ignored():
    assert "formality" not in parse_header("formality: 대충\n")


@pytest.mark.parametrize(
    "raw,want", [("2026-07-15", date(2026, 7, 15)), ("없음", None), (None, None)]
)
def test_parse_date(raw, want):
    assert parse_date(raw) == want


# ══════════════════════════════════════════════════════════════════════
# 본문 읽기 (BR-S-10)
# ══════════════════════════════════════════════════════════════════════


def test_run_log_reads_the_tail(tmp_path):
    """로그는 뒤가 중요하다."""
    path = tmp_path / "train.log"
    path.write_text("\n".join(f"line {i}" for i in range(1000)), encoding="utf-8")
    text, truncated = read_body(path, "run_log", max_bytes=MAX_FILE_BYTES)
    assert truncated
    assert "line 999" in text
    assert "line 0\n" not in text
    assert len(text.splitlines()) == RUN_LOG_TAIL_LINES


def test_document_reads_the_head(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("첫 줄\n" + "가" * 1000, encoding="utf-8")
    text, truncated = read_body(path, "design_doc", max_bytes=200)
    assert truncated
    assert text.startswith("첫 줄")
    assert len(text.encode()) <= 200


def test_short_file_is_not_truncated(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("짧다", encoding="utf-8")
    text, truncated = read_body(path, "design_doc", max_bytes=MAX_FILE_BYTES)
    assert not truncated
    assert text == "짧다"


# ══════════════════════════════════════════════════════════════════════
# read() — 2중 검사 (BR-S-03)
# ══════════════════════════════════════════════════════════════════════


def test_read_returns_chunks_without_tier(store):
    """⚠️ `Chunk.tier` 를 채우지 않는다. 등급 판정은 Gatekeeper 의 일이다."""
    chunks = store.read(["corpus/kim/docs/auth-design.md"], "person:kim")
    assert len(chunks) == 1
    assert chunks[0].tier is None
    assert chunks[0].display_title
    assert chunks[0].internal_path == "corpus/kim/docs/auth-design.md"
    assert chunks[0].text


def test_read_parses_metadata(store):
    chunk = store.read(["corpus/kim/notes/2025-11-auth.md"], "person:kim")[0]
    assert chunk.source_kind == "note"
    assert chunk.formality == "informal"
    assert chunk.as_of is not None


@pytest.mark.parametrize(
    "escape",
    ["../../../etc/passwd", "/etc/passwd", "~/secrets.txt", "corpus/../../outside.md", ""],
)
def test_read_rejects_path_escape(store, escape):
    """세션 JSON 은 사람이 편집한다. 여기에 `../../../etc/passwd` 가 들어갈 수 있다."""
    assert store.read([escape], "person:kim") == []


def test_read_rejects_out_of_scope_path(store):
    """🔴 에이전트 간 지식 격리 — 김책임 Agent 가 박선임 파일을 못 읽는다."""
    with pytest.raises(ScopeViolationError, match="knowledge_scope"):
        store.read(["corpus/park/scripts/preprocess_v3.py"], "person:kim")


def test_scope_violation_is_raised_not_skipped(store):
    """읽지 못한 파일은 건너뛰지만 **scope 위반은 올린다** —
    설정 오류이거나 공격이므로 조용히 넘기지 않는다."""
    with pytest.raises(ScopeViolationError):
        store.read(["corpus/kim/docs/auth-design.md", "corpus/park/configs/v3.yaml"], "person:kim")


def test_kim_can_read_customer_docs(store):
    """김책임은 협의 담당이므로 `corpus/customer-H/**` 가 scope 안이다."""
    chunks = store.read(["corpus/customer-H/req-spec-2026H.md"], "person:kim")
    assert len(chunks) == 1


def test_missing_file_is_skipped_not_fatal(store):
    """세션 JSON 이 오래되어 파일이 지워졌을 수 있다. 그 하나 때문에 질의가 죽으면 안 된다."""
    chunks = store.read(["corpus/kim/docs/auth-design.md", "corpus/kim/docs/gone.md"], "person:kim")
    assert len(chunks) == 1


def test_read_of_run_log_within_scope(store):
    chunks = store.read(["corpus/park/runs/2026-08-19/train.log"], "person:park")
    assert chunks[0].source_kind == "run_log"
    assert "atlas_ml" in chunks[0].text


# ══════════════════════════════════════════════════════════════════════
# 후보 경로 (BR-S-01)
# ══════════════════════════════════════════════════════════════════════


def test_candidates_include_run_logs(store):
    """🔴 `train.log` 는 `recent_runs[].log` 에만 있다. 빠뜨리면
    "지금 학습 중" 답이 불가능해진다."""
    session = store.load_session("person:park")
    candidates = store.candidate_paths(session)
    assert "corpus/park/runs/2026-08-19/train.log" in candidates
    assert "corpus/park/scripts/preprocess_v3.py" in candidates


def test_candidates_are_deduplicated(store):
    session = store.load_session("person:choi")
    candidates = store.candidate_paths(session)
    assert len(candidates) == len(set(candidates))


def test_no_global_scan_in_source():
    """BR-S-01 — 전역 파일 검색이 없다."""
    from pathlib import Path

    src = Path("src/mesh/store.py").read_text(encoding="utf-8")
    assert "rglob" not in src
    assert "glob(" not in src


# ══════════════════════════════════════════════════════════════════════
# select_paths (BR-S-02)
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def store_with_exaone(full_cfg):
    from mesh.config import DataBundle

    def build(exaone):
        return KnowledgeStore(full_cfg, DataBundle(full_cfg), exaone=exaone)

    return build


async def test_select_paths_prompt_has_no_file_content(store_with_exaone):
    """🔴 경로 선택은 등급 판정 **전**에 일어난다. 아직 어떤 파일이 기밀인지
    모르는 시점에 본문을 EXAONE 에 보내는 것은 순서가 뒤바뀐 것이다."""
    captured: list[str] = []

    class Capturing(FakeExaone):
        async def complete_json(self, system, user, *, name="generic", max_tokens=800):
            captured.append(user)
            return await super().complete_json(system, user, name=name, max_tokens=max_tokens)

    store = store_with_exaone(Capturing(selected=[0]))
    session = store.load_session("person:kim")
    await store.select_paths(session, "세션 바인딩 충돌?")

    prompt = captured[0]
    assert "corpus/customer-H/req-spec-2026H.md" in prompt  # 경로는 있다
    for leak in ("REQ-4412", "EAP-AKA", "CTR-204817", "12억", "SDK v3.2 는 토큰"):
        assert leak not in prompt, leak


async def test_select_paths_returns_only_chosen(store_with_exaone):
    store = store_with_exaone(FakeExaone(selected=[1]))
    session = store.load_session("person:kim")
    picked = await store.select_paths(session, "질문")
    assert picked == ["corpus/kim/docs/auth-design.md"]


async def test_select_paths_ignores_out_of_range_indices(store_with_exaone):
    store = store_with_exaone(FakeExaone(selected=[0, 99, -5]))
    session = store.load_session("person:kim")
    picked = await store.select_paths(session, "질문")
    assert picked == ["corpus/customer-H/req-spec-2026H.md"]


async def test_select_paths_falls_back_to_all_on_failure(store_with_exaone):
    """실패 시 후보 전체 — 더 많이 읽고 게이트키퍼가 막게 한다 (fail closed 방향)."""
    store = store_with_exaone(FakeExaone(fail={"select_paths": ExaoneUnavailable("타임아웃")}))
    session = store.load_session("person:kim")
    picked = await store.select_paths(session, "질문")
    assert set(picked) == set(store.candidate_paths(session))


async def test_select_paths_falls_back_on_bad_shape(store_with_exaone):
    store = store_with_exaone(FakeExaone(selected=None))
    session = store.load_session("person:kim")
    assert len(await store.select_paths(session, "질문")) > 1


async def test_select_paths_falls_back_when_nothing_chosen(store_with_exaone):
    store = store_with_exaone(FakeExaone(selected=[]))
    session = store.load_session("person:kim")
    assert len(await store.select_paths(session, "질문")) > 1


async def test_single_candidate_skips_the_model(store_with_exaone):
    ex = FakeExaone(selected=[0])
    store = store_with_exaone(ex)
    session = store.load_session("person:choi")
    trimmed = session.model_copy(
        update={"open_paths": ("corpus/choi/docs/auth-review.md",), "recent_edits": ()}
    )
    assert await store.select_paths(trimmed, "질문") == ["corpus/choi/docs/auth-review.md"]
    assert ex.count("select_paths") == 0


async def test_no_exaone_reads_everything(store):
    session = store.load_session("person:kim")
    assert set(await store.select_paths(session, "질문")) == set(store.candidate_paths(session))


# ══════════════════════════════════════════════════════════════════════
# verified QA (BR-S-05)
# ══════════════════════════════════════════════════════════════════════


def test_verified_chunks_preserve_tier(store):
    """🔴 승인은 답변의 정확성을 검증한 것이고 등급을 낮춘 것이 아니다."""
    from datetime import datetime

    qa = VerifiedQA(
        qa_id="qa_1",
        question="세션 바인딩?",
        answer="바인딩이 필요합니다",
        tier=Tier.SECRET,
        verified_by="person:kim",
        verified_at=datetime(2026, 8, 19, 14, 0),
    )
    store.append_verified("person:kim", qa)
    session = store.load_session("person:kim")
    chunks = store.verified_chunks(session)
    assert len(chunks) == 1
    assert chunks[0].tier is Tier.SECRET
    assert "바인딩이 필요합니다" in chunks[0].text


def test_verified_chunks_are_newest_first(store):
    from datetime import datetime

    for i in range(5):
        store.append_verified(
            "person:kim",
            VerifiedQA(
                qa_id=f"qa_{i}",
                question="q",
                answer=f"answer {i}",
                tier=Tier.INTERNAL,
                verified_by="person:kim",
                verified_at=datetime(2026, 8, 19, 10 + i, 0),
            ),
        )
    chunks = store.verified_chunks(store.load_session("person:kim"), limit=2)
    assert [c.text for c in chunks] == ["answer 4", "answer 3"]


# ══════════════════════════════════════════════════════════════════════
# list_agents (BR-S-06, FR-31)
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def listing(full_cfg):
    from mesh.audit import AuditLog
    from mesh.config import DataBundle
    from mesh.gatekeeper import Gatekeeper
    from tests.fakes import FakeBroker

    def build(*, topic="인증 관련 작업", fail=None):
        data = DataBundle(full_cfg)
        audit = AuditLog(full_cfg)
        exaone = FakeExaone(topic=topic, fail=fail or {})
        gk = Gatekeeper(full_cfg, data, exaone, FakeBroker(), audit)
        store = KnowledgeStore(full_cfg, data, gatekeeper=gk, audit=audit)
        return store, audit, exaone

    return build


async def test_list_agents_never_leaks_session_text(listing):
    """🔴 이 화면은 인증 없이 보인다. 여기서 고객사명이 새면
    게이트키퍼를 우회한 유출이다."""
    store, audit, _ = listing()
    try:
        cards = await store.list_agents()
    finally:
        audit.close()

    blob = "".join(c.model_dump_json() for c in cards)
    for leak in ("고객사 H", "SDK v3.2", "atlas-ml", "레거시 SSO", "재학습", "릴리스 파이프라인"):
        assert leak not in blob, leak


async def test_focus_summary_is_a_closed_vocabulary_label(listing):
    store, audit, _ = listing()
    try:
        cards = await store.list_agents()
    finally:
        audit.close()
    from mesh.gatekeeper import FOCUS_TOPICS

    for card in cards:
        if card.current_focus_summary:
            assert card.current_focus_summary.removesuffix(" 중") in FOCUS_TOPICS


async def test_focus_summary_is_none_on_failure(listing):
    """변환 실패 시 표시하지 않는다. **원문 폴백은 없다** (fail closed)."""
    store, audit, _ = listing(fail={"focus_topic": ExaoneUnavailable("타임아웃")})
    try:
        cards = await store.list_agents()
    finally:
        audit.close()
    assert all(c.current_focus_summary is None for c in cards)


async def test_focus_summary_is_none_when_out_of_vocabulary(listing):
    store, audit, _ = listing(topic="고객사 H 인증 검토")
    try:
        cards = await store.list_agents()
    finally:
        audit.close()
    assert all(c.current_focus_summary is None for c in cards)


async def test_activity_status_maps_from_freshness(listing):
    store, audit, _ = listing()
    try:
        cards = {c.entity_id: c for c in await store.list_agents()}
    finally:
        audit.close()
    assert cards["person:kim"].activity_status == "active"  # 14:31, now 14:35
    assert cards["person:choi"].activity_status == "away"  # 12:30 -> STALE
    assert cards["person:choi"].away_minutes == 125
    assert cards["person:kim"].away_minutes is None
    assert cards["person:choi"].freshness is Freshness.STALE


async def test_expertise_is_always_present(listing):
    """`Disclose.expertise` 가 `Literal[True]` 다 — 끄면 지목이 불가능해진다."""
    store, audit, _ = listing()
    try:
        cards = await store.list_agents()
    finally:
        audit.close()
    assert all(c.expertise for c in cards)


async def test_disclose_off_yields_none(listing, full_cfg):
    store, audit, _ = listing()
    try:
        from mesh.schemas import Disclose

        agent = store.data.agents["person:kim"]
        store.data.agents["person:kim"] = agent.model_copy(
            update={"disclose": Disclose(activity_status=False, current_focus=False)}
        )
        card = {c.entity_id: c for c in await store.list_agents()}["person:kim"]
    finally:
        audit.close()
    assert card.activity_status is None
    assert card.away_minutes is None
    assert card.current_focus_summary is None
    assert card.session_as_of is None
    assert card.expertise  # 끌 수 없다


async def test_focus_summary_is_cached_per_session_version(listing):
    store, audit, exaone = listing()
    try:
        await store.list_agents()
        first = exaone.count("focus_topic")
        await store.list_agents()
        assert exaone.count("focus_topic") == first  # 재호출 없음
    finally:
        audit.close()


async def test_warmup_populates_the_cache(listing):
    store, audit, exaone = listing()
    try:
        await store.warm_focus_cache()
        warmed = exaone.count("focus_topic")
        assert warmed >= 1
        await store.list_agents()
        assert exaone.count("focus_topic") == warmed
    finally:
        audit.close()


async def test_question_count_and_daily_limit(listing):
    store, audit, _ = listing()
    try:
        for i in range(3):
            audit.record_local(
                actor="person:lee",
                target_entity_id="person:kim",
                tier=Tier.INTERNAL,
                reason_code="extraction_failed",
                question_sha256=f"h{i}",
            )
        card = {c.entity_id: c for c in await store.list_agents()}["person:kim"]
        assert card.question_count_today == 3
        assert not card.daily_limit_reached
    finally:
        audit.close()


async def test_agent_without_session_still_listed(listing, full_data_root):
    """담당 영역은 항상 공개이므로 세션이 없어도 지목이 가능해야 한다."""
    (full_data_root / "sessions" / "person_choi.json").unlink()
    store, audit, _ = listing()
    try:
        cards = {c.entity_id: c for c in await store.list_agents()}
    finally:
        audit.close()
    assert "person:choi" in cards
    assert cards["person:choi"].activity_status is None
    assert cards["person:choi"].expertise

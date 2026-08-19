"""KnowledgeStore 세션 로드 — Day 1 구현분.

핵심: 신선도 3단 판정과 `verified_qa` 등급 보존.

신선도 보정이 실제로 결과를 바꾼다:
    최민수 0.78 (STALE) x 0.8 = 0.62  ->  UNVERIFIED 배지
보정이 없으면 자동 응답(>=0.75)이었을 것이다. 2시간 전 상태로 답한 것이니
배지가 붙는 게 더 정직하다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mesh.config import Config, DataBundle
from mesh.exceptions import PathEscapeError
from mesh.schemas import Freshness, Tier, VerifiedQA
from mesh.store import (
    EXPIRED_AFTER,
    KnowledgeStore,
    SessionNotFound,
    activity_status,
    confidence_factor,
    elapsed_minutes,
    freshness,
)

REPO = Path(__file__).resolve().parents[2]
DEMO_NOW = "2026-08-19T14:35:00+09:00"


@pytest.fixture
def store(monkeypatch, mock_env) -> KnowledgeStore:
    """실제 세션 3개와 agents.yaml 을 쓰는 스토어."""
    for name in ("person_kim.json", "person_park.json", "person_choi.json"):
        (mock_env / "sessions" / name).write_bytes((REPO / "data" / "sessions" / name).read_bytes())
    monkeypatch.setenv("MESH_DEMO_NOW", DEMO_NOW)
    monkeypatch.chdir(REPO)  # config/agents.yaml 을 찾기 위해
    cfg = Config.load()
    return KnowledgeStore(cfg, DataBundle(cfg))


# ══════════════════════════════════════════════════════════════════════
# 세션 로드
# ══════════════════════════════════════════════════════════════════════


def test_loads_three_demo_sessions(store):
    for eid in ("person:kim", "person:park", "person:choi"):
        assert store.load_session(eid).entity_id == eid


def test_session_path_mapping(store):
    assert store.session_path("person:kim").name == "person_kim.json"


def test_missing_session_raises(store):
    with pytest.raises(SessionNotFound, match="세션 파일이 없다"):
        store.load_session("person:ghost")


def test_entity_id_mismatch_raises(store, mock_env):
    p = mock_env / "sessions" / "person_kim.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["entity_id"] = "person:someone_else"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SessionNotFound, match="entity_id 가 다르다"):
        store.load_session("person:kim")


def test_kim_session_content(store):
    s = store.load_session("person:kim")
    assert "고객사 H" in s.focus
    assert "corpus/customer-H/req-spec-2026H.md" in s.open_paths
    assert len(s.recent_edits) == 1
    assert s.recent_runs == ()


def test_park_session_has_running_job(store):
    """시나리오 2의 핵심 — 어떤 문서에도 없는 실시간 상태."""
    s = store.load_session("person:park")
    assert len(s.recent_runs) == 1
    run = s.recent_runs[0]
    assert run.status == "running"
    assert run.gpu == "cuda:0"
    assert run.eta == datetime.fromisoformat("2026-08-19T17:10:00+09:00")
    assert run.log == "corpus/park/runs/2026-08-19/train.log"


def test_park_dataset_inherits_secret_tier(store):
    """고객 로그에서 파생된 데이터셋은 원본의 등급을 물려받는다."""
    s = store.load_session("person:park")
    assert s.datasets[0].tier is Tier.SECRET
    assert s.datasets[0].derived_from == "customer-H session logs"
    assert s.datasets[0].rows == 420135


def test_all_session_paths_are_relative(store):
    """절대 경로를 저장하지 않는다 (NFR-PO-01, FR-22).
    다른 컴퓨터에서 그대로 동작해야 한다."""
    for eid in ("person:kim", "person:park", "person:choi"):
        s = store.load_session(eid)
        for p in s.open_paths:
            assert not p.startswith(("/", "~")), f"{eid}: 절대 경로 {p}"
            store.resolve(p)  # PathEscapeError 가 나면 실패
        for e in s.recent_edits:
            store.resolve(e.path)
        for r in s.recent_runs:
            if r.log:
                store.resolve(r.log)


def test_session_open_paths_exist_in_real_corpus():
    """세션이 가리키는 파일이 실제 코퍼스에 있어야 한다 — 데모가 깨지지 않게.

    `mock_env` 가 아니라 저장소의 `data/` 를 직접 본다.
    세션과 코퍼스는 함께 커밋되므로 정합성을 여기서 확인한다.
    """
    real_data = REPO / "data"
    missing: list[str] = []
    for name in ("person_kim.json", "person_park.json", "person_choi.json"):
        raw = json.loads((real_data / "sessions" / name).read_text(encoding="utf-8"))
        for rel in raw["open_paths"]:
            if not (real_data / rel).exists():
                missing.append(f"{name}: {rel}")
        for edit in raw.get("recent_edits") or ():
            if not (real_data / edit["path"]).exists():
                missing.append(f"{name}: {edit['path']}")
        for run in raw.get("recent_runs") or ():
            if run.get("log") and not (real_data / run["log"]).exists():
                missing.append(f"{name}: {run['log']}")
    assert not missing, "세션이 없는 파일을 가리킨다:\n  " + "\n  ".join(missing)


def test_path_escape_in_session_is_rejected(store, mock_env):
    """세션 JSON 은 사람이 편집하므로 탈출 시도가 들어올 수 있다."""
    p = mock_env / "sessions" / "person_kim.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["open_paths"] = ["../../../etc/passwd"]
    p.write_text(json.dumps(raw), encoding="utf-8")
    s = store.load_session("person:kim")
    with pytest.raises(PathEscapeError):
        store.resolve(s.open_paths[0])


# ══════════════════════════════════════════════════════════════════════
# mtime 재로드 — 데몬 없이 동작 (BR-S-08, FR-21)
# ══════════════════════════════════════════════════════════════════════


def test_cache_hit_returns_same_object(store):
    assert store.load_session("person:kim") is store.load_session("person:kim")


def test_mtime_change_triggers_reload(store, mock_env):
    """시연 중 JSON 을 편집하면 즉시 반영된다."""
    first = store.load_session("person:kim")

    p = mock_env / "sessions" / "person_kim.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["focus"] = "새로운 작업"
    p.write_text(json.dumps(raw), encoding="utf-8")
    import os

    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))

    second = store.load_session("person:kim")
    assert second is not first
    assert second.focus == "새로운 작업"


# ══════════════════════════════════════════════════════════════════════
# 신선도 3단 (BR-S-04) — 데모 시각 기준으로 kim/park LIVE, choi STALE
# ══════════════════════════════════════════════════════════════════════


def test_demo_sessions_have_expected_freshness(store):
    """MESH_DEMO_NOW=14:35 기준.

    kim  14:31:20  ->  4분 경과   LIVE
    park 14:33:05  ->  2분 경과   LIVE
    choi 12:30:00  -> 125분 경과  STALE  (시나리오 3 "자리 비움 2시간")
    """
    assert store.freshness_of(store.load_session("person:kim")) is Freshness.LIVE
    assert store.freshness_of(store.load_session("person:park")) is Freshness.LIVE
    assert store.freshness_of(store.load_session("person:choi")) is Freshness.STALE


def test_choi_away_minutes_is_about_two_hours(store):
    mins = elapsed_minutes(store.load_session("person:choi"), store.cfg.now())
    assert 120 <= mins <= 130


def _session_aged(minutes: float):
    from mesh.schemas import Session

    now = datetime.fromisoformat(DEMO_NOW)
    return Session(
        entity_id="person:x",
        updated_at=now - timedelta(minutes=minutes),
        focus="f",
        summary="s",
    ), now


@pytest.mark.parametrize(
    "minutes,expected",
    [
        (0, Freshness.LIVE),
        (14.9, Freshness.LIVE),
        (15, Freshness.STALE),  # 경계
        (125, Freshness.STALE),
        (23 * 60, Freshness.STALE),
        (24 * 60, Freshness.EXPIRED),  # 경계
        (48 * 60, Freshness.EXPIRED),
    ],
)
def test_freshness_boundaries(minutes, expected):
    session, now = _session_aged(minutes)
    assert freshness(session, now, stale_minutes=15) is expected


def test_freshness_is_monotonically_worse():
    """시간이 지날수록 나빠지기만 한다 (PB-S3 의 예제 버전)."""
    order = {Freshness.LIVE: 0, Freshness.STALE: 1, Freshness.EXPIRED: 2}
    prev = -1
    for minutes in (0, 5, 15, 60, 500, 24 * 60, 100 * 60):
        session, now = _session_aged(minutes)
        rank = order[freshness(session, now, stale_minutes=15)]
        assert rank >= prev
        prev = rank


def test_expired_after_is_24_hours():
    assert EXPIRED_AFTER == timedelta(hours=24)


# ══════════════════════════════════════════════════════════════════════
# 신뢰도 보정 — 실제로 결과를 바꾼다
# ══════════════════════════════════════════════════════════════════════


def test_live_session_does_not_reduce_confidence():
    assert confidence_factor(Freshness.LIVE, stale_factor=0.8) == 1.0


@pytest.mark.parametrize("fresh", [Freshness.STALE, Freshness.EXPIRED])
def test_stale_session_reduces_confidence(fresh):
    assert confidence_factor(fresh, stale_factor=0.8) == 0.8


def test_choi_confidence_crosses_the_auto_threshold(store):
    """시나리오 3: 최민수 0.78 이 보정으로 0.624 가 되어 UNVERIFIED 가 된다.

    보정이 없으면 자동 응답(>=0.75)이었다. 2시간 전 상태로 답한 것이니
    배지가 붙는 게 더 정직하다.
    """
    fresh = store.freshness_of(store.load_session("person:choi"))
    raw = 0.78
    adjusted = raw * confidence_factor(fresh, stale_factor=store.cfg.stale_confidence_factor)

    assert raw >= store.cfg.confidence_auto  # 보정 전에는 자동 응답
    assert adjusted < store.cfg.confidence_auto  # 보정 후에는 미검증
    assert adjusted >= store.cfg.confidence_escalate  # 에스컬레이션은 아니다
    assert round(adjusted, 3) == 0.624


def test_kim_confidence_unchanged(store):
    fresh = store.freshness_of(store.load_session("person:kim"))
    assert 0.82 * confidence_factor(fresh, stale_factor=0.8) == 0.82


# ══════════════════════════════════════════════════════════════════════
# 활동 상태
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "fresh,expected",
    [
        (Freshness.LIVE, "active"),
        (Freshness.STALE, "away"),
        (Freshness.EXPIRED, "offline"),
    ],
)
def test_activity_status_mapping(fresh, expected):
    assert activity_status(fresh) == expected


def test_no_separate_heartbeat_is_needed(store):
    """활동 상태는 세션 updated_at 에서 파생한다.
    별도 하트비트를 만들지 않는다 — 데몬 없이 동작해야 하므로 (FR-21)."""
    s = store.load_session("person:choi")
    assert activity_status(store.freshness_of(s)) == "away"


# ══════════════════════════════════════════════════════════════════════
# verified_qa — 등급 보존 (BR-S-05, Round 2 Q14)
# ══════════════════════════════════════════════════════════════════════


def test_no_verified_file_yields_empty(store):
    assert store.load_session("person:park").verified_qa == ()


def _qa(tier: Tier = Tier.INTERNAL, qa_id: str = "qa_001") -> VerifiedQA:
    return VerifiedQA(
        qa_id=qa_id,
        question="preprocess_v3 라벨 불균형 처리 방식",
        answer="RandomOverSampler(sampling_strategy=0.5) + class_weight='balanced_subsample'",
        tier=tier,
        verified_by="person:park",
        verified_at=datetime.fromisoformat("2026-08-19T14:36:00+09:00"),
        citations=("preprocess_v3.py",),
    )


def test_append_and_merge_verified(store):
    store.append_verified("person:park", _qa())
    s = store.load_session("person:park")
    assert len(s.verified_qa) == 1
    assert s.verified_qa[0].question == "preprocess_v3 라벨 불균형 처리 방식"


def test_verified_tier_is_preserved(store):
    """승인된 답변도 등급을 갖는다. 사람이 승인했다고 등급이 낮아지지 않는다.

    "사람이 승인했으니 그대로 내보내도 된다"가 되면 설계 §3.8이 금지한
    논리("구조 추출을 거쳤으니 무엇이든 보내도 된다")와 같은 구멍이 생긴다.
    """
    store.append_verified("person:park", _qa(tier=Tier.SECRET))
    assert store.load_session("person:park").verified_qa[0].tier is Tier.SECRET


def test_append_is_add_only(store):
    """기존 항목을 수정·삭제하지 않는다. 감사 흔적이 남는다."""
    store.append_verified("person:park", _qa(qa_id="qa_001"))
    store.append_verified("person:park", _qa(qa_id="qa_002"))
    ids = [q.qa_id for q in store.load_session("person:park").verified_qa]
    assert ids == ["qa_001", "qa_002"]


def test_append_invalidates_cache(store):
    before = store.load_session("person:park")
    store.append_verified("person:park", _qa())
    after = store.load_session("person:park")
    assert after is not before
    assert len(after.verified_qa) == 1


def test_verified_file_is_separate_from_session(store, mock_env):
    """세션은 데몬이 덮어쓰는 휘발성 상태다. 승인 QA 를 세션 안에 넣으면 사라진다."""
    store.append_verified("person:park", _qa())
    assert (mock_env / "verified" / "person_park.json").exists()
    session_raw = json.loads(
        (mock_env / "sessions" / "person_park.json").read_text(encoding="utf-8")
    )
    assert "verified_qa" not in session_raw


# ══════════════════════════════════════════════════════════════════════
# knowledge_scope — 에이전트 간 지식 격리 (BR-S-03)
# ══════════════════════════════════════════════════════════════════════


def test_kim_can_read_customer_docs(store):
    """협의 담당이므로 접근 범위에 포함된다."""
    assert store.in_scope("corpus/customer-H/req-spec-2026H.md", "person:kim")


def test_park_cannot_read_customer_docs(store):
    assert not store.in_scope("corpus/customer-H/req-spec-2026H.md", "person:park")


def test_kim_cannot_read_park_files(store):
    assert not store.in_scope("corpus/park/scripts/preprocess_v3.py", "person:kim")


def test_everyone_can_read_public(store):
    for eid in ("person:kim", "person:park", "person:choi"):
        assert store.in_scope("corpus/public/oauth-rfc-summary.md", eid)


def test_each_agent_can_read_own_files(store):
    assert store.in_scope("corpus/kim/notes/2025-11-auth.md", "person:kim")
    assert store.in_scope("corpus/park/configs/v3.yaml", "person:park")
    assert store.in_scope("corpus/choi/docs/auth-review.md", "person:choi")


# ══════════════════════════════════════════════════════════════════════
# Day 3 (B) 로 미룬 것들 — 조용히 None 을 반환하지 않는다
# ══════════════════════════════════════════════════════════════════════


def test_read_is_not_implemented_yet(store):
    with pytest.raises(NotImplementedError, match="Day 3"):
        store.read(["corpus/kim/docs/auth-design.md"], "person:kim")


async def test_select_paths_is_not_implemented_yet(store):
    with pytest.raises(NotImplementedError, match="Day 3"):
        await store.select_paths(store.load_session("person:kim"), "질문")


async def test_list_agents_is_not_implemented_yet(store):
    with pytest.raises(NotImplementedError, match="Day 3"):
        await store.list_agents()


# ══════════════════════════════════════════════════════════════════════
# 경계 — store 는 원문을 U1 에만 넘긴다
# ══════════════════════════════════════════════════════════════════════


def test_store_does_not_do_global_scan():
    """검색이 아니라 지목이다 (BR-S-01). 전역 스캔 코드가 없어야 한다."""
    src = (REPO / "src" / "mesh" / "store.py").read_text(encoding="utf-8")
    for pattern in ('rglob("**/*")', "rglob('**/*')", 'glob("**/*")', "walk("):
        assert pattern not in src, f"전역 스캔 흔적: {pattern}"


def test_store_does_not_classify():
    """등급 판정은 Gatekeeper 의 일이다. store 는 tier 를 채우지 않는다."""
    src = (REPO / "src" / "mesh" / "store.py").read_text(encoding="utf-8")
    assert "rule_tier" not in src
    assert "classify" not in src

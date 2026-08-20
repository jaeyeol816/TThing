"""인박스 — 3버튼 상태 전이와 환류.

가장 중요한 셋:
  - 승인 시 `VerifiedQA` 로 환류하며 **`tier` 를 보존한다** (BR-I-02)
  - `not_me` 는 **자동 재지목하지 않는다** (BR-I-03)
  - `UPDATE` 가 감사 흔적(`draft_*`·`situation`)을 건드리지 않는다
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from mesh import inbox as inbox_mod
from mesh.api_models import ResolveRequest
from mesh.audit import AuditLog
from mesh.inbox import ACTION_STATUS, MUTABLE_COLUMNS, Inbox
from mesh.schemas import Citation, EscalationDraft, Tier

DRAFT = EscalationDraft(
    summary="세션 바인딩 요구와 현재 갱신 방식의 정합성 확인이 필요합니다",
    situation=["근거: 요구사항명세서 (2026-07-15)", "세션: 11:02 에 파일을 수정했습니다"],
    draft_answer="바인딩을 추가하고 토큰 수명을 단축하는 방향으로 검토했습니다",
    already_answered=["기법 질문은 Agent 가 이미 답변했습니다"],
)
CITATION = Citation(ref="REQ_A", display_title="요구사항명세서", tier=Tier.SECRET)


@pytest.fixture
def inbox(full_cfg):
    audit = AuditLog(full_cfg)
    yield Inbox(full_cfg, audit)
    audit.close()


def add(inbox: Inbox, *, owner="person:kim", thread="req_1", tier=Tier.SECRET):
    return inbox.add(
        owner_entity_id=owner,
        asker="person:lee",
        thread_id=thread,
        question_summary="요구와 갱신 방식이 충돌하나요?",
        draft=DRAFT,
        tier=tier,
        citations=(CITATION,),
    )


# ══════════════════════════════════════════════════════════════════════
# 추가와 조회
# ══════════════════════════════════════════════════════════════════════


def test_add_and_read_back(inbox):
    item = add(inbox)
    got = inbox.get(item.item_id)
    assert got is not None
    assert got.status == "open"
    assert got.draft.summary == DRAFT.summary
    assert got.draft.situation == DRAFT.situation
    assert got.draft.already_answered == DRAFT.already_answered
    assert got.citations[0].display_title == "요구사항명세서"
    assert got.tier is Tier.SECRET


def test_inbox_carries_four_things(inbox):
    """질문 원문만 던지면 알림이 하나 늘 뿐이다."""
    got = inbox.get(add(inbox).item_id)
    assert got.question_summary
    assert got.draft.situation
    assert got.draft.draft_answer
    assert got.draft.already_answered  # "자기가 답할 조각만" 보게 하는 장치


def test_list_for_filters_by_owner(inbox):
    add(inbox, owner="person:kim")
    add(inbox, owner="person:park")
    assert len(inbox.list_for("person:kim")) == 1
    assert len(inbox.list_for("person:choi")) == 0


def test_list_for_filters_by_status(inbox):
    a = add(inbox)
    add(inbox)
    inbox.resolve(a.item_id, ResolveRequest(action="approve"))
    assert len(inbox.list_for("person:kim", status="open")) == 1
    assert len(inbox.list_for("person:kim", status="approved")) == 1


def test_count_open(inbox):
    add(inbox)
    add(inbox)
    assert inbox.count_open("person:kim") == 2


def test_thread_groups_two_targets(inbox):
    """🔴 2명 지목 시 같은 스레드 — 한쪽이 해결하면 다른 쪽에 표시된다 (BR-I-04)."""
    add(inbox, owner="person:kim", thread="req_shared")
    add(inbox, owner="person:choi", thread="req_shared")
    add(inbox, owner="person:kim", thread="req_other")

    group = inbox.thread("req_shared")
    assert len(group) == 2
    assert {i.owner_entity_id for i in group} == {"person:kim", "person:choi"}


def test_get_returns_none_for_unknown(inbox):
    assert inbox.get("inb_nope") is None


# ══════════════════════════════════════════════════════════════════════
# 3버튼 (BR-I-01)
# ══════════════════════════════════════════════════════════════════════


def test_approve_keeps_the_draft_as_answer(inbox):
    item = inbox.resolve(add(inbox).item_id, ResolveRequest(action="approve"))
    assert item.status == "approved"
    assert item.resolution_text == DRAFT.draft_answer
    assert item.resolved_at is not None


def test_approve_with_edit_uses_edited_text(inbox):
    item = inbox.resolve(
        add(inbox).item_id,
        ResolveRequest(action="approve_with_edit", edited_text="수정된 답변입니다"),
    )
    assert item.status == "approved_with_edit"
    assert item.resolution_text == "수정된 답변입니다"


def test_not_me_records_redirect_without_auto_reask(inbox):
    """🔴 시스템이 자동으로 다시 묻지 않는다. 질문자가 다시 누른다 (BR-I-03)."""
    item = inbox.resolve(
        add(inbox).item_id, ResolveRequest(action="not_me", redirect_to="person:park")
    )
    assert item.status == "redirected"
    assert item.redirect_to == "person:park"
    # 새 항목이 자동으로 만들어지지 않는다
    assert inbox.count_open("person:park") == 0


def test_all_three_actions_map_to_a_status():
    assert set(ACTION_STATUS) == {"approve", "approve_with_edit", "not_me"}


def test_resolve_of_unknown_item_raises(inbox):
    with pytest.raises(KeyError):
        inbox.resolve("inb_nope", ResolveRequest(action="approve"))


def test_double_resolve_is_rejected(inbox):
    """재해결은 감사 흔적을 흐린다."""
    item = add(inbox)
    inbox.resolve(item.item_id, ResolveRequest(action="approve"))
    with pytest.raises(ValueError, match="이미 해결된"):
        inbox.resolve(item.item_id, ResolveRequest(action="approve"))


def test_approve_with_edit_requires_text():
    with pytest.raises(ValueError, match="edited_text"):
        ResolveRequest(action="approve_with_edit")


def test_not_me_requires_redirect_target():
    with pytest.raises(ValueError, match="redirect_to"):
        ResolveRequest(action="not_me")


def test_not_me_validates_entity_id_format():
    with pytest.raises(ValueError, match="형식"):
        ResolveRequest(action="not_me", redirect_to="박선영")


# ══════════════════════════════════════════════════════════════════════
# 환류 (BR-I-02)
# ══════════════════════════════════════════════════════════════════════


def test_approve_produces_verified_qa_preserving_tier(inbox):
    """🔴 승인은 답변의 정확성을 검증한 것이고 **등급을 낮춘 것이 아니다**."""
    item = inbox.resolve(add(inbox, tier=Tier.SECRET).item_id, ResolveRequest(action="approve"))
    qa = inbox.to_verified_qa(item)
    assert qa is not None
    assert qa.tier is Tier.SECRET
    assert qa.answer == DRAFT.draft_answer
    assert qa.verified_by == "person:kim"
    assert qa.citations == ("REQ_A",)


def test_edited_answer_is_what_gets_stored(inbox):
    item = inbox.resolve(
        add(inbox).item_id,
        ResolveRequest(action="approve_with_edit", edited_text="담당자가 고친 답"),
    )
    assert inbox.to_verified_qa(item).answer == "담당자가 고친 답"


def test_not_me_does_not_feed_back(inbox):
    """담당자가 답을 준 것이 아니다."""
    item = inbox.resolve(
        add(inbox).item_id, ResolveRequest(action="not_me", redirect_to="person:park")
    )
    assert inbox.to_verified_qa(item) is None


def test_open_item_does_not_feed_back(inbox):
    assert inbox.to_verified_qa(add(inbox)) is None


def test_internal_tier_is_preserved_too(inbox):
    item = inbox.resolve(add(inbox, tier=Tier.INTERNAL).item_id, ResolveRequest(action="approve"))
    assert inbox.to_verified_qa(item).tier is Tier.INTERNAL


# ══════════════════════════════════════════════════════════════════════
# 감사 흔적 보존
# ══════════════════════════════════════════════════════════════════════


def test_update_touches_only_mutable_columns():
    """🔴 `draft_*`·`situation_json`·`citations_json` 은 감사 흔적이다.

    "무엇을 근거로 이 초안이 만들어졌나"를 나중에 확인할 수 있어야 한다.
    """
    src = Path(inspect.getfile(inbox_mod)).read_text(encoding="utf-8")
    for match in re.finditer(r"UPDATE\s+inbox\s+SET\s+(.+?)WHERE", src, re.IGNORECASE | re.DOTALL):
        assigned = set(re.findall(r"(\w+)\s*=\s*\?", match.group(1)))
        assert assigned <= MUTABLE_COLUMNS, assigned


def test_no_delete_from_inbox():
    src = Path(inspect.getfile(inbox_mod)).read_text(encoding="utf-8")
    assert not re.search(r"\bDELETE\s+FROM\s+inbox\b", src, re.IGNORECASE)


def test_draft_survives_resolution(inbox):
    item = add(inbox)
    resolved = inbox.resolve(item.item_id, ResolveRequest(action="approve"))
    assert resolved.draft.situation == DRAFT.situation
    assert resolved.draft.summary == DRAFT.summary
    assert resolved.citations == (CITATION,)


def test_inbox_shares_the_audit_connection(full_cfg):
    """DB 파일을 나누면 "한 파일만 지우면 증거가 반쪽"이 된다."""
    audit = AuditLog(full_cfg)
    try:
        box = Inbox(full_cfg, audit)
        add(box)
        # 같은 커넥션이므로 audit 쪽에서 바로 보인다
        rows = audit.connection.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
        assert rows == 1
    finally:
        audit.close()

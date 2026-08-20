"""에스컬레이션 인박스 — 담당자가 실제로 처리하는 곳 (FR-38, FR-39, BR-I-*).

이 화면이 이 프로젝트의 성패를 가른다.

> 담당자에게 질문 원문만 던지면 **알림이 하나 늘 뿐이다.**
> 요약·근거·초안이 함께 가야 처리 비용이 몇 분에서 몇 초로 떨어진다.

그래서 인박스 항목은 4개를 담는다.

  1. `question_summary`   3초에 파악할 형태
  2. `draft.situation`     지금까지 찾은 근거 (세션 사실 + 파일 인용)
  3. `draft.draft_answer`  그대로 승인할 수 있는 답변 문장
  4. `draft.already_answered`  Agent 가 이미 답한 조각

4번이 시나리오 2 의 장치다. "기법 질문은 Agent 가 이미 답변함"이 보이면
담당자는 **자기가 답해야 하는 조각만** 본다.

──────────────────────────────────────────────────────────────────────
자동 재지목을 하지 않는다 (BR-I-03)
──────────────────────────────────────────────────────────────────────

`not_me` 는 시스템이 자동으로 다시 묻지 않는다. 질문자 화면에 표시하고
**질문자가 다시 누르게** 한다.

    "김책임이 박선임을 지목했습니다  [ 박선임에게 다시 묻기 ]"

현실에서 벌어지는 일과 같고, **사람이 지목했으므로 정확하다** —
알고리즘 추정보다 낫다. 자동 재지목을 넣으면 오라우팅이 연쇄된다.

──────────────────────────────────────────────────────────────────────
`UPDATE` 의 범위를 좁힌다 (BR-I-04 주석)
──────────────────────────────────────────────────────────────────────

`status`/`resolved_at`/`resolution_text`/`redirect_to` **만** 수정한다.
`draft_*`·`situation_json`·`citations_json` 은 **감사 흔적**이다 —
"무엇을 근거로 이 초안이 만들어졌나"를 나중에 확인할 수 있어야 한다.
`tests/unit/test_inbox.py` 가 UPDATE 문의 대상 컬럼을 검사한다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import datetime

from mesh.api_models import InboxItem, ResolveRequest
from mesh.audit import AuditLog
from mesh.config import Config, get_logger, log_extra
from mesh.schemas import Citation, EscalationDraft, Tier, VerifiedQA

log = get_logger("inbox")

#: `UPDATE inbox` 가 건드릴 수 있는 컬럼의 **전체 집합**.
#: 여기 없는 컬럼은 감사 흔적이므로 고치지 않는다.
MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {"status", "resolved_at", "resolution_text", "redirect_to"}
)

#: `resolve()` 의 action -> 최종 status.
ACTION_STATUS: dict[str, str] = {
    "approve": "approved",
    "approve_with_edit": "approved_with_edit",
    "not_me": "redirected",
}


def new_item_id() -> str:
    return f"inb_{uuid.uuid4().hex[:20]}"


def new_qa_id() -> str:
    return f"qa_{uuid.uuid4().hex[:16]}"


class Inbox:
    """SQLite 인박스. `AuditLog` 의 커넥션을 공유한다.

    커넥션을 따로 열면 SQLite 락이 충돌하고, DB 파일을 나누면 "한 파일만 지우면
    증거가 반쪽"이 된다. 스키마 DDL 은 `audit.py` 한 곳에만 있다.
    """

    def __init__(self, cfg: Config, audit: AuditLog) -> None:
        self.cfg = cfg
        self._conn: sqlite3.Connection = audit.connection

    # ── 추가 (BR-I-04) ───────────────────────────────────────────────

    def add(
        self,
        *,
        owner_entity_id: str,
        asker: str,
        thread_id: str,
        question_summary: str,
        draft: EscalationDraft,
        tier: Tier,
        citations: Sequence[Citation] = (),
    ) -> InboxItem:
        """항목 추가.

        `thread_id` 는 요청 단위다 (`request_id`). 2명을 지목했으면 두 인박스에
        **같은 `thread_id`** 로 들어가고, 한쪽이 해결하면 다른 쪽에 그 사실이
        표시돼 중재를 유도한다 (BR-I-04).

        ⚠️ `tier` 를 보존한다 (BR-I-05). `situation` 과 `question_summary` 는
           신뢰 구역 안에서 만든 원문 기반 텍스트다 — 시나리오 2 의
           "13:47에 스크립트를 고쳤으니"가 그 예다. 클라우드 미러에는
           `tier == open` 인 항목만 전문을 올린다.
        """
        item = InboxItem(
            item_id=new_item_id(),
            at=self.cfg.now(),
            owner_entity_id=owner_entity_id,
            asker=asker,
            thread_id=thread_id,
            question_summary=question_summary,
            draft=draft,
            citations=tuple(citations),
            tier=tier,
            status="open",
        )
        self._conn.execute(
            "INSERT INTO inbox (item_id, at, owner_entity_id, asker, thread_id, "
            "question_summary, draft_summary, situation_json, draft_answer, "
            "already_answered_json, citations_json, tier, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
            (
                item.item_id,
                item.at.isoformat(),
                owner_entity_id,
                asker,
                thread_id,
                question_summary,
                draft.summary,
                json.dumps(list(draft.situation), ensure_ascii=False),
                draft.draft_answer,
                json.dumps(list(draft.already_answered), ensure_ascii=False),
                json.dumps([c.model_dump(mode="json") for c in item.citations], ensure_ascii=False),
                tier.value,
            ),
        )
        self._conn.commit()
        log.info(
            "에스컬레이션 추가",
            extra=log_extra(
                item_id=item.item_id,
                owner=owner_entity_id,
                thread_id=thread_id,
                tier=tier.value,
            ),
        )
        return item

    # ── 조회 ─────────────────────────────────────────────────────────

    def get(self, item_id: str) -> InboxItem | None:
        row = self._conn.execute("SELECT * FROM inbox WHERE item_id = ?", (item_id,)).fetchone()
        return _row_to_item(row) if row else None

    def list_for(
        self, owner_entity_id: str, *, status: str | None = None, limit: int = 50
    ) -> tuple[InboxItem, ...]:
        """담당자별 목록. `status` 로 필터할 수 있다."""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM inbox WHERE owner_entity_id = ? AND status = ? "
                "ORDER BY at DESC LIMIT ?",
                (owner_entity_id, status, int(limit)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM inbox WHERE owner_entity_id = ? ORDER BY at DESC LIMIT ?",
                (owner_entity_id, int(limit)),
            ).fetchall()
        return tuple(_row_to_item(r) for r in rows)

    def thread(self, thread_id: str) -> tuple[InboxItem, ...]:
        """같은 스레드의 항목 전체 (BR-I-04).

        UI 가 "다른 한 분이 이미 답했습니다"를 표시하는 근거다.
        """
        rows = self._conn.execute(
            "SELECT * FROM inbox WHERE thread_id = ? ORDER BY at", (thread_id,)
        ).fetchall()
        return tuple(_row_to_item(r) for r in rows)

    def count_open(self, owner_entity_id: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM inbox WHERE owner_entity_id = ? AND status = 'open'",
                (owner_entity_id,),
            ).fetchone()[0]
        )

    # ── 해결 (BR-I-01 ~ BR-I-03) ─────────────────────────────────────

    def resolve(self, item_id: str, request: ResolveRequest) -> InboxItem:
        """3버튼 처리.

        | action | status | 결과 |
        |---|---|---|
        | `approve` | `approved` | 초안을 그대로 답변으로 확정 |
        | `approve_with_edit` | `approved_with_edit` | `edited_text` 가 답변 |
        | `not_me` | `redirected` | `redirect_to` 표시. **자동 재지목 없음** |

        Raises:
            KeyError: 항목이 없다
            ValueError: 이미 해결된 항목 (재해결 금지 — 감사 흔적이 흐려진다)
        """
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"인박스 항목이 없다: {item_id}")
        if item.status != "open":
            raise ValueError(
                f"이미 해결된 항목이다: {item_id} (status={item.status}). "
                "재해결은 감사 흔적을 흐린다"
            )

        status = ACTION_STATUS[request.action]
        text = _resolution_text(request, item)

        # ⚠️ MUTABLE_COLUMNS 만 건드린다. draft_*·situation 은 감사 흔적이다.
        self._conn.execute(
            "UPDATE inbox SET status = ?, resolved_at = ?, resolution_text = ?, "
            "redirect_to = ? WHERE item_id = ?",
            (status, self.cfg.now().isoformat(), text, request.redirect_to, item_id),
        )
        self._conn.commit()
        log.info(
            "에스컬레이션 해결",
            extra=log_extra(item_id=item_id, action=request.action, status=status),
        )
        resolved = self.get(item_id)
        assert resolved is not None  # noqa: S101 — 방금 UPDATE 했다
        return resolved

    # ── 환류 (BR-I-02) ───────────────────────────────────────────────

    def to_verified_qa(self, item: InboxItem) -> VerifiedQA | None:
        """승인된 항목을 `VerifiedQA` 로 바꾼다.

        ⚠️ **`tier` 를 원 질의의 등급으로 보존한다** (BR-S-05).
           승인은 *답변의 정확성*을 검증한 것이고 *등급*을 낮춘 것이 아니다.
           이 QA 가 이후 Agent 호출에 동원될 때 다른 지식과 똑같이
           게이트키퍼를 통과해야 한다.

        `not_me` 는 환류하지 않는다 — 담당자가 답을 준 것이 아니다.
        """
        if item.status not in {"approved", "approved_with_edit"}:
            return None
        answer = (item.resolution_text or item.draft.draft_answer).strip()
        if not answer:
            return None
        return VerifiedQA(
            qa_id=new_qa_id(),
            question=item.question_summary,
            answer=answer,
            tier=item.tier,  # ⚠️ 보존
            verified_by=item.owner_entity_id,
            verified_at=item.resolved_at or self.cfg.now(),
            confidence=0.95,
            citations=tuple(c.ref for c in item.citations),
        )


# ══════════════════════════════════════════════════════════════════════
# 행 -> 모델
# ══════════════════════════════════════════════════════════════════════


def _row_to_item(row: sqlite3.Row) -> InboxItem:
    return InboxItem(
        item_id=row["item_id"],
        at=datetime.fromisoformat(row["at"]),
        owner_entity_id=row["owner_entity_id"],
        asker=row["asker"],
        thread_id=row["thread_id"],
        question_summary=row["question_summary"],
        draft=EscalationDraft(
            summary=row["draft_summary"],
            situation=tuple(json.loads(row["situation_json"])),
            draft_answer=row["draft_answer"],
            already_answered=tuple(json.loads(row["already_answered_json"])),
        ),
        citations=tuple(Citation.model_validate(c) for c in json.loads(row["citations_json"])),
        tier=Tier(row["tier"]),
        status=row["status"],
        resolved_at=(datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None),
        resolution_text=row["resolution_text"],
        redirect_to=row["redirect_to"],
    )


def _resolution_text(request: ResolveRequest, item: InboxItem) -> str | None:
    match request.action:
        case "approve":
            return item.draft.draft_answer
        case "approve_with_edit":
            return request.edited_text
        case "not_me":
            # 자동 재지목을 하지 않는다. 질문자가 다시 누른다 (BR-I-03).
            return f"담당이 아니라고 회신했습니다. {request.redirect_to} 를 지목했습니다."
    return None  # pragma: no cover — Literal 이 막는다

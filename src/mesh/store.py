"""Knowledge Store — 세션 + 파일 직접 읽기.

**인덱스 대신 세션이다.**

    [데몬 또는 수동 갱신] --> [개인 세션] --> [필요할 때 파일 직접 읽기]
       작업 상태 모니터링       메모리/JSON      경로를 이미 알고 있으므로
                                                인덱스 없이 바로 읽는다

인덱스는 항상 뒤처지지만 세션은 실시간이다. 그래서 "박선임이 지금 그 스크립트를
돌리고 있다" 같은 답이 가능해진다. 어떤 벡터 DB 도 이건 담지 못한다.

**검색이 아니라 지목이다** (BR-S-01). 질문이 오면 임베딩으로 청크를 뒤지지 않고,
세션이 이미 좁혀 놓은 후보 경로 중에서 고르고 그 파일만 읽는다.
세션에 없는 것은 못 찾는다 — 한계이자 "지금 이 사람의 관심사"라는 강력한 사전 필터다.

⚠️ 이 모듈은 원문을 읽어 **U1(Gatekeeper)에만** 넘긴다.
   등급 판정도 하지 않는다 (`Chunk.tier` 를 채우지 않는다) — 그건 Gatekeeper 의 일이다.
   경계 밖 클라이언트를 import 하지 않는다.

Day 1 상태: 세션 로드 · 신선도 · verified QA 병합 구현.
            파일 읽기(`read`)와 경로 선택(`select_paths`)은 Day 3 (B).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from mesh.config import Config, DataBundle, get_logger, log_extra, safe_resolve
from mesh.exceptions import MeshError
from mesh.schemas import (
    AgentCard,
    Chunk,
    DatasetInfo,
    EditInfo,
    Freshness,
    RunInfo,
    Session,
    VerifiedQA,
)

log = get_logger("store")

#: 세션이 `EXPIRED` 로 넘어가는 기준. 이걸 넘으면 실시간 주장에서 제외한다.
EXPIRED_AFTER = timedelta(hours=24)


class SessionNotFound(MeshError):
    """세션 파일이 없다.

    귀결: 해당 에이전트를 지목 목록에서 제외하거나 404.
    """


# ══════════════════════════════════════════════════════════════════════
# 신선도 (BR-S-04)
# ══════════════════════════════════════════════════════════════════════


def freshness(session: Session, now: datetime, *, stale_minutes: int) -> Freshness:
    """세션 신선도 3단 판정.

    | 경과 | Freshness | 신뢰도 | session_facts |
    |---|---|---|---|
    | < stale_minutes | LIVE    | 보정 없음 | 사용 |
    | < 24h           | STALE   | x0.8      | 사용 (시각 병기) |
    | >= 24h          | EXPIRED | x0.8      | **실시간 주장에서 제외** |

    `EXPIRED` 에서도 **파일은 그대로 읽는다.** 파일은 언제든 유효하고
    세션만 신뢰도를 깎는다. 이게 시나리오 3(자리에 없는 최민수도 답한다)이
    성립하는 이유다 (FR-18).
    """
    elapsed = now - session.updated_at
    if elapsed < timedelta(minutes=stale_minutes):
        return Freshness.LIVE
    if elapsed < EXPIRED_AFTER:
        return Freshness.STALE
    return Freshness.EXPIRED


def confidence_factor(fresh: Freshness, *, stale_factor: float) -> float:
    """신선도에 따른 신뢰도 보정 계수.

    실측 효과: 최민수(0.78, STALE) x 0.8 = 0.62 -> `UNVERIFIED` 배지.
    보정이 없으면 자동 응답(>=0.75)이었을 것이 배지가 붙는다.
    2시간 전 상태로 답한 것이니 더 정직하다.
    """
    return 1.0 if fresh is Freshness.LIVE else stale_factor


def elapsed_minutes(session: Session, now: datetime) -> int:
    return max(0, int((now - session.updated_at).total_seconds() // 60))


def activity_status(fresh: Freshness) -> str:
    return {
        Freshness.LIVE: "active",
        Freshness.STALE: "away",
        Freshness.EXPIRED: "offline",
    }[fresh]


# ══════════════════════════════════════════════════════════════════════
# KnowledgeStore
# ══════════════════════════════════════════════════════════════════════


class KnowledgeStore:
    """세션을 유지하고 지목된 경로의 파일만 읽는다."""

    def __init__(self, cfg: Config, data: DataBundle) -> None:
        self.cfg = cfg
        self.data = data
        self._cache: dict[str, tuple[float, Session]] = {}

    # ── 세션 로드 (Day 1) ────────────────────────────────────────────

    def session_path(self, entity_id: str) -> Path:
        """`person:kim` -> `data/sessions/person_kim.json`."""
        return self.cfg.sessions_root / f"{entity_id.replace(':', '_')}.json"

    def verified_path(self, entity_id: str) -> Path:
        return self.cfg.verified_root / f"{entity_id.replace(':', '_')}.json"

    def load_session(self, entity_id: str) -> Session:
        """세션 JSON + 승인된 QA 병합.

        `mtime` 을 비교해 변경됐으면 재로드한다 — 시연 중 JSON 을 편집하면
        즉시 반영된다. 데몬의 효과를 흉내 내는 가장 저렴한 방법이다 (BR-S-08).

        Raises:
            SessionNotFound: 세션 파일이 없을 때
        """
        path = self.session_path(entity_id)
        if not path.exists():
            raise SessionNotFound(f"세션 파일이 없다: {path.name}")

        mtime = path.stat().st_mtime
        cached = self._cache.get(entity_id)
        if cached and cached[0] == mtime:
            return cached[1]

        raw = json.loads(path.read_text(encoding="utf-8"))
        session = self._parse_session(raw, entity_id)
        session = session.model_copy(update={"verified_qa": self._load_verified(entity_id)})
        self._cache[entity_id] = (mtime, session)
        return session

    def _parse_session(self, raw: dict, entity_id: str) -> Session:
        if raw.get("entity_id") != entity_id:
            raise SessionNotFound(
                f"세션 파일의 entity_id 가 다르다: {raw.get('entity_id')!r} != {entity_id!r}"
            )
        return Session(
            entity_id=entity_id,
            updated_at=datetime.fromisoformat(raw["updated_at"]),
            focus=raw.get("focus", ""),
            summary=raw.get("summary", ""),
            open_paths=tuple(raw.get("open_paths") or ()),
            recent_edits=tuple(
                EditInfo(path=e["path"], at=datetime.fromisoformat(e["at"]))
                for e in raw.get("recent_edits") or ()
            ),
            recent_runs=tuple(
                RunInfo(
                    cmd=r["cmd"],
                    started_at=datetime.fromisoformat(r["started_at"]),
                    status=r["status"],
                    eta=datetime.fromisoformat(r["eta"]) if r.get("eta") else None,
                    gpu=r.get("gpu"),
                    log=r.get("log"),
                )
                for r in raw.get("recent_runs") or ()
            ),
            datasets=tuple(
                DatasetInfo(**{k: v for k, v in d.items()}) for d in raw.get("datasets") or ()
            ),
        )

    def _load_verified(self, entity_id: str) -> tuple[VerifiedQA, ...]:
        """승인된 Q&A 를 세션과 **별도 파일**에서 읽는다 (BR-S-05).

        세션은 데몬이 계속 덮어쓰는 휘발성 상태이므로 승인된 QA 를 세션 안에
        넣으면 사라진다.

        ⚠️ `tier` 를 보존한다. 승인된 답변은 사람이 검토했지만 여전히
           사내/기밀 내용을 담을 수 있고, 이후 Agent 호출에 동원될 때
           다른 지식과 똑같이 게이트키퍼를 통과해야 한다.
        """
        path = self.verified_path(entity_id)
        if not path.exists():
            return ()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            VerifiedQA(
                qa_id=i["qa_id"],
                question=i["question"],
                answer=i["answer"],
                tier=i["tier"],  # ⚠️ 보존 필수
                verified_by=i["verified_by"],
                verified_at=datetime.fromisoformat(i["verified_at"]),
                confidence=i.get("confidence", 0.95),
                citations=tuple(i.get("citations") or ()),
            )
            for i in raw.get("items") or ()
        )

    def append_verified(self, entity_id: str, qa: VerifiedQA) -> None:
        """승인된 QA 추가. **추가 전용** — 기존 항목을 수정·삭제하지 않는다.

        같은 질문에 대한 새 승인은 새 항목으로 쌓이고,
        조회 시 `verified_at` 이 최신인 것을 쓴다.
        """
        path = self.verified_path(entity_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"entity_id": entity_id, "items": []}
        )
        raw["items"].append(qa.model_dump(mode="json"))
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        self._cache.pop(entity_id, None)  # 다음 로드에서 병합되게
        log.info(
            "승인된 QA 저장",
            extra=log_extra(entity_id=entity_id, qa_id=qa.qa_id, tier=qa.tier.value),
        )

    def freshness_of(self, session: Session) -> Freshness:
        return freshness(session, self.cfg.now(), stale_minutes=self.cfg.session_stale_minutes)

    def resolve(self, rel: str) -> Path:
        """`${MESH_DATA_ROOT}` 기준으로 해석. 경로 탈출을 거부한다."""
        return safe_resolve(rel, self.cfg.data_root)

    def in_scope(self, rel: str, entity_id: str) -> bool:
        """`knowledge_scope` glob 매치. 에이전트 간 지식 격리 (BR-S-03)."""
        from fnmatch import fnmatch

        scopes = self.data.agent(entity_id).knowledge_scope
        return any(fnmatch(rel, pattern) for pattern in scopes)

    # ── 파일 읽기 (Day 3, B) ─────────────────────────────────────────

    async def select_paths(self, session: Session, question: str) -> list[str]:
        """`open_paths` 중 질문과 관련된 것을 고른다 (FR-17, BR-S-02).

        ⚠️ 프롬프트에 **파일 본문을 넣지 않는다.** 전달하는 것은
           경로 · display_title · 세션 focus/summary 뿐이다.

           본문을 넣으면 안 되는 이유: 경로 선택은 등급 판정 *전*에 일어난다.
           아직 어떤 파일이 기밀인지 모르는 시점에 본문을 EXAONE 에 보내는 것은
           순서가 뒤바뀐 것이다.

        출력은 **인덱스 배열**이다 — 경로 문자열을 생성하게 하면
        존재하지 않는 경로를 만들어낼 수 있다.

        실패·파싱 오류 시 `open_paths` 전체를 반환한다
        (더 많이 읽고 게이트키퍼가 막게 한다).

        Day 3 (B)
        """
        raise NotImplementedError("Day 3 (B) — EXAONE 경로 선택")

    def read(self, paths: list[str], entity_id: str) -> list[Chunk]:
        """선택된 파일만 읽는다 (FR-22, BR-S-03, BR-S-09, BR-S-10).

        2중 검사:
          1. `safe_resolve()` — MESH_DATA_ROOT 하위인가
          2. `knowledge_scope` glob — 그 에이전트의 지식 범위인가

        `run_log` 은 마지막 200줄, 그 외는 앞부분. 256KB 상한.

        ⚠️ `Chunk.tier` 를 채우지 않는다. 등급 판정은 Gatekeeper 의 일이다.

        Day 3 (B)
        """
        raise NotImplementedError("Day 3 (B) — 파일 읽기 + 프런트매터 파싱")

    async def list_agents(self) -> list[AgentCard]:
        """지목 목록 (FR-30, FR-31, BR-S-06).

        ⚠️ `current_focus_summary` 는 `Session.focus` 원문이 **아니다.**
           식별자를 제거한 요약이며 그 변환도 게이트키퍼를 통과한다.
           이 화면은 인증 없이 보이므로 여기서 고객사명이 새면
           게이트키퍼를 우회한 유출이다.

        변환 실패 시 `None` 을 담는다 — 원문 폴백은 없다 (fail closed).

        Day 3 (B)
        """
        raise NotImplementedError("Day 3 (B) — 목록 구성 + 요약 캐시")

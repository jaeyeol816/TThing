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

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from mesh.config import (
    Config,
    DataBundle,
    get_logger,
    log_extra,
    safe_resolve,
    to_relative,
)
from mesh.exceptions import (
    ExaoneUnavailable,
    MeshError,
    PathEscapeError,
    ScopeViolationError,
)
from mesh.schemas import (
    AgentCard,
    AgentConfig,
    Chunk,
    DatasetInfo,
    EditInfo,
    Formality,
    Freshness,
    RunInfo,
    Session,
    SourceKind,
    VerifiedQA,
)

if TYPE_CHECKING:  # pragma: no cover
    from mesh.audit import AuditLog
    from mesh.gatekeeper import Gatekeeper
    from mesh.llm.exaone import ExaoneClient

log = get_logger("store")

#: 세션이 `EXPIRED` 로 넘어가는 기준. 이걸 넘으면 실시간 주장에서 제외한다.
EXPIRED_AFTER = timedelta(hours=24)

#: 파일 하나당 상한 (BR-S-10). 실험 로그가 수십 MB 가 될 수 있고, 전부 읽으면
#: EXAONE 토큰 한도를 넘고 지연이 폭발한다.
MAX_FILE_BYTES = 256 * 1024

#: `run_log` 은 **마지막** N 줄을 읽는다. 로그는 뒤가 중요하다.
RUN_LOG_TAIL_LINES = 200

#: 주제 라벨 캐시 TTL. 세션이 갱신되면 키가 바뀌어 자동 무효화되므로
#: 이 값은 "세션이 그대로일 때 얼마나 오래 재사용할지"만 정한다.
FOCUS_CACHE_TTL_SECONDS = 300

SELECT_PATHS_SYSTEM = (
    "You choose which candidate file paths are relevant to a question.\n"
    'Output exactly one JSON object: {"selected": [<index>, ...]}\n'
    "\n"
    "Hard rules:\n"
    "  - Output indices only. Never output path strings.\n"
    "  - Choose at most 3 indices. Prefer fewer.\n"
    "  - If nothing is clearly relevant, output every index.\n"
    "  - Never output prose or any other key.\n"
    "  - You are given paths and titles only, never file contents. Do not ask for them."
)


# ══════════════════════════════════════════════════════════════════════
# 파일 종류 판정 (BR-S-09)
# ══════════════════════════════════════════════════════════════════════

#: 경로 패턴 -> `(source_kind, formality)`. **순서가 중요하다** — 앞에서 매치되면
#: 뒤를 보지 않는다.
#:
#: `notes/` 가 `informal` 인 것이 시나리오 3 의 핵심이다. 김책임 근거는 개인
#: 메모(비공식)이고 최민수 근거는 설계 리뷰(공식)다. 화면에 이 차이를 표시하는
#: 것이 "둘 다 사실일 수 있습니다"라는 서술을 뒷받침한다 (FR-33).
SOURCE_PATTERNS: tuple[tuple[str, SourceKind, Formality], ...] = (
    ("*/notes/*", "note", "informal"),
    ("*/minutes/*", "minutes", "official"),
    ("*/docs/*", "design_doc", "official"),
    ("*/scripts/*", "script", "official"),
    ("*/configs/*", "config", "official"),
    ("*/runs/*", "run_log", "official"),
    ("*/benchmark/*", "benchmark", "official"),
    ("corpus/customer-*/*", "spec", "official"),    # 구 경로 하위 호환
    ("*/data/customer-*/*", "spec", "official"),    # 새 경로
)

DEFAULT_SOURCE: tuple[SourceKind, Formality] = ("design_doc", "official")


def source_kind_of(rel: str) -> tuple[SourceKind, Formality]:
    """경로에서 **문서 종류**와 공식성을 유도한다. 보안 등급과 무관하다.

    이름에 `classify` 를 쓰지 않는 것이 의도적이다. 이 저장소에서 "분류"는
    등급 판정을 뜻하고 그건 `classifier.py` 의 일이다. 이름이 겹치면
    "store 가 등급을 판정하지 않는다"는 규칙이 흐려진다.

    본문을 읽지 않고 판정한다 — `select_paths()` 가 본문 없이 후보를 설명할 수
    있어야 하고(BR-S-02), 파일이 없어도 종류는 알 수 있어야 한다.
    """
    posix = rel.replace("\\", "/")
    for pattern, kind, formality in SOURCE_PATTERNS:
        if fnmatch(posix, pattern) or fnmatch(posix, pattern.lstrip("*/")):
            return kind, formality
    if posix.endswith(".log"):
        return "run_log", "official"
    if posix.endswith((".py", ".sh")):
        return "script", "official"
    if posix.endswith((".yaml", ".yml", ".json", ".toml")):
        return "config", "official"
    return DEFAULT_SOURCE


def chunk_id_for(rel: str) -> str:
    """경로에서 유도하는 결정적 ID.

    결정적이어야 하는 이유: `AgentCall.chunk_ids` 가 `prepare` 와 `send` 사이를
    건너가고, 같은 파일이 매번 다른 ID 를 받으면 근거 대조가 깨진다.
    """
    return "ch_" + hashlib.sha1(rel.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _write_json(path: Path, raw: object) -> None:
    """시드 JSON 과 같은 모양으로 되쓴다.

    끝에 줄바꿈을 붙이는 이유: 이 함수가 되쓰는 파일 중 일부는 저장소에
    커밋된 시드 파일이다. 줄바꿈이 빠지면 데모를 한 번 돌릴 때마다
    `git diff` 에 `\\ No newline at end of file` 잡음이 남는다.
    """
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# 본문 읽기 (BR-S-10)
# ══════════════════════════════════════════════════════════════════════


def read_body(path: Path, kind: SourceKind, *, max_bytes: int) -> tuple[str, bool]:
    """파일 본문과 잘림 여부.

    `run_log` 은 **마지막 200줄**, 그 외는 앞부분을 읽는다.
    로그는 뒤가 중요하고 문서는 앞이 중요하다.
    """
    raw = path.read_bytes()
    truncated = len(raw) > max_bytes

    if kind == "run_log":
        text = (
            raw[-max_bytes:].decode("utf-8", errors="replace")
            if truncated
            else raw.decode("utf-8", errors="replace")
        )
        lines = text.splitlines()
        if len(lines) > RUN_LOG_TAIL_LINES:
            lines = lines[-RUN_LOG_TAIL_LINES:]
            truncated = True
        return "\n".join(lines), truncated

    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return text, truncated


# ══════════════════════════════════════════════════════════════════════
# 헤더 파싱
# ══════════════════════════════════════════════════════════════════════

#: 헤더를 찾는 범위. 본문 깊숙한 곳의 `title:` 이 제목을 바꾸지 않게 한다.
HEADER_SCAN_LINES = 20

#: 마크다운 프런트매터(`title: x`)와 주석 헤더(`# title: x`)를 함께 받는다.
_HEADER_FIELD_RE = re.compile(
    r"^[\s#/*\-]*(title|as_of|formality|owner)\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_header(text: str) -> dict[str, str]:
    """파일 앞부분의 메타데이터.

    ⚠️ `보안등급` 은 **읽지 않는다.** 등급 판정은 `classifier.py` 의 일이고,
       두 곳에서 해석하면 한쪽이 느슨해진다.
    """
    head = "\n".join(text.splitlines()[:HEADER_SCAN_LINES])
    out: dict[str, str] = {}
    for m in _HEADER_FIELD_RE.finditer(head):
        key = m.group(1).lower()
        value = m.group(2).strip().strip("\"'`,")
        if key == "formality" and value not in {"official", "informal"}:
            continue
        out.setdefault(key, value)
    return out


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    m = _DATE_RE.search(value)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def file_date(path: Path) -> date | None:
    """헤더에 날짜가 없으면 파일 `mtime` 을 쓴다 (BR-S-09)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:  # pragma: no cover
        return None


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

    def __init__(
        self,
        cfg: Config,
        data: DataBundle,
        *,
        exaone: ExaoneClient | None = None,
        gatekeeper: Gatekeeper | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.cfg = cfg
        self.data = data
        #: 경로 선택용. 없으면 후보 전체를 읽는다 (fail closed 방향).
        self.exaone = exaone
        #: 목록 요약용. 없으면 `current_focus_summary` 가 `None` 이다.
        self.gatekeeper = gatekeeper
        #: 질문 수 집계용. 없으면 0.
        self.audit = audit
        self._cache: dict[str, tuple[float, Session]] = {}
        self._focus_cache: dict[tuple[str, str], tuple[float, str | None]] = {}

    # ── 세션 로드 (Day 1) ────────────────────────────────────────────

    def session_path(self, entity_id: str) -> Path:
        """`person:kim` -> `agents/person_kim/gatekeeper/session.json`."""
        return self.cfg.agent_session_path(entity_id)

    def verified_path(self, entity_id: str) -> Path:
        return self.cfg.agent_verified_path(entity_id)

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
        _write_json(path, raw)
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
        scopes = self.data.agent(entity_id).knowledge_scope
        return any(fnmatch(rel, pattern) for pattern in scopes)

    # ── 파일 읽기 ────────────────────────────────────────────────────

    def candidate_paths(self, session: Session) -> tuple[str, ...]:
        """세션이 좁혀 놓은 후보 경로 전체 (BR-S-01).

        `open_paths` 외에 **최근 편집 파일과 실행 로그**도 후보다.
        시나리오 2 의 `train.log` 는 `recent_runs[].log` 에만 있고
        `open_paths` 에는 없다 — 여기서 빠뜨리면 "지금 학습 중" 답이 불가능해진다.

        전역 스캔을 하지 않는다. 세션에 없는 것은 못 찾는다 — 한계이자
        "지금 이 사람의 관심사"라는 강력한 사전 필터다.
        """
        seen: dict[str, None] = {}
        for rel in session.open_paths:
            seen.setdefault(rel, None)
        for edit in session.recent_edits:
            seen.setdefault(edit.path, None)
        for run in session.recent_runs:
            if run.log:
                seen.setdefault(run.log, None)
        return tuple(seen)

    def read(self, paths: list[str], entity_id: str) -> list[Chunk]:
        """선택된 파일만 읽는다 (FR-22, BR-S-03, BR-S-09, BR-S-10).

        2중 검사:
          1. `safe_resolve()` — MESH_DATA_ROOT 하위인가
          2. `knowledge_scope` glob — 그 에이전트의 지식 범위인가

        **왜 2중인가**: `open_paths` 는 세션 JSON 에서 오고 세션 JSON 은 사람이
        편집한다. `../../../etc/passwd` 가 들어갈 수 있다. 그리고 scope 검사는
        김책임 Agent 가 박선임 파일을 읽는 것을 막는다 — 에이전트 간 지식 격리다.

        ⚠️ `Chunk.tier` 를 채우지 않는다. 등급 판정은 Gatekeeper 의 일이다.
           여기서 판정하면 판정 로직이 두 곳에 생기고, 한쪽이 느슨해진다.

        읽지 못한 파일은 **건너뛴다** (예외를 올리지 않는다). 세션 JSON 이
        오래되어 파일이 지워졌을 수 있고, 그 하나 때문에 질의 전체가 죽으면
        데모가 멈춘다. 대신 로그에 남긴다.

        Raises:
            ScopeViolationError: scope 밖 경로. **이건 올린다** — 지식 격리
                위반은 설정 오류이거나 공격이므로 조용히 넘기지 않는다.
        """
        out: list[Chunk] = []
        for rel in paths:
            try:
                resolved = self.resolve(rel)
            except PathEscapeError as e:
                log.warning("경로 탈출 거부", extra=log_extra(reason=str(e)))
                continue

            normalized = to_relative(resolved, self.cfg.data_root)
            if not self.in_scope(normalized, entity_id):
                raise ScopeViolationError(f"{entity_id} 의 knowledge_scope 밖 경로다: {normalized}")

            if not resolved.is_file():
                log.warning("파일이 없다 — 건너뛴다", extra=log_extra(path=normalized))
                continue

            chunk = self._read_one(resolved, normalized, entity_id)
            if chunk is not None:
                out.append(chunk)
        return out

    def _read_one(self, resolved: Path, rel: str, entity_id: str) -> Chunk | None:
        kind, formality = source_kind_of(rel)
        try:
            text, truncated = read_body(resolved, kind, max_bytes=MAX_FILE_BYTES)
        except OSError as e:
            log.warning("파일 읽기 실패 — 건너뛴다", extra=log_extra(reason=type(e).__name__))
            return None

        meta = parse_header(text)
        as_of = meta.get("as_of")
        return Chunk(
            chunk_id=chunk_id_for(rel),
            entity_id=entity_id,
            text=text,
            tier=None,  # ⚠️ Gatekeeper 의 일이다
            display_title=meta.get("title") or resolved.stem,
            internal_path=rel,
            as_of=parse_date(as_of) or file_date(resolved),
            formality=meta.get("formality") or formality,
            source_kind=kind,
            truncated=truncated,
        )

    def verified_chunks(self, session: Session, *, limit: int = 3) -> list[Chunk]:
        """승인된 QA 를 `Chunk` 로 바꾼다 (BR-S-05).

        ⚠️ **`tier` 를 보존한다.** 승인은 *답변의 정확성*을 검증한 것이고
           *등급*을 낮춘 것이 아니다. 이 청크도 다른 지식과 똑같이
           게이트키퍼를 통과한다.

        여기서만 `Chunk.tier` 가 채워진다 — 이미 판정된 등급을 옮겨오는 것이므로
        새로 판정하는 것이 아니다. Gatekeeper 는 `max()` 로 합치므로
        재판정 결과가 더 높으면 그쪽이 이긴다.
        """
        recent = sorted(session.verified_qa, key=lambda q: q.verified_at, reverse=True)
        return [
            Chunk(
                chunk_id=f"ch_qa_{qa.qa_id}",
                entity_id=session.entity_id,
                text=qa.answer,
                tier=qa.tier,  # ⚠️ 보존
                display_title=f"승인된 답변 ({qa.verified_at:%Y-%m-%d})",
                internal_path=f"verified/{session.entity_id.replace(':', '_')}.json",
                as_of=qa.verified_at.date(),
                formality="official",
                source_kind="note",
            )
            for qa in recent[:limit]
        ]

    # ── 경로 선택 ────────────────────────────────────────────────────

    async def select_paths(self, session: Session, question: str) -> list[str]:
        """`open_paths` 중 질문과 관련된 것을 고른다 (FR-17, BR-S-02).

        ⚠️ 프롬프트에 **파일 본문을 넣지 않는다.** 전달하는 것은
           경로 · display_title · 세션 focus/summary 뿐이다.

           본문을 넣으면 안 되는 이유: 경로 선택은 등급 판정 *전*에 일어난다.
           아직 어떤 파일이 기밀인지 모르는 시점에 본문을 EXAONE 에 보내는 것은
           순서가 뒤바뀐 것이다.

        출력은 **인덱스 배열**이다 — 경로 문자열을 생성하게 하면
        존재하지 않는 경로를 만들어낼 수 있다.

        실패·파싱 오류 시 후보 **전체**를 반환한다 — fail closed 방향이다.
        더 많이 읽고 게이트키퍼가 막게 하는 것이, 덜 읽고 답을 못 하는 것보다 낫다.
        """
        candidates = self.candidate_paths(session)
        if len(candidates) <= 1:
            return list(candidates)

        if self.exaone is None:
            return list(candidates)

        lines = []
        for i, rel in enumerate(candidates):
            # ⚠️ display_title 은 파일을 열지 않고 경로에서 유도한다.
            #    제목을 얻으려고 본문을 읽으면 "본문 미포함" 규칙이 무의미해진다.
            lines.append(f"  {i}. {rel}  (kind: {source_kind_of(rel)[0]})")

        user = (
            f"QUESTION:\n{question[:1000]}\n\n"
            f"CANDIDATES:\n" + "\n".join(lines) + "\n\n"
            f"SESSION FOCUS: {session.focus}\n"
            f"SESSION SUMMARY: {session.summary}"
        )

        try:
            raw = await self.exaone.complete_json(
                SELECT_PATHS_SYSTEM, user, name="select_paths", max_tokens=96
            )
        except ExaoneUnavailable as e:
            log.warning(
                "경로 선택 실패 — 후보 전체를 읽는다 (게이트키퍼가 막는다)",
                extra=log_extra(reason=str(e), candidates=len(candidates)),
            )
            return list(candidates)

        picked = raw.get("selected")
        if not isinstance(picked, list):
            log.warning("경로 선택 응답 형식 오류 — 후보 전체를 읽는다")
            return list(candidates)

        # 인덱스만 받는다. 경로 문자열을 생성하게 하면 존재하지 않는 경로를
        # 만들어낼 수 있고, 그 문자열이 `read()` 의 경로 검사로 들어간다.
        chosen: list[str] = []
        for value in picked:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            if 0 <= value < len(candidates):
                rel = candidates[value]
                if rel not in chosen:
                    chosen.append(rel)

        if not chosen:
            log.info("선택된 경로가 없다 — 후보 전체를 읽는다")
            return list(candidates)
        return chosen

    # ── 업로드 (Day 4) ───────────────────────────────────────────────

    def uploads_dir(self, entity_id: str) -> Path:
        """`agents/{entity_id}/data/uploads/`."""
        return self.cfg.agent_uploads_dir(entity_id)

    def save_upload(self, entity_id: str, filename: str, content: str) -> tuple[str, Path]:
        """업로드 저장. `(상대 경로, 절대 경로)` 를 반환한다.

        ⚠️ **파일명을 다시 검증한다.** `api_models.UploadRequest` 가 이미
           검증했지만 그건 "사용자에게 빨리 알려주기 위한 것"이고 신뢰의
           근거가 아니다. 이 메서드는 HTTP 를 거치지 않는 경로(스크립트·테스트)
           에서도 호출된다.

        3중 검사:
          1. `Path(filename).name` — 경로 성분을 강제로 벗긴다
          2. `safe_resolve()` — MESH_DATA_ROOT 하위인지
          3. `in_scope()` — 그 사람의 지식 범위인지

        같은 이름이 있으면 **덮어쓰지 않고** 접미사를 붙인다. 업로드는
        되돌릴 수 없는 작업이고, 조용히 덮어쓰면 원본이 사라진다.

        Raises:
            PathEscapeError: 파일명이 경로를 벗어난다
            ScopeViolationError: 저장 위치가 그 사람의 범위 밖이다
        """
        base = Path(filename).name  # ① 경로 성분 제거
        if not base or base.startswith(".") or base != filename:
            raise PathEscapeError(f"안전하지 않은 파일명이다: {filename!r}")

        target_dir = self.uploads_dir(entity_id)
        rel_dir = to_relative(target_dir, self.cfg.data_root)
        rel = f"{rel_dir}/{base}"

        resolved = self.resolve(rel)  # ② root 하위 확인
        if not self.in_scope(rel, entity_id):  # ③ scope 확인
            raise ScopeViolationError(
                f"{entity_id} 의 knowledge_scope 밖에 저장하려 한다: {rel}. "
                f"agents.yaml 의 knowledge_scope 에 corpus/<사람>/** 가 있는지 확인하라"
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        stem, suffix = (base.rsplit(".", 1) + [""])[:2]
        suffix = f".{suffix}" if suffix else ""
        candidate = resolved
        index = 1
        while candidate.exists():
            candidate = target_dir / f"{stem}-{index}{suffix}"
            index += 1
        candidate.write_text(content, encoding="utf-8")

        final_rel = to_relative(candidate, self.cfg.data_root)
        log.info(
            "문서 업로드 저장",
            extra=log_extra(
                entity_id=entity_id, path=final_rel, size_bytes=len(content.encode("utf-8"))
            ),
        )
        return final_rel, candidate

    def list_uploads(self, entity_id: str) -> tuple[str, ...]:
        """업로드 디렉터리의 파일 목록.

        ⚠️ 여기서만 디렉터리를 훑는다. `iterdir()` 이고 재귀가 아니다 —
           BR-S-01 이 금지한 것은 **질의 때 전역 스캔으로 지식을 찾는 것**이고,
           소유자가 자기 업로드 목록을 보는 것은 다른 일이다.
        """
        target = self.uploads_dir(entity_id)
        if not target.is_dir():
            return ()
        return tuple(
            sorted(
                to_relative(p, self.cfg.data_root)
                for p in target.iterdir()
                if p.is_file() and not p.name.startswith(".")
            )
        )

    def delete_upload(self, entity_id: str, rel: str) -> bool:
        """업로드 삭제. **`uploads/` 아래만** 지울 수 있다.

        샘플 코퍼스를 지우면 데모가 깨지므로 업로드 디렉터리로 제한한다.
        세션의 `open_paths` 에서도 함께 뺀다 — 지워진 파일이 후보에 남으면
        매 질의마다 "파일이 없다" 경고가 뜬다.
        """
        allowed_prefix = to_relative(self.uploads_dir(entity_id), self.cfg.data_root) + "/"
        if not rel.startswith(allowed_prefix):
            raise ScopeViolationError(
                f"업로드한 문서만 삭제할 수 있다: {rel} (허용: {allowed_prefix}*)"
            )
        resolved = self.resolve(rel)
        if not resolved.is_file():
            return False
        resolved.unlink()
        self.detach_path(entity_id, rel)
        log.info("문서 삭제", extra=log_extra(entity_id=entity_id, path=rel))
        return True

    # ── 세션 후보 편집 ───────────────────────────────────────────────

    def attach_path(self, entity_id: str, rel: str) -> None:
        """세션 `open_paths` 에 추가한다 — 그러면 질의 후보가 된다.

        세션 JSON 을 직접 고치는 이유: 이 프로젝트에는 데몬이 없고(BR-S-08),
        세션 파일이 "지금 이 사람의 관심사"의 단일 출처다. 업로드가 후보에
        반영되지 않으면 올린 문서가 아무 질문에도 쓰이지 않는다.
        """
        self._rewrite_open_paths(entity_id, add=rel)

    def detach_path(self, entity_id: str, rel: str) -> None:
        self._rewrite_open_paths(entity_id, remove=rel)

    def _rewrite_open_paths(
        self, entity_id: str, *, add: str | None = None, remove: str | None = None
    ) -> None:
        path = self.session_path(entity_id)
        if not path.exists():
            raise SessionNotFound(f"세션 파일이 없다: {path.name}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        paths: list[str] = list(raw.get("open_paths") or ())
        if remove and remove in paths:
            paths.remove(remove)
        if add and add not in paths:
            paths.append(add)
        raw["open_paths"] = paths
        # ⚠️ `updated_at` 을 손대지 않는다. 신선도는 **사람의 작업 상태**를
        #    나타내는 값이고, 파일을 올린 것만으로 "지금 활동 중"이 되면
        #    STALE 보정(BR-S-04)이 의미를 잃는다.
        _write_json(path, raw)
        self._cache.pop(entity_id, None)

    # ── 지목 목록 ────────────────────────────────────────────────────

    async def list_agents(self) -> list[AgentCard]:
        """지목 목록 (FR-30, FR-31, BR-S-06).

        ⚠️ `current_focus_summary` 는 `Session.focus` 원문이 **아니다.**
           식별자를 제거한 요약이며 그 변환도 게이트키퍼를 통과한다.
           이 화면은 인증 없이 보이므로 여기서 고객사명이 새면
           게이트키퍼를 우회한 유출이다.

        변환 실패 시 `None` 을 담는다 — 원문 폴백은 없다 (fail closed).
        `disclose` 가 꺼진 필드도 `None` 이고, UI 는 "비공개"라고 쓰지 않고
        아예 렌더하지 않는다 — "비공개"라는 표시 자체가 정보이기 때문이다.
        """
        now = self.cfg.now()
        cards: list[AgentCard] = []

        for entity_id, agent in self.data.agents.items():
            disclose = agent.disclose
            org = self._org_fields(agent)
            try:
                session = self.load_session(entity_id)
            except SessionNotFound:
                # 세션이 없어도 목록에는 남는다 — 담당 영역은 항상 공개이므로
                # 지목이 가능해야 한다. 상태 관련 필드만 비운다.
                cards.append(
                    AgentCard(
                        entity_id=entity_id,
                        display_name=agent.display_name,
                        expertise=agent.expertise,
                        daily_limit_reached=self._limit_reached(entity_id, agent.daily_limit),
                        **org,
                    )
                )
                continue

            fresh = self.freshness_of(session)
            summary = None
            if disclose.current_focus:
                summary = await self._focus_summary(session)

            cards.append(
                AgentCard(
                    entity_id=entity_id,
                    display_name=agent.display_name,
                    expertise=agent.expertise,  # 항상 공개 (Literal[True])
                    activity_status=(activity_status(fresh) if disclose.activity_status else None),
                    away_minutes=(
                        elapsed_minutes(session, now)
                        if disclose.activity_status and fresh is not Freshness.LIVE
                        else None
                    ),
                    question_count_today=(
                        self._question_count(entity_id) if disclose.question_count_today else None
                    ),
                    current_focus_summary=summary,
                    session_as_of=session.updated_at if disclose.current_focus else None,
                    freshness=fresh if disclose.activity_status else None,
                    daily_limit_reached=self._limit_reached(entity_id, agent.daily_limit),
                    **org,
                )
            )
        return cards

    def _org_fields(self, agent: AgentConfig) -> dict[str, object]:
        """조직도 좌표를 카드에 실을 형태로 편다.

        ⚠️ **조직도가 없어도 빈 dict 를 돌려준다.** 조직도는 표시용이고,
           자리를 못 찾았다고 그 사람이 목록에서 사라지면 안 된다 —
           화면은 평평한 목록을 그리면 그만이다 (fail soft).

        ⚠️ `disclose` 를 보지 않는 것이 의도적이다. 소속과 직급은 조직도에
           이미 인증 없이 떠 있는 값이고, 여기서 숨겨도 `GET /api/org` 로
           보인다. 같은 사실을 두 화면이 다르게 말하면 그게 더 나쁘다.
        """
        chart = getattr(self.data, "org", None)
        place = agent.org
        if chart is None or place is None:
            return {}
        unit = chart.unit(place.unit)
        rank = chart.rank(place.rank)
        if unit is None or rank is None:
            # `org.yaml` 에 없는 id 다. 조용히 넘기지 않고 로그를 남긴다 —
            # 오타는 화면에서 사람이 사라지는 방식으로만 드러난다.
            log.warning(
                "조직도에 없는 자리 — 미배치로 그린다",
                extra=log_extra(entity_id=agent.entity_id, unit=place.unit, rank=place.rank),
            )
            return {}
        return {
            "unit_id": unit.id,
            "unit_path": chart.unit_path(unit.id),
            "rank_id": rank.id,
            "rank_label": rank.label,
            "rank_badge": rank.badge,
            "rank_order": rank.order,
            "org_title": place.title or None,
        }

    async def _focus_summary(self, session: Session) -> str | None:
        """게이트키퍼를 경유한 주제 라벨. 캐시 키는 `(entity_id, updated_at)`.

        목록 조회마다 EXAONE 을 부르면 비싸고, 세션이 바뀌지 않았으면 요약도
        바뀌지 않는다. `updated_at` 을 키에 넣으므로 세션이 갱신되면 자동 무효화된다.
        """
        if self.gatekeeper is None:
            return None

        key = (session.entity_id, session.updated_at.isoformat())
        hit = self._focus_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < FOCUS_CACHE_TTL_SECONDS:
            return hit[1]

        label = await self.gatekeeper.summarize_focus(session.focus, session.summary)
        self._focus_cache[key] = (time.monotonic(), label)
        return label

    async def warm_focus_cache(self) -> None:
        """앱 시작 시 워밍업. 첫 화면에서 사용자가 기다리지 않게 한다."""
        for entity_id in self.data.agents:
            try:
                await self._focus_summary(self.load_session(entity_id))
            except SessionNotFound:
                continue

    def _question_count(self, entity_id: str) -> int:
        """오늘 이 사람에게 온 질문 수. `audit` + `local_queries` 합산.

        신뢰 구역 안에서 처리된 질의도 "물어본 것"이므로 함께 센다 —
        감사 로그 탭에는 안 보이지만 카운트에서 빠지면 숫자가 거짓이 된다.
        """
        if self.audit is None:
            return 0
        return self.audit.count_today(entity_id, now=self.cfg.now())

    def _limit_reached(self, entity_id: str, daily_limit: int) -> bool:
        return self._question_count(entity_id) >= daily_limit

    # ── 지식 갱신 (Knowledge Miss → Search → Save) ───────────────────

    async def kb_search_and_save(
        self,
        entity_id: str,
        question: str,
        *,
        max_chars: int = 3000,
    ) -> Chunk | None:
        """세션에 관련 근거가 없을 때 EXAONE 으로 답변을 생성하고
        agents/{id}/data/kb/ 에 .md 로 저장한 뒤 세션에 등록한다.

        흐름:
          1. EXAONE 에게 질문 + "공개 정보만 사용" 제약을 주고 답변 생성
          2. Gatekeeper 로 등급 판정 (SECRET 이면 저장 안 함)
          3. agents/{id}/data/kb/{slug}.md 저장
          4. session.json open_paths 에 등록 → 다음 쿼리부터 후보가 됨
          5. Chunk 반환 → orchestrator 가 바로 사용

        ⚠️ EXAONE 이 생성한 내용은 "공개 정보 기반 요약"이므로 원문 유출이
           아니다. 하지만 Gatekeeper 등급 판정을 반드시 거친다.

        Returns:
            생성·저장된 Chunk. EXAONE 실패 또는 SECRET 판정 시 None.
        """
        if self.exaone is None:
            log.warning("EXAONE 없음 — 지식 갱신 불가", extra=log_extra(entity_id=entity_id))
            return None

        # ① EXAONE 으로 공개 정보 기반 답변 생성
        system = (
            "You are a helpful internal knowledge assistant.\n"
            "Answer the question using only publicly available information "
            "(standards, RFCs, open documentation). "
            "Do NOT include any customer names, contract numbers, pricing, or "
            "confidential internal data. "
            "Write in Korean. Keep the answer concise (under 500 words). "
            "Format: plain text with optional markdown headers."
        )
        try:
            content = await self.exaone.complete_text(
                system,
                f"QUESTION:\n{question}",
                name="kb_search",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("EXAONE 지식 검색 실패", extra=log_extra(reason=str(e)))
            return None

        if not content or not content.strip():
            return None

        # ② 등급 판정 — 생성된 내용도 검사한다
        if self.gatekeeper is not None:
            try:
                decision = await self.gatekeeper.classify(content)
                if decision.tier.value == "secret":
                    log.warning(
                        "EXAONE 생성 내용이 SECRET 판정 — 저장 거부",
                        extra=log_extra(entity_id=entity_id, reasons=decision.reasons),
                    )
                    return None
            except Exception as e:  # noqa: BLE001
                log.warning("등급 판정 실패 — 저장 거부", extra=log_extra(reason=str(e)))
                return None

        # ③ 파일 저장: agents/{id}/data/kb/{slug}.md
        import re
        from datetime import datetime as _dt

        kb_dir = self.cfg.agent_data_root(entity_id) / "kb"
        kb_dir.mkdir(parents=True, exist_ok=True)

        # 파일명: 질문에서 안전한 slug 생성
        slug_base = re.sub(r"[^\w가-힣]+", "_", question.strip())[:40].strip("_")
        timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
        filename = f"kb_{timestamp}_{slug_base}.md"
        file_path = kb_dir / filename

        md_content = (
            f"# {question}\n\n"
            f"> 생성일: {_dt.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"> 출처: EXAONE 공개 정보 기반 생성 (자동)\n\n"
            f"{content.strip()}\n"
        )
        file_path.write_text(md_content, encoding="utf-8")

        # ④ session.json open_paths 에 등록
        rel = to_relative(file_path, self.cfg.data_root)
        try:
            self.attach_path(entity_id, rel)
        except SessionNotFound:
            log.warning("세션 없음 — open_paths 등록 건너뜀", extra=log_extra(entity_id=entity_id))

        log.info(
            "지식 갱신 완료",
            extra=log_extra(entity_id=entity_id, path=rel, question_len=len(question)),
        )

        # ⑤ Chunk 반환
        return Chunk(
            chunk_id=chunk_id_for(rel),
            entity_id=entity_id,
            text=content[:max_chars],
            tier=None,  # Gatekeeper 가 판정한다
            display_title=f"KB: {question[:60]}",
            internal_path=rel,
            as_of=_dt.now().date(),
            formality="official",
            source_kind="note",
        )

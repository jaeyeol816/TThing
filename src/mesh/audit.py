"""감사 로그 — 경계를 넘은 것의 기록 (FR-15, FR-42, BR-A-*).

이 모듈의 목적은 "무엇이 나갔는지"를 **사후에 증명**하는 것이다.
그래서 두 가지가 중요하다.

  1. **호출 직전에 기록한다** (BR-A-01). 호출이 실패해도 "나갔다"는 사실은 남는다.
     성공 후에 기록하면 실패한 전송이 로그에 없어 증거가 불완전해진다.

  2. **레코드가 없는 것도 증거다** (BR-A-03). `answer_in_zone()` 폴백은 감사
     레코드를 남기지 않는다. 경계를 넘은 것이 없으므로.
     시나리오 3의 결정적 장면이 이것이다 — 감사 로그 탭이 비어 있는 것으로
     "이 질문은 밖으로 나가지 않았다"를 보인다.

     혼동을 막기 위해 신뢰 구역 내 처리는 별도 테이블(`local_queries`)에 남긴다.
     감사 로그 탭에는 표시하지 않는다.

──────────────────────────────────────────────────────────────────────
기록하지 않는 것 (BR-A-02, NFR-S-03)
──────────────────────────────────────────────────────────────────────

원문(`Chunk.text`) · 매핑 테이블 · API 키 · AWS 자격증명 ·
EXAONE `reasoning`/`reasoning_content` · HTTP 요청 헤더.

`record()` 는 페이로드에 금지 키가 있으면 **거부한다** (`GatekeeperError`).
"기록하지 않는다"를 주석이 아니라 실행되는 검사로 만든다. 여기서 실패하면
전송도 일어나지 않는다 — fail closed 방향이다.

`local_queries` 에도 질문 원문을 넣지 않는다. `question_sha256` 만 남긴다.
로컬 테이블이라고 원문을 흘리면 이 프로젝트의 주장이 무너진다.

──────────────────────────────────────────────────────────────────────
추가 전용 (NFR-S-13)
──────────────────────────────────────────────────────────────────────

`audit` 테이블에 대한 `DELETE`/`UPDATE` 문이 **앱 코드에 존재하지 않는다.**
`tests/unit/test_audit.py` 가 소스를 grep 해 강제한다.

**로컬 감사 로그는 사용자가 파일을 지울 수 있다.** 이건 근본적 한계이고
그래서 클라우드 미러(U5)가 필요하다. 숨기지 않고 문서에 적는다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from mesh.config import FORBIDDEN_LOG_KEYS, Config, get_logger, log_extra
from mesh.exceptions import GatekeeperError
from mesh.schemas import (
    AuditRecord,
    LeakHit,
    LeakReport,
    Representation,
    Tier,
    Transport,
)
from mesh.validator import ngram_set, normalize_text, payload_text

log = get_logger("audit")

# ══════════════════════════════════════════════════════════════════════
# 스키마
# ══════════════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    record_id                 TEXT PRIMARY KEY,
    at                        TEXT NOT NULL,
    kind                      TEXT NOT NULL,
    actor                     TEXT NOT NULL,
    target_entity_id          TEXT NOT NULL,
    model_id                  TEXT NOT NULL,
    transport                 TEXT NOT NULL,
    trusted_zone_llm_base_url TEXT NOT NULL,
    tier                      TEXT NOT NULL,
    representation            TEXT NOT NULL,
    payload_json              TEXT NOT NULL,
    payload_sha256            TEXT NOT NULL,
    size_bytes                INTEGER NOT NULL,
    validation_summary        TEXT NOT NULL,
    approved_by               TEXT NOT NULL,
    envelope_id               TEXT NOT NULL,
    vocab_sha256              TEXT,
    confidence                REAL,
    citation_count            INTEGER,
    usage_json                TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at       ON audit(at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_envelope ON audit(envelope_id);
CREATE INDEX IF NOT EXISTS idx_audit_target   ON audit(target_entity_id);

-- 신뢰 구역 내 처리. 감사 로그 탭에 표시하지 않는다 (BR-A-03).
-- ⚠️ 질문 원문을 담지 않는다. 해시만 남긴다.
CREATE TABLE IF NOT EXISTS local_queries (
    query_id         TEXT PRIMARY KEY,
    at               TEXT NOT NULL,
    actor            TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    tier             TEXT NOT NULL,
    reason_code      TEXT NOT NULL,
    question_sha256  TEXT NOT NULL,
    chunk_count      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_local_at ON local_queries(at DESC);

-- 에스컬레이션 인박스. 조작은 `inbox.py` 가 한다 (스키마만 여기 둔다 —
-- 테이블 정의가 두 곳에 있으면 갈라진다).
--
-- ⚠️ `UPDATE` 는 `status`/`resolved_at`/`resolution_text`/`redirect_to` 만
--    대상으로 한다. `draft_*`·`situation_json` 은 감사 흔적이므로 고치지 않는다.
CREATE TABLE IF NOT EXISTS inbox (
    item_id               TEXT PRIMARY KEY,
    at                    TEXT NOT NULL,
    owner_entity_id       TEXT NOT NULL,
    asker                 TEXT NOT NULL,
    thread_id             TEXT NOT NULL,
    question_summary      TEXT NOT NULL,
    draft_summary         TEXT NOT NULL,
    situation_json        TEXT NOT NULL,
    draft_answer          TEXT NOT NULL,
    already_answered_json TEXT NOT NULL,
    citations_json        TEXT NOT NULL DEFAULT '[]',
    tier                  TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'open',
    resolved_at           TEXT,
    resolution_text       TEXT,
    redirect_to           TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_owner  ON inbox(owner_entity_id, status);
CREATE INDEX IF NOT EXISTS idx_inbox_thread ON inbox(thread_id);

-- 질의 하나의 최종 처분. 자동 응답률(목표 >= 50%)의 분모·분자가 된다.
-- `audit` 과 분리한다 — 처분은 경계를 넘은 것과 무관하고, 폴백도 처분을 갖는다.
CREATE TABLE IF NOT EXISTS outcomes (
    request_id    TEXT PRIMARY KEY,
    at            TEXT NOT NULL,
    disposition   TEXT NOT NULL,
    answer_count  INTEGER NOT NULL DEFAULT 0
);
"""

_AUDIT_COLUMNS: tuple[str, ...] = (
    "record_id",
    "at",
    "kind",
    "actor",
    "target_entity_id",
    "model_id",
    "transport",
    "trusted_zone_llm_base_url",
    "tier",
    "representation",
    "payload_json",
    "payload_sha256",
    "size_bytes",
    "validation_summary",
    "approved_by",
    "envelope_id",
    "vocab_sha256",
    "confidence",
    "citation_count",
    "usage_json",
)

#: 신뢰 구역 내 처리 이유. 자유 문자열을 쓰지 않는다 —
#: 이유에 질문 원문이 들어가면 `local_queries` 가 원문 저장소가 된다.
LOCAL_REASON_CODES: tuple[str, ...] = (
    "extraction_failed",
    "validation_blocked",
    "broker_unavailable",
    "user_cancelled",
    "open_tier_local",
    "policy_no_external",
)


# ══════════════════════════════════════════════════════════════════════
# AuditLog
# ══════════════════════════════════════════════════════════════════════


class AuditLog:
    """로컬 SQLite 감사 저장소.

    `check_same_thread=False` 를 쓰는 이유: FastAPI 가 동기 호출을 스레드풀로
    보낸다. `_lock` 없이 커넥션을 공유하면 경합이 생기므로 SQLite 의
    직렬화 모드에 의존하지 않고 매 호출을 짧은 트랜잭션으로 끝낸다.
    """

    def __init__(self, cfg: Config, *, path: Path | None = None) -> None:
        self.cfg = cfg
        self.path = path or cfg.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secure_dir(self.path.parent)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._secure_file(self.path)
        self._mirror_failures = 0

    # ── 파일 권한 (NFR-S-01) ─────────────────────────────────────────

    @staticmethod
    def _secure_file(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError as e:  # pragma: no cover — 파일시스템에 따라 실패 가능
            log.warning("감사 DB 권한 설정 실패", extra=log_extra(reason=str(e)))

    @staticmethod
    def _secure_dir(path: Path) -> None:
        try:
            path.chmod(0o700)
        except OSError as e:  # pragma: no cover
            log.warning("감사 DB 디렉터리 권한 설정 실패", extra=log_extra(reason=str(e)))

    def close(self) -> None:
        self._conn.close()

    # ── 기록 (BR-A-01) ───────────────────────────────────────────────

    @staticmethod
    def reject_forbidden(payload: object) -> None:
        """페이로드에 금지 키가 있으면 거부한다.

        "원문을 기록하지 않는다"를 주석이 아니라 검사로 만든다.
        여기서 실패하면 `record()` 가 예외를 던지고, `ask_agent()` 가
        레코드 없이 전송하는 일이 없으므로 전송도 일어나지 않는다.
        """
        stack: list[object] = [payload]
        seen = 0
        while stack and seen < 10_000:
            cur = stack.pop()
            seen += 1
            if isinstance(cur, dict):
                for k, v in cur.items():
                    if str(k).lower() in FORBIDDEN_LOG_KEYS:
                        raise GatekeeperError(
                            f"감사 기록에 금지 필드가 있다: {k!r} (BR-A-02). "
                            "페이로드 조립 경로를 확인하라"
                        )
                    stack.append(v)
            elif isinstance(cur, list | tuple):
                stack.extend(cur)

    def record(self, rec: AuditRecord) -> None:
        """경계를 넘기 **직전**에 호출한다 (BR-A-01)."""
        self.reject_forbidden(rec.payload)
        self._conn.execute(
            f"INSERT INTO audit ({', '.join(_AUDIT_COLUMNS)}) "  # noqa: S608 — 상수 목록
            f"VALUES ({', '.join('?' * len(_AUDIT_COLUMNS))})",
            (
                rec.record_id,
                rec.at.isoformat(),
                rec.kind,
                rec.actor,
                rec.target_entity_id,
                rec.model_id,
                rec.transport.value,
                rec.trusted_zone_llm_base_url,
                rec.tier.value,
                rec.representation.value,
                json.dumps(rec.payload, ensure_ascii=False, sort_keys=True),
                rec.payload_sha256,
                rec.size_bytes,
                rec.validation_summary,
                rec.approved_by,
                rec.envelope_id,
                rec.vocab_sha256,
                rec.confidence,
                rec.citation_count,
                json.dumps(rec.usage, ensure_ascii=False) if rec.usage else None,
            ),
        )
        self._conn.commit()
        log.info(
            "감사 기록",
            extra=log_extra(
                record_id=rec.record_id,
                envelope_id=rec.envelope_id,
                tier=rec.tier.value,
                transport=rec.transport.value,
                trusted_zone_llm=rec.trusted_zone_llm_base_url,
                size_bytes=rec.size_bytes,
            ),
        )

    def record_local(
        self,
        *,
        actor: str,
        target_entity_id: str,
        tier: Tier,
        reason_code: str,
        question_sha256: str,
        chunk_count: int = 0,
    ) -> str:
        """신뢰 구역 내 처리 기록. **감사 테이블이 아니다** (BR-A-03).

        `reason_code` 는 `LOCAL_REASON_CODES` 안의 값이어야 한다.
        자유 문자열을 허용하면 이유에 질문 원문이 들어간다.
        """
        if reason_code not in LOCAL_REASON_CODES:
            raise GatekeeperError(
                f"미등록 local reason_code: {reason_code!r}. 허용값: {list(LOCAL_REASON_CODES)}"
            )
        query_id = f"loc_{uuid.uuid4().hex[:20]}"
        self._conn.execute(
            "INSERT INTO local_queries "
            "(query_id, at, actor, target_entity_id, tier, reason_code, "
            " question_sha256, chunk_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                query_id,
                self.cfg.now().isoformat(),
                actor,
                target_entity_id,
                tier.value,
                reason_code,
                question_sha256,
                chunk_count,
            ),
        )
        self._conn.commit()
        return query_id

    # ── 조회 (BR-A-04) ───────────────────────────────────────────────

    def recent(self, limit: int = 50) -> tuple[AuditRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM audit ORDER BY at DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return tuple(_row_to_record(r) for r in rows)

    def by_envelope(self, envelope_id: str) -> tuple[AuditRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM audit WHERE envelope_id = ? ORDER BY at", (envelope_id,)
        ).fetchall()
        return tuple(_row_to_record(r) for r in rows)

    def search(self, term: str, *, limit: int = 100) -> tuple[AuditRecord, ...]:
        """페이로드 전문 부분 문자열 검색 (FR-42).

        ⚠️ **파라미터화 쿼리를 쓴다.** 검색어는 사용자 입력이고, 이 화면의
           목적상 `REQ-4412` 처럼 특수문자가 섞인 문구가 그대로 들어온다.
           문자열 연결로 만들면 SQL 주입이 된다.

        결과 0건이 이 화면의 핵심 기능이다 — "이 문구는 경계를 넘은 적이
        없습니다"를 보이기 위한 검색이다.
        """
        if not term.strip():
            return ()
        rows = self._conn.execute(
            "SELECT * FROM audit WHERE lower(payload_json) LIKE ? ORDER BY at DESC LIMIT ?",
            (f"%{term.strip().lower()}%", int(limit)),
        ).fetchall()
        return tuple(_row_to_record(r) for r in rows)

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0])

    def local_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM local_queries").fetchone()[0])

    def count_today(self, target_entity_id: str, *, now: datetime) -> int:
        """오늘 이 사람에게 온 질문 수 (`audit` + `local_queries`).

        신뢰 구역 안에서 처리된 질의도 "물어본 것"이므로 함께 센다 —
        감사 로그 탭에는 안 보이지만 카운트에서 빠지면 숫자가 거짓이 되고,
        `daily_limit` 을 우회하는 경로가 생긴다.

        `kind='request'` 만 센다. `result` 레코드는 같은 호출의 사본이다.
        """
        day = now.date().isoformat()
        crossed = self._conn.execute(
            "SELECT COUNT(*) FROM audit "
            "WHERE target_entity_id = ? AND kind = 'request' AND substr(at, 1, 10) = ?",
            (target_entity_id, day),
        ).fetchone()[0]
        local = self._conn.execute(
            "SELECT COUNT(*) FROM local_queries "
            "WHERE target_entity_id = ? AND substr(at, 1, 10) = ?",
            (target_entity_id, day),
        ).fetchone()[0]
        return int(crossed) + int(local)

    def disposition_counts(self) -> dict[str, int]:
        """처분 분포. `/api/health` 의 평가 지표로 그대로 쓴다 (요구사항 §6)."""
        rows = self._conn.execute(
            "SELECT disposition, COUNT(*) FROM outcomes GROUP BY disposition"
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def record_outcome(self, *, request_id: str, disposition: str, answer_count: int) -> None:
        """질의 하나의 최종 처분. 자동 응답률 계산의 분모가 된다.

        `audit` 과 분리한 이유: 처분은 경계를 넘은 것과 무관하다 —
        `answer_in_zone` 폴백도 처분(`blocked`)을 갖는다.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO outcomes (request_id, at, disposition, answer_count) "
            "VALUES (?, ?, ?, ?)",
            (request_id, self.cfg.now().isoformat(), disposition, answer_count),
        )
        self._conn.commit()

    def payloads(self) -> tuple[tuple[str, str, Representation], ...]:
        """`(record_id, payload_json, representation)`. 전수 유출 검사용.

        `representation` 이 필요한 이유: 등급마다 "유출"의 정의가 다르다
        (BR-P-03). 가명화 페이로드는 원문 문장을 대부분 유지하므로 평탄한
        5-gram 규칙을 적용하면 정상 동작이 전부 유출로 잡힌다.
        """
        rows = self._conn.execute(
            "SELECT record_id, payload_json, representation FROM audit"
        ).fetchall()
        return tuple(
            (r["record_id"], r["payload_json"], Representation(r["representation"])) for r in rows
        )

    # ── 인박스 접근 (조작은 inbox.py 가 한다) ────────────────────────

    @property
    def connection(self) -> sqlite3.Connection:
        """`inbox.py` 가 같은 DB 를 쓰게 한다.

        커넥션을 두 개 열면 SQLite 락이 충돌하고, DB 파일을 둘로 나누면
        "한 파일만 지우면 증거가 반쪽" 이 된다.
        """
        return self._conn

    # ── 전수 유출 검사 (FR-16, S-05) ─────────────────────────────────

    def sweep_for_leaks(
        self,
        documents: Sequence[tuple[str, str]],
        *,
        n: int = 5,
        n_internal: int = 3,
        identifiers: Sequence[str] = (),
        banned_literals: Sequence[str] = (),
        banned_patterns: Sequence[str] = (),
    ) -> LeakReport:
        """저장된 **모든** 페이로드 × **모든** 문서를 대조한다.

        샘플 데이터라서 가능한 검증이다 — 문서를 우리가 만들었으니 전수 검사가
        현실적이다 (설계 §2.1). 실서비스라면 표본 검사가 된다.

        ⚠️ **등급마다 "유출"의 정의가 다르다** (BR-P-03). 검증 5단계와 같은
           규칙을 써야 한다 — 실측에서 이걸 빠뜨려 **오탐 1076건**이 났고,
           목록이 그만큼 길면 아무도 읽지 않는다. 즉 가장 강력한 주장이
           **실제 유출을 가리는 도구**가 되어 있었다.

           | 표현 | 검사 대상 |
           |---|---|
           | `STRUCTURED` (기밀) | 원문 5-gram 전체 |
           | `PSEUDONYMIZED` (사내) | **식별자를 포함한** n-gram 만 |
           | `VERBATIM` (공개) | 없음 — 원문 전송이 등급의 정의다 |

        `identifiers` 는 가명화 대상 전체다 (`pseudonyms.all_literals()`).
        치환된 것만 넘기면 놓친 표기 변형을 검사할 방법이 사라진다.

        금칙어 검사는 **모든 등급에 동일하게** 적용된다 — 사내 등급의 하한선이다.

        `documents` 는 `(경로, 원문)` 쌍이다. `Chunk` 를 받지 않는 이유:
        U6 의 평가 스크립트가 파일을 직접 읽으므로 `Chunk` 조립이 불필요하고,
        원문을 다루는 모듈 수를 늘리지 않는 편이 낫다.
        """
        started = time.monotonic()
        stored = self.payloads()
        # 저장된 JSON 문자열을 그대로 정규화하면 안 된다 — `\n` 이스케이프가
        # 공백 정규화를 빠져나가 대조가 헐거워진다 (validator.payload_text 참조).
        blobs = [
            (rid, normalize_text(payload_text(json.loads(pj))), rep) for rid, pj, rep in stored
        ]
        low_identifiers = [i.lower() for i in identifiers if i.strip()]

        hits: list[LeakHit] = []
        for path, text in documents:
            full = ngram_set(text, n)
            identifier_only = frozenset(
                g
                for g in (full | ngram_set(text, n_internal))
                if any(tok in g for tok in low_identifiers)
            )
            for record_id, blob, representation in blobs:
                if representation is Representation.VERBATIM:
                    continue  # 원문 전송이 등급의 정의다
                grams = identifier_only if representation is Representation.PSEUDONYMIZED else full
                for gram in grams:
                    if gram in blob:
                        hits.append(
                            LeakHit(
                                record_id=record_id,
                                document_path=path,
                                ngram=gram,
                                kind="ngram",
                            )
                        )

        banned_hits: list[LeakHit] = []
        compiled = [(p, re.compile(p, re.IGNORECASE)) for p in banned_patterns]
        for record_id, payload_json, _ in stored:
            low = payload_json.lower()
            for lit in banned_literals:
                if lit.lower() in low:
                    banned_hits.append(
                        LeakHit(
                            record_id=record_id,
                            document_path="-",
                            ngram=lit,
                            kind="banned_literal",
                        )
                    )
            for raw, rx in compiled:
                if rx.search(payload_json):
                    banned_hits.append(
                        LeakHit(
                            record_id=record_id,
                            document_path="-",
                            ngram=raw,
                            kind="banned_pattern",
                        )
                    )

        return LeakReport(
            payloads_scanned=len(stored),
            documents_scanned=len(documents),
            ngram_size=n,
            hits=tuple(hits),
            banned_hits=tuple(banned_hits),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    # ── 클라우드 미러 (BR-A-05) — 유일한 fail-open 경로 ──────────────

    async def mirror(self, rec: AuditRecord) -> bool:
        """DynamoDB 미러링. **실패해도 질의를 죽이지 않는다.**

        이 프로젝트에서 fail-open 인 유일한 경로다. 근거:
          - 미러링 실패로 질의가 죽으면 데모가 멈춘다
          - 로컬 SQLite 가 원본이므로 증거가 사라지지 않는다
          - 실패 건수를 `/api/health` 에 노출해 조용히 넘기지 않는다

        `AGENT_TRANSPORT != broker` 면 미러 대상이 없으므로 조용히 건너뛴다.
        """
        if self.cfg.agent_transport is not Transport.BROKER:
            return False
        table = f"mesh-audit-{self.cfg.aws_region}"
        try:
            import asyncio

            import boto3

            def _put() -> None:
                boto3.client("dynamodb", region_name=self.cfg.aws_region).put_item(
                    TableName=table,
                    Item={
                        "record_id": {"S": rec.record_id},
                        "at": {"S": rec.at.isoformat()},
                        "envelope_id": {"S": rec.envelope_id},
                        "tier": {"S": rec.tier.value},
                        "payload_sha256": {"S": rec.payload_sha256},
                        "size_bytes": {"N": str(rec.size_bytes)},
                        "trusted_zone_llm_base_url": {"S": rec.trusted_zone_llm_base_url},
                    },
                )

            await asyncio.to_thread(_put)
            return True
        except Exception as e:  # noqa: BLE001 — fail open (BR-A-05)
            self._mirror_failures += 1
            log.warning(
                "감사 미러링 실패 — 로컬 기록은 유지된다",
                extra=log_extra(reason=type(e).__name__, failures=self._mirror_failures),
            )
            return False

    @property
    def mirror_failures(self) -> int:
        """`/api/health` 에 노출한다. 실패를 조용히 삼키지 않는다."""
        return self._mirror_failures


# ══════════════════════════════════════════════════════════════════════
# 행 -> 레코드
# ══════════════════════════════════════════════════════════════════════


def _row_to_record(row: sqlite3.Row) -> AuditRecord:
    return AuditRecord(
        record_id=row["record_id"],
        at=datetime.fromisoformat(row["at"]),
        kind=row["kind"],
        actor=row["actor"],
        target_entity_id=row["target_entity_id"],
        model_id=row["model_id"],
        transport=Transport(row["transport"]),
        trusted_zone_llm_base_url=row["trusted_zone_llm_base_url"],
        tier=Tier(row["tier"]),
        representation=Representation(row["representation"]),
        payload=json.loads(row["payload_json"]),
        payload_sha256=row["payload_sha256"],
        size_bytes=row["size_bytes"],
        validation_summary=row["validation_summary"],
        approved_by=row["approved_by"],
        envelope_id=row["envelope_id"],
        vocab_sha256=row["vocab_sha256"],
        confidence=row["confidence"],
        citation_count=row["citation_count"],
        usage=json.loads(row["usage_json"]) if row["usage_json"] else None,
    )


def payload_preview(payload: object, *, limit: int = 400) -> str:
    """감사 목록에 보여줄 축약. 전문은 상세 화면에서 본다."""
    blob = payload_text(payload)
    return blob if len(blob) <= limit else blob[:limit] + " …"

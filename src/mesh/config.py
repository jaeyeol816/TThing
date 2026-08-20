"""설정 · 경로 가드 · 구조화 로깅.

전 유닛이 공유한다 (`shared-infrastructure.md` §5, §6).

세 가지를 담당한다:
  1. Config.load()   — 환경변수 로드 + fail-fast 검증
  2. safe_resolve()  — 경로 탈출 거부 (NFR-S-05). 파일을 여는 모든 코드가 이걸 쓴다
  3. 로깅           — JSON 구조화 + 금지 필드 리댁션. 개발자가 실수해도 원문이 안 남는다
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from mesh.exceptions import ConfigError, PathEscapeError
from mesh.schemas import (
    AgentConfig,
    BannedTerms,
    ClassificationRules,
    Disclose,
    OrgPlacement,
    PseudonymTargets,
    Transport,
    Vocabulary,
)

# ══════════════════════════════════════════════════════════════════════
# 요청 추적
# ══════════════════════════════════════════════════════════════════════

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


# ══════════════════════════════════════════════════════════════════════
# 로깅 — 금지 필드 리댁션 (NFR-S-03)
# ══════════════════════════════════════════════════════════════════════

#: 이 키들은 로그에 절대 나가지 않는다.
#: reasoning* 이 포함된 이유: EXAONE 의 사고 과정이 원문을 인용할 수 있다 (실측 확인).
FORBIDDEN_LOG_KEYS: frozenset[str] = frozenset(
    {
        # 원문
        "text",
        "chunk_text",
        "raw_document",
        "payload_text",
        "source_text",
        "originals",
        "document",
        "focus",
        "summary",
        # 매핑 테이블
        "mapping",
        "table",
        # EXAONE thinking — 원문 유출 채널
        "reasoning",
        "reasoning_content",
        # 자격증명
        "friendli_token",
        "broker_api_key",
        "aws_secret_access_key",
        "aws_session_token",
        "aws_access_key_id",
        "authorization",
        "x-api-key",
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
    }
)

REDACTED = "<redacted>"

_LOG_RECORD_BUILTINS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)

#: `logging` 이 `extra=` 로 덮어쓰지 못하게 하는 예약 키.
#: 여기 있는 이름을 extra 에 쓰면 KeyError 가 난다 — 로그 한 줄 때문에 요청이 죽는다.
#: `log_extra()` 를 쓰면 자동으로 접두사가 붙어 충돌을 피한다.
RESERVED_LOG_KEYS: frozenset[str] = _LOG_RECORD_BUILTINS | {"message", "asctime"}


def log_extra(**kw: object) -> dict[str, object]:
    """`extra=` 에 안전하게 넘길 dict 를 만든다.

    `logging` 은 `extra` 의 키가 LogRecord 속성과 겹치면 KeyError 를 던진다.
    `name`, `module`, `args`, `msg`, `levelname` 등이 전부 예약어다.
    충돌하는 키에는 `x_` 접두사를 붙인다.

        log.warning("...", extra=log_extra(name="classify"))  ->  x_name
    """
    return {(f"x_{k}" if k in RESERVED_LOG_KEYS else k): v for k, v in kw.items()}


def _redact(value: object, depth: int = 0) -> object:
    """금지 키를 재귀적으로 치환. 깊이 6에서 멈춘다 (순환·폭발 방지)."""
    if depth > 6:
        return REDACTED
    if isinstance(value, dict):
        return {
            k: (REDACTED if str(k).lower() in FORBIDDEN_LOG_KEYS else _redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(v, depth + 1) for v in value]
    return value


class RedactingFilter(logging.Filter):
    """개발자가 `log.info("chunk: %s", chunk)` 를 써도 원문이 로그에 남지 않게 한다.

    규율이 아니라 필터로 막는다. 5일 동안 3명이 작업하면 반드시 누군가 실수한다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__):
            if key in _LOG_RECORD_BUILTINS:
                continue
            if key.lower() in FORBIDDEN_LOG_KEYS:
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = _redact(record.__dict__[key])
        return True


class JsonFormatter(logging.Formatter):
    """구조화 로깅 (NFR-S-03): timestamp · correlation_id · level · component · message."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "at": datetime.fromtimestamp(record.created).astimezone().isoformat(),
            "level": record.levelname,
            "correlation_id": correlation_id.get(),
            "component": record.name.removeprefix("mesh."),
            "message": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _LOG_RECORD_BUILTINS and key not in payload:
                payload[key] = val
        if record.exc_info:
            payload["exc_type"] = getattr(record.exc_info[0], "__name__", "?")
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_logging_configured = False


def setup_logging(level: str = "INFO") -> None:
    global _logging_configured
    if _logging_configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger("mesh")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mesh.{name}")


# ══════════════════════════════════════════════════════════════════════
# 경로 가드 (NFR-S-05)
# ══════════════════════════════════════════════════════════════════════

_DATA_ROOT_VAR = "${MESH_DATA_ROOT}"


def safe_resolve(rel: str, root: Path) -> Path:
    """`${MESH_DATA_ROOT}` 치환 + 경로 탈출 거부.

    **파일을 여는 모든 코드가 이 함수를 쓴다.** `open()` 직접 호출을 금지한다.

    세션 JSON 의 open_paths 는 사람이 편집하므로 `../../../etc/passwd` 가
    들어갈 수 있다.

    절대 경로는 **거부한다.** `/etc/passwd` 를 `root/etc/passwd` 로 조용히
    재해석하면 root 안에 머물지만 의도와 다른 파일을 읽는다.
    조용한 재해석보다 큰 실패가 낫다 — 절대 경로는 설정 오류다 (NFR-PO-01).

    Raises:
        PathEscapeError: 빈 경로, 절대 경로, 또는 정규화 결과가 root 밖일 때
    """
    s = rel.replace(_DATA_ROOT_VAR + "/", "").replace(_DATA_ROOT_VAR, "")
    if not s.strip():
        raise PathEscapeError(f"빈 경로: {rel!r}")
    if "\x00" in s:
        # ⚠️ PBT(PB-S1) 가 찾은 결함. `os.path.realpath("\x00")` 는
        #    `ValueError: embedded null byte` 를 낸다 — `PathEscapeError` 가
        #    아니다. 이 함수의 계약은 "탈출 시도는 PathEscapeError" 이고,
        #    호출자는 그 예외만 처리한다. `ValueError` 가 새어 나가면
        #    업로드 경로에서 500 이 되고, 감사 로그에 남지 않는다.
        raise PathEscapeError(f"경로에 NUL 바이트가 있다: {rel!r}")
    if s.startswith(("/", "\\")) or (len(s) > 1 and s[1] == ":"):
        raise PathEscapeError(
            f"절대 경로는 저장·사용할 수 없다. MESH_DATA_ROOT 상대 경로를 쓰라: {rel!r}"
        )
    if "~" in s:
        raise PathEscapeError(f"홈 디렉터리 확장은 허용하지 않는다: {rel!r}")

    root_r = root.resolve()
    target = (root_r / s).resolve()
    if not target.is_relative_to(root_r):
        raise PathEscapeError(f"경로가 MESH_DATA_ROOT 밖을 가리킨다: {rel!r}")
    return target


def to_relative(path: Path, root: Path) -> str:
    """저장용 상대 경로. 절대 경로를 저장하지 않는다 (NFR-PO-01, FR-22)."""
    return path.resolve().relative_to(root.resolve()).as_posix()


# ══════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════

_LOCALHOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: 공개 SaaS 호스트. 여기에 해당하면 신뢰 경계가 시뮬레이션이라고 고지한다.
#: 숨기지 않고 드러내는 것이 이 프로젝트의 신뢰성이다.
_PUBLIC_LLM_HOSTS = ("friendli.ai", "openai.com", "anthropic.com", "googleapis.com")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw else default
    except ValueError as e:
        raise ConfigError(f"{key} 는 정수여야 한다: {raw!r}") from e


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    try:
        return float(raw) if raw else default
    except ValueError as e:
        raise ConfigError(f"{key} 는 실수여야 한다: {raw!r}") from e


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    return raw in {"1", "true", "yes", "on"} if raw else default


#: 피어 토큰 최소 길이. 짧은 토큰은 없는 것과 같다 — LAN 에서 시도 횟수 제한이 없다.
MIN_PEER_TOKEN_CHARS = 16

#: 피어 주소 최대 개수. 데모용 상한이고, 넘으면 설정 오류일 가능성이 높다.
MAX_PEERS = 8


def _default_node_name() -> str:
    """호스트명을 노드 이름으로 쓴다.

    사람이 화면에서 "어느 컴퓨터인지"를 알아야 하고, 대개 호스트명이 그 답이다.
    실패하면 빈 문자열이 아니라 고정 문자열을 준다 — 이름 없는 노드가 목록에
    나오면 무엇인지 알 수 없다.
    """
    import socket

    with contextlib.suppress(Exception):
        name = socket.gethostname().split(".")[0].strip()
        if name:
            return name
    return "unknown-node"


def _parse_peers(raw: str) -> tuple[str, ...]:
    """`MESH_PEERS` 파싱. `http`/`https` 만 받고 끝 슬래시를 떼어 낸다.

    스킴을 검사하는 이유: 이 값은 `httpx` 에 그대로 들어간다. 오타로
    `192.168.0.11:8080` (스킴 없음)을 적으면 상대 경로로 해석되어 조용히
    자기 자신을 부른다 — 그러면 "피어에 물었는데 내 답이 왔다"가 된다.
    """
    peers: list[str] = []
    for chunk in raw.replace(";", ",").split(","):
        url = chunk.strip().rstrip("/")
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            raise ConfigError(
                f"MESH_PEERS 항목에 스킴이 없다: {url!r}. http://192.168.0.11:8080 형태로 적으라"
            )
        if url not in peers:
            peers.append(url)
    if len(peers) > MAX_PEERS:
        raise ConfigError(f"MESH_PEERS 가 너무 많다 ({len(peers)} > {MAX_PEERS})")
    return tuple(peers)


@dataclass(frozen=True, slots=True)
class Config:
    # 경로 · 바인딩
    data_root: Path
    bind_host: str
    bind_port: int
    dev_mode: bool

    # 신뢰 구역 LLM — 이 값이 신뢰 경계의 위치를 정한다
    trusted_zone_llm_base_url: str
    exaone_model_id: str
    friendli_token: str | None
    exaone_mode: str  # live | mock
    exaone_timeout: int

    # Agent (경계 밖)
    agent_transport: Transport
    agent_model_id: str
    draft_model_id: str
    aws_region: str
    broker_api_url: str | None
    broker_api_key: str | None
    agent_timeout: int

    # 동작 파라미터
    session_stale_minutes: int
    max_targets: int
    total_timeout: int
    max_payload_bytes: int
    ngram_size: int
    ngram_size_internal: int
    confidence_auto: float
    confidence_escalate: float
    stale_confidence_factor: float

    # ── 브로드캐스트 선별 ─────────────────────────────────────────────
    #: 후보로 남길 점수 하한. 낮출수록 넓게 남는다.
    #:
    #: ⚠️ 이 값은 **경계와 무관하다.** 선별은 문서를 읽지 않고 경계를 넘지
    #:    않으므로, 넓게 잡아도 새로 나가는 것이 없다. 좁게 잡았을 때의 손해
    #:    (답할 수 있는 사람이 화면에서 사라진다) 가 더 크다 (`triage` 참조).
    broadcast_threshold: float
    #: 한 번의 브로드캐스트에서 후보로 남길 최대 인원.
    broadcast_max_relevant: int
    #: 질문자의 Agent 가 **자동으로** 물어볼 최대 인원.
    #:
    #: `broadcast_max_relevant` 보다 작게 잡는 것이 기본이다. 후보로 보여주는
    #: 것은 공짜지만 실제로 묻는 것은 매번 경계를 넘는 일이다 — 후보 6명에게
    #: 전부 자동으로 물으면 한 번의 질문이 경계를 6번 넘는다.
    consult_max_targets: int
    #: 데모에서 "나" 로 시작할 사람. 비우면 `agents.yaml` 의 첫 항목.
    #:
    #: ⚠️ **인증이 아니다.** 화면이 그 사실을 표시한다 (BR-U-15).
    demo_user: str | None

    # ── 피어 메시 (같은 네트워크의 다른 컴퓨터) ───────────────────────
    #: 이 노드의 이름. 화면과 피어 목록에 보인다. 기본값은 호스트명이다.
    node_name: str
    #: 피어 공유 비밀. **LAN 모드의 유일한 접근 통제다.**
    #:
    #: 피어 응답에는 재수화된 실제 이름이 들어간다 (설계 §3.6). 그것이 사내망을
    #: 건너간다면, 토큰이 없으면 같은 네트워크의 누구나 남의 지식을 조회할 수 있다.
    #: 그래서 `validate()` 가 **LAN 모드에서 토큰 없이 시작하는 것을 막는다.**
    peer_token: str | None
    #: 피어 노드 주소. `MESH_PEERS=http://192.168.0.11:8080,http://192.168.0.12:8080`
    peers: tuple[str, ...]
    #: `MESH_BIND_HOST` 가 localhost 가 아닐 때 명시적으로 허용했는지.
    allow_network_bind: bool
    # 데모 보조
    demo_now: datetime | None
    record_fixtures: bool
    #: 이미 있는 픽스처를 덮어쓸지. 기본값 `False` —
    #: 재녹화 한 번이 게이트를 조용히 뒤집는 것을 막는다 (`FixtureStore.save` 참조).
    fixture_overwrite: bool

    # ── 파생 ──────────────────────────────────────────────────────────

    @property
    def trust_boundary_simulated(self) -> bool:
        """신뢰 구역 LLM 이 공개 SaaS 엔드포인트인가.

        True 면 `make preflight` 와 화면 헤더가 "경계 시뮬레이션"을 표시한다.
        먼저 밝히는 것이 지적당하는 것보다 낫다.
        """
        url = self.trusted_zone_llm_base_url.lower()
        return any(h in url for h in _PUBLIC_LLM_HOSTS)

    @property
    def lan_mode(self) -> bool:
        """같은 네트워크의 다른 컴퓨터가 이 노드에 닿을 수 있는가."""
        return self.bind_host not in _LOCALHOSTS

    @property
    def uploads_root(self) -> Path:
        """업로드가 저장되는 뿌리. 하위 호환."""
        return self.corpus_root

    @property
    def shared_root(self) -> Path:
        """공유 데이터 루트 — agents/shared/"""
        return self.data_root / "shared"

    @property
    def vocab_path(self) -> Path:
        return self.shared_root / "vocab.json"

    @property
    def banned_path(self) -> Path:
        return self.shared_root / "banned.json"

    @property
    def pseudonyms_path(self) -> Path:
        return self.shared_root / "pseudonyms.json"

    @property
    def labels_path(self) -> Path:
        return self.shared_root / "labels.json"

    @property
    def questions_path(self) -> Path:
        return self.shared_root / "questions.json"

    @property
    def fixtures_root(self) -> Path:
        return self.shared_root / "fixtures"

    @property
    def db_path(self) -> Path:
        return self.shared_root / "mesh.db"

    # ── agent별 경로 헬퍼 ─────────────────────────────────────────────

    def _agent_safe_id(self, entity_id: str) -> str:
        """entity_id → 파일시스템 안전 디렉터리명. person:kim → person_kim"""
        return entity_id.replace(":", "_")

    def agent_root(self, entity_id: str) -> Path:
        """agents/{entity_id}/ — agent의 최상위 디렉터리"""
        return self.data_root / self._agent_safe_id(entity_id)

    def agent_data_root(self, entity_id: str) -> Path:
        """agents/{entity_id}/data/ — agent가 접근할 수 있는 지식 파일들"""
        return self.agent_root(entity_id) / "data"

    def agent_gatekeeper_root(self, entity_id: str) -> Path:
        """agents/{entity_id}/gatekeeper/ — 세션·검증 QA"""
        return self.agent_root(entity_id) / "gatekeeper"

    def agent_protocol_root(self, entity_id: str) -> Path:
        """agents/{entity_id}/security_protocol/ — 개인 보안 프로토콜"""
        return self.agent_root(entity_id) / "security_protocol"

    def agent_session_path(self, entity_id: str) -> Path:
        return self.agent_gatekeeper_root(entity_id) / "session.json"

    def agent_verified_path(self, entity_id: str) -> Path:
        return self.agent_gatekeeper_root(entity_id) / "verified.json"

    def agent_uploads_dir(self, entity_id: str) -> Path:
        return self.agent_data_root(entity_id) / "uploads"

    # ── 하위 호환: corpus_root는 shared/data/ 를 가리킨다 ─────────────
    # (기존 코드가 corpus_root를 직접 참조하는 곳이 있으면 안전하게 폴백)

    @property
    def corpus_root(self) -> Path:
        """하위 호환용. 새 코드는 agent_data_root(entity_id) 를 사용할 것."""
        return self.shared_root / "data"

    # ── 하위 호환: sessions_root / verified_root ──────────────────────
    # KnowledgeStore가 아직 이 프로퍼티를 직접 쓰는 경우를 위한 임시 유지

    @property
    def sessions_root(self) -> Path:
        """하위 호환용. 새 코드는 agent_session_path(entity_id) 를 사용할 것."""
        return self.data_root / "_legacy_sessions"

    @property
    def verified_root(self) -> Path:
        """하위 호환용. 새 코드는 agent_verified_path(entity_id) 를 사용할 것."""
        return self.data_root / "_legacy_verified"

    @property
    def agents_path(self) -> Path:
        return Path("config/agents.yaml")

    @property
    def org_path(self) -> Path:
        """조직도 정의. **없어도 앱은 뜬다** — `org.load_org()` 가 빈 차트를 준다.

        `agents.yaml` 옆에 두는 이유: 둘 다 "이 회사는 이렇게 생겼다"는 선언이고
        데이터(`MESH_DATA_ROOT`)가 아니다. 데이터 루트를 갈아끼워도 조직 구조는
        같아야 한다.
        """
        return Path("config/org.yaml")

    def now(self) -> datetime:
        """MESH_DEMO_NOW 가 설정돼 있으면 그 값. 데모 재현성 (BR-S-04)."""
        return self.demo_now or datetime.now().astimezone()

    # ── 로드 ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls, *, strict: bool = True) -> Config:
        """환경변수에서 로드하고 검증한다.

        Args:
            strict: True 면 fail-fast 검증을 수행한다.
                    테스트에서 부분 설정으로 인스턴스를 만들 때 False.
        """
        data_root_raw = _env("MESH_DATA_ROOT", "./agents")
        data_root = Path(data_root_raw)

        demo_now_raw = _env("MESH_DEMO_NOW")
        demo_now: datetime | None = None
        if demo_now_raw:
            try:
                demo_now = datetime.fromisoformat(demo_now_raw)
            except ValueError as e:
                raise ConfigError(f"MESH_DEMO_NOW 는 ISO 8601 이어야 한다: {demo_now_raw!r}") from e

        transport_raw = _env("AGENT_TRANSPORT", "direct").lower()
        if transport_raw not in {t.value for t in Transport}:
            raise ConfigError(
                f"AGENT_TRANSPORT 는 broker|direct|mock 중 하나여야 한다: {transport_raw!r}"
            )

        exaone_mode = _env("EXAONE_MODE", "live").lower()
        if exaone_mode not in {"live", "mock"}:
            raise ConfigError(f"EXAONE_MODE 는 live|mock 이어야 한다: {exaone_mode!r}")

        cfg = cls(
            data_root=data_root,
            bind_host=_env("MESH_BIND_HOST", "127.0.0.1"),
            bind_port=_env_int("MESH_BIND_PORT", 8080),
            dev_mode=_env_bool("MESH_DEV"),
            trusted_zone_llm_base_url=_env(
                "TRUSTED_ZONE_LLM_BASE_URL", "https://api.friendli.ai/dedicated/v1"
            ),
            exaone_model_id=_env("EXAONE_MODEL_ID", "depe675tjc2rcpo"),
            friendli_token=_env("FRIENDLI_TOKEN") or None,
            exaone_mode=exaone_mode,
            exaone_timeout=_env_int("EXAONE_TIMEOUT_SECONDS", 10),
            agent_transport=Transport(transport_raw),
            agent_model_id=_env("AGENT_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            draft_model_id=_env("DRAFT_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            aws_region=_env("AWS_REGION", "us-east-1"),
            broker_api_url=_env("BROKER_API_URL") or None,
            broker_api_key=_env("BROKER_API_KEY") or None,
            agent_timeout=_env_int("AGENT_TIMEOUT_SECONDS", 25),
            session_stale_minutes=_env_int("SESSION_STALE_MINUTES", 15),
            max_targets=_env_int("MAX_TARGETS", 2),
            total_timeout=_env_int("TOTAL_TIMEOUT_SECONDS", 30),
            max_payload_bytes=_env_int("MAX_PAYLOAD_BYTES", 2048),
            ngram_size=_env_int("NGRAM_SIZE", 5),
            ngram_size_internal=_env_int("NGRAM_SIZE_INTERNAL", 3),
            confidence_auto=_env_float("CONFIDENCE_AUTO", 0.75),
            confidence_escalate=_env_float("CONFIDENCE_ESCALATE", 0.45),
            stale_confidence_factor=_env_float("STALE_CONFIDENCE_FACTOR", 0.8),
            broadcast_threshold=_env_float("BROADCAST_THRESHOLD", 0.5),
            broadcast_max_relevant=_env_int("BROADCAST_MAX_RELEVANT", 6),
            consult_max_targets=_env_int("CONSULT_MAX_TARGETS", 3),
            demo_user=_env("MESH_DEMO_USER") or None,
            node_name=_env("MESH_NODE_NAME") or _default_node_name(),
            peer_token=_env("MESH_PEER_TOKEN") or None,
            peers=_parse_peers(_env("MESH_PEERS")),
            allow_network_bind=_env_bool("MESH_ALLOW_NETWORK_BIND"),
            demo_now=demo_now,
            record_fixtures=_env_bool("MESH_RECORD_FIXTURES"),
            fixture_overwrite=_env_bool("MESH_FIXTURE_OVERWRITE"),
        )
        if strict:
            cfg.validate()
        return cfg

    def validate(self) -> None:
        """fail-fast 검증. 잘못된 설정으로 도는 것보다 안 뜨는 게 낫다."""
        log = get_logger("config")

        if not self.data_root.exists():
            raise ConfigError(f"MESH_DATA_ROOT 가 존재하지 않는다: {self.data_root}")

        if self.data_root.is_absolute():
            log.warning(
                "MESH_DATA_ROOT 가 절대 경로다. 다른 컴퓨터로 옮길 때 깨진다 (NFR-PO-01)",
                extra={"data_root": str(self.data_root)},
            )

        if self.exaone_mode == "live" and not self.friendli_token:
            raise ConfigError(
                "EXAONE_MODE=live 인데 FRIENDLI_TOKEN 이 없다. "
                ".env 를 확인하거나 EXAONE_MODE=mock 으로 실행하라"
            )

        if self.agent_transport is Transport.BROKER and not (
            self.broker_api_url and self.broker_api_key
        ):
            raise ConfigError(
                "AGENT_TRANSPORT=broker 인데 BROKER_API_URL 또는 BROKER_API_KEY 가 없다. "
                "make deploy 후 출력값을 .env 에 기입하거나 AGENT_TRANSPORT=direct 로 실행하라"
            )

        if self.lan_mode:
            # ⚠️ **토큰 없이 LAN 모드로 시작할 수 없다.**
            #
            #    피어 응답에는 재수화된 실제 이름이 들어간다 (설계 §3.6). 토큰이
            #    없으면 같은 네트워크의 누구나 남의 지식을 조회할 수 있고, 그러면
            #    이 도구는 게이트키퍼를 우회하는 지름길이 된다.
            #
            #    경고로 두지 않는 이유: 경고는 아무도 읽지 않는다. Day 2 에서
            #    같은 판단을 했다 (`check_bind_host`).
            if not self.peer_token:
                raise ConfigError(
                    f"MESH_BIND_HOST={self.bind_host} 로 네트워크에 노출하려면 "
                    "MESH_PEER_TOKEN 이 필요하다.\n"
                    "  이 서비스는 원문 파일을 읽고 재수화된 실제 이름을 반환한다. "
                    "토큰이 LAN 모드의 유일한 접근 통제다.\n"
                    '  -> 토큰 생성:  python -c "import secrets; print(secrets.token_urlsafe(24))"\n'
                    "  -> 참여하는 모든 컴퓨터에 같은 값을 넣는다"
                )
            if len(self.peer_token) < MIN_PEER_TOKEN_CHARS:
                raise ConfigError(
                    f"MESH_PEER_TOKEN 이 너무 짧다 ({len(self.peer_token)} < "
                    f"{MIN_PEER_TOKEN_CHARS}자). LAN 에서는 시도 횟수 제한이 없다"
                )
            log.warning(
                "LAN 모드 — 같은 네트워크의 피어가 /api/peer/* 에 접근할 수 있다. "
                "소유자 표면(문서 목록·감사 로그·저장 경로)은 여전히 loopback 전용이다",
                extra={"bind_host": self.bind_host, "node_name": self.node_name},
            )

        if not 0.0 <= self.confidence_escalate <= self.confidence_auto <= 1.0:
            raise ConfigError(
                f"신뢰도 임계값이 잘못됐다: "
                f"escalate={self.confidence_escalate} auto={self.confidence_auto}"
            )

        if self.ngram_size < 3:
            raise ConfigError(f"NGRAM_SIZE 가 너무 작다 (오탐 폭발): {self.ngram_size}")

        for path in (self.vocab_path, self.banned_path, self.pseudonyms_path):
            if not path.exists():
                raise ConfigError(f"필수 데이터 파일이 없다: {path}")

        if self.trust_boundary_simulated:
            log.info(
                "신뢰 경계가 시뮬레이션이다. TRUSTED_ZONE_LLM_BASE_URL 이 공개 SaaS 를 "
                "가리킨다. 아키텍처가 보장하는 것은 '원문이 이 엔드포인트 하나에만 "
                "전달된다'이며, 사내망으로 옮기는 것은 이 값만 바꾸면 된다",
                extra={"trusted_zone_llm": self.trusted_zone_llm_base_url},
            )


# ══════════════════════════════════════════════════════════════════════
# 데이터 로더 (캐시)
# ══════════════════════════════════════════════════════════════════════


class DataBundle:
    """vocab / banned / rules / agents 를 한 번 로드해 공유한다.

    vocab_sha256 은 브로커 응답의 것과 비교해 어휘 사전 drift 를 탐지한다
    (U5 Vocabulary Version Pinning 패턴).
    """

    def __init__(self, cfg: Config, *, load_agent_configs: bool = True) -> None:
        self.cfg = cfg
        self.vocab: Vocabulary = Vocabulary.load(cfg.vocab_path)
        self.banned: BannedTerms = BannedTerms.load(cfg.banned_path)
        self.pseudonyms: PseudonymTargets = PseudonymTargets.load(cfg.pseudonyms_path)
        self._base_rules: ClassificationRules = ClassificationRules(banned=self.banned)
        self.vocab_sha256: str = sha256_file(cfg.vocab_path)
        self.agents: dict[str, AgentConfig] = (
            load_agents(cfg.agents_path) if load_agent_configs else {}
        )
        self._add_entity_ids_to_pseudonyms()
        self._check_lists_are_disjoint()

        # ── 조직도 ────────────────────────────────────────────────────
        #
        # ⚠️ 지연 import 다. `mesh.org` 는 `schemas`·`exceptions` 만 쓰는 같은 층
        #    모듈이지만, 파일 상단에서 부르면 `config` 가 조직도 없이는 못 뜨는
        #    것처럼 보인다. 조직도는 **표시용**이고 없어도 질의는 돌아야 한다.
        #
        # `banned` 를 넘기는 것이 요점이다. 조직도는 인증 없이 보이는 화면이라
        # 여기 실린 고객사명은 게이트키퍼를 우회한 유출이 된다 (FR-31 과 같은
        # 이유). 로드 시점에 막는다.
        from mesh.org import OrgChart, load_org  # 지연 import — 순환 방지

        self.org: OrgChart = load_org(cfg.org_path, banned=self.banned)

        # ProtocolStore — 보안 프로토콜 CRUD
        from mesh.protocol_store import ProtocolStore  # 지연 import — 순환 방지
        self.protocol_store: ProtocolStore = ProtocolStore(cfg.data_root, cfg=cfg)

    @property
    def placements(self) -> dict[str, OrgPlacement]:
        """`entity_id` -> 조직도의 자리. 자리가 없는 사람은 **빠진다.**

        `agents` 를 매번 훑는 이유: 테스트가 `data.agents[...]` 에 사람을
        꽂아 넣는 경로가 있고(FR-23 검증), 캐시하면 그 사람이 조직도에
        나타나지 않는다.
        """
        return {eid: a.org for eid, a in self.agents.items() if a.org is not None}

    @property
    def rules(self) -> ClassificationRules:
        """프로토콜 머지 결과를 항상 최신으로 반환한다.

        UI에서 프로토콜을 수정하면 다음 classify 호출부터 즉시 반영된다.
        서버 재시작 불필요.
        """
        try:
            return self.protocol_store.merged_rules(base_banned=self.banned)
        except Exception:
            # 프로토콜 파일 오류 시 기본 규칙으로 안전하게 폴백
            return self._base_rules

    def _add_entity_ids_to_pseudonyms(self) -> None:
        """등록된 `entity_id` 와 표시 이름을 PERSON 치환 대상에 넣는다.

        ⚠️ **G4 육안 확인이 찾아낸 결함이다** (자동 검사는 통과했다).

        `pseudonyms.json` 의 PERSON 목록에는 사람 이름(`박선영`)만 있었다.
        그런데 코퍼스 파일 헤더는 `# owner: person:park` 라고 적는다 —
        같은 사람의 **다른 표기**다. 그래서 사내 등급 발췌가 경계를 넘을 때
        `person:park` 가 그대로 실려 나갔다.

        자동 검사가 놓친 이유: `sweep_for_leaks` 는 "식별자를 포함한 n-gram"만
        보고, `person:park` 는 식별자 목록에 없었으므로 식별자가 아니었다.
        **목록에 없는 것은 검사되지 않는다** — 이것이 G4 를 사람이 하는 이유다.

        `pseudonyms.json` 에 손으로 적지 않는 이유: `agents.yaml` 에 사람을
        추가할 때 두 파일을 고쳐야 하고, 하나를 잊으면 조용히 새어 나간다.
        FR-23 이 "설정 한 곳"을 요구하는 것과 같은 이유다. 여기서 유도하면
        새 사람이 자동으로 보호된다.
        """
        if not self.agents:
            return
        extra: list[str] = []
        for entity_id, agent in self.agents.items():
            extra.append(entity_id)
            # `person:kim` 의 지역 부분(`kim`)은 넣지 않는다. 2~3자 토큰은
            # 본문의 무관한 단어와 충돌해 답변을 망가뜨린다 (BR-P-01 의 반대편
            # 위험). 전체 형태만 치환하고, 사람 이름은 아래에서 다룬다.
            if agent.display_name:
                extra.append(agent.display_name)
        person = tuple(dict.fromkeys((*self.pseudonyms.targets.get("PERSON", ()), *extra)))
        self.pseudonyms = self.pseudonyms.model_copy(
            update={"targets": {**self.pseudonyms.targets, "PERSON": person}}
        )

    def _check_lists_are_disjoint(self) -> None:
        """차단 목록과 치환 목록이 겹치면 안 된다.

        겹치면 그 항목이 SECRET 상향을 유발해 가명화 경로가 실행되지 않는다.
        v1.0.0 에서 실제로 발생한 결함이라 로드 시점에 검사한다.
        """
        banned_low = {lit.lower() for lit in self.banned.literals}
        overlap = [lit for _, lit in self.pseudonyms.all_literals() if lit.lower() in banned_low]
        if overlap:
            raise ConfigError(
                "banned.json 과 pseudonyms.json 이 겹친다. 겹친 항목은 SECRET 상향을 "
                f"유발해 가명화 경로가 실행되지 않는다: {sorted(overlap)}"
            )

    def agent(self, entity_id: str) -> AgentConfig:
        try:
            return self.agents[entity_id]
        except KeyError as e:
            raise ConfigError(f"미등록 에이전트: {entity_id!r}") from e


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_canonical(obj: object) -> str:
    """페이로드 무결성 해시. 키 순서에 무관하게 안정적이어야 한다 —
    로컬과 Lambda 가 같은 값을 계산해야 한다 (SECURITY-13)."""
    canon = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_agents(path: Path) -> dict[str, AgentConfig]:
    """config/agents.yaml 로드.

    에이전트 추가가 이 파일에 항목 하나 더하는 것으로 끝나야 한다 (FR-23).
    """
    if not path.exists():
        raise ConfigError(f"에이전트 설정이 없다: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("agents") or []
    if not items:
        raise ConfigError(f"{path} 에 agents 항목이 없다")

    out: dict[str, AgentConfig] = {}
    for item in items:
        disclose_raw = item.get("disclose") or {}
        # expertise 는 끌 수 없다 (Literal[True]). 설정에 있으면 무시하고 경고.
        if disclose_raw.pop("expertise", True) is not True:
            get_logger("config").warning(
                "disclose.expertise 는 끌 수 없다. 담당 영역을 숨기면 지목이 불가능해진다",
                extra={"entity_id": item.get("entity_id")},
            )
        # 조직도의 자리. **id 만 받는다** — 본부·팀 이름을 여기 적게 하면
        # 조직 개편이 이 파일 열 줄을 고치는 일이 된다 (OrgPlacement 참조).
        org_raw = item.get("org")
        placement: OrgPlacement | None = None
        if org_raw:
            try:
                placement = OrgPlacement(**org_raw)
            except Exception as e:  # noqa: BLE001 — 어느 항목인지 알려주고 멈춘다
                raise ConfigError(
                    f"{item.get('entity_id')!r} 의 org 항목이 잘못됐다: {e}"
                ) from e

        cfg = AgentConfig(
            entity_id=item["entity_id"],
            display_name=item["display_name"],
            expertise=item["expertise"],
            persona_prompt=item["persona_prompt"],
            knowledge_scope=tuple(item["knowledge_scope"]),
            escalation_inbox=item.get("escalation_inbox", item["entity_id"]),
            daily_limit=int(item.get("daily_limit", 50)),
            disclose=Disclose(**disclose_raw),
            org=placement,
            topics=tuple(str(t) for t in (item.get("topics") or [])),
        )
        if cfg.entity_id in out:
            raise ConfigError(f"중복 entity_id: {cfg.entity_id}")
        out[cfg.entity_id] = cfg
    return out


# ══════════════════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════════════════
#
# ⚠️ `normalize_text()` 는 여기 없다. `mesh.validator` 로 옮겼다.
#
#    U5 Lambda 가 재검증을 위해 `validator.py` 를 번들하는데, 그 함수가
#    `config.py` 에 있으면 Lambda 가 `yaml`·환경변수까지 끌고 들어간다.
#    그리고 로컬과 Lambda 가 **같은 정규화**를 써야 판정이 갈리지 않으므로
#    구현이 두 곳에 있어서는 안 된다. 5-gram 을 쓰는 모듈 옆에 둔다.

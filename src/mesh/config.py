"""설정 · 경로 가드 · 구조화 로깅.

전 유닛이 공유한다 (`shared-infrastructure.md` §5, §6).

세 가지를 담당한다:
  1. Config.load()   — 환경변수 로드 + fail-fast 검증
  2. safe_resolve()  — 경로 탈출 거부 (NFR-S-05). 파일을 여는 모든 코드가 이걸 쓴다
  3. 로깅           — JSON 구조화 + 금지 필드 리댁션. 개발자가 실수해도 원문이 안 남는다
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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

    # 데모 보조
    demo_now: datetime | None
    record_fixtures: bool

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
    def vocab_path(self) -> Path:
        return self.data_root / "vocab.json"

    @property
    def banned_path(self) -> Path:
        return self.data_root / "banned.json"

    @property
    def pseudonyms_path(self) -> Path:
        return self.data_root / "pseudonyms.json"

    @property
    def labels_path(self) -> Path:
        return self.data_root / "labels.json"

    @property
    def questions_path(self) -> Path:
        return self.data_root / "questions.json"

    @property
    def corpus_root(self) -> Path:
        return self.data_root / "corpus"

    @property
    def sessions_root(self) -> Path:
        return self.data_root / "sessions"

    @property
    def verified_root(self) -> Path:
        return self.data_root / "verified"

    @property
    def fixtures_root(self) -> Path:
        return self.data_root / "fixtures"

    @property
    def db_path(self) -> Path:
        return self.data_root / "mesh.db"

    @property
    def agents_path(self) -> Path:
        return Path("config/agents.yaml")

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
        data_root_raw = _env("MESH_DATA_ROOT", "./data")
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
            demo_now=demo_now,
            record_fixtures=_env_bool("MESH_RECORD_FIXTURES"),
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

        if self.bind_host not in _LOCALHOSTS:
            log.warning(
                "MESH_BIND_HOST 가 localhost 가 아니다. 이 서비스는 원문 파일을 읽고 "
                "재수화된 실제 이름을 반환한다. 인증이 없는 MVP 에서 네트워크에 노출하면 "
                "권한 우회 도구가 된다 (BR-M-01)",
                extra={"bind_host": self.bind_host},
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
        self.rules: ClassificationRules = ClassificationRules(banned=self.banned)
        self.vocab_sha256: str = sha256_file(cfg.vocab_path)
        self.agents: dict[str, AgentConfig] = (
            load_agents(cfg.agents_path) if load_agent_configs else {}
        )
        self._check_lists_are_disjoint()

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
        cfg = AgentConfig(
            entity_id=item["entity_id"],
            display_name=item["display_name"],
            expertise=item["expertise"],
            persona_prompt=item["persona_prompt"],
            knowledge_scope=tuple(item["knowledge_scope"]),
            escalation_inbox=item.get("escalation_inbox", item["entity_id"]),
            daily_limit=int(item.get("daily_limit", 50)),
            disclose=Disclose(**disclose_raw),
        )
        if cfg.entity_id in out:
            raise ConfigError(f"중복 entity_id: {cfg.entity_id}")
        out[cfg.entity_id] = cfg
    return out


# ══════════════════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════════════════

_WS_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """5-gram 대조와 divergent 판정에 쓰는 정규화.

    공백 축약 + 소문자화. 공백만 바꿔 우회하는 것을 막는다 (BR-V-05).
    """
    return _WS_RE.sub(" ", s).strip().lower()

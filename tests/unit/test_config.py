"""config.py — 경로 가드 · 로그 리댁션 · fail-fast 검증."""

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from mesh.config import (
    FORBIDDEN_LOG_KEYS,
    REDACTED,
    Config,
    DataBundle,
    JsonFormatter,
    RedactingFilter,
    correlation_id,
    load_agents,
    safe_resolve,
    sha256_canonical,
    to_relative,
)
from mesh.exceptions import ConfigError, PathEscapeError
from mesh.schemas import Transport

REPO = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════════════════════
# 경로 가드 (NFR-S-05) — 세션 JSON 은 사람이 편집하므로 탈출 시도가 들어올 수 있다
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "corpus" / "kim").mkdir(parents=True)
    (tmp_path / "corpus" / "kim" / "doc.md").write_text("hi", encoding="utf-8")
    (tmp_path / "secret-outside.txt").write_text("nope", encoding="utf-8")
    return tmp_path / "corpus"


@pytest.mark.parametrize(
    "rel",
    [
        "../secret-outside.txt",
        "../../etc/passwd",
        "kim/../../secret-outside.txt",
        "kim/../../../../../../etc/passwd",
        "${MESH_DATA_ROOT}/../secret-outside.txt",
        "kim/./../../secret-outside.txt",
    ],
)
def test_path_traversal_is_rejected(root: Path, rel: str):
    with pytest.raises(PathEscapeError, match="밖을 가리킨다"):
        safe_resolve(rel, root)


@pytest.mark.parametrize("rel", ["/etc/passwd", "/Users/jaeyeol/work/x.md", "C:\\Windows\\x"])
def test_absolute_path_is_rejected_not_reinterpreted(root: Path, rel: str):
    """절대 경로를 root 하위로 조용히 재해석하지 않는다.
    root/etc/passwd 는 root 안이지만 의도와 다른 파일이다."""
    with pytest.raises(PathEscapeError, match="절대 경로"):
        safe_resolve(rel, root)


@pytest.mark.parametrize("rel", ["~/work/sdk/docs/auth-design.md", "kim/~doc.md"])
def test_home_expansion_is_rejected(root: Path, rel: str):
    """설계 문서의 ~/work/... 표기는 코퍼스 경로로 쓰지 않는다 (BR-D-06)."""
    with pytest.raises(PathEscapeError, match="홈 디렉터리"):
        safe_resolve(rel, root)


@pytest.mark.parametrize(
    "rel",
    [
        "kim/doc.md",
        "./kim/doc.md",
        "${MESH_DATA_ROOT}/kim/doc.md",
        "${MESH_DATA_ROOT}kim/doc.md",
    ],
)
def test_valid_paths_resolve(root: Path, rel: str):
    p = safe_resolve(rel, root)
    assert p.is_relative_to(root.resolve())
    assert p.name == "doc.md"


def test_empty_path_rejected(root: Path):
    with pytest.raises(PathEscapeError):
        safe_resolve("${MESH_DATA_ROOT}", root)


def test_nonexistent_but_inside_is_allowed(root: Path):
    """존재 검사는 호출자 책임. safe_resolve 는 경계만 본다."""
    assert safe_resolve("kim/ghost.md", root).name == "ghost.md"


def test_to_relative_roundtrip(root: Path):
    p = safe_resolve("kim/doc.md", root)
    assert to_relative(p, root) == "kim/doc.md"


# ══════════════════════════════════════════════════════════════════════
# 로그 리댁션 (NFR-S-03) — 개발자가 실수해도 원문이 남지 않아야 한다
# ══════════════════════════════════════════════════════════════════════


def _emit(**extra) -> dict:
    """RedactingFilter + JsonFormatter 를 통과한 로그 레코드를 dict 로 반환."""
    rec = logging.LogRecord("mesh.test", logging.INFO, __file__, 1, "msg", (), None)
    for k, v in extra.items():
        rec.__dict__[k] = v
    assert RedactingFilter().filter(rec) is True
    return json.loads(JsonFormatter().format(rec))


def test_forbidden_top_level_key_is_redacted():
    out = _emit(text="H社 REQ-4412 인증은 세션에 바인딩된 EAP-AKA 방식이어야 한다")
    assert out["text"] == REDACTED
    assert "REQ-4412" not in json.dumps(out, ensure_ascii=False)


def test_reasoning_is_redacted():
    """EXAONE thinking 이 원문을 인용할 수 있다 (실측 확인)."""
    out = _emit(reasoning="문서에는 '세션 최대 유지시간은 8시간이다' 라고 나와 있으므로...")
    assert out["reasoning"] == REDACTED
    out2 = _emit(reasoning_content="원문 인용...")
    assert out2["reasoning_content"] == REDACTED


def test_nested_forbidden_key_is_redacted():
    out = _emit(response={"choices": [{"message": {"reasoning": "원문 인용", "content": "OK"}}]})
    dumped = json.dumps(out, ensure_ascii=False)
    assert "원문 인용" not in dumped
    assert "OK" in dumped  # 정상 필드는 남는다


def test_credentials_are_redacted():
    out = _emit(friendli_token="flp_abcdefghijklmnop", broker_api_key="key123")
    assert out["friendli_token"] == REDACTED
    assert out["broker_api_key"] == REDACTED


def test_mapping_key_is_redacted():
    out = _emit(mapping={"REQ_A": "고객사 H · REQ-4412"})
    assert out["mapping"] == REDACTED


def test_session_focus_is_redacted():
    """세션 focus 에 고객사명이 있다."""
    out = _emit(focus="고객사 H 인증 요구사항 검토")
    assert out["focus"] == REDACTED


def test_allowed_fields_survive():
    out = _emit(tier="secret", validation="6/6", size_bytes=1124, envelope_id="env_abc")
    assert out["tier"] == "secret"
    assert out["validation"] == "6/6"
    assert out["size_bytes"] == 1124


def test_log_has_required_structure():
    out = _emit()
    assert {"at", "level", "correlation_id", "component", "message"} <= set(out)
    assert out["component"] == "test"  # "mesh." 접두사 제거


def test_correlation_id_is_included():
    token = correlation_id.set("req_01JTEST")
    try:
        assert _emit()["correlation_id"] == "req_01JTEST"
    finally:
        correlation_id.reset(token)


def test_deep_nesting_is_capped():
    """순환·폭발 방지. 깊이 6에서 멈춘다."""
    deep: dict = {"a": {}}
    node = deep["a"]
    for _ in range(20):
        node["a"] = {}
        node = node["a"]
    node["text"] = "원문"
    out = _emit(payload=deep)
    assert "원문" not in json.dumps(out, ensure_ascii=False)


def test_forbidden_keys_cover_the_measured_risks():
    """실측에서 확인한 유출 채널이 목록에 있는지."""
    for key in ("reasoning", "reasoning_content", "text", "mapping", "focus", "summary"):
        assert key in FORBIDDEN_LOG_KEYS


# ══════════════════════════════════════════════════════════════════════
# Config fail-fast 검증
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def base_env(mock_env):
    """conftest 의 mock_env 별칭. 새 필수 데이터 파일은 conftest 한 곳만 고친다."""
    return mock_env


def test_config_loads_with_mock_modes(base_env):
    cfg = Config.load()
    assert cfg.exaone_mode == "mock"
    assert cfg.agent_transport is Transport.MOCK


def test_live_mode_without_token_fails(base_env, monkeypatch):
    monkeypatch.setenv("EXAONE_MODE", "live")
    with pytest.raises(ConfigError, match="FRIENDLI_TOKEN"):
        Config.load()


def test_broker_mode_without_url_fails(base_env, monkeypatch):
    monkeypatch.setenv("AGENT_TRANSPORT", "broker")
    with pytest.raises(ConfigError, match="BROKER_API_URL"):
        Config.load()


def test_broker_mode_with_credentials_ok(base_env, monkeypatch):
    monkeypatch.setenv("AGENT_TRANSPORT", "broker")
    monkeypatch.setenv("BROKER_API_URL", "https://x.execute-api.us-east-1.amazonaws.com/prod")
    monkeypatch.setenv("BROKER_API_KEY", "k")
    assert Config.load().agent_transport is Transport.BROKER


def test_missing_data_root_fails(base_env, monkeypatch):
    monkeypatch.setenv("MESH_DATA_ROOT", "/nonexistent/path/xyz")
    with pytest.raises(ConfigError, match="MESH_DATA_ROOT"):
        Config.load()


def test_bad_transport_fails(base_env, monkeypatch):
    monkeypatch.setenv("AGENT_TRANSPORT", "ftp")
    with pytest.raises(ConfigError, match="AGENT_TRANSPORT"):
        Config.load()


def test_bad_exaone_mode_fails(base_env, monkeypatch):
    monkeypatch.setenv("EXAONE_MODE", "sometimes")
    with pytest.raises(ConfigError, match="EXAONE_MODE"):
        Config.load()


def test_inverted_confidence_thresholds_fail(base_env, monkeypatch):
    monkeypatch.setenv("CONFIDENCE_AUTO", "0.3")
    monkeypatch.setenv("CONFIDENCE_ESCALATE", "0.8")
    with pytest.raises(ConfigError, match="신뢰도 임계값"):
        Config.load()


def test_tiny_ngram_fails(base_env, monkeypatch):
    monkeypatch.setenv("NGRAM_SIZE", "2")
    with pytest.raises(ConfigError, match="NGRAM_SIZE"):
        Config.load()


def test_bad_demo_now_fails(base_env, monkeypatch):
    monkeypatch.setenv("MESH_DEMO_NOW", "yesterday")
    with pytest.raises(ConfigError, match="MESH_DEMO_NOW"):
        Config.load()


def test_demo_now_fixes_clock(base_env, monkeypatch):
    monkeypatch.setenv("MESH_DEMO_NOW", "2026-08-19T14:35:00+09:00")
    cfg = Config.load()
    assert cfg.now() == datetime.fromisoformat("2026-08-19T14:35:00+09:00")


def test_now_without_override_is_live(base_env):
    cfg = Config.load()
    assert cfg.demo_now is None
    assert (datetime.now().astimezone() - cfg.now()).total_seconds() < 5


# ══════════════════════════════════════════════════════════════════════
# LAN 모드 — 같은 네트워크의 다른 컴퓨터
# ══════════════════════════════════════════════════════════════════════
#
# 이 서비스는 원문 파일을 읽고 **재수화된 실제 이름**을 반환한다. 네트워크에
# 노출하는 순간 토큰이 유일한 접근 통제가 된다. 그래서 경고가 아니라 **거부**다.

VALID_TOKEN = "x" * 32


def test_lan_mode_without_token_is_refused(base_env, monkeypatch):
    """**경고가 아니라 시작 거부다.** 경고는 아무도 읽지 않는다."""
    monkeypatch.setenv("MESH_BIND_HOST", "0.0.0.0")
    with pytest.raises(ConfigError, match="MESH_PEER_TOKEN"):
        Config.load()


def test_lan_mode_rejects_a_short_token(base_env, monkeypatch):
    """LAN 에서는 시도 횟수 제한이 없다 — 짧은 토큰은 없는 것과 같다."""
    monkeypatch.setenv("MESH_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("MESH_PEER_TOKEN", "short")
    with pytest.raises(ConfigError, match="너무 짧다"):
        Config.load()


def test_lan_mode_loads_with_a_token(base_env, monkeypatch, caplog):
    monkeypatch.setenv("MESH_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("MESH_PEER_TOKEN", VALID_TOKEN)
    with caplog.at_level(logging.WARNING, logger="mesh.config"):
        cfg = Config.load()
    assert cfg.bind_host == "0.0.0.0"
    assert cfg.lan_mode is True
    # 소유자 표면이 여전히 loopback 전용임을 로그로 밝힌다
    assert any("loopback 전용" in r.message for r in caplog.records)


def test_localhost_does_not_need_a_token(base_env):
    cfg = Config.load()
    assert cfg.lan_mode is False
    assert cfg.peer_token is None


def test_lan_mode_is_decided_by_bind_host_not_peer_list(base_env, monkeypatch):
    """피어 목록이 비어도 **누가 나를 부를 수는** 있다.

    목록 유무로 판단하면 "나는 아무에게도 안 묻는다"가
    "아무도 나에게 못 묻는다"로 잘못 읽힌다.
    """
    monkeypatch.setenv("MESH_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("MESH_PEER_TOKEN", VALID_TOKEN)
    cfg = Config.load()
    assert cfg.peers == ()
    assert cfg.lan_mode is True


def test_node_name_defaults_to_hostname(base_env):
    cfg = Config.load()
    assert cfg.node_name
    assert "." not in cfg.node_name, "짧은 호스트명을 쓴다"


def test_node_name_can_be_overridden(base_env, monkeypatch):
    monkeypatch.setenv("MESH_NODE_NAME", "김책임-맥북")
    assert Config.load().node_name == "김책임-맥북"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ()),
        ("http://192.168.0.11:8080", ("http://192.168.0.11:8080",)),
        ("http://a:8080/", ("http://a:8080",)),
        ("http://a:8080, http://b:8080", ("http://a:8080", "http://b:8080")),
        ("http://a:8080,http://a:8080", ("http://a:8080",)),
        ("http://a:8080;http://b:8080", ("http://a:8080", "http://b:8080")),
        ("  ,  ", ()),
    ],
    ids=["빈값", "한개", "끝슬래시", "여러개", "중복제거", "세미콜론", "공백만"],
)
def test_peer_list_parsing(base_env, monkeypatch, raw: str, expected: tuple[str, ...]):
    monkeypatch.setenv("MESH_PEERS", raw)
    assert Config.load().peers == expected


def test_peer_without_scheme_is_refused(base_env, monkeypatch):
    """스킴이 없으면 상대 경로로 해석되어 **조용히 자기 자신을 부른다.**

    그러면 "피어에 물었는데 내 답이 왔다"가 되고, 그것을 알아차릴 방법이 없다.
    """
    monkeypatch.setenv("MESH_PEERS", "192.168.0.11:8080")
    with pytest.raises(ConfigError, match="스킴이 없다"):
        Config.load()


def test_too_many_peers_is_refused(base_env, monkeypatch):
    monkeypatch.setenv("MESH_PEERS", ",".join(f"http://h{i}:8080" for i in range(20)))
    with pytest.raises(ConfigError, match="너무 많다"):
        Config.load()


# ══════════════════════════════════════════════════════════════════════
# 신뢰 경계 고지 — 숨기지 않고 드러낸다
# ══════════════════════════════════════════════════════════════════════


def test_friendli_endpoint_is_flagged_as_simulated(base_env, monkeypatch):
    monkeypatch.setenv("TRUSTED_ZONE_LLM_BASE_URL", "https://api.friendli.ai/dedicated/v1")
    assert Config.load().trust_boundary_simulated is True


def test_internal_endpoint_is_not_flagged(base_env, monkeypatch):
    monkeypatch.setenv("TRUSTED_ZONE_LLM_BASE_URL", "https://exaone.internal.corp/v1")
    assert Config.load().trust_boundary_simulated is False


# ══════════════════════════════════════════════════════════════════════
# 해시 — 로컬과 Lambda 가 같은 값을 계산해야 한다
# ══════════════════════════════════════════════════════════════════════


def test_sha256_canonical_is_key_order_independent():
    a = {"task": "x", "domain": "y", "entities": [{"ref": "A", "role": "r"}]}
    b = {"entities": [{"role": "r", "ref": "A"}], "domain": "y", "task": "x"}
    assert sha256_canonical(a) == sha256_canonical(b)


def test_sha256_canonical_detects_change():
    a = {"max_session_hours": 8}
    b = {"max_session_hours": 9}
    assert sha256_canonical(a) != sha256_canonical(b)


# ══════════════════════════════════════════════════════════════════════
# agents.yaml
# ══════════════════════════════════════════════════════════════════════


def test_load_agents_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="에이전트 설정이 없다"):
        load_agents(tmp_path / "nope.yaml")


def test_load_agents_rejects_empty(tmp_path):
    p = tmp_path / "agents.yaml"
    p.write_text("agents: []", encoding="utf-8")
    with pytest.raises(ConfigError, match="agents 항목이 없다"):
        load_agents(p)


def _agent_yaml(entity_id: str = "person:kim", disclose: str = "") -> str:
    return f"""
agents:
  - entity_id: {entity_id}
    display_name: 김철수 책임
    expertise: 인증 · SSO
    persona_prompt: |
      당신은 김철수 책임의 Agent입니다.
    knowledge_scope: [corpus/kim/**]
    escalation_inbox: {entity_id}
{disclose}
"""


def test_load_agents_ok(tmp_path):
    p = tmp_path / "agents.yaml"
    p.write_text(_agent_yaml(), encoding="utf-8")
    agents = load_agents(p)
    assert "person:kim" in agents
    a = agents["person:kim"]
    assert a.disclose.expertise is True
    assert a.disclose.activity_status is False  # opt-in 이 기본


def test_load_agents_rejects_duplicate(tmp_path):
    p = tmp_path / "agents.yaml"
    p.write_text(_agent_yaml() + _agent_yaml().replace("agents:", ""), encoding="utf-8")
    with pytest.raises(ConfigError, match="중복"):
        load_agents(p)


def test_disclose_expertise_false_is_ignored_with_warning(tmp_path, caplog):
    p = tmp_path / "agents.yaml"
    p.write_text(
        _agent_yaml(
            disclose="    disclose:\n      expertise: false\n      activity_status: true\n"
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="mesh.config"):
        agents = load_agents(p)
    assert agents["person:kim"].disclose.expertise is True
    assert agents["person:kim"].disclose.activity_status is True
    assert any("끌 수 없다" in r.message for r in caplog.records)


def test_to_persona_label(tmp_path):
    p = tmp_path / "agents.yaml"
    p.write_text(_agent_yaml(), encoding="utf-8")
    persona = load_agents(p)["person:kim"].to_persona()
    assert persona.agent_label == "김철수 책임의 Agent"


# ══════════════════════════════════════════════════════════════════════
# DataBundle
# ══════════════════════════════════════════════════════════════════════


def test_data_bundle_loads_without_agents(base_env):
    cfg = Config.load()
    bundle = DataBundle(cfg, load_agent_configs=False)
    assert bundle.vocab.version == "1.0.0"
    assert bundle.banned.literals
    assert bundle.rules.banned == bundle.banned  # 판정과 차단이 같은 사전을 쓴다
    assert len(bundle.vocab_sha256) == 64


# ══════════════════════════════════════════════════════════════════════
# log_extra — logging 예약어 충돌 방어
#
# `extra={"name": ...}` 는 KeyError 를 던진다. 로그 한 줄 때문에 요청이 죽는다.
# 실제로 이 버그를 test_exaone.py 가 잡았다.
# ══════════════════════════════════════════════════════════════════════


def test_log_extra_renames_reserved_keys():
    from mesh.config import log_extra

    out = log_extra(name="classify", module="x", args=(1,), tier="secret")
    assert out == {"x_name": "classify", "x_module": "x", "x_args": (1,), "tier": "secret"}


@pytest.mark.parametrize(
    "key", ["name", "module", "args", "msg", "levelname", "message", "asctime", "pathname"]
)
def test_reserved_keys_are_known(key):
    from mesh.config import RESERVED_LOG_KEYS

    assert key in RESERVED_LOG_KEYS


@pytest.fixture
def enabled_logger():
    """레코드가 실제로 생성되도록 레벨을 낮춘 로거.

    레벨이 막혀 있으면 logging 이 makeRecord 를 부르지 않아 충돌 검사가
    무의미해진다 — 통과하는 것처럼 보이는 빈 테스트가 된다.
    """
    logger = logging.getLogger("mesh.test_reserved")
    prev = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield logger
    finally:
        logger.setLevel(prev)


@pytest.mark.parametrize("key", ["name", "module", "args", "levelname", "message"])
def test_log_extra_output_is_accepted_by_logging(enabled_logger, key):
    """log_extra 를 거친 dict 는 logging 이 받아들여야 한다."""
    from mesh.config import log_extra

    enabled_logger.info("msg", extra=log_extra(**{key: "v"}))  # KeyError 면 실패


def test_raw_reserved_key_would_break_logging(enabled_logger):
    """가드가 필요한 이유를 문서화한다 — 이게 실제로 터졌던 버그다."""
    with pytest.raises(KeyError, match="name"):
        enabled_logger.info("msg", extra={"name": "classify"})


def test_log_extra_survives_redaction():
    """접두사가 붙어도 금지 키 검사는 살아 있어야 한다."""
    from mesh.config import log_extra

    out = _emit(**log_extra(text="원문 문장", tier="secret"))
    assert out["text"] == REDACTED
    assert out["tier"] == "secret"

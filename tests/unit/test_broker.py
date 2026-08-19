"""BrokerClient — 3개 전송 모드와 fail-closed 동작.

가장 중요한 테스트: 브로커가 `revalidated: true` 를 주지 않으면 응답을 거부한다.
검증을 건너뛴 경로로 배포됐을 수 있기 때문이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from mesh.config import Config, sha256_canonical, sha256_file
from mesh.exceptions import BrokerError, FixtureMissing
from mesh.llm.broker import BrokerClient
from mesh.schemas import PayloadEnvelope, Representation, Tier

REPO = Path(__file__).resolve().parents[2]

PAYLOAD = {
    "task": "constraint_conflict_check",
    "domain": "authentication",
    "entities": [
        {
            "ref": "REQ_A",
            "role": "external_requirement",
            "facts": {"session_binding": "required", "max_session_hours": 8},
        },
        {
            "ref": "COMP_B",
            "role": "our_component",
            "facts": {"session_binding": "none", "credential_lifetime_hours": 24},
        },
    ],
    "question_template": "conflict_and_mitigation",
}

GOOD_BROKER_RESPONSE = {
    "envelope_id": "env_" + "A" * 22,
    "answer": {
        "conflict": True,
        "reason": "REQ_A 는 세션 바인딩을 요구하나 COMP_B 는 세션과 무관하게 갱신한다",
        "mitigations": ["COMP_B 수명을 REQ_A 상한 이하로"],
    },
    "confidence": 0.83,
    "citations": ["REQ_A", "COMP_B"],
    "usage": {"inputTokens": 412, "outputTokens": 288},
    "revalidated": True,
}


@pytest.fixture
def env() -> PayloadEnvelope:
    body = json.dumps(PAYLOAD, ensure_ascii=False)
    return PayloadEnvelope(
        envelope_id="env_" + "A" * 22,
        tier=Tier.SECRET,
        task_schema_id="constraint_conflict_check",
        payload=PAYLOAD,
        representation=Representation.STRUCTURED,
        payload_sha256=sha256_canonical(PAYLOAD),
        size_bytes=len(body.encode()),
    )


def _cfg(monkeypatch, mock_env, **env_over) -> Config:
    for k, v in env_over.items():
        monkeypatch.setenv(k, v)
    return Config.load()


def _broker_cfg(monkeypatch, mock_env) -> Config:
    return _cfg(
        monkeypatch,
        mock_env,
        AGENT_TRANSPORT="broker",
        BROKER_API_URL="https://api.example.invalid/prod",
        BROKER_API_KEY="test-key",
    )


def _client(cfg: Config, handler) -> BrokerClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=cfg.broker_api_url or "https://x.invalid",
        timeout=1,
    )
    return BrokerClient(cfg, client=http)


# ══════════════════════════════════════════════════════════════════════
# broker 모드
# ══════════════════════════════════════════════════════════════════════


async def test_broker_success(monkeypatch, mock_env, env):
    cfg = _broker_cfg(monkeypatch, mock_env)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=GOOD_BROKER_RESPONSE)

    out = await _client(cfg, handler).invoke(env, "sys prompt", cfg.agent_model_id)

    assert out.confidence == 0.83
    assert out.citations == ("REQ_A", "COMP_B")
    assert out.revalidated is True
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["body"]["payload_sha256"] == env.payload_sha256
    assert captured["body"]["tier"] == "secret"


async def test_broker_without_revalidated_is_rejected(monkeypatch, mock_env, env):
    """fail closed: 브로커가 재검증을 안 했다는 신호면 응답을 거부한다."""
    cfg = _broker_cfg(monkeypatch, mock_env)
    body = {**GOOD_BROKER_RESPONSE, "revalidated": False}

    with pytest.raises(BrokerError, match="재검증하지 않았다"):
        await _client(cfg, lambda _: httpx.Response(200, json=body)).invoke(
            env, "sys", cfg.agent_model_id
        )


async def test_broker_missing_revalidated_field_is_rejected(monkeypatch, mock_env, env):
    cfg = _broker_cfg(monkeypatch, mock_env)
    body = {k: v for k, v in GOOD_BROKER_RESPONSE.items() if k != "revalidated"}

    with pytest.raises(BrokerError, match="재검증하지 않았다"):
        await _client(cfg, lambda _: httpx.Response(200, json=body)).invoke(
            env, "sys", cfg.agent_model_id
        )


async def test_broker_400_does_not_leak_detail(monkeypatch, mock_env, env):
    """브로커의 Uninformative Rejection 을 클라이언트도 유지한다."""
    cfg = _broker_cfg(monkeypatch, mock_env)
    body = {"error": "payload_rejected", "stage": "vocab", "envelope_id": env.envelope_id}

    with pytest.raises(BrokerError) as exc:
        await _client(cfg, lambda _: httpx.Response(400, json=body)).invoke(
            env, "sys", cfg.agent_model_id
        )
    msg = str(exc.value)
    assert "vocab" in msg  # 단계는 남는다 (로컬 디버깅용)
    assert "400" in msg


async def test_broker_timeout_raises_broker_error(monkeypatch, mock_env, env):
    cfg = _broker_cfg(monkeypatch, mock_env)

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("t")

    with pytest.raises(BrokerError, match="타임아웃"):
        await _client(cfg, handler).invoke(env, "sys", cfg.agent_model_id)


async def test_vocab_drift_warns_but_does_not_block(monkeypatch, mock_env, env, caplog):
    """데모 중에 이걸로 죽으면 안 된다. 경고만 한다."""
    import logging

    cfg = _broker_cfg(monkeypatch, mock_env)
    body = {**GOOD_BROKER_RESPONSE, "vocab_sha256": "f" * 64}

    with caplog.at_level(logging.WARNING, logger="mesh.llm.broker"):
        out = await _client(cfg, lambda _: httpx.Response(200, json=body)).invoke(
            env, "sys", cfg.agent_model_id
        )
    assert out.confidence == 0.83  # 차단하지 않는다
    assert any("drift" in r.message for r in caplog.records)


async def test_matching_vocab_sha_does_not_warn(monkeypatch, mock_env, env, caplog):
    import logging

    cfg = _broker_cfg(monkeypatch, mock_env)
    body = {**GOOD_BROKER_RESPONSE, "vocab_sha256": sha256_file(cfg.vocab_path)}

    with caplog.at_level(logging.WARNING, logger="mesh.llm.broker"):
        await _client(cfg, lambda _: httpx.Response(200, json=body)).invoke(
            env, "sys", cfg.agent_model_id
        )
    assert not [r for r in caplog.records if "drift" in r.message]


# ══════════════════════════════════════════════════════════════════════
# direct 모드
# ══════════════════════════════════════════════════════════════════════


class _FakeBedrock:
    def __init__(self, text: str, *, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return {
            "output": {"message": {"content": [{"text": self.text}]}},
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }


async def test_direct_mode_parses_json_answer(monkeypatch, mock_env, env):
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")
    text = json.dumps(
        {
            "conflict": True,
            "reason": "REQ_A 와 COMP_B 가 충돌한다",
            "mitigations": ["a", "b"],
            "confidence": 0.83,
            "citations": ["REQ_A", "COMP_B"],
        },
        ensure_ascii=False,
    )
    fake = _FakeBedrock(text)
    out = await BrokerClient(cfg, bedrock=fake).invoke(env, "sys prompt", cfg.agent_model_id)

    assert out.confidence == 0.83
    assert out.citations == ("REQ_A", "COMP_B")
    assert out.answer["conflict"] is True
    assert "confidence" not in out.answer  # answer 에서 분리됐다
    assert out.revalidated is True  # direct 는 로컬 검증이 유일 (의도된 예외)
    assert fake.calls[0]["modelId"] == cfg.agent_model_id
    assert fake.calls[0]["inferenceConfig"]["temperature"] == 0


async def test_direct_mode_strips_markdown_fence(monkeypatch, mock_env, env):
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")
    text = '```json\n{"conflict": false, "confidence": 0.9, "citations": ["REQ_A"]}\n```'
    out = await BrokerClient(cfg, bedrock=_FakeBedrock(text)).invoke(env, "s", cfg.agent_model_id)
    assert out.confidence == 0.9
    assert out.answer["conflict"] is False


async def test_direct_mode_freetext_becomes_zero_citations(monkeypatch, mock_env, env):
    """JSON 이 아니면 인용 0개가 되어 Orchestrator 가 에스컬레이션한다 — 안전한 방향."""
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")
    out = await BrokerClient(cfg, bedrock=_FakeBedrock("충돌합니다. 이유는...")).invoke(
        env, "s", cfg.agent_model_id
    )
    assert out.citations == ()
    assert out.confidence == 0.0


async def test_direct_mode_clamps_confidence(monkeypatch, mock_env, env):
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")
    text = '{"x": 1, "confidence": 7.5, "citations": ["A"]}'
    out = await BrokerClient(cfg, bedrock=_FakeBedrock(text)).invoke(env, "s", cfg.agent_model_id)
    assert out.confidence == 1.0


async def test_direct_mode_bad_confidence_becomes_zero(monkeypatch, mock_env, env):
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")
    text = '{"x": 1, "confidence": "높음", "citations": ["A"]}'
    out = await BrokerClient(cfg, bedrock=_FakeBedrock(text)).invoke(env, "s", cfg.agent_model_id)
    assert out.confidence == 0.0


async def test_direct_mode_string_citation_is_wrapped(monkeypatch, mock_env, env):
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")
    text = '{"x": 1, "confidence": 0.8, "citations": "REQ_A"}'
    out = await BrokerClient(cfg, bedrock=_FakeBedrock(text)).invoke(env, "s", cfg.agent_model_id)
    assert out.citations == ("REQ_A",)


async def test_direct_mode_client_error_raises_broker_error(monkeypatch, mock_env, env):
    from botocore.exceptions import ClientError

    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")
    err = ClientError({"Error": {"Code": "AccessDeniedException"}}, "Converse")
    fake = _FakeBedrock("", error=err)

    with pytest.raises(BrokerError, match="AccessDeniedException"):
        await BrokerClient(cfg, bedrock=fake).invoke(env, "s", cfg.agent_model_id)


async def test_direct_mode_malformed_response_raises(monkeypatch, mock_env, env):
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct")

    class Broken:
        def converse(self, **_):
            return {"output": {}}

    with pytest.raises(BrokerError, match="형식"):
        await BrokerClient(cfg, bedrock=Broken()).invoke(env, "s", cfg.agent_model_id)


# ══════════════════════════════════════════════════════════════════════
# mock 모드
# ══════════════════════════════════════════════════════════════════════


async def test_mock_mode_missing_fixture_raises(monkeypatch, mock_env, env):
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="mock")
    with pytest.raises(FixtureMissing, match="MESH_RECORD_FIXTURES"):
        await BrokerClient(cfg).invoke(env, "s", cfg.agent_model_id)


async def test_record_in_direct_then_replay_in_mock(monkeypatch, mock_env, env):
    """녹화 -> 재생 왕복. 확인하지 않으면 데모 당일 실패한다."""
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct", MESH_RECORD_FIXTURES="1")
    text = json.dumps(
        {"conflict": True, "reason": "r", "confidence": 0.83, "citations": ["REQ_A"]},
        ensure_ascii=False,
    )
    recorded = await BrokerClient(cfg, bedrock=_FakeBedrock(text)).invoke(
        env, "sys", cfg.agent_model_id
    )

    mock_cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="mock", MESH_RECORD_FIXTURES="0")
    replayed = await BrokerClient(mock_cfg).invoke(env, "sys", mock_cfg.agent_model_id)

    assert replayed.confidence == recorded.confidence
    assert replayed.citations == recorded.citations
    assert replayed.answer == recorded.answer


async def test_fixture_key_depends_on_payload(monkeypatch, mock_env, env):
    """페이로드가 다르면 다른 픽스처를 찾는다."""
    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="direct", MESH_RECORD_FIXTURES="1")
    text = '{"x": 1, "confidence": 0.8, "citations": ["A"]}'
    await BrokerClient(cfg, bedrock=_FakeBedrock(text)).invoke(env, "s", cfg.agent_model_id)

    other = env.model_copy(update={"payload": {**PAYLOAD, "domain": "deployment"}})
    mock_cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="mock", MESH_RECORD_FIXTURES="0")
    with pytest.raises(FixtureMissing):
        await BrokerClient(mock_cfg).invoke(other, "s", mock_cfg.agent_model_id)


# ══════════════════════════════════════════════════════════════════════
# 설정 방어
# ══════════════════════════════════════════════════════════════════════


async def test_broker_mode_requires_url(monkeypatch, mock_env, env):
    """Config.validate 를 우회해 만든 경우에도 방어한다."""
    import dataclasses

    from mesh.schemas import Transport

    cfg = _cfg(monkeypatch, mock_env, AGENT_TRANSPORT="mock")
    broken = dataclasses.replace(cfg, agent_transport=Transport.BROKER, broker_api_url=None)
    with pytest.raises(BrokerError, match="BROKER_API_URL"):
        await BrokerClient(broken).invoke(env, "s", cfg.agent_model_id)


async def test_broker_mode_requires_api_key(monkeypatch, mock_env, env):
    """인증 헤더 없이 경계 밖으로 나가지 않는다."""
    import dataclasses

    cfg = _broker_cfg(monkeypatch, mock_env)
    broken = dataclasses.replace(cfg, broker_api_key=None)
    client = BrokerClient(
        broken,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=GOOD_BROKER_RESPONSE)),
            base_url=cfg.broker_api_url,
        ),
    )
    with pytest.raises(BrokerError, match="BROKER_API_KEY"):
        await client.invoke(env, "s", cfg.agent_model_id)

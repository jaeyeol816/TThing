"""ExaoneClient — reasoning* 삭제와 재시도 정책.

reasoning* 삭제가 이 파일에서 가장 중요한 테스트다.
실패하면 원문이 로그·감사 기록으로 흘러간다 (실측 확인된 유출 채널).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from mesh.config import Config
from mesh.exceptions import ExaoneUnavailable, FixtureMissing
from mesh.llm.exaone import STRIP_KEYS, ExaoneClient, strip_thinking

REPO = Path(__file__).resolve().parents[2]

#: 실측한 실제 EXAONE 응답 형태 (enable_thinking=True 일 때).
#: reasoning 에 원문이 인용돼 있다 — 이게 유출 채널이다.
REAL_RESPONSE_WITH_THINKING = {
    "id": "chatcmpl-x",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": '{"session_binding": "required"}',
                "reasoning": (
                    "문서에 'H社 5G 코어망 요구사항 REQ-4412: 인증은 세션에 바인딩된 "
                    "EAP-AKA 방식이어야 하며' 라고 나와 있으므로 session_binding 은 required 다."
                ),
                "reasoning_content": "원문 재인용: 세션 최대 유지시간은 8시간이다.",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 235, "completion_tokens": 204},
}

LEAKED_PHRASES = ("REQ-4412", "EAP-AKA", "H社", "세션 최대 유지시간은 8시간이다")


# ══════════════════════════════════════════════════════════════════════
# strip_thinking — 순수 함수. 이게 유출 방어의 핵심
# ══════════════════════════════════════════════════════════════════════


def test_strip_thinking_removes_both_keys():
    msg = REAL_RESPONSE_WITH_THINKING["choices"][0]["message"]
    clean = strip_thinking(msg)
    for key in STRIP_KEYS:
        assert key not in clean
    assert clean["content"] == '{"session_binding": "required"}'


def test_strip_thinking_removes_original_text():
    msg = REAL_RESPONSE_WITH_THINKING["choices"][0]["message"]
    dumped = json.dumps(strip_thinking(msg), ensure_ascii=False)
    for phrase in LEAKED_PHRASES:
        assert phrase not in dumped, f"원문 조각이 남았다: {phrase}"


def test_strip_thinking_does_not_mutate_input():
    msg = dict(REAL_RESPONSE_WITH_THINKING["choices"][0]["message"])
    strip_thinking(msg)
    assert "reasoning" in msg  # 사본을 반환한다


def test_strip_keys_covers_measured_fields():
    assert set(STRIP_KEYS) == {"reasoning", "reasoning_content"}


# ══════════════════════════════════════════════════════════════════════
# 요청 본문 — enable_thinking=False 고정
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def cfg(monkeypatch, mock_env) -> Config:
    monkeypatch.setenv("EXAONE_MODE", "live")
    monkeypatch.setenv("FRIENDLI_TOKEN", "flp_test")
    monkeypatch.setenv("EXAONE_TIMEOUT_SECONDS", "10")
    return Config.load()


def _client(cfg: Config, handler) -> ExaoneClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=cfg.trusted_zone_llm_base_url, timeout=1)
    return ExaoneClient(cfg, client=http)


async def test_request_disables_thinking(cfg):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=REAL_RESPONSE_WITH_THINKING)

    async with _client(cfg, handler) as c:
        await c.complete_json("sys", "user", name="classify")

    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["temperature"] == 0
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["model"] == cfg.exaone_model_id


async def test_text_mode_omits_response_format(cfg):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "자유 텍스트 답변"}}]})

    async with _client(cfg, handler) as c:
        text = await c.complete_text("sys", "user")

    assert text == "자유 텍스트 답변"
    assert "response_format" not in captured
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}


async def test_response_thinking_never_reaches_caller(cfg):
    """엔드투엔드: 서버가 reasoning 을 보내도 호출자는 못 본다."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=REAL_RESPONSE_WITH_THINKING)

    async with _client(cfg, handler) as c:
        out = await c.complete_json("sys", "user", name="classify")

    dumped = json.dumps(out, ensure_ascii=False)
    for phrase in LEAKED_PHRASES:
        assert phrase not in dumped
    assert out == {"session_binding": "required"}


# ══════════════════════════════════════════════════════════════════════
# 재시도 정책
# ══════════════════════════════════════════════════════════════════════


async def test_json_parse_failure_retries_twice_then_succeeds(cfg):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["messages"][1]["content"])
        content = "not json" if len(calls) < 3 else '{"ok": true}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with _client(cfg, handler) as c:
        out = await c.complete_json("sys", "질문", name="extract")

    assert out == {"ok": True}
    assert len(calls) == 3
    assert "Output valid JSON only" in calls[1]  # 재시도에 nudge 가 붙는다
    assert "Output valid JSON only" not in calls[0]


async def test_json_parse_failure_gives_up_after_three(cfg):
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "nope"}}]})

    async with _client(cfg, handler) as c:
        with pytest.raises(ExaoneUnavailable, match="3회 실패"):
            await c.complete_json("sys", "user", name="extract")

    assert len(calls) == 3


async def test_timeout_is_not_retried(cfg):
    """10s x 3 = 30s 로 전체 예산을 다 먹으므로 재시도하지 않는다."""
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("timeout")

    async with _client(cfg, handler) as c:
        with pytest.raises(ExaoneUnavailable, match="타임아웃"):
            await c.complete_json("sys", "user", name="classify")

    assert len(calls) == 1


async def test_http_error_is_not_retried(cfg):
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={"error": "H社 관련 원문이 담긴 오류"})

    async with _client(cfg, handler) as c:
        with pytest.raises(ExaoneUnavailable) as exc:
            await c.complete_json("sys", "user", name="classify")

    assert len(calls) == 1
    # 응답 본문을 예외 메시지에 넣지 않는다 (원문 반사 방지)
    assert "H社" not in str(exc.value)
    assert "503" in str(exc.value)


# ══════════════════════════════════════════════════════════════════════
# 잘못된 응답 형태 — 전부 ExaoneUnavailable 로 귀결 (fail closed)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
    ],
)
async def test_malformed_response_raises(cfg, body):
    async with _client(cfg, lambda _: httpx.Response(200, json=body)) as c:
        with pytest.raises(ExaoneUnavailable):
            await c.complete_json("sys", "user", name="classify")


async def test_json_array_is_rejected(cfg):
    """JSON 객체를 기대한다. 배열이면 조립 단계가 깨진다."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "[1,2,3]"}}]})

    async with _client(cfg, handler) as c:
        with pytest.raises(ExaoneUnavailable, match="JSON 객체가 아니다"):
            await c.complete_json("sys", "user", name="classify")


# ══════════════════════════════════════════════════════════════════════
# 목업 모드 — 재생 실패는 명시적이어야 한다
# ══════════════════════════════════════════════════════════════════════


async def test_mock_mode_missing_fixture_raises_explicitly(cfg, monkeypatch):
    """조용히 기본값을 반환하면 리허설에서 누락을 발견할 수 없다."""
    monkeypatch.setenv("EXAONE_MODE", "mock")
    c = ExaoneClient(Config.load())
    with pytest.raises(FixtureMissing, match="MESH_RECORD_FIXTURES"):
        await c.complete_json("sys", "user", name="classify")


async def test_record_then_replay_roundtrip(cfg, monkeypatch, mock_env):
    """녹화 -> 재생이 같은 결과를 내야 한다. 이걸 확인하지 않으면 데모 당일 실패한다."""
    monkeypatch.setenv("MESH_RECORD_FIXTURES", "1")
    rec_cfg = Config.load()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=REAL_RESPONSE_WITH_THINKING)

    async with _client(rec_cfg, handler) as c:
        recorded = await c.complete_json("sys-prompt", "user-prompt", name="classify")

    monkeypatch.setenv("EXAONE_MODE", "mock")
    monkeypatch.delenv("MESH_RECORD_FIXTURES")
    replayed = await ExaoneClient(Config.load()).complete_json(
        "sys-prompt", "user-prompt", name="classify"
    )

    assert replayed == recorded

    # 녹화된 픽스처 파일에도 reasoning 이 없어야 한다
    files = list((mock_env / "fixtures" / "exaone").glob("classify_*.json"))
    assert len(files) == 1
    saved = files[0].read_text(encoding="utf-8")
    for phrase in LEAKED_PHRASES:
        assert phrase not in saved, f"픽스처 파일에 원문이 남았다: {phrase}"


async def test_fixture_key_changes_with_prompt(cfg, monkeypatch):
    """프롬프트를 바꾸면 키가 달라져 재생이 실패한다 — 다시 녹화하라는 신호."""
    monkeypatch.setenv("MESH_RECORD_FIXTURES", "1")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async with _client(Config.load(), handler) as c:
        await c.complete_json("sys-A", "user", name="classify")

    monkeypatch.setenv("EXAONE_MODE", "mock")
    monkeypatch.delenv("MESH_RECORD_FIXTURES")
    with pytest.raises(FixtureMissing):
        await ExaoneClient(Config.load()).complete_json("sys-B", "user", name="classify")

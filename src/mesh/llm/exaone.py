"""신뢰 구역 LLM (EXAONE) 클라이언트.

원문을 보는 **유일한** 모델이다. 이 클라이언트가 호출하는 엔드포인트가
`TRUSTED_ZONE_LLM_BASE_URL` 이고, 그 값이 신뢰 경계의 위치를 정한다.

실측 기반 고정 설정 (`aidlc-docs/construction/preflight-findings.md` §1):

  temperature = 0
      결정성. 3회 반복 동일 결과 확인.

  chat_template_kwargs = {"enable_thinking": False}          ⚠️ FR-14
      true 면 응답에 reasoning / reasoning_content 가 온다.
      이 필드는 모델의 사고 과정이므로 **원문 문장을 그대로 포함할 수 있다.**
      지연도 함께 줄어든다 (91 -> 4 completion tokens 수준).

  response_format = {"type": "json_object"}
      실측 지원 확인. JSON 파싱 실패율을 크게 낮춘다.

  응답에서 reasoning* 키를 **파싱 전에 삭제**              ⚠️ FR-14
      파싱 중 예외가 나면 예외 메시지에 원문이 실릴 수 있다.
      그래서 삭제가 파싱보다 먼저다.

재시도 정책:
  JSON 파싱 실패 -> 2회 재시도 ("output valid JSON only" 덧붙임)
  타임아웃       -> **재시도하지 않는다.** 10s x 3 = 30s 로 전체 예산을 다 먹는다
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from mesh.config import Config, get_logger
from mesh.exceptions import ExaoneUnavailable
from mesh.llm.fixtures import FixtureStore, fixture_key

log = get_logger("llm.exaone")

#: 응답에서 반드시 제거할 키. 원문 유출 채널이다.
STRIP_KEYS: tuple[str, ...] = ("reasoning", "reasoning_content")

MAX_ATTEMPTS = 3  # 1회 + 재시도 2회
_JSON_NUDGE = "\n\nOutput valid JSON only. No prose, no markdown fences."


def strip_thinking(message: dict[str, Any]) -> dict[str, Any]:
    """reasoning* 키를 제거한 사본을 반환.

    순수 함수로 분리한 이유: 테스트가 이것만 따로 검증할 수 있어야 한다.
    이게 실패하면 원문이 로그와 감사 기록으로 흘러간다.
    """
    return {k: v for k, v in message.items() if k not in STRIP_KEYS}


class ExaoneClient:
    """OpenAI 호환 `/chat/completions` 호출. 전용 SDK 를 쓰지 않는다 —
    엔드포인트 교체(사내망 전환)에 httpx 직접 호출이 더 유연하다."""

    def __init__(self, cfg: Config, *, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self._client = client
        self._owns_client = client is None
        self._fixtures = FixtureStore(cfg.fixtures_root, record=cfg.record_fixtures)

    # ── 수명 관리 ────────────────────────────────────────────────────

    async def __aenter__(self) -> ExaoneClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.cfg.trusted_zone_llm_base_url,
                timeout=httpx.Timeout(self.cfg.exaone_timeout),
                headers={
                    "Authorization": f"Bearer {self.cfg.friendli_token or ''}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    # ── 공개 API ─────────────────────────────────────────────────────

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        name: str = "generic",
        max_tokens: int = 800,
    ) -> dict:
        """JSON 객체를 받는다. 등급 판정 · 구조 추출 · 경로 선택에 쓰인다.

        Args:
            name: 픽스처 파일 이름 접두사 (classify / extract / select_paths ...)

        Raises:
            ExaoneUnavailable: 타임아웃, HTTP 오류, 또는 3회 시도 후 파싱 실패
        """
        key = fixture_key(name, system, user)

        if self.cfg.exaone_mode == "mock":
            return self._fixtures.load("exaone", name, key)

        content = await self._request_text(system, user, max_tokens, expect_json=True, name=name)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:  # pragma: no cover - _request_text 가 이미 검증
            raise ExaoneUnavailable(f"JSON 파싱 실패: {e}") from e

        if not isinstance(parsed, dict):
            raise ExaoneUnavailable(f"JSON 객체가 아니다: {type(parsed).__name__}")

        self._fixtures.save("exaone", name, key, parsed)
        return parsed

    async def complete_text(
        self,
        system: str,
        user: str,
        *,
        name: str = "answer",
        max_tokens: int = 1200,
    ) -> str:
        """자유 텍스트를 받는다. **신뢰 구역 내 폴백 답변 생성 전용**
        (`Gatekeeper.answer_in_zone`).

        이 응답은 사용자에게 직접 가고 경계를 넘지 않는다.
        """
        key = fixture_key(name, system, user)

        if self.cfg.exaone_mode == "mock":
            return self._fixtures.load("exaone", name, key)["text"]

        text = await self._request_text(system, user, max_tokens, expect_json=False, name=name)
        self._fixtures.save("exaone", name, key, {"text": text})
        return text

    # ── 내부 ─────────────────────────────────────────────────────────

    def _body(self, system: str, user: str, max_tokens: int, expect_json: bool) -> dict:
        body: dict[str, Any] = {
            "model": self.cfg.exaone_model_id,
            "temperature": 0,
            "max_tokens": max_tokens,
            # ⚠️ 원문 유출 채널 차단 (FR-14). 절대 True 로 바꾸지 말 것.
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if expect_json:
            body["response_format"] = {"type": "json_object"}
        return body

    async def _request_text(
        self, system: str, user: str, max_tokens: int, *, expect_json: bool, name: str
    ) -> str:
        current_user = user
        last_error: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await self._http().post(
                    "/chat/completions",
                    json=self._body(system, current_user, max_tokens, expect_json),
                )
            except httpx.TimeoutException as e:
                # 타임아웃은 재시도하지 않는다 — 전체 예산을 다 먹는다
                raise ExaoneUnavailable(
                    f"EXAONE 타임아웃 ({self.cfg.exaone_timeout}s). name={name}"
                ) from e
            except httpx.HTTPError as e:
                raise ExaoneUnavailable(f"EXAONE 연결 실패: {type(e).__name__}") from e

            if resp.status_code >= 400:
                # 응답 본문을 예외 메시지에 넣지 않는다 (원문 반사 가능성)
                raise ExaoneUnavailable(f"EXAONE HTTP {resp.status_code}. name={name}")

            content = self._extract_content(resp.json())

            if not expect_json:
                return content
            try:
                json.loads(content)
                return content
            except json.JSONDecodeError as e:
                last_error = e
                log.warning(
                    "EXAONE JSON 파싱 실패, 재시도",
                    extra={"attempt": attempt + 1, "op": name},
                )
                current_user = user + _JSON_NUDGE
                await asyncio.sleep(0.2 * (attempt + 1))

        raise ExaoneUnavailable(
            f"EXAONE JSON 파싱 {MAX_ATTEMPTS}회 실패. name={name}"
        ) from last_error

    @staticmethod
    def _extract_content(raw: dict) -> str:
        """응답에서 content 를 꺼낸다.

        ⚠️ reasoning* 삭제를 **파싱보다 먼저** 한다.
           파싱 중 예외가 나면 예외 메시지에 원문이 실릴 수 있다.
        """
        try:
            choice = raw["choices"][0]
        except (KeyError, IndexError, TypeError) as e:
            raise ExaoneUnavailable("EXAONE 응답에 choices 가 없다") from e

        message = choice.get("message")
        if not isinstance(message, dict):
            raise ExaoneUnavailable("EXAONE 응답에 message 가 없다")

        clean = strip_thinking(message)  # ⚠️ 여기서 원문 유출 채널을 끊는다
        content = clean.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ExaoneUnavailable("EXAONE 응답 content 가 비어 있다")
        return content.strip()

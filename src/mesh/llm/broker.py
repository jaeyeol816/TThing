"""경계 밖 Agent (Claude) 호출.

⚠️ **`gatekeeper.py` 만 이 모듈을 import 한다** (경계 규칙, SECURITY-11).
   `tests/unit/test_import_boundary.py` 가 ast 로 파싱해 강제한다.

세 가지 전송 모드 (FR-49). 전환은 환경변수 하나 + 앱 재시작이고 코드 변경이 없다.

  broker  Lambda 경유. 재검증 2겹 + 지울 수 없는 감사. **데모 기본**
          노트북에 AWS 자격증명이 필요 없다 (Lambda 실행 역할 사용)
  direct  노트북에서 Bedrock 직접. CDK 미배포·브로커 장애 시 안전망
  mock    픽스처 재생. 오프라인 데모

실측 확인된 모델 ID (`preflight-findings.md` §2):
  us.anthropic.claude-sonnet-4-5-20250929-v1:0    2.17s   기본값
  us.anthropic.claude-haiku-4-5-20251001-v1:0     0.92s   에스컬레이션 초안
  설계 문서의 `claude-sonnet-5` 는 이 계정에서 AccessDeniedException.
  모든 Claude 가 추론 프로파일(`us.` 접두사) 필수.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from mesh.config import Config, get_logger
from mesh.exceptions import BrokerError
from mesh.llm.fixtures import FixtureStore, fixture_key
from mesh.schemas import AgentResponse, PayloadEnvelope, Transport

log = get_logger("llm.broker")


class BrokerClient:
    """Agent 호출의 유일한 구현. Gatekeeper 가 소유한다."""

    def __init__(
        self,
        cfg: Config,
        *,
        client: httpx.AsyncClient | None = None,
        bedrock: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self._client = client
        self._owns_client = client is None
        self._bedrock = bedrock
        self._fixtures = FixtureStore(cfg.fixtures_root, record=cfg.record_fixtures)

    # ── 수명 관리 ────────────────────────────────────────────────────

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            if not self.cfg.broker_api_url:
                raise BrokerError("BROKER_API_URL 이 없다")
            self._client = httpx.AsyncClient(
                base_url=self.cfg.broker_api_url,
                timeout=httpx.Timeout(self.cfg.agent_timeout),
            )
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        """요청마다 붙인다. 클라이언트 생성 시에만 붙이면 주입된 클라이언트에서
        인증 헤더가 빠지고, 그 사실이 테스트로 드러나지 않는다."""
        if not self.cfg.broker_api_key:
            raise BrokerError("BROKER_API_KEY 가 없다")
        return {"x-api-key": self.cfg.broker_api_key, "Content-Type": "application/json"}

    def _bedrock_client(self) -> Any:
        if self._bedrock is None:
            import boto3  # 지연 import — mock/broker 모드에서 필요 없다
            from botocore.config import Config as BotoConfig

            self._bedrock = boto3.client(
                "bedrock-runtime",
                region_name=self.cfg.aws_region,
                config=BotoConfig(
                    read_timeout=self.cfg.agent_timeout,
                    connect_timeout=5,
                    retries={"max_attempts": 1},  # 재시도는 상위에서 결정한다
                ),
            )
        return self._bedrock

    # ── 공개 API ─────────────────────────────────────────────────────

    async def invoke(
        self,
        env: PayloadEnvelope,
        system_prompt: str,
        model_id: str,
    ) -> AgentResponse:
        """경계를 넘는 호출.

        ⚠️ 이 메서드는 `Gatekeeper.ask_agent()` 에서만 호출된다.
           그쪽에서 검증 통과와 사용자 승인을 이미 확인했다.

        Raises:
            BrokerError: 호출 실패 또는 재검증 미수행. 호출자는 answer_in_zone 폴백
        """
        match self.cfg.agent_transport:
            case Transport.BROKER:
                return await self._via_broker(env, system_prompt, model_id)
            case Transport.DIRECT:
                return await self._via_bedrock(env, system_prompt, model_id)
            case Transport.MOCK:
                return self._via_fixture(env, model_id)

    # ── broker 모드 ──────────────────────────────────────────────────

    async def _via_broker(
        self, env: PayloadEnvelope, system_prompt: str, model_id: str
    ) -> AgentResponse:
        body = {
            "envelope_id": env.envelope_id,
            "task_schema_id": env.task_schema_id,
            "tier": env.tier.value,
            "payload": env.payload,
            "system_prompt": system_prompt,
            "model_id": model_id,
            "payload_sha256": env.payload_sha256,
        }
        client = self._http()
        headers = self._auth_headers()
        try:
            resp = await client.post("/agent/invoke", json=body, headers=headers)
        except httpx.TimeoutException as e:
            raise BrokerError(f"브로커 타임아웃 ({self.cfg.agent_timeout}s)") from e
        except httpx.HTTPError as e:
            raise BrokerError(f"브로커 연결 실패: {type(e).__name__}") from e

        if resp.status_code >= 400:
            # 브로커는 실패 상세를 담지 않는다 (Uninformative Rejection).
            # 여기서도 응답 본문을 예외 메시지에 넣지 않는다.
            payload = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            raise BrokerError(
                f"브로커 거부 HTTP {resp.status_code} "
                f"stage={payload.get('stage', '?')} error={payload.get('error', '?')}"
            )

        data = resp.json()
        out = self._to_agent_response(data)

        # ⚠️ fail closed: 브로커가 재검증을 안 했다는 신호면 응답을 거부한다.
        #    검증을 건너뛴 경로로 배포됐을 수 있다.
        if not out.revalidated:
            raise BrokerError("브로커가 재검증하지 않았다 (revalidated != true)")

        self._warn_on_vocab_drift(out.vocab_sha256)
        return out

    def _warn_on_vocab_drift(self, remote_sha: str | None) -> None:
        """어휘 사전 버전 고정 (U5 Vocabulary Version Pinning).

        차단하지 않고 경고만 한다 — 데모 중에 이걸로 죽으면 안 된다.
        근본 대응은 Makefile 의 `deploy: bundle-lambda` 의존이다.
        """
        if remote_sha is None:
            return
        from mesh.config import sha256_file

        local = sha256_file(self.cfg.vocab_path)
        if remote_sha != local:
            log.warning(
                "어휘 사전 drift — 브로커 재배포 필요 (make deploy)",
                extra={"local_vocab": local[:12], "remote_vocab": remote_sha[:12]},
            )

    # ── direct 모드 ──────────────────────────────────────────────────

    async def _via_bedrock(
        self, env: PayloadEnvelope, system_prompt: str, model_id: str
    ) -> AgentResponse:
        import asyncio

        from botocore.exceptions import BotoCoreError, ClientError

        def _call() -> dict:
            return self._bedrock_client().converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"text": json.dumps(env.payload, ensure_ascii=False, indent=2)}
                        ],
                    }
                ],
                inferenceConfig={"maxTokens": 2000, "temperature": 0},
            )

        try:
            raw = await asyncio.to_thread(_call)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "?")
            raise BrokerError(f"Bedrock 오류: {code}") from e
        except BotoCoreError as e:
            raise BrokerError(f"Bedrock 연결 실패: {type(e).__name__}") from e

        try:
            text = raw["output"]["message"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise BrokerError("Bedrock 응답 형식이 예상과 다르다") from e

        out = self._parse_agent_text(text, usage=raw.get("usage"))

        # direct 모드에서는 로컬 검증이 유일한 검증이다.
        # 그 사실을 감사 로그의 transport=direct 로 남긴다 (의도된 예외).
        out = out.model_copy(update={"revalidated": True})

        key = fixture_key("agent", env.task_schema_id, json.dumps(env.payload, sort_keys=True))
        self._fixtures.save("agent", env.task_schema_id, key, out.model_dump(mode="json"))
        return out

    # ── mock 모드 ────────────────────────────────────────────────────

    def _via_fixture(self, env: PayloadEnvelope, model_id: str) -> AgentResponse:
        key = fixture_key("agent", env.task_schema_id, json.dumps(env.payload, sort_keys=True))
        data = self._fixtures.load("agent", env.task_schema_id, key)
        return self._to_agent_response({**data, "revalidated": True})

    # ── 응답 파싱 ────────────────────────────────────────────────────

    @staticmethod
    def _to_agent_response(data: dict) -> AgentResponse:
        try:
            return AgentResponse(
                answer=data.get("answer") or {},
                confidence=float(data.get("confidence", 0.0)),
                citations=tuple(data.get("citations") or ()),
                usage=data.get("usage"),
                revalidated=bool(data.get("revalidated", False)),
                vocab_sha256=data.get("vocab_sha256"),
            )
        except (TypeError, ValueError) as e:
            raise BrokerError(f"Agent 응답 파싱 실패: {type(e).__name__}") from e

    @classmethod
    def _parse_agent_text(cls, text: str, *, usage: dict | None) -> AgentResponse:
        """Claude 가 반환한 텍스트에서 JSON 을 뽑는다.

        마크다운 코드 펜스로 감싸는 경우가 흔하므로 벗겨낸다.
        신뢰도·인용이 없으면 0.0 / 빈 튜플이 되고, 그러면 Orchestrator 가
        인용 0개 규칙(BR-O-04)으로 에스컬레이션한다 — 안전한 방향이다.
        """
        body = text.strip()
        if body.startswith("```"):
            lines = [ln for ln in body.splitlines() if not ln.strip().startswith("```")]
            body = "\n".join(lines).strip()

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # 자유 텍스트로 답한 경우: 인용 0개가 되어 에스컬레이션된다
            log.warning("Agent 응답이 JSON 이 아니다 — 인용 0개로 처리")
            return AgentResponse(answer={"text": body}, confidence=0.0, citations=())

        if not isinstance(data, dict):
            return AgentResponse(answer={"text": body}, confidence=0.0, citations=())

        confidence = data.pop("confidence", 0.0)
        citations = data.pop("citations", ())
        try:
            confidence = min(max(float(confidence), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if isinstance(citations, str):
            citations = (citations,)

        return AgentResponse(
            answer=data,
            confidence=confidence,
            citations=tuple(str(c) for c in (citations or ())),
            usage=usage,
        )

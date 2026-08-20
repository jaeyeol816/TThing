"""피어 노드 — 같은 네트워크의 다른 컴퓨터에 있는 대리 에이전트.

    ┌─ 김책임의 노트북 ────────┐        ┌─ 박선임의 노트북 ────────┐
    │ 화면 · 내 문서            │        │ 화면 · 내 문서            │
    │ Gatekeeper · 감사         │◀──────▶│ Gatekeeper · 감사         │
    │ corpus/kim/**  (원문)     │  LAN   │ corpus/park/** (원문)     │
    └──────────────────────────┘        └──────────────────────────┘

──────────────────────────────────────────────────────────────────────
왜 "한 서버에 여러 브라우저" 가 아닌가
──────────────────────────────────────────────────────────────────────

가장 쉬운 방법은 한 컴퓨터에서 서버를 띄우고 모두가 그 주소를 여는 것이다.
그러면 **모든 사람의 기밀 문서가 한 컴퓨터에 모인다.** 이 프로젝트가 막으려는
것과 정확히 반대다. 그리고 그 컴퓨터의 소유자는 남의 문서를 파인더로 열 수 있다 —
게이트키퍼를 통째로 우회하는 경로가 설계에 들어가는 셈이다.

그래서 각자 자기 컴퓨터에서 노드를 띄우고, 노드끼리 묻는다.

| 무엇 | 어디서 일어나나 |
|---|---|
| 원문 읽기 | **문서를 가진 노드** |
| 등급 판정 · 구조 추출 · 검증 | **문서를 가진 노드** |
| 경계 밖 Agent 호출 | **문서를 가진 노드** (그 노드의 감사 로그에 남는다) |
| 재수화 | **문서를 가진 노드** |
| 질문자가 받는 것 | 재수화된 답변 + 인용 + 신뢰도 |

즉 **원문은 그것을 가진 컴퓨터를 떠나지 않는다.** 질문자 노드는 남의 원문을
한 번도 만지지 않고, 만질 방법도 없다 — 피어 표면에 파일을 읽는 경로가 없다.

──────────────────────────────────────────────────────────────────────
이 모듈이 하지 않는 것
──────────────────────────────────────────────────────────────────────

  · 자동 발견(mDNS/브로드캐스트)을 하지 않는다. 주소를 사람이 적는다.
    발견을 넣으면 "옆자리 사람이 내 노드를 찾았다" 가 기본 동작이 되고,
    그건 이 프로젝트가 취할 기본값이 아니다.
  · 피어의 응답을 검증하지 않는다 — 검증은 **보내는 쪽**이 한다.
    받는 쪽에서 다시 검증하려면 그 노드의 어휘·금칙어·원문이 필요하고,
    원문을 받아야 한다면 이 설계 전체가 무의미해진다.
  · 재시도하지 않는다. 실패는 그 대상만 건너뛰고 나머지는 진행한다 (R-02).

──────────────────────────────────────────────────────────────────────
레이어
──────────────────────────────────────────────────────────────────────

L5 다. `api_models`(L1)와 `config`(L0)만 쓴다. `store`·`gatekeeper` 를 쓰지
않는 것이 중요하다 — 이 모듈은 **남의 지식을 다루지 않는다.** HTTP 로 청하고
받은 것을 그대로 위로 넘긴다.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from mesh.api_models import (
    PeerAnswer,
    PeerIdentity,
    PeerNodeView,
    PeerPreparedCall,
    PeerPrepareRequest,
    PeerSendRequest,
)
from mesh.config import Config, get_logger, log_extra
from mesh.exceptions import MeshError
from mesh.schemas import AgentCard

log = get_logger("peer")

#: 피어 토큰 헤더. `main.PEER_TOKEN_HEADER` 와 같은 값이어야 한다.
#: 여기 다시 적는 이유: `peer`(L5) 가 `main`(L7) 을 import 하면 레이어 위반이다.
PEER_TOKEN_HEADER = "X-Mesh-Peer-Token"  # noqa: S105 — 헤더 **이름**이다. 비밀은 값이고 이 파일에 없다

#: 노드 상태 확인 타임아웃. 짧게 잡는다 — 화면이 이 시간만큼 멈춘다.
HELLO_TIMEOUT = 2.0

#: 지목 목록 조회 타임아웃. 화면 첫 렌더에 관여한다.
AGENTS_TIMEOUT = 5.0

#: prepare 타임아웃. 원격 노드가 자기 EXAONE 을 호출하므로 로컬보다 길다.
PREPARE_TIMEOUT = 30.0

#: send 타임아웃. 원격 노드가 자기 Agent(Claude)를 호출한다.
SEND_TIMEOUT = 45.0


class PeerUnavailable(MeshError):
    """피어에 닿지 못했거나 피어가 거부했다.

    귀결: 그 대상만 건너뛴다. 2명을 지목했는데 1명이 원격이고 그 노드가
    꺼져 있으면, 나머지 1명의 답은 그대로 나와야 한다 (R-02).
    """


class PeerTokenRejected(PeerUnavailable):
    """토큰 불일치. **원인이 다르므로 예외도 다르다.**

    노드가 꺼진 것과 토큰이 틀린 것은 고치는 방법이 다르다. 하나로 뭉치면
    화면이 "연결 실패" 만 보여주고 사람이 원인을 짐작하게 된다.
    """


class PeerClient:
    """피어 노드에 HTTP 로 청한다.

    `httpx.AsyncClient` 하나를 재사용한다 — 노드마다 커넥션을 새로 열면
    LAN 왕복에 TCP 핸드셰이크가 매번 붙는다.
    """

    def __init__(self, cfg: Config, *, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    def _headers(self) -> dict[str, str]:
        """토큰이 없으면 헤더를 붙이지 않는다.

        빈 문자열을 보내면 상대가 "토큰 불일치" 로 답하고, 그건 사실이지만
        원인 표시가 흐려진다 — 진짜 원인은 "내 쪽에 토큰이 없다" 다.
        """
        token = self.cfg.peer_token
        return {PEER_TOKEN_HEADER: token} if token else {}

    # ── 저수준 ───────────────────────────────────────────────────────

    async def _call(
        self,
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        json_body: dict | None = None,
        timeout: float,  # noqa: ASYNC109 — httpx 타임아웃. 아래 주석 참조
        with_token: bool = True,
    ) -> dict:
        """피어 호출 1회. 실패는 `PeerUnavailable` 계열로 정규화한다.

        ⚠️ `asyncio.timeout` 이 아니라 `httpx` 의 타임아웃을 쓴다 (ruff ASYNC109).
           `asyncio.timeout` 으로 감싸면 취소는 되지만 **소켓이 그대로 남는다.**
           httpx 는 connect·read·write 를 따로 보고 소켓까지 정리한다.
           LAN 에서 꺼진 노드를 부르는 것이 흔한 경로이므로 이쪽이 맞다.

        ⚠️ 오류 본문을 그대로 예외 메시지에 넣지 않는다. 피어가 보낸 문자열이
           우리 로그와 화면에 그대로 흐르면, 피어가 우리 화면에 내용을 심을 수
           있다. `detail` 만 뽑아 길이를 자른다.
        """
        url = f"{base_url.rstrip('/')}{path}"
        try:
            response = await self._http().request(
                method,
                url,
                json=json_body,
                headers=self._headers() if with_token else {},
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise PeerUnavailable(f"{base_url} 에 닿지 못했다: {type(e).__name__}") from e

        if response.status_code == 403:
            detail = _short_detail(response)
            if "token" in detail.lower() or "토큰" in detail:
                raise PeerTokenRejected(f"{base_url}: 피어 토큰이 일치하지 않는다")
            raise PeerUnavailable(f"{base_url}: 접근이 거부됐다 ({detail})")
        if response.status_code >= 400:
            raise PeerUnavailable(
                f"{base_url} 가 {response.status_code} 를 냈다: {_short_detail(response)}"
            )
        try:
            body = response.json()
        except ValueError as e:
            raise PeerUnavailable(f"{base_url} 응답이 JSON 이 아니다") from e
        if not isinstance(body, dict | list):
            raise PeerUnavailable(f"{base_url} 응답 형태가 예상과 다르다")
        return body  # type: ignore[return-value]

    # ── 고수준 ───────────────────────────────────────────────────────

    async def hello(self, base_url: str) -> PeerIdentity:
        """식별 확인. **토큰 없이** 부른다 (`PEER_OPEN_PATHS`)."""
        raw = await self._call(base_url, "/api/peer/hello", timeout=HELLO_TIMEOUT, with_token=False)
        return PeerIdentity.model_validate(raw)

    async def agents(self, base_url: str) -> tuple[AgentCard, ...]:
        """그 노드의 지목 목록. 토큰이 필요하다.

        `AgentCard` 는 이미 식별자를 제거한 요약이다 (`store.list_agents`) —
        `current_focus_summary` 도 닫힌 어휘에서 고른 라벨이고 게이트키퍼를
        지난다. 그래서 LAN 을 건너가도 새로 검사할 것이 없다.
        """
        raw = await self._call(base_url, "/api/peer/agents", timeout=AGENTS_TIMEOUT)
        if not isinstance(raw, list):  # pragma: no cover — 서버가 리스트를 준다
            raise PeerUnavailable(f"{base_url}: 지목 목록 형태가 다르다")
        return tuple(AgentCard.model_validate(item) for item in raw)

    async def prepare(self, base_url: str, request: PeerPrepareRequest) -> PeerPreparedCall:
        raw = await self._call(
            base_url,
            "/api/peer/prepare",
            method="POST",
            json_body=request.model_dump(mode="json"),
            timeout=PREPARE_TIMEOUT,
        )
        return PeerPreparedCall.model_validate(raw)

    async def send(self, base_url: str, request: PeerSendRequest) -> PeerAnswer:
        raw = await self._call(
            base_url,
            "/api/peer/send",
            method="POST",
            json_body=request.model_dump(mode="json"),
            timeout=SEND_TIMEOUT,
        )
        return PeerAnswer.model_validate(raw)

    # ── 상태 요약 (화면용) ───────────────────────────────────────────

    async def probe(self, base_url: str) -> PeerNodeView:
        """한 노드의 상태. **실패해도 예외를 내지 않는다** — 화면이 목록을 그린다.

        상태를 네 가지로 나눈다. 전부 "실패" 로 뭉치면 사람이 원인을 짐작해야 한다.
        """
        started = time.monotonic()
        try:
            identity = await self.hello(base_url)
        except PeerUnavailable as e:
            return PeerNodeView(base_url=base_url, status="unreachable", detail=str(e)[:160])

        latency = int((time.monotonic() - started) * 1000)
        if identity.node_name == self.cfg.node_name:
            # 자기 자신을 피어로 적은 경우. 조용히 통과시키면 "내 답을 내가 받는"
            # 상태가 되고, 그걸 알아차릴 방법이 없다.
            return PeerNodeView(
                base_url=base_url,
                status="self",
                node_name=identity.node_name,
                latency_ms=latency,
                detail="이 노드 자신이다. MESH_PEERS 에서 빼라",
            )

        # 토큰이 맞는지는 보호된 경로를 한 번 불러 봐야 안다.
        try:
            cards = await self.agents(base_url)
        except PeerTokenRejected as e:
            return PeerNodeView(
                base_url=base_url,
                status="token_invalid",
                node_name=identity.node_name,
                latency_ms=latency,
                detail=str(e)[:160],
            )
        except PeerUnavailable as e:
            return PeerNodeView(
                base_url=base_url,
                status="unreachable",
                node_name=identity.node_name,
                latency_ms=latency,
                detail=str(e)[:160],
            )

        return PeerNodeView(
            base_url=base_url,
            status="connected",
            node_name=identity.node_name,
            agent_count=len(cards),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def probe_all(self) -> tuple[PeerNodeView, ...]:
        """설정된 모든 피어를 **병렬로** 확인한다.

        순차로 하면 꺼진 노드 하나가 타임아웃만큼 화면을 멈춘다.
        """
        if not self.cfg.peers:
            return ()
        results = await asyncio.gather(*[self.probe(url) for url in self.cfg.peers])
        return tuple(results)


# ══════════════════════════════════════════════════════════════════════
# 레지스트리 — entity_id 가 어느 노드에 있는가
# ══════════════════════════════════════════════════════════════════════


class PeerRegistry:
    """`entity_id -> base_url` 해석. 로컬이 항상 이긴다.

    ⚠️ **로컬 우선이 보안 규칙이다.** 피어가 `person:kim` 을 자기 것이라고
       주장할 수 있다. 로컬에 같은 id 가 있으면 로컬을 쓴다 — 그러지 않으면
       악의적인 피어가 내 에이전트를 가로채 아무 답이나 돌려줄 수 있다.

    캐시 TTL 을 두는 이유: 화면이 지목 목록을 자주 다시 그린다. 매번 LAN 을
    왕복하면 느리고, 영구 캐시로 두면 노드가 꺼진 뒤에도 목록에 남는다.
    """

    #: 지목 목록 캐시 수명. 짧게 잡는다 — 사람이 노드를 켜고 끄는 주기가 짧다.
    TTL_SECONDS = 20.0

    def __init__(self, cfg: Config, client: PeerClient, *, local_ids: frozenset[str]) -> None:
        self.cfg = cfg
        self.client = client
        self.local_ids = local_ids
        self._cache: dict[str, tuple[float, tuple[AgentCard, ...]]] = {}
        self._owner: dict[str, str] = {}

    def node_of(self, entity_id: str) -> str | None:
        """`entity_id` 가 사는 노드의 base_url. 로컬이면 `None`."""
        if entity_id in self.local_ids:
            return None
        return self._owner.get(entity_id)

    def is_remote(self, entity_id: str) -> bool:
        return self.node_of(entity_id) is not None

    async def remote_cards(self) -> tuple[tuple[str, AgentCard], ...]:
        """모든 피어의 지목 목록. `(base_url, card)` 쌍.

        실패한 노드는 **조용히 빠진다.** 화면의 지목 목록은 "지금 물을 수 있는
        사람" 이고, 꺼진 노드의 사람을 보여주면 눌렀을 때 실패한다.
        노드 상태는 `/api/peers` 가 따로 보여 준다.
        """
        if not self.cfg.peers:
            return ()

        async def one(base_url: str) -> tuple[str, tuple[AgentCard, ...]]:
            now = time.monotonic()
            cached = self._cache.get(base_url)
            if cached and now - cached[0] < self.TTL_SECONDS:
                return base_url, cached[1]
            try:
                cards = await self.client.agents(base_url)
            except PeerUnavailable as e:
                log.info("피어 지목 목록 실패", extra=log_extra(peer=base_url, reason=str(e)[:80]))
                return base_url, ()
            self._cache[base_url] = (now, cards)
            return base_url, cards

        pairs: list[tuple[str, AgentCard]] = []
        for base_url, cards in await asyncio.gather(*[one(u) for u in self.cfg.peers]):
            for card in cards:
                if card.entity_id in self.local_ids:
                    # 피어가 로컬 id 를 주장한다. 무시하고 기록해 둔다.
                    log.warning(
                        "피어가 로컬 entity_id 를 주장했다 — 무시한다",
                        extra=log_extra(peer=base_url, entity_id=card.entity_id),
                    )
                    continue
                if card.entity_id in self._owner and self._owner[card.entity_id] != base_url:
                    log.warning(
                        "두 피어가 같은 entity_id 를 주장했다 — 먼저 본 노드를 쓴다",
                        extra=log_extra(
                            entity_id=card.entity_id, keeping=self._owner[card.entity_id]
                        ),
                    )
                    continue
                self._owner[card.entity_id] = base_url
                pairs.append((base_url, card))
        return tuple(pairs)


def _short_detail(response: httpx.Response) -> str:
    """오류 본문에서 `detail` 만 뽑아 길이를 자른다.

    피어가 보낸 문자열을 그대로 로그·화면에 흘리지 않는다.
    """
    with_default = f"HTTP {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return with_default
    if isinstance(body, dict):
        for key in ("detail", "error", "message"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
    return with_default

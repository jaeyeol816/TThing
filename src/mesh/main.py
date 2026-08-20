"""FastAPI 앱 — HTTP 표면 (BR-M-*).

이 파일이 담당하는 것은 **경계 하나 더**다. 앱 내부의 신뢰 경계는
`gatekeeper.py` 가 지키고, 여기서는 브라우저와의 경계를 지킨다.

──────────────────────────────────────────────────────────────────────
이 서비스는 localhost 전용이다 (BR-M-01)
──────────────────────────────────────────────────────────────────────

원문 파일을 읽고 **재수화된 답변(실제 이름 포함)** 을 반환한다.
인증이 없는 MVP 에서 네트워크에 노출하면 **권한 우회 도구**가 된다.
`MESH_BIND_HOST` 가 localhost 가 아니면 시작 시 경고하고
`MESH_ALLOW_NETWORK_BIND=1` 로 명시적 확인을 요구한다.

──────────────────────────────────────────────────────────────────────
없는 것들
──────────────────────────────────────────────────────────────────────

    /docs · /redoc · /openapi.json     기본 비활성 (SECURITY-09).
                                       MESH_DEV=1 일 때만 열린다
    CORS 미들웨어                       추가하지 않는다 (동일 출처만, BR-M-04)
    StaticFiles(디렉터리)               3개 파일 명시 매핑 (디렉터리 리스팅 차단)
    스택 트레이스                        전역 핸들러가 correlation_id 만 준다

`/docs` 를 끄는 이유: OpenAPI 스키마는 내부 필드 이름과 구조를 전부 노출한다.
이 프로젝트에서는 그것 자체가 공격 표면 정보다.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import shlex
import socket
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import ValidationError

from mesh import __version__
from mesh.agent import AgentClient
from mesh.api_models import (
    MAX_SEARCH_CHARS,
    AgentCardView,
    AskRequest,
    AskResult,
    AuditRowView,
    AuditSearchResult,
    BroadcastRequest,
    BroadcastResult,
    DocumentList,
    ErrorResponse,
    HealthStatus,
    InboxItem,
    MergedRulesView,
    OrgChartResponse,
    PeerAnswer,
    PeerIdentity,
    PeerNodeView,
    PeerPreparedCall,
    PeerPrepareRequest,
    PeerSendRequest,
    PeerStatus,
    PrepareResult,
    PresetQuestion,
    ProtocolUpsertRequest,
    ProtocolView,
    ResolveRequest,
    SendRequest,
    StorageInfo,
    UploadRequest,
    UploadResult,
    UserView,
)
from mesh.audit import AuditLog
from mesh.config import (
    Config,
    DataBundle,
    correlation_id,
    get_logger,
    log_extra,
    setup_logging,
    sha256_file,
)
from mesh.documents import DocumentService
from mesh.exceptions import GatekeeperError, MeshError
from mesh.gatekeeper import Gatekeeper
from mesh.inbox import Inbox
from mesh.llm.exaone import ExaoneClient
from mesh.orchestrator import Orchestrator
from mesh.peer import PeerClient, PeerRegistry
from mesh.store import KnowledgeStore
from mesh.trace import GatekeeperTrace, TraceStore

log = get_logger("main")

WEB_ROOT = Path(__file__).resolve().parent / "web"

#: 모든 응답에 붙는다 (BR-M-03, NFR-S-04).
#:
#: `unsafe-inline` 을 쓰지 않는다 -> U4 의 JS·CSS 는 인라인이 아니라 별도 파일이다.
#: HSTS 는 localhost HTTP 이므로 **N/A** 다 (근거를 여기 남긴다).
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

#: 동시 처리 상한. 질의 하나가 LLM 을 여러 번 부르므로 동시성이 높으면
#: 토큰 비용과 지연이 함께 폭발한다. 데모 사용자는 1~5명이다.
MAX_CONCURRENT_REQUESTS = 5

_LOCALHOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


# ══════════════════════════════════════════════════════════════════════
# 객체 그래프
# ══════════════════════════════════════════════════════════════════════


class Services:
    """앱이 쓰는 것 전부. 배선을 한 곳에 모아 테스트가 갈아끼울 수 있게 한다.

    ⚠️ 배선 순서가 보안 속성이다. `KnowledgeStore` 가 `Gatekeeper` 를 받아야
       목록 요약이 게이트키퍼를 통과하고, `AgentClient` 가 `Gatekeeper` 를
       받아야 Bedrock 직접 호출 경로가 존재하지 않는다.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        data: DataBundle | None = None,
        audit: AuditLog | None = None,
        exaone: object | None = None,
        gatekeeper: Gatekeeper | None = None,
    ) -> None:
        self.cfg = cfg
        self.data = data or DataBundle(cfg)
        self.exaone = exaone or ExaoneClient(cfg)
        self.audit = audit or AuditLog(cfg)
        # ⚠️ 브로커를 여기서 만들지 않는다. 만들면 `main.py` 가 경계 밖
        #    클라이언트를 import 해야 하고 SECURITY-11 의 예외가 하나 늘어난다.
        self.gatekeeper = gatekeeper or Gatekeeper.build(
            cfg, self.data, self.audit, exaone=self.exaone
        )
        self.store = KnowledgeStore(
            cfg,
            self.data,
            exaone=self.exaone,
            gatekeeper=self.gatekeeper,
            audit=self.audit,
        )
        self.agent = AgentClient(cfg, self.gatekeeper, audit=self.audit)
        self.inbox = Inbox(cfg, self.audit)
        self.documents = DocumentService(cfg, self.data, self.store, self.gatekeeper)

        # ⚠️ 트레이스는 **메모리에만** 산다 (TTL 30분). 파일로 쓰지 않는 이유는
        #    `trace.TraceStore` 에 적혀 있다 — 답변에 등장한 기호의 매핑을
        #    일부 품고 있고, 그것이 디스크에 남으면 `Mapping` 이 직렬화 불가인
        #    이유(BR-G-09)를 우회하는 셈이 된다.
        self.traces = TraceStore()

        # ── 피어 메시 ────────────────────────────────────────────────
        #
        # ⚠️ `peers` 가 비어 있으면 레지스트리를 만들지 않는다 (`None`).
        #    빈 레지스트리를 넘기면 `Orchestrator` 가 매 질문마다 "원격인가"를
        #    묻는 코드 경로를 타고, 단독 노드에서 쓰지 않는 분기가 항상 돈다.
        #    `None` 이면 그 분기가 아예 없다 — 단독 동작이 기본값이라는 사실이
        #    타입에 드러난다.
        self.peer_client = PeerClient(cfg) if cfg.peers or cfg.lan_mode else None
        self.peers = (
            PeerRegistry(cfg, self.peer_client, local_ids=frozenset(self.data.agents))
            if self.peer_client and cfg.peers
            else None
        )
        #: 피어가 발급받은 `envelope_id` -> 이 노드의 `request_id`.
        #:
        #: 왜 이 사상이 필요한가: 두 노드가 `request_id` 공간을 공유하면 충돌하고,
        #: 충돌하면 남의 준비 결과를 전송할 수 있다. 질문자는 `envelope_id` 만
        #: 들고 다시 오고, 이 노드가 그것으로 자기 pending 을 찾는다.
        #: `EnvelopeCache` 와 `PendingRequest` 가 이미 TTL 로 만료되므로
        #: 여기 남은 항목은 만료된 키를 가리키고 `send` 가 410 을 낸다.
        self.peer_pending: dict[str, str] = {}

        self.orchestrator = Orchestrator(
            cfg,
            self.data,
            self.store,
            self.gatekeeper,
            self.agent,
            self.audit,
            self.inbox,
            peers=self.peers,
            traces=self.traces,
        )

    async def aclose(self) -> None:
        for closer in (self.exaone, self.gatekeeper.broker, self.peer_client):
            aclose = getattr(closer, "aclose", None)
            if aclose is not None:
                await aclose()
        self.audit.close()


def check_bind_host(cfg: Config, *, allow_network: bool | None = None) -> None:
    """localhost 가 아니면 명시적 확인을 요구한다 (BR-M-01).

    LAN 모드에는 관문이 **두 개**다.

      ① `MESH_PEER_TOKEN` — `Config.validate()` 가 강제한다. 실제 접근 통제다
      ② `MESH_ALLOW_NETWORK_BIND=1` — 여기서 강제한다. 의도 확인이다

    둘을 겹치는 이유: 토큰이 있다는 것은 "보호했다"이고, 플래그는 "노출을
    의도했다"다. 다른 사실이다. 토큰을 넣어 둔 채로 무심히 `0.0.0.0` 을
    설정하는 일이 실제로 생긴다.

    Raises:
        MeshError: 확인 없이 네트워크에 노출하려 할 때. **시작을 막는다** —
            경고만 하면 아무도 읽지 않는다.
    """
    if cfg.bind_host in _LOCALHOSTS:
        return
    if allow_network if allow_network is not None else cfg.allow_network_bind:
        log.warning(
            "네트워크 바인딩을 명시적으로 허용했다. 원격 출처는 /api/peer/* 만 "
            "접근할 수 있고 피어 토큰이 필요하다. 소유자 표면(문서 목록·감사 로그·"
            "저장 경로)은 loopback 전용이다",
            extra=log_extra(bind_host=cfg.bind_host, node_name=cfg.node_name),
        )
        return
    raise MeshError(
        f"MESH_BIND_HOST={cfg.bind_host} 는 localhost 가 아니다. "
        "이 서비스는 원문 파일을 읽고 재수화된 실제 이름을 반환한다. "
        "네트워크 노출은 의도를 명시해야 한다 (BR-M-01).\n"
        "  -> MESH_BIND_HOST=127.0.0.1 로 실행하라\n"
        "  -> 여러 컴퓨터로 쓰려는 것이라면 MESH_ALLOW_NETWORK_BIND=1 을 함께 지정하라\n"
        "     (MESH_PEER_TOKEN 도 필요하다 — 참여하는 모든 컴퓨터에 같은 값)"
    )


# ══════════════════════════════════════════════════════════════════════
# 앱
# ══════════════════════════════════════════════════════════════════════


def create_app(cfg: Config | None = None, *, services: Services | None = None) -> FastAPI:
    """앱을 만든다. 테스트는 `services` 를 주입해 대역을 쓴다."""
    cfg = cfg or Config.load()
    setup_logging("DEBUG" if cfg.dev_mode else "INFO")
    check_bind_host(cfg)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = services or Services(cfg)
        if cfg.trust_boundary_simulated:
            log.info(
                "신뢰 경계가 시뮬레이션이다 — 화면 헤더에 상시 표시된다",
                extra=log_extra(trusted_zone_llm=cfg.trusted_zone_llm_base_url),
            )
        # 첫 화면에서 사용자가 요약 생성을 기다리지 않게 한다
        with contextlib.suppress(Exception):
            await app.state.services.store.warm_focus_cache()
        try:
            yield
        finally:
            if services is None:  # 우리가 만든 것만 우리가 닫는다
                await app.state.services.aclose()

    dev = cfg.dev_mode
    app = FastAPI(
        title="MIA; But AI got you",
        lifespan=lifespan,
        # ⚠️ 기본 비활성 (SECURITY-09). OpenAPI 스키마는 내부 구조를 전부 노출한다.
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.cfg = cfg
    _install_middleware(app)
    _install_error_handlers(app)
    _install_routes(app)
    return app


# ══════════════════════════════════════════════════════════════════════
# 미들웨어
# ══════════════════════════════════════════════════════════════════════

Handler = Callable[[Request], Awaitable[Response]]


#: 원격 피어가 접근할 수 있는 유일한 경로 접두사.
PEER_PREFIX = "/api/peer/"

#: 피어 토큰을 실어 보내는 헤더.
PEER_TOKEN_HEADER = "X-Mesh-Peer-Token"  # noqa: S105 — 헤더 **이름**이다. 비밀은 값이고 이 파일에 없다

#: 토큰 없이도 원격에서 부를 수 있는 경로. **식별 정보만** 돌려준다.
#:
#: 왜 하나를 열어 두는가: 화면의 피어 목록이 "연결됨/응답 없음/토큰 불일치"를
#: 구분해 보여줘야 한다. 전부 막으면 토큰이 틀린 것과 노드가 꺼진 것이 같아 보이고,
#: 사람이 원인을 짐작하게 된다. 이 경로는 노드 이름과 버전만 준다 —
#: 사람 목록도, 문서도, 경로도 없다.
PEER_OPEN_PATHS = frozenset({"/api/peer/hello"})


def is_loopback(request: Request) -> bool:
    """요청이 이 컴퓨터에서 왔는가.

    `request.client` 가 `None` 인 경우(테스트 전송·유닉스 소켓)는 **로컬로 본다** —
    `TestClient` 가 그렇고, 그 경로는 네트워크를 타지 않는다.

    ⚠️ `X-Forwarded-For` 를 보지 않는다. 이 서비스 앞에 프록시를 두지 않으므로
       그 헤더는 오직 **공격자가 심는 값**이다. 신뢰하면 게이팅이 무의미해진다.
    """
    client = request.client
    if client is None or not client.host:
        return True
    return client.host in _LOCALHOSTS or client.host == "::ffff:127.0.0.1"


def _install_middleware(app: FastAPI) -> None:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    # ⚠️ CORS 미들웨어를 추가하지 않는다 (BR-M-04). 동일 출처만 허용한다.
    #    와일드카드 `*` 는 이 서비스에서 곧 데이터 유출이다.

    @app.middleware("http")
    async def _origin_gate(request: Request, call_next: Handler) -> Response:
        """원격 출처는 `/api/peer/*` 만, 그리고 토큰이 있어야 한다.

        ──────────────────────────────────────────────────────────────
        표면이 두 개다
        ──────────────────────────────────────────────────────────────

        | 표면 | 누가 | 무엇 |
        |---|---|---|
        | 소유자 표면 | loopback 만 | 화면 · 문서 목록 · **저장 경로** · 감사 로그 · 업로드 |
        | 피어 표면 | 토큰을 가진 LAN | `/api/peer/*` — 지목 목록 · prepare · send |

        포트를 둘로 나누지 않고 미들웨어로 가른 이유: uvicorn 을 두 번 띄우면
        `make run` 이 복잡해지고 감사 DB 를 두 프로세스가 열게 된다. 출처로
        가르는 것이 30줄이고 같은 성질을 준다.

        **소유자 표면이 loopback 전용인 것이 중요하다.** 그쪽에는 절대 경로와
        업로드 삭제와 원문 검색이 있다. 피어에게 필요한 것은 "질문에 답하는 것"
        뿐이고, 그건 게이트키퍼를 지난 결과다.
        """
        cfg: Config = request.app.state.cfg
        # loopback 에 바인딩했으면 **원격 출처가 존재할 수 없다.** 커널이 외부
        # 패킷을 전달하지 않는다. 그때 출처를 따지는 것은 비용만 늘리고,
        # 프록시 헤더 같은 것을 신뢰하고 싶은 유혹을 만든다.
        if not cfg.lan_mode or is_loopback(request):
            return await call_next(request)

        path = request.url.path

        if not path.startswith(PEER_PREFIX):
            log.warning(
                "원격 출처가 소유자 표면에 접근하려 했다",
                extra=log_extra(path=path, source="remote"),
            )
            # 404 가 아니라 403 이다. 경로가 있다는 것은 이미 알려진 사실이고,
            # 숨기려다 "왜 안 되지?" 를 만드는 편이 더 나쁘다.
            return _error(403, "peer_forbidden", "이 경로는 이 컴퓨터에서만 쓸 수 있습니다")

        if path in PEER_OPEN_PATHS:
            return await call_next(request)

        supplied = request.headers.get(PEER_TOKEN_HEADER, "")
        expected = cfg.peer_token or ""
        # ⚠️ 상수 시간 비교. 문자열 `==` 는 앞에서 갈리면 빨리 끝나므로
        #    LAN 에서 반복 시도로 한 글자씩 맞출 수 있다.
        if not expected or not secrets.compare_digest(supplied, expected):
            log.warning(
                "피어 토큰 불일치",
                extra=log_extra(path=path, supplied_len=len(supplied)),
            )
            return _error(
                403,
                "peer_token_invalid",
                "피어 토큰이 일치하지 않습니다. 참여하는 모든 컴퓨터에 같은 "
                "MESH_PEER_TOKEN 을 넣었는지 확인해 주세요",
            )
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    @app.middleware("http")
    async def _correlation(request: Request, call_next: Handler) -> Response:
        cid = request.headers.get("X-Correlation-Id") or f"req_{uuid.uuid4().hex[:16]}"
        token = correlation_id.set(cid[:64])
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers["X-Correlation-Id"] = cid[:64]
        return response

    @app.middleware("http")
    async def _concurrency(request: Request, call_next: Handler) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        async with semaphore:
            return await call_next(request)


# ══════════════════════════════════════════════════════════════════════
# 오류 처리 (BR-M-05)
# ══════════════════════════════════════════════════════════════════════


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatekeeperError)
    async def _gatekeeper(request: Request, exc: GatekeeperError) -> JSONResponse:
        """전제조건 위반·envelope 만료.

        `410 Gone` 인 이유: envelope 은 **있었다가 없어진** 것이다 (일회용 + TTL).
        `404` 로 답하면 클라이언트가 "잘못된 id" 로 오해해 재시도한다.
        """
        message = str(exc)
        gone = "만료" in message or "찾을 수 없다" in message or "이미 전송" in message
        status = 410 if gone else 400
        log.warning("게이트키퍼 거부", extra=log_extra(status=status, reason=message))
        return _error(status, "gone" if gone else "precondition_failed", message)

    @app.exception_handler(MeshError)
    async def _mesh(request: Request, exc: MeshError) -> JSONResponse:
        log.warning("앱 오류", extra=log_extra(reason=str(exc)))
        return _error(400, type(exc).__name__, str(exc))

    @app.exception_handler(ValidationError)
    async def _validation(request: Request, exc: ValidationError) -> JSONResponse:
        return _error(422, "validation_error", _field_summary(exc.errors()))

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """⚠️ FastAPI 기본 422 응답을 **반드시** 교체한다.

        기본 핸들러는 오류마다 `input` 을 담는다 — 즉 **요청 본문을 그대로
        되돌려준다.** 업로드 요청에서 이 일이 벌어지면 방금 올린 기밀 문서
        전문(최대 200,000자)이 오류 응답에 실린다.

        실측: `content` 가 상한을 1자 넘겼을 때 기본 응답이 문서 전문을
        되비췄다. 유출은 아니다(요청자 자신에게 돌아간다). 그러나

          - 오류 응답이 원문을 담는 습관이 생기면 로그·프록시·브라우저
            히스토리에 원문이 퍼진다
          - 응답 크기가 요청 크기에 비례해 커진다
          - 이 프로젝트의 오류 계약(`error` + `correlation_id`)이 깨져
            화면이 오류를 표시하지 못한다

        그래서 **필드 이름과 사유만** 남기고 값은 버린다.
        """
        return _error(422, "validation_error", _field_summary(exc.errors()))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """⚠️ 응답에 스택 트레이스·내부 경로·프레임워크 버전을 담지 않는다.

        `correlation_id` 만 주고 상세는 로그에서 찾는다. 오류 메시지가
        경로나 코드 구조를 노출하면 그 자체가 정보다.
        """
        log.exception("처리되지 않은 오류")
        return _error(500, "internal_error", None)


#: 오류 요약에 담을 필드 개수 상한. 전부 담으면 스키마 구조를 알려준다.
MAX_ERROR_FIELDS = 3


def _field_summary(errors: list[dict[str, object]]) -> str:
    """검증 오류를 **필드 이름 + 사유**로만 요약한다. 값은 담지 않는다.

    사용자에게 무엇을 고쳐야 하는지는 알려주되(`filename: ...`), 그가 보낸
    값을 되비추지 않는다. 파일명은 짧아서 담아도 될 것 같지만, 규칙을
    필드별로 나누면 "어떤 필드는 되비춰도 된다"는 판단이 코드에 흩어진다.
    """
    parts: list[str] = []
    for err in errors[:MAX_ERROR_FIELDS]:
        loc = err.get("loc") or ()
        # `("body", "filename")` -> `filename`. `body` 는 사용자에게 무의미하다.
        names = [str(x) for x in loc if isinstance(loc, tuple) and str(x) != "body"]
        field = ".".join(names) or "요청"
        parts.append(f"{field}: {err.get('msg', '값이 올바르지 않습니다')}")
    if len(errors) > MAX_ERROR_FIELDS:
        parts.append(f"(그 밖에 {len(errors) - MAX_ERROR_FIELDS}건)")
    return "요청 형식이 올바르지 않습니다 — " + "; ".join(parts)


def _reveal_command(path: Path) -> str | None:
    """이 컴퓨터에서 폴더를 열 명령. 화면이 그대로 보여 준다.

    실행하지 않는다 — **문자열만 준다.** 서버가 `open` 을 실행하면 원격에서
    임의 경로를 열게 만들 수 있고, 그건 이 프로젝트가 감당할 위험이 아니다.
    사람이 복사해서 자기 터미널에 붙이는 것으로 충분하다.
    """
    quoted = shlex.quote(str(path))
    if sys.platform == "darwin":
        return f"open {quoted}"
    if sys.platform.startswith("linux"):
        return f"xdg-open {quoted}"
    if sys.platform.startswith("win"):
        return f"explorer {quoted}"
    return None


def _advertised_host(cfg: Config) -> str:
    """다른 컴퓨터에 알려 줄 주소.

    `0.0.0.0` 은 "모든 인터페이스" 라는 뜻이고 **접속 주소가 아니다.** 그대로
    보여주면 사람이 그것을 복사해 `MESH_PEERS` 에 넣고 실패한다.
    실제 LAN IP 를 찾아 준다.
    """
    if cfg.bind_host not in {"0.0.0.0", "::", ""}:  # noqa: S104
        return cfg.bind_host
    # 외부로 나가는 소켓을 만들어(패킷은 보내지 않는다) 커널이 고른 로컬 주소를 읽는다.
    # `gethostbyname(gethostname())` 은 macOS 에서 127.0.0.1 을 주는 일이 잦다.
    with contextlib.suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.1)
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1. 라우팅만 해석된다
            return str(probe.getsockname()[0])
    return cfg.bind_host or "127.0.0.1"


def _peer_hint(cfg: Config, views: tuple[PeerNodeView, ...]) -> str | None:
    """사람이 다음에 무엇을 해야 하는지. 상태만 보여주면 고칠 수 없다."""
    if not cfg.lan_mode:
        return (
            "지금은 이 컴퓨터에서만 동작합니다. 같은 네트워크의 다른 컴퓨터와 "
            "연결하려면 MESH_BIND_HOST=0.0.0.0 · MESH_ALLOW_NETWORK_BIND=1 · "
            "MESH_PEER_TOKEN 을 설정하고 다시 시작해 주세요"
        )
    if not cfg.peers:
        return (
            f"이 노드는 {_advertised_host(cfg)}:{cfg.bind_port} 에서 기다리고 있습니다. "
            "다른 컴퓨터의 주소를 MESH_PEERS 에 적으면 그쪽 사람에게도 물을 수 있습니다"
        )
    if any(v.status == "token_invalid" for v in views):
        return "피어 토큰이 다릅니다. 참여하는 모든 컴퓨터에 같은 MESH_PEER_TOKEN 을 넣어 주세요"
    if any(v.status == "self" for v in views):
        return "MESH_PEERS 에 이 노드 자신이 들어 있습니다. 빼 주세요"
    if all(v.status != "connected" for v in views):
        return "연결된 노드가 없습니다. 상대 컴퓨터에서 서버가 떠 있는지 확인해 주세요"
    return None


def _error(status: int, code: str, detail: str | None) -> JSONResponse:
    body = ErrorResponse(error=code, correlation_id=correlation_id.get(), detail=detail)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


# ══════════════════════════════════════════════════════════════════════
# 라우트
# ══════════════════════════════════════════════════════════════════════


def _services(request: Request) -> Services:
    return request.app.state.services


def _install_routes(app: FastAPI) -> None:
    # ── 정적 파일: 3개 명시 매핑 (BR-M-08) ───────────────────────────
    #    StaticFiles 를 디렉터리에 붙이지 않는다 — 디렉터리 리스팅을 원천 차단한다.

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return _static("index.html", "text/html; charset=utf-8")

    @app.get("/app.js", include_in_schema=False)
    async def app_js() -> Response:
        return _static("app.js", "text/javascript; charset=utf-8")

    @app.get("/style.css", include_in_schema=False)
    async def style_css() -> Response:
        return _static("style.css", "text/css; charset=utf-8")

    # ── 에이전트 목록 ────────────────────────────────────────────────

    @app.get("/api/agents", response_model=list[AgentCardView])
    async def agents(request: Request) -> list[AgentCardView]:
        cards = await _services(request).orchestrator.agent_cards()
        return [AgentCardView.model_validate(c.model_dump()) for c in cards]

    # ── 조직도 ───────────────────────────────────────────────────────

    @app.get("/api/org", response_model=OrgChartResponse)
    async def org_chart(request: Request) -> OrgChartResponse:
        """본부 → 센터/연구소 → 팀 → 사람의 트리.

        ⚠️ **인증 없이 보이는 화면이다** (FR-31 과 같은 위험). 여기 실리는
           문자열은 `config/org.yaml` 뿐이고, 금칙어 검사는 로드 시점에
           끝난다 (`OrgChart.validate_no_banned`). 사람 이름·직급은 여기
           없다 — `member_ids` 만 있고 표시 정보는 `/api/agents` 가 준다.

        조직도 파일이 없으면 빈 트리를 돌려준다. 화면은 평평한 목록을
        그리면 되고, 질의는 조직도 없이도 동작한다.
        """
        svc = _services(request)
        view = svc.data.org.to_view(svc.data.placements)
        return OrgChartResponse.model_validate(view.model_dump())

    # ── 브로드캐스트: 지목보다 먼저 오는 단계 ────────────────────────

    @app.post("/api/ask/broadcast", response_model=BroadcastResult)
    async def broadcast(request: Request, body: BroadcastRequest) -> BroadcastResult:
        """질문을 전원에게 뿌리고 **답할 수 있는 사람만 남긴다.**

        ⚠️ 이 왕복은 경계를 넘지 않는다 (`crossed_boundary: Literal[False]`).
           문서를 읽지 않고, 경계 밖 Agent 를 부르지 않고, 담당자 인박스에
           아무것도 쓰지 않는다. 그래서 감사 레코드도 없다 — 나간 것이 없다.

        실제 질의는 사용자가 사람을 고른 뒤 `/api/ask/prepare` 부터 시작한다.
        선별은 목록을 줄일 뿐이고 **지목은 여전히 사람이 한다** (FR-29).
        """
        return await _services(request).orchestrator.broadcast(body)

    # ── 처리 경과 (게이트키퍼 트레이스) ──────────────────────────────

    @app.get("/api/trace/{trace_id}", response_model=GatekeeperTrace)
    async def trace(request: Request, trace_id: str) -> GatekeeperTrace:
        """말풍선의 "경과 보기" 가 여는 것.

        ⚠️ **loopback 전용이다** (`_origin_gate` 가 원격을 막는다). 트레이스는
           답변에 등장한 기호의 매핑 일부를 담고, 그것은 재수화된 실제 이름이다.

        ⚠️ TTL(30분)이 지나면 404 다. 감사 로그와 달리 트레이스는 사라져야
           한다 — 법적 증거는 `audit.py` 가 맡고 이것은 설명용이다.
        """
        found = _services(request).traces.get(trace_id)
        if found is None:
            raise HTTPException(
                status_code=404, detail="처리 경과가 만료되었거나 존재하지 않습니다"
            )
        return found

    # ── 질문: prepare / send 2단계 (BR-M-02) ─────────────────────────

    @app.post("/api/ask/prepare", response_model=PrepareResult)
    async def prepare(request: Request, body: AskRequest) -> PrepareResult:
        """변환 · 검증 · 미리보기. **Agent 를 호출하지 않는다.**"""
        return await _services(request).orchestrator.prepare(body)

    @app.post("/api/ask/send", response_model=AskResult)
    async def send(request: Request, body: SendRequest) -> AskResult:
        """사용자 승인 후 전송.

        `envelope_id` 가 만료·중복이면 `410 Gone` (핸들러가 변환한다).
        `approved_by` 가 없으면 pydantic 이 422 로 막는다 — 승인 없는 전송이
        여기까지 오지도 못한다.
        """
        return await _services(request).orchestrator.send(
            body.request_id, body.envelope_ids, body.approved_by
        )

    # ── 인박스 ───────────────────────────────────────────────────────

    @app.get("/api/inbox", response_model=list[InboxItem])
    async def inbox_list(
        request: Request,
        owner: str = Query(min_length=1, max_length=64),
        status: str | None = Query(default=None, max_length=32),
    ) -> list[InboxItem]:
        return list(_services(request).inbox.list_for(owner, status=status))

    @app.post("/api/inbox/{item_id}/resolve", response_model=InboxItem)
    async def inbox_resolve(request: Request, item_id: str, body: ResolveRequest) -> InboxItem:
        """3버튼 (BR-I-01). 승인이면 `VerifiedQA` 로 환류한다 (BR-I-02).

        ⚠️ 환류 시 `tier` 를 보존한다. 승인은 답변의 정확성을 검증한 것이고
           등급을 낮춘 것이 아니다.
        """
        svc = _services(request)
        try:
            item = svc.inbox.resolve(item_id, body)
        except KeyError as e:
            raise HTTPException(status_code=404, detail="인박스 항목이 없습니다") from e
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        qa = svc.inbox.to_verified_qa(item)
        if qa is not None:
            svc.store.append_verified(item.owner_entity_id, qa)
        return item

    # ── 문서 업로드 (Day 4) ──────────────────────────────────────────

    @app.post("/api/documents", response_model=UploadResult)
    async def upload_document(request: Request, body: UploadRequest) -> UploadResult:
        """문서를 올리고 **즉시 등급을 판정해** 근거와 함께 돌려준다.

        파일은 `MESH_DATA_ROOT` 아래에만 저장되고 경계를 넘지 않는다.
        판정에는 규칙(순수 함수)과 신뢰 구역 모델만 쓴다.
        """
        return await _services(request).documents.upload(body)

    @app.get("/api/documents", response_model=DocumentList)
    async def list_documents(
        request: Request, owner: str = Query(min_length=1, max_length=64)
    ) -> DocumentList:
        return await _services(request).documents.list_for(owner)

    @app.delete("/api/documents/{document_id}")
    async def delete_document(
        request: Request,
        document_id: str,
        owner: str = Query(min_length=1, max_length=64),
    ) -> dict[str, bool]:
        """**업로드한 문서만** 삭제한다. 샘플 코퍼스는 지울 수 없다."""
        deleted = _services(request).documents.delete(owner, document_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="업로드한 문서가 아닙니다")
        return {"deleted": True}

    # ── 저장 위치 (loopback 전용) ────────────────────────────────────

    @app.get("/api/storage", response_model=StorageInfo)
    async def storage(
        request: Request, owner: str | None = Query(default=None, max_length=64)
    ) -> StorageInfo:
        """파일이 **실제로 어디에 있는가.**

        이 프로젝트의 주장은 "원문이 경계를 넘지 않는다" 다. 그 주장을 확인하는
        가장 직접적인 방법은 파일을 파인더에서 열어 보는 것이고, 그러려면 경로를
        알아야 한다. 경로를 숨기면 주장을 검증할 수 없다.

        ⚠️ **loopback 전용이다.** 절대 경로는 사용자 이름과 디렉터리 구조를 담는다.
           `_origin_gate` 가 원격 출처를 막는다 (`/api/peer/*` 만 허용).
        """
        svc = _services(request)
        cfg = svc.cfg
        configured = os.environ.get("MESH_DATA_ROOT", "./data")
        root = cfg.data_root.resolve()

        my_uploads: Path | None = None
        if owner and owner in svc.data.agents:
            my_uploads = svc.store.uploads_dir(owner).resolve()

        target = my_uploads or cfg.uploads_root.resolve()
        return StorageInfo(
            data_root=str(root),
            uploads_root=str(cfg.uploads_root.resolve()),
            my_uploads=str(my_uploads) if my_uploads else None,
            audit_db=str(cfg.db_path.resolve()),
            sessions_root=str(cfg.sessions_root.resolve()),
            configured_relative=not Path(configured).is_absolute(),
            configured_value=configured,
            exists=target.is_dir(),
            reveal_command=_reveal_command(target),
        )

    # ── 피어 메시 ────────────────────────────────────────────────────
    #
    # `/api/peers` 는 **loopback 전용** (내 화면이 노드 상태를 본다).
    # `/api/peer/*` 는 **원격이 부르는 것** (토큰 필요, `/hello` 만 예외).
    #
    # 이름이 비슷해 헷갈리기 쉬우므로 규칙을 하나로 정한다:
    #   복수형 `peers` = 내가 남을 본다 · 단수형 `peer` = 남이 나를 본다

    @app.get("/api/peers", response_model=PeerStatus)
    async def peers(request: Request) -> PeerStatus:
        """설정된 피어 노드의 상태. **loopback 전용.**

        상태를 네 가지로 나눈다 (`connected`/`unreachable`/`token_invalid`/`self`) —
        전부 "실패" 로 뭉치면 사람이 원인을 짐작해야 한다. 토큰이 틀린 것과
        노드가 꺼진 것은 고치는 방법이 다르다.
        """
        svc = _services(request)
        cfg = svc.cfg
        views = await svc.peer_client.probe_all() if svc.peer_client else ()
        return PeerStatus(
            node_name=cfg.node_name,
            lan_mode=cfg.lan_mode,
            listen_url=f"http://{_advertised_host(cfg)}:{cfg.bind_port}",
            peer_token_set=bool(cfg.peer_token),
            peers=views,
            hint=_peer_hint(cfg, views),
        )

    @app.get("/api/peer/hello", response_model=PeerIdentity)
    async def peer_hello(request: Request) -> PeerIdentity:
        """토큰 없이 답하는 유일한 피어 경로.

        ⚠️ **사람 목록·문서·경로를 담지 않는다.** 토큰 없이 답하므로 담는 순간
           인증 없는 정보 공개가 된다. 노드 이름과 개수까지만이다.

        하나를 열어 두는 이유: 화면이 "응답 없음" 과 "토큰 불일치" 를 구분해
        보여줘야 한다. 전부 막으면 두 원인이 같아 보이고 사람이 짐작하게 된다.
        """
        svc = _services(request)
        return PeerIdentity(
            node_name=svc.cfg.node_name,
            version=__version__,
            peer_ready=bool(svc.cfg.peer_token),
            agent_count=len(svc.data.agents),
        )

    @app.get("/api/peer/agents", response_model=list[AgentCardView])
    async def peer_agents(request: Request) -> list[AgentCardView]:
        """이 노드의 지목 목록. **`node_name` 을 여기서 찍는다.**

        질문자 쪽에서 찍으면 URL 밖에 모른다. 사람이 읽을 이름이 필요하다.

        ⚠️ 로컬 목록만 준다 (`store.list_agents`). `orchestrator.agent_cards()`
           를 부르면 그 노드가 자기 피어까지 합쳐 돌려주고, 그러면 A→B→C 를
           거쳐 같은 사람이 두 번 나오거나 순환이 생긴다. **한 홉만 간다.**
        """
        svc = _services(request)
        cards = await svc.store.list_agents()
        node = svc.cfg.node_name
        return [AgentCardView.model_validate({**c.model_dump(), "node_name": node}) for c in cards]

    @app.post("/api/peer/prepare", response_model=PeerPreparedCall)
    async def peer_prepare(request: Request, body: PeerPrepareRequest) -> PeerPreparedCall:
        """다른 컴퓨터가 "이 질문을 네 Agent 에게 준비해 달라" 고 청한다.

        **이 노드가 자기 원문을 읽고 판정·조립·검증을 한다.** 질문자에게 가는
        것은 미리보기(구조 페이로드 전문 + 검증 결과)이고, 원문은 가지 않는다.

        `agents_notified=False` 는 여기서도 유지된다 (BR-O-03) — prepare 는
        이 노드의 인박스에도 아무것도 쓰지 않는다.
        """
        svc = _services(request)
        if body.target not in svc.data.agents:
            raise HTTPException(status_code=404, detail="이 노드에 없는 사람입니다")
        log.info(
            "피어 prepare 요청",
            extra=log_extra(asker_node=body.asker_node, asker=body.asker, target=body.target),
        )
        prepared = await svc.orchestrator.prepare(
            AskRequest(asker=body.asker, question=body.question, targets=[body.target])
        )
        call = prepared.calls[0]
        # ⚠️ `request_id` 를 질문자에게 알려 주지 않는다. 질문자는 `envelope_id`
        #    만 들고 다시 오고, 이 노드가 그것으로 자기 pending 을 찾는다.
        #    두 노드가 같은 id 공간을 공유하면 충돌하고, 충돌하면 남의 준비
        #    결과를 전송할 수 있다.
        svc.peer_pending[call.envelope_id or f"blocked_{body.target}"] = prepared.request_id
        return PeerPreparedCall(node_name=svc.cfg.node_name, call=call)

    @app.post("/api/peer/send", response_model=PeerAnswer)
    async def peer_send(request: Request, body: PeerSendRequest) -> PeerAnswer:
        """승인 후 전송. **이 노드가 경계를 넘고, 이 노드에 기록이 남는다.**

        원문을 가진 쪽에 감사 레코드가 남는 것이 맞다 — "무엇이 경계를 넘었나"는
        원문을 가진 사람이 증명해야 하고, 증거가 남의 컴퓨터에 있으면 증명이
        성립하지 않는다.

        에스컬레이션도 이 노드의 인박스에 만들어진다. 담당자가 이 컴퓨터에 있다.
        """
        svc = _services(request)
        local_request_id = svc.peer_pending.pop(body.envelope_id, None)
        if local_request_id is None:
            raise GatekeeperError(
                f"준비된 요청을 찾을 수 없다 (TTL 만료 또는 이미 전송됨): {body.envelope_id}"
            )
        result = await svc.orchestrator.send(local_request_id, [body.envelope_id], body.approved_by)
        answer = result.merged.answers[0]
        escalated = bool(result.escalations)
        log.info(
            "피어 send 완료",
            extra=log_extra(
                asker_node=body.asker_node,
                target=answer.entity_id,
                escalated=escalated,
            ),
        )
        return PeerAnswer(
            node_name=svc.cfg.node_name,
            answer=answer,
            escalated=escalated,
            escalation_note=(
                f"{answer.agent_label} 의 담당자에게 확인을 요청했습니다 "
                f"({svc.cfg.node_name} 의 인박스)"
                if escalated
                else None
            ),
        )

    # ── 사용자 · 질문 프리셋 ─────────────────────────────────────────

    @app.get("/api/users", response_model=list[UserView])
    async def users(request: Request) -> list[UserView]:
        """전환 가능한 사용자.

        ⚠️ **인증이 아니다.** 데모용 관점 전환이며 화면이 그 사실을 표시한다.
           프런트가 사람 목록을 하드코딩하지 않게 하려고 서버가 준다 —
           `agents.yaml` 에 한 명 추가하면 화면에도 나타난다 (FR-23).
        """
        return [
            UserView(entity_id=a.entity_id, display_name=a.display_name, expertise=a.expertise)
            for a in _services(request).data.agents.values()
        ]

    @app.get("/api/questions", response_model=list[PresetQuestion])
    async def questions(request: Request) -> list[PresetQuestion]:
        """데모 질문 프리셋. 없으면 빈 목록 (화면이 입력창만 쓴다)."""
        path = _services(request).cfg.questions_path
        if not path.is_file():
            return []
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        return [PresetQuestion.model_validate(item) for item in raw.get("presets") or []]

    # ── 감사 로그 (BR-A-04, FR-42) ───────────────────────────────────

    @app.get("/api/audit", response_model=AuditSearchResult)
    async def audit_search(
        request: Request,
        q: str | None = Query(default=None, max_length=MAX_SEARCH_CHARS),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> AuditSearchResult:
        """원문 검색. **0건이 이 화면의 핵심 기능이다.**

        검색어가 있고 결과가 0건일 때 UI 가
        "0건 — 이 문구는 경계를 넘은 적이 없습니다"를 크게 표시한다.

        ⚠️ `local_queries`(신뢰 구역 내 처리)는 포함하지 않는다 (BR-U-11).
           "레코드가 없다"가 증거가 되려면 섞이면 안 된다.
        """
        svc = _services(request)
        rows = svc.audit.search(q, limit=limit) if q else svc.audit.recent(limit=limit)
        return AuditSearchResult(
            query=q,
            rows=tuple(AuditRowView.model_validate(r.model_dump()) for r in rows),
            total_records=svc.audit.count(),
        )

    # ── 상태 ─────────────────────────────────────────────────────────

    @app.get("/api/health", response_model=HealthStatus)
    async def health(request: Request) -> HealthStatus:
        """⚠️ `trust_boundary_simulated` 를 노출한다.

        UI 헤더가 이 값을 상시 표시한다. 숨기면 심사자를 속이는 것이고,
        먼저 밝히는 것이 지적당하는 것보다 낫다.
        """
        svc = _services(request)
        cfg = svc.cfg
        local_vocab = sha256_file(cfg.vocab_path)
        return HealthStatus(
            exaone_mode=cfg.exaone_mode,  # type: ignore[arg-type]
            agent_transport=cfg.agent_transport,
            trusted_zone_llm_base_url=cfg.trusted_zone_llm_base_url,
            trust_boundary_simulated=cfg.trust_boundary_simulated,
            agent_model_id=cfg.agent_model_id,
            draft_model_id=cfg.draft_model_id,
            vocab_version=svc.data.vocab.version,
            vocab_sha256=local_vocab,
            vocab_drift=local_vocab != svc.data.vocab_sha256,
            mirror_backlog=svc.audit.mirror_failures,
            demo_now_override=cfg.demo_now,
            envelope_cache_size=len(svc.gatekeeper.cache),
            disposition_counts=svc.audit.disposition_counts(),
        )

    # ── 보안 프로토콜 CRUD ───────────────────────────────────────────

    def _protocol_to_view(p: object) -> ProtocolView:
        from mesh.protocol_schemas import SecurityProtocol as SP
        assert isinstance(p, SP)
        return ProtocolView(
            level=p.level,
            owner=p.owner,
            description=p.description,
            updated_at=p.updated_at.isoformat() if p.updated_at else "",
            secret_keywords=p.secret_keywords,
            secret_patterns=p.secret_patterns,
            secret_directories=p.secret_directories,
            secret_extensions=p.secret_extensions,
            secret_content_patterns=p.secret_content_patterns,
            internal_keywords=p.internal_keywords,
            internal_directories=p.internal_directories,
            internal_extensions=p.internal_extensions,
            open_directories=p.open_directories,
            exaone_context_hints=p.exaone_context_hints,
        )

    @app.get("/api/protocols", response_model=list[ProtocolView])
    async def list_protocols(request: Request) -> list[ProtocolView]:
        """저장된 모든 보안 프로토콜 목록."""
        store = _services(request).data.protocol_store
        return [_protocol_to_view(p) for p in store.list_all()]

    @app.get("/api/protocols/{level}/{owner}", response_model=ProtocolView)
    async def get_protocol(request: Request, level: str, owner: str) -> ProtocolView:
        store = _services(request).data.protocol_store
        from mesh.protocol_schemas import ProtocolLevel
        p = store.get(level, owner)  # type: ignore[arg-type]
        if p is None:
            raise HTTPException(status_code=404, detail="프로토콜이 없습니다")
        return _protocol_to_view(p)

    @app.post("/api/protocols", response_model=ProtocolView)
    async def upsert_protocol(request: Request, body: ProtocolUpsertRequest) -> ProtocolView:
        """프로토콜 생성 또는 수정. 저장 즉시 분류 규칙에 반영된다."""
        from mesh.protocol_schemas import SecurityProtocol as SP
        store = _services(request).data.protocol_store
        p = SP(
            level=body.level,
            owner=body.owner,
            description=body.description,
            secret_keywords=body.secret_keywords,
            secret_patterns=body.secret_patterns,
            secret_directories=body.secret_directories,
            secret_extensions=body.secret_extensions,
            secret_content_patterns=body.secret_content_patterns,
            internal_keywords=body.internal_keywords,
            internal_directories=body.internal_directories,
            internal_extensions=body.internal_extensions,
            open_directories=body.open_directories,
            exaone_context_hints=body.exaone_context_hints,
        )
        store.save(p)
        return _protocol_to_view(p)

    @app.delete("/api/protocols/{level}/{owner}")
    async def delete_protocol(request: Request, level: str, owner: str) -> dict[str, bool]:
        store = _services(request).data.protocol_store
        deleted = store.delete(level, owner)  # type: ignore[arg-type]
        if not deleted:
            raise HTTPException(status_code=404, detail="프로토콜이 없습니다")
        return {"deleted": True}

    @app.get("/api/protocols-merged", response_model=MergedRulesView)
    async def merged_rules_preview(request: Request) -> MergedRulesView:
        """현재 적용 중인 머지 규칙 미리보기. 프로토콜 UI 실시간 확인용."""
        data = _services(request).data
        rules = data.rules
        protocols = data.protocol_store.list_all()
        return MergedRulesView(
            secret_keywords=list(rules.banned.literals),
            secret_patterns=list(rules.banned.patterns),
            secret_path_globs=list(rules.secret_path_globs),
            open_path_globs=list(rules.open_path_globs),
            internal_path_globs=list(rules.internal_path_globs),
            protocol_count=len(protocols),
        )


def _static(name: str, media_type: str) -> Response:
    """U4 가 아직 파일을 만들지 않았어도 앱이 뜨게 한다.

    404 대신 안내를 주는 이유: Day 3 에 API 를 확인할 때 `/` 가 500 을 내면
    "앱이 안 뜬다"고 오해한다.
    """
    path = WEB_ROOT / name
    if path.is_file():
        return FileResponse(path, media_type=media_type)
    return PlainTextResponse(
        f"{name} 이 아직 없습니다 (U4 는 Day 4). API 는 /api/health 로 확인하세요.",
        status_code=200,
        media_type="text/plain; charset=utf-8",
    )


# ══════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════


def main() -> None:  # pragma: no cover — uvicorn 실행 경로
    import uvicorn

    cfg = Config.load()
    uvicorn.run(
        create_app(cfg),
        host=cfg.bind_host,
        port=cfg.bind_port,
        log_config=None,  # 우리 JSON 로거를 쓴다
    )


if __name__ == "__main__":  # pragma: no cover
    main()

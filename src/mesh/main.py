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
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import ValidationError

from mesh.agent import AgentClient
from mesh.api_models import (
    MAX_SEARCH_CHARS,
    AgentCardView,
    AskRequest,
    AskResult,
    AuditRowView,
    AuditSearchResult,
    DocumentList,
    ErrorResponse,
    HealthStatus,
    InboxItem,
    PrepareResult,
    PresetQuestion,
    ResolveRequest,
    SendRequest,
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
from mesh.store import KnowledgeStore

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
        self.orchestrator = Orchestrator(
            cfg,
            self.data,
            self.store,
            self.gatekeeper,
            self.agent,
            self.audit,
            self.inbox,
        )

    async def aclose(self) -> None:
        for closer in (self.exaone, self.gatekeeper.broker):
            aclose = getattr(closer, "aclose", None)
            if aclose is not None:
                await aclose()
        self.audit.close()


def check_bind_host(cfg: Config, *, allow_network: bool) -> None:
    """localhost 가 아니면 명시적 확인을 요구한다 (BR-M-01).

    Raises:
        MeshError: 확인 없이 네트워크에 노출하려 할 때. **시작을 막는다** —
            경고만 하면 아무도 읽지 않는다.
    """
    if cfg.bind_host in _LOCALHOSTS:
        return
    if allow_network:
        log.warning(
            "네트워크 바인딩을 명시적으로 허용했다. 이 서비스는 인증이 없고 "
            "재수화된 실제 이름을 반환한다 — 신뢰된 네트워크에서만 쓰라",
            extra=log_extra(bind_host=cfg.bind_host),
        )
        return
    raise MeshError(
        f"MESH_BIND_HOST={cfg.bind_host} 는 localhost 가 아니다. "
        "이 서비스는 원문 파일을 읽고 재수화된 실제 이름을 반환하며 인증이 없다. "
        "네트워크에 노출하면 권한 우회 도구가 된다 (BR-M-01).\n"
        "  -> MESH_BIND_HOST=127.0.0.1 로 실행하라\n"
        "  -> 의도한 것이라면 MESH_ALLOW_NETWORK_BIND=1 을 함께 지정하라"
    )


# ══════════════════════════════════════════════════════════════════════
# 앱
# ══════════════════════════════════════════════════════════════════════


def create_app(cfg: Config | None = None, *, services: Services | None = None) -> FastAPI:
    """앱을 만든다. 테스트는 `services` 를 주입해 대역을 쓴다."""
    import os

    cfg = cfg or Config.load()
    setup_logging("DEBUG" if cfg.dev_mode else "INFO")
    check_bind_host(cfg, allow_network=os.environ.get("MESH_ALLOW_NETWORK_BIND", "") == "1")

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


def _install_middleware(app: FastAPI) -> None:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    # ⚠️ CORS 미들웨어를 추가하지 않는다 (BR-M-04). 동일 출처만 허용한다.
    #    와일드카드 `*` 는 이 서비스에서 곧 데이터 유출이다.

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

"""문서 업로드 — 사용자가 자기 컴퓨터의 문서를 올리는 지점 (Day 4 신설).

**이 도구의 실사용은 여기서 시작한다.** 지금까지는 저장소에 심어둔 샘플
코퍼스로 동작을 보였지만, 실제로는 사람이 자기 문서를 올리고 그 문서가
다른 사람의 질문에 동원된다.

──────────────────────────────────────────────────────────────────────
업로드 응답이 곧 첫 데모 장면이다
──────────────────────────────────────────────────────────────────────

올리는 즉시 등급을 판정해 **근거와 함께** 돌려준다.

    "방금 올린 문서는 기밀로 판정됐습니다.
     근거: 금칙어 패턴 /\\d+\\s*억\\s*원?/ (본문의 금액 표기)
     → 이 문서를 쓰는 질문은 원문이 경계를 넘지 않습니다."

판정을 나중이 아니라 **여기서** 보여주는 이유: 답변이 무뎌졌을 때
"왜?"를 되짚을 수 있어야 하고, 등급 판정이 블랙박스가 아니라는 것을
이 화면 하나로 보일 수 있다.

──────────────────────────────────────────────────────────────────────
업로드는 신뢰 구역 안의 행위다
──────────────────────────────────────────────────────────────────────

파일은 `MESH_DATA_ROOT` 아래에만 저장되고 경계를 넘지 않는다.
등급 판정은 규칙(순수 함수) + EXAONE(신뢰 구역)만 쓴다.
Bedrock 은 관여하지 않는다 — 그래서 이 모듈에 경계 밖 클라이언트가 없다.

3중 경로 검사를 한다 (`store.save_upload`):
  ① `Path(filename).name` 으로 경로 성분 제거
  ② `safe_resolve()` — root 하위 확인
  ③ `in_scope()` — 그 사람의 지식 범위 확인

②③이 겹쳐 보이지만 다른 것을 막는다. ②는 파일시스템 탈출을, ③은
"박선임 디렉터리에 김책임 문서를 올리는" 것을 막는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime
from pathlib import Path

from mesh.api_models import (
    DocumentList,
    DocumentView,
    TierEvidence,
    UploadRequest,
    UploadResult,
)
from mesh.classifier import rule_tier
from mesh.config import Config, DataBundle, get_logger, log_extra
from mesh.exceptions import MeshError
from mesh.gatekeeper import Gatekeeper
from mesh.schemas import Tier
from mesh.store import KnowledgeStore, SessionNotFound, source_kind_of

log = get_logger("documents")

#: 업로드 결과에 담을 판정 근거 최대 개수.
MAX_EVIDENCE = 4


class UploadRejected(MeshError):
    """업로드 거부.

    귀결: 400/422. 파일은 저장되지 않는다.
    """


def _read_and_stat(path: Path) -> tuple[str, os.stat_result]:
    """블로킹 I/O 를 한 곳에 모아 `asyncio.to_thread` 로 넘긴다."""
    return path.read_text(encoding="utf-8", errors="replace"), path.stat()


def document_id_for(rel: str) -> str:
    """경로에서 유도하는 결정적 ID. 목록·삭제가 같은 값을 쓴다."""
    return "doc_" + hashlib.sha1(rel.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


class DocumentService:
    """업로드 저장 + 즉시 등급 판정 + 세션 후보 반영."""

    def __init__(
        self,
        cfg: Config,
        data: DataBundle,
        store: KnowledgeStore,
        gatekeeper: Gatekeeper,
    ) -> None:
        self.cfg = cfg
        self.data = data
        self.store = store
        self.gatekeeper = gatekeeper

    # ── 업로드 ───────────────────────────────────────────────────────

    async def upload(self, request: UploadRequest) -> UploadResult:
        """문서를 저장하고 등급을 판정해 돌려준다.

        Raises:
            UploadRejected: 미등록 소유자 또는 내용이 비어 있다
            PathEscapeError / ScopeViolationError: 경로 검사 실패
        """
        if request.owner not in self.data.agents:
            raise UploadRejected(
                f"미등록 사용자다: {request.owner}. config/agents.yaml 을 확인하라"
            )
        if not request.content.strip():
            raise UploadRejected("내용이 비어 있는 문서는 올릴 수 없다")

        rel, resolved = self.store.save_upload(request.owner, request.filename, request.content)

        warnings: list[str] = []
        attached = False
        if request.attach_to_session:
            try:
                self.store.attach_path(request.owner, rel)
                attached = True
            except SessionNotFound:
                warnings.append(
                    "세션 파일이 없어 질의 후보에 추가하지 못했습니다. 문서는 저장되었습니다"
                )

        view = await self._describe(rel, resolved, request.owner, attached=attached)

        if view.tier is Tier.SECRET:
            warnings.append(
                "기밀로 판정됐습니다. 이 문서를 쓰는 질문은 원문 대신 구조 요약만 경계를 넘습니다"
            )
        elif view.tier is Tier.OPEN:
            warnings.append("공개로 판정됐습니다. 이 문서는 원문 그대로 전달될 수 있습니다")

        log.info(
            "업로드 완료",
            extra=log_extra(owner=request.owner, path=rel, tier=view.tier.value, attached=attached),
        )
        return UploadResult(
            document=view,
            in_scope=self.store.in_scope(rel, request.owner),
            warnings=tuple(warnings),
        )

    # ── 목록 ─────────────────────────────────────────────────────────

    async def list_for(self, owner: str) -> DocumentList:
        """소유자의 문서 목록.

        업로드한 것과 세션이 이미 가리키던 샘플 문서를 함께 보여준다.
        후자를 `seeded=True` 로 구분한다 — 사용자가 "내가 올린 것"과
        "원래 있던 것"을 헷갈리지 않아야 삭제가 안전하다.
        """
        if owner not in self.data.agents:
            raise UploadRejected(f"미등록 사용자다: {owner}")

        uploaded = list(self.store.list_uploads(owner))
        seeded: list[str] = []
        try:
            session = self.store.load_session(owner)
            attached = set(session.open_paths)
            seeded = [p for p in self.store.candidate_paths(session) if p not in uploaded]
        except SessionNotFound:
            attached = set()

        views: list[DocumentView] = []
        for rel in (*uploaded, *seeded):
            try:
                resolved = self.store.resolve(rel)
            except MeshError:
                continue
            if not resolved.is_file():
                continue
            views.append(
                await self._describe(
                    rel,
                    resolved,
                    owner,
                    attached=rel in attached,
                    seeded=rel not in uploaded,
                )
            )
        return DocumentList(owner=owner, documents=tuple(views))

    # ── 삭제 ─────────────────────────────────────────────────────────

    def delete(self, owner: str, document_id: str) -> bool:
        """업로드한 문서만 삭제한다.

        `document_id` 로 받는 이유: 경로를 그대로 받으면 클라이언트가 임의
        경로를 보낼 수 있다. ID 는 **이미 목록에 있는 것**에서만 나온다.
        """
        for rel in self.store.list_uploads(owner):
            if document_id_for(rel) == document_id:
                return self.store.delete_upload(owner, rel)
        return False

    # ── 판정 ─────────────────────────────────────────────────────────

    async def _describe(
        self,
        rel: str,
        resolved: Path,
        owner: str,
        *,
        attached: bool,
        seeded: bool = False,
    ) -> DocumentView:
        # 파일 I/O 를 스레드로 넘긴다. 이 함수는 이벤트 루프에서 돌고
        # 목록 조회는 문서 수만큼 반복되므로, 작은 파일이어도 루프를 막지 않는다.
        text, stat = await asyncio.to_thread(_read_and_stat, resolved)

        # 규칙 판정은 순수 함수라 근거를 그대로 쓸 수 있다.
        verdict = rule_tier(text, rel, self.data.rules)
        evidence = [
            TierEvidence(rule=verdict.rule, reason=reason)
            for reason in verdict.reasons[:MAX_EVIDENCE]
        ]

        # 규칙이 SECRET 이 아니면 EXAONE 이 올릴 수 있다 (BR-C-01).
        decision = await self.gatekeeper.classify(text, rel)
        if decision.tier > verdict.tier:
            evidence.append(
                TierEvidence(
                    rule=0,
                    reason=(
                        f"신뢰 구역 모델이 {decision.tier.label_ko} 로 상향했습니다"
                        if not decision.exaone_failed
                        else "등급 판정이 실패해 기밀로 간주했습니다 (fail closed)"
                    ),
                )
            )

        source_kind_of(rel)  # 형식 확인 (뷰에는 담지 않는다)
        return DocumentView(
            document_id=document_id_for(rel),
            owner=owner,
            filename=resolved.name,
            internal_path=rel,
            size_bytes=stat.st_size,
            uploaded_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
            tier=decision.tier,
            tier_evidence=tuple(evidence),
            attached=attached,
            seeded=seeded,
        )

    def kind_of(self, rel: str) -> str:
        return source_kind_of(rel)[0]

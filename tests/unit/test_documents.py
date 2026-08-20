"""업로드 경로 — 사용자가 자기 문서를 올리는 지점 (Day 4).

여기서 검증하는 것은 "파일이 저장되는가"가 아니다. 그건 쉽고 안 깨진다.
검증하는 것은 **업로드가 다른 보안 규칙을 우회하는 새 구멍이 되지 않는가**다.

업로드는 이 시스템에서 유일하게 *외부에서 들어온 이름*으로 파일시스템을
건드리는 경로다. 그래서 다음을 각각 다른 테스트로 고정한다.

  ① 파일명으로 디렉터리를 벗어날 수 없다            (PathEscapeError)
  ② 남의 `knowledge_scope` 에 쓸 수 없다             (ScopeViolationError)
  ③ 조용히 덮어쓰지 않는다                           (`-1` 접미사)
  ④ 시드 코퍼스를 지울 수 없다                       (ScopeViolationError)
  ⑤ 등급 판정에 **근거**가 붙는다                     (TierEvidence)
  ⑥ 업로드가 `session.updated_at` 을 건드리지 않는다  (BR-S-04 보존)

⑥이 가장 미묘하다. 파일을 올린 것만으로 세션이 "방금 활동함"이 되면
STALE 보정이 무의미해지고, 오래된 세션에서 자신 있게 답하는 회귀가 생긴다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mesh.api_models import UploadRequest
from mesh.documents import DocumentService, UploadRejected, document_id_for
from mesh.exceptions import PathEscapeError, ScopeViolationError
from mesh.schemas import Tier

OWNER = "person:kim"
OTHER = "person:park"
#: `agents.yaml` 에 없는 사람. 패턴은 통과하지만 등록되지 않았다.
UNKNOWN = "person:nobody"

#: 규칙 판정만으로 SECRET 이 되는 본문. 금칙어(고객사명 + 금액)를 함께 넣는다.
SECRET_BODY = """# title: 임시 계약 메모
# as_of: 2026-08-18

고객사 H 와의 재계약 협상에서 총액 12억 원 규모를 제시받았다.
납기는 2026-11-30 이며 위약 조항이 붙는다.
"""

OPEN_BODY = """# title: 표준 재시도 지침
# as_of: 2026-08-01

외부 호출은 지수 백오프로 최대 3회 재시도한다. 타임아웃은 2초다.
"""


@pytest.fixture
def documents(wiring) -> DocumentService:
    return wiring.documents


# ══════════════════════════════════════════════════════════════════════
# ① 파일명 — 경로 탈출
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.md",
        "../../../../etc/passwd",
        "sub/dir/nested.md",
        "/absolute.md",
        ".hidden.md",
        "..",
        "",
    ],
    ids=[
        "부모-참조",
        "깊은-탈출",
        "하위-디렉터리",
        "절대경로",
        "숨김파일",
        "점점",
        "빈-이름",
    ],
)
async def test_파일명이_경로를_벗어나면_거부한다(documents, filename: str) -> None:
    """`Path(filename).name` 으로 성분을 벗기고 **원본과 달라지면 거부**한다.

    벗겨서 조용히 쓰지 않는 이유: `sub/dir/a.md` 를 `a.md` 로 저장하면
    사용자가 올린 것과 저장된 것이 달라지고, 목록에서 그걸 알 수 없다.
    거부하고 이유를 말하는 편이 낫다.
    """
    with pytest.raises((PathEscapeError, UploadRejected, ValueError)):
        await documents.upload(UploadRequest(owner=OWNER, filename=filename, content=OPEN_BODY))


@pytest.mark.parametrize(
    "filename",
    ["report.pdf", "spec.docx", "archive.zip", "tool.exe", "photo.png", "notes"],
    ids=["pdf", "docx", "zip", "exe", "png", "확장자-없음"],
)
async def test_텍스트가_아닌_확장자를_거부한다(documents, filename: str) -> None:
    """이 도구는 **텍스트 지식**만 다룬다.

    바이너리를 받지 않는 것은 기능 미비가 아니라 좁힌 범위다. 받아서
    코퍼스에 두면 등급 판정도 안 되고 읽히지도 않는 파일이 질의 후보에 낀다.

    ⚠️ `.sh`·`.sql`·`.py` 는 **허용한다.** 배포 스크립트와 마이그레이션은
       실제로 팀이 서로 묻는 지식이고, 이 시스템은 파일을 실행하지 않고
       텍스트로만 읽는다. 확장자 목록의 기준은 "실행 위험"이 아니라
       "텍스트로 읽히는가"다.
    """
    with pytest.raises((UploadRejected, ValueError)):
        await documents.upload(UploadRequest(owner=OWNER, filename=filename, content=OPEN_BODY))


async def test_스크립트는_텍스트로_받는다(documents) -> None:
    """`.sh` 허용을 명시적으로 고정한다 — 위 테스트와 짝이다."""
    result = await documents.upload(
        UploadRequest(owner=OWNER, filename="deploy.sh", content="#!/bin/sh\nmake run\n")
    )
    assert result.document.filename == "deploy.sh"


async def test_빈_내용을_거부한다(documents) -> None:
    with pytest.raises((UploadRejected, ValueError)):
        await documents.upload(
            UploadRequest(owner=OWNER, filename="empty.md", content="   \n\t\n  ")
        )


async def test_미등록_소유자를_거부한다(documents) -> None:
    """`agents.yaml` 에 없는 사람 이름으로 디렉터리를 만들 수 없다.

    막지 않으면 임의 이름으로 `corpus/<아무거나>/uploads/` 가 생기고,
    그 디렉터리는 어느 `knowledge_scope` 에도 속하지 않아 아무도 읽지 못하는
    쓰레기가 된다.
    """
    with pytest.raises(UploadRejected):
        await documents.upload(UploadRequest(owner=UNKNOWN, filename="a.md", content=OPEN_BODY))


# ══════════════════════════════════════════════════════════════════════
# ② scope — 남의 영역
# ══════════════════════════════════════════════════════════════════════


async def test_저장_위치가_소유자_scope_안이다(documents, wiring) -> None:
    result = await documents.upload(
        UploadRequest(owner=OWNER, filename="scope.md", content=OPEN_BODY)
    )
    rel = result.document.internal_path
    assert result.in_scope is True
    assert wiring.store.in_scope(rel, OWNER) is True
    # 같은 파일이 남의 범위에는 들어가지 않는다 — 지식 격리 (BR-S-03)
    assert wiring.store.in_scope(rel, OTHER) is False


async def test_scope_밖_저장은_거부한다(wiring, monkeypatch) -> None:
    """`uploads_dir` 이 범위를 벗어나게 조작되면 저장하지 않는다.

    설정 오류(`knowledge_scope` 에 `corpus/<사람>/**` 를 빼먹음)를 조용히
    통과시키면, 올린 문서를 자기 Agent 도 읽지 못하는 상태가 된다.
    """
    store = wiring.store
    monkeypatch.setattr(
        type(store), "uploads_dir", lambda self, eid: self.cfg.corpus_root / "elsewhere"
    )
    with pytest.raises(ScopeViolationError):
        store.save_upload(OWNER, "x.md", OPEN_BODY)


# ══════════════════════════════════════════════════════════════════════
# ③ 중복 — 덮어쓰지 않는다
# ══════════════════════════════════════════════════════════════════════


async def test_같은_파일명은_덮어쓰지_않고_접미사를_붙인다(documents) -> None:
    first = await documents.upload(UploadRequest(owner=OWNER, filename="dup.md", content=OPEN_BODY))
    second = await documents.upload(
        UploadRequest(owner=OWNER, filename="dup.md", content=SECRET_BODY)
    )

    assert first.document.internal_path != second.document.internal_path
    assert second.document.filename == "dup-1.md"
    assert first.document.document_id != second.document.document_id

    listing = await documents.list_for(OWNER)
    uploaded = [d for d in listing.documents if not d.seeded]
    assert {d.filename for d in uploaded} == {"dup.md", "dup-1.md"}


# ══════════════════════════════════════════════════════════════════════
# ⑤ 등급 판정 — 근거가 붙는다
# ══════════════════════════════════════════════════════════════════════


async def test_기밀_문서는_근거와_함께_기밀로_판정된다(documents) -> None:
    """업로드 응답이 곧 첫 데모 장면이다 — 등급만이 아니라 **왜**를 준다."""
    result = await documents.upload(
        UploadRequest(owner=OWNER, filename="contract-memo.md", content=SECRET_BODY)
    )
    doc = result.document

    assert doc.tier is Tier.SECRET
    assert doc.tier_evidence, "기밀 판정에 근거가 없으면 블랙박스다"
    joined = " ".join(e.reason for e in doc.tier_evidence)
    assert "고객사 H" in joined or "억" in joined
    assert any("기밀" in w for w in result.warnings)


async def test_금칙어가_없으면_기밀이_아니다(documents) -> None:
    """기본 등급은 `INTERNAL` 이다 — `OPEN` 이 아니다.

    ⚠️ 이 구분이 중요하다. 판단이 안 서는 문서를 `OPEN` 으로 두면
       원문이 경계를 넘어도 된다는 뜻이 되고, 그건 fail-open 이다.
       금칙어가 없다는 것은 "안전하다"가 아니라 "규칙이 걸리지 않았다"다.
    """
    result = await documents.upload(
        UploadRequest(owner=OWNER, filename="retry-policy.md", content=OPEN_BODY)
    )
    assert result.document.tier is Tier.INTERNAL
    assert not any("기밀" in w for w in result.warnings)


async def test_업로드는_스스로_공개를_주장할_수_없다(documents) -> None:
    """**업로드한 문서는 절대 `OPEN` 이 되지 않는다.**

    `OPEN` 은 헤더 표기 *그리고* 공개 경로(`corpus/public/**`)를 함께 요구한다
    (classifier 규칙 ④). 업로드는 `corpus/<사람>/uploads/` 에 저장되므로
    두 번째 조건을 만족할 수 없다.

    이게 왜 중요한가: `OPEN` 은 원문이 경계를 넘어도 된다는 뜻이다. 만약
    헤더 한 줄로 `OPEN` 을 얻을 수 있다면, 기밀 문서 맨 위에 `tier: public`
    을 적는 것만으로 게이트키퍼를 통째로 우회할 수 있다. 등급 하향의 권한은
    **문서 작성자가 아니라 배치 경로**가 갖는다.
    """
    result = await documents.upload(
        UploadRequest(
            owner=OWNER,
            filename="claims-public.md",
            content="# title: 공개 안내\n# 보안 등급: 공개\n\n외부 공개 가능한 안내문이다.\n",
        )
    )
    assert result.document.tier is not Tier.OPEN
    assert result.document.tier is Tier.INTERNAL
    joined = " ".join(e.reason for e in result.document.tier_evidence)
    assert "하향 거부" in joined, "왜 공개가 안 됐는지 근거로 보여야 한다"


async def test_근거_개수를_제한한다(documents) -> None:
    """금칙어가 스무 개 걸려도 화면에 스무 줄을 쏟지 않는다."""
    noisy = SECRET_BODY + "\n" + "\n".join(f"고객사 H 관련 {i}억 원 항목" for i in range(20))
    result = await documents.upload(UploadRequest(owner=OWNER, filename="noisy.md", content=noisy))
    # 규칙 근거 MAX_EVIDENCE(4) + 모델 상향 근거 1 이 상한이다
    assert len(result.document.tier_evidence) <= 5


async def test_판정_근거에_원문이_그대로_실리지_않는다(documents) -> None:
    """근거는 *어떤 규칙이 걸렸는지*를 말한다. 문장을 옮기지 않는다.

    이 응답은 소유자만 보지만, 근거 문구는 감사 로그와 화면 양쪽에 남는다.
    여기서 원문을 통째로 복사하는 습관이 생기면 다른 화면에도 번진다.
    """
    result = await documents.upload(
        UploadRequest(owner=OWNER, filename="leak-check.md", content=SECRET_BODY)
    )
    for evidence in result.document.tier_evidence:
        assert "위약 조항이 붙는다" not in evidence.reason
        assert len(evidence.reason) < 200


# ══════════════════════════════════════════════════════════════════════
# ⑥ 세션 반영 — attach 는 하되 신선도는 건드리지 않는다
# ══════════════════════════════════════════════════════════════════════


async def test_attach_to_session_이_질의_후보에_넣는다(documents, wiring) -> None:
    result = await documents.upload(
        UploadRequest(
            owner=OWNER, filename="attached.md", content=OPEN_BODY, attach_to_session=True
        )
    )
    rel = result.document.internal_path
    assert result.document.attached is True
    session = wiring.store.load_session(OWNER)
    assert rel in session.open_paths


async def test_attach_하지_않으면_후보에_들어가지_않는다(documents, wiring) -> None:
    result = await documents.upload(
        UploadRequest(
            owner=OWNER, filename="detached.md", content=OPEN_BODY, attach_to_session=False
        )
    )
    session = wiring.store.load_session(OWNER)
    assert result.document.internal_path not in session.open_paths
    assert result.document.attached is False


async def test_업로드가_세션_신선도를_되살리지_않는다(documents, wiring) -> None:
    """BR-S-04 — 파일을 올린 것은 "사람이 그 일을 하고 있다"가 아니다.

    되살리면 오래된 세션이 업로드 한 번으로 LIVE 가 되고,
    STALE 신뢰도 보정이 통째로 무력화된다.
    """
    before = wiring.store.load_session(OWNER).updated_at
    before_fresh = wiring.store.freshness_of(wiring.store.load_session(OWNER))

    await documents.upload(
        UploadRequest(
            owner=OWNER, filename="freshness.md", content=OPEN_BODY, attach_to_session=True
        )
    )

    after_session = wiring.store.load_session(OWNER)
    assert after_session.updated_at == before
    assert wiring.store.freshness_of(after_session) is before_fresh


async def test_세션_파일이_없으면_경고하고_저장은_한다(documents, wiring) -> None:
    """세션이 없는 사람도 문서는 올릴 수 있다 — 다만 후보에는 못 들어간다.

    저장까지 실패시키면 사용자는 파일을 잃고 이유도 모른다.
    """
    session_path = wiring.store.session_path(OTHER)
    session_path.unlink()

    result = await documents.upload(
        UploadRequest(owner=OTHER, filename="orphan.md", content=OPEN_BODY, attach_to_session=True)
    )
    assert result.document.attached is False
    assert any("세션" in w for w in result.warnings)
    assert wiring.store.resolve(result.document.internal_path).is_file()


async def test_세션_json_이_줄바꿈으로_끝난다(documents, wiring) -> None:
    """되쓴 시드 파일이 `git diff` 잡음을 남기지 않는다.

    사소해 보이지만, 데모를 돌릴 때마다 `\\ No newline at end of file` 이
    뜨면 실제 변경과 구별이 안 된다.
    """
    await documents.upload(
        UploadRequest(owner=OWNER, filename="newline.md", content=OPEN_BODY, attach_to_session=True)
    )
    raw = wiring.store.session_path(OWNER).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    json.loads(raw)  # 여전히 유효한 JSON


# ══════════════════════════════════════════════════════════════════════
# 목록
# ══════════════════════════════════════════════════════════════════════


async def test_목록이_업로드와_시드를_구분한다(documents) -> None:
    """`seeded` 플래그가 없으면 사용자가 샘플 코퍼스를 지우려 든다."""
    await documents.upload(UploadRequest(owner=OWNER, filename="mine.md", content=OPEN_BODY))
    listing = await documents.list_for(OWNER)

    by_name = {d.filename: d for d in listing.documents}
    assert by_name["mine.md"].seeded is False
    assert any(d.seeded for d in listing.documents), "시드 코퍼스도 함께 보여야 한다"


async def test_목록은_남의_문서를_섞지_않는다(documents) -> None:
    await documents.upload(UploadRequest(owner=OWNER, filename="kim-only.md", content=OPEN_BODY))
    other = await documents.list_for(OTHER)
    assert "kim-only.md" not in {d.filename for d in other.documents}
    for doc in other.documents:
        assert doc.owner == OTHER


async def test_목록의_미등록_소유자를_거부한다(documents) -> None:
    with pytest.raises(UploadRejected):
        await documents.list_for(UNKNOWN)


async def test_파일이_사라진_시드_경로는_목록에서_조용히_빠진다(documents, wiring) -> None:
    """세션이 가리키는 파일이 지워져 있어도 목록 조회는 죽지 않는다."""
    session = wiring.store.load_session(OWNER)
    victim = wiring.store.resolve(session.open_paths[0])
    victim.unlink()

    listing = await documents.list_for(OWNER)
    assert all(d.internal_path != session.open_paths[0] for d in listing.documents)


# ══════════════════════════════════════════════════════════════════════
# ④ 삭제 — 업로드한 것만
# ══════════════════════════════════════════════════════════════════════


async def test_업로드한_문서를_삭제한다(documents, wiring) -> None:
    result = await documents.upload(
        UploadRequest(owner=OWNER, filename="temp.md", content=OPEN_BODY, attach_to_session=True)
    )
    rel = result.document.internal_path
    resolved = wiring.store.resolve(rel)
    assert resolved.is_file()

    assert documents.delete(OWNER, result.document.document_id) is True
    assert not resolved.exists()
    # 후보에서도 빠진다 — 남으면 매 질의마다 "파일 없음" 경고가 뜬다
    assert rel not in wiring.store.load_session(OWNER).open_paths


async def test_시드_코퍼스는_삭제되지_않는다(wiring) -> None:
    """`uploads/` 밖은 손대지 못한다. 샘플을 지우면 데모가 깨진다."""
    session = wiring.store.load_session(OWNER)
    seeded_rel = session.open_paths[0]
    assert "/uploads/" not in seeded_rel

    with pytest.raises(ScopeViolationError):
        wiring.store.delete_upload(OWNER, seeded_rel)
    assert wiring.store.resolve(seeded_rel).is_file()


async def test_남의_문서_id_로는_삭제되지_않는다(documents, wiring) -> None:
    result = await documents.upload(
        UploadRequest(owner=OWNER, filename="kims.md", content=OPEN_BODY)
    )
    assert documents.delete(OTHER, result.document.document_id) is False
    assert wiring.store.resolve(result.document.internal_path).is_file()


async def test_없는_문서_id_는_False_를_돌려준다(documents) -> None:
    assert documents.delete(OWNER, "doc_deadbeef1234") is False


async def test_document_id_는_결정적이다() -> None:
    """목록과 삭제가 같은 값을 써야 한다 — 매번 달라지면 삭제가 불가능하다."""
    rel = "corpus/kim/uploads/a.md"
    assert document_id_for(rel) == document_id_for(rel)
    assert document_id_for(rel) != document_id_for(rel + "x")
    assert document_id_for(rel).startswith("doc_")


# ══════════════════════════════════════════════════════════════════════
# 업로드 디렉터리 자체
# ══════════════════════════════════════════════════════════════════════


def test_업로드_디렉터리가_소유자_scope_안이다(wiring) -> None:
    """`corpus/kim/**` 가 `corpus/kim/uploads/**` 를 덮어야 한다.

    이게 깨지면 올린 문서를 자기 Agent 도 못 읽는다 — 조용히 쓸모없어진다.
    """
    from mesh.config import to_relative

    store = wiring.store
    for entity_id in store.data.agents:
        rel = to_relative(store.uploads_dir(entity_id), store.cfg.data_root) + "/probe.md"
        assert store.in_scope(rel, entity_id), f"{entity_id} 의 업로드가 범위 밖이다"


def test_list_uploads_는_재귀하지_않는다(wiring) -> None:
    """`iterdir()` 이다. 전역 스캔(BR-S-01)과 구분된다."""
    store = wiring.store
    target = store.uploads_dir(OWNER)
    (target / "nested").mkdir(parents=True)
    (target / "nested" / "deep.md").write_text(OPEN_BODY, encoding="utf-8")
    (target / "top.md").write_text(OPEN_BODY, encoding="utf-8")

    names = [Path(rel).name for rel in store.list_uploads(OWNER)]
    assert names == ["top.md"]

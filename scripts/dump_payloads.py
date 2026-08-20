#!/usr/bin/env python3
"""게이트 G4 — 경계를 넘은 것 **전부**를 사람이 읽을 수 있게 덤프한다.

    make eval-dump-payloads              # 감사 DB 에 있는 것을 덤프
    make eval-dump-payloads ARGS=--generate   # 시나리오를 돌려 채운 뒤 덤프

──────────────────────────────────────────────────────────────────────
왜 자동 검사만으로는 G4 가 아닌가
──────────────────────────────────────────────────────────────────────

`audit.sweep_for_leaks()` 는 원문 n-gram 과 금칙어를 기계적으로 대조한다.
강력하지만 **아는 것만 잡는다.** 잡지 못하는 것:

  · 금칙어 목록에 없는 새 고객사명
  · 문장을 옮기지 않고 *의미*를 옮긴 값 (`"납기가 촉박함"`)
  · 슬롯 이름 자체가 정보인 경우 (`penalty_clause_exists`)
  · 값 조합으로 대상이 특정되는 경우 (5G + 인증 + 8시간)

이것들은 사람이 읽어야 보인다. 그래서 G4 의 기준은 "자동 검사 통과"가
아니라 **"사람이 전부 읽고 통과"** 다. 이 스크립트는 그 읽기를 가능하게 한다.

──────────────────────────────────────────────────────────────────────
출력물
──────────────────────────────────────────────────────────────────────

`aidlc-docs/construction/g4-payload-dump.md`

  · 페이로드 1건 = 섹션 1개. 전문이 그대로 들어간다 (절단 없음)
  · 각 섹션에 확인 체크박스 — 읽은 사람이 표시한다
  · 자동 검사 결과를 함께 싣는다 (사람 판단을 대체하지 않고 보조한다)
  · 화면의 미리보기(BR-U-01)와 **같은 내용**이다. 다른 것을 보여주면
    "화면에서 본 것"과 "감사에 남은 것"이 갈린다

표준 출력에는 요약과 판정만 낸다. 실패하면 종료 코드가 1 이다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mesh.api_models import AskRequest, UploadRequest  # noqa: E402
from mesh.config import Config, setup_logging  # noqa: E402
from mesh.main import Services  # noqa: E402
from mesh.schemas import AuditRecord, Representation  # noqa: E402

DEFAULT_OUT = REPO / "aidlc-docs" / "construction" / "g4-payload-dump.md"

#: `--generate` 가 돌리는 질의. `data/questions.json` 의 대본과 같은 질문이다.
#: 여기서만 다른 질문을 쓰면 덤프가 시연과 다른 것을 검사하게 된다.
GENERATED_ASKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("고객사 요구사항과 우리 SDK 토큰 갱신 방식이 충돌하나요?", ("person:kim",)),
    ("라벨 불균형을 어떤 기법으로 처리했나요?", ("person:park",)),
    ("왜 세션 바인딩을 넣지 않았나요? 그 결정 배경을 알고 싶습니다", ("person:kim", "person:choi")),
    ("그때 p99 지연이 얼마였나요?", ("person:kim",)),
)

#: `--generate` 가 먼저 올리는 문서. **사용자가 자기 기밀 문서를 올리는 경로**를
#: 덤프에 반드시 포함시킨다 — Day 4 에 생긴 새 입구이고, 새 입구는 새 위험이다.
GENERATED_UPLOAD = UploadRequest(
    owner="person:kim",
    filename="g4-uploaded-secret.md",
    content=(
        "# title: G4 검사용 업로드 문서\n"
        "# as_of: 2026-08-19\n\n"
        "고객사 H 는 요구사항 REQ-4412 에서 30분 주기 재인증을 강제한다.\n"
        "재계약 총액은 12억 원 규모이고 납기는 2026-11-30 이다.\n"
        "위약 조항이 붙어 있어 일정 협상 여지가 없다.\n"
        "우리 SDK 는 무상태 토큰 갱신을 쓰므로 이 제약과 정면으로 충돌한다.\n"
    ),
    attach_to_session=True,
)

#: 눈으로 확인했고 **남기기로 결정한** 것. 판단 근거를 함께 적는다.
#:
#: 이 목록이 있는 이유: "남아 있지만 괜찮다"고 판단한 것을 적어두지 않으면,
#: 다음 사람이 같은 것을 보고 결함으로 다시 조사한다. 반대로 적어두면
#: 그 판단이 맞는지 남이 반박할 수 있다.
KNOWN_RESIDUALS: tuple[tuple[str, str], ...] = (
    (
        "`SDK v3.2` (사내 등급 발췌)",
        "제품 버전 표기다. `sdk-core` 같은 **패키지명은 치환된다** — 이건 산문 속 "
        "일반 명사구다. 치환하면 Agent 가 무엇에 대한 질문인지 알 수 없어 답이 "
        "무너진다 (BR-P-01 의 반대편 위험). 미리보기도 사내 등급에서는 "
        "'제품명·버전 제외'를 **약속하지 않는다** — 화면과 페이로드가 일치한다.",
    ),
    (
        "`configs/v3.yaml`, `runs/` (사내 등급 발췌)",
        "문서 본문이 스스로 언급하는 상대 경로다. 저장소 구조(`corpus/...`)가 "
        "아니고, 이 값으로 파일에 접근할 수 있는 경로가 없다. `PATH` 치환 대상인 "
        "`data/raw/session_logs` 등은 치환된다.",
    ),
    (
        "컬럼명 (`region_code`, `session_duration_sec` 등)",
        "사내 등급의 정의가 '식별자만 치환하고 기술 내용은 남긴다'다. 이 값들이 "
        "비밀이면 그 문서는 애초에 기밀 등급이어야 하고, 그러면 구조 추출 경로로 "
        "간다. 등급 판정이 틀린 것과 가명화가 틀린 것은 다른 문제다.",
    ),
)

#: 덤프를 읽는 사람이 눈으로 찾아야 하는 것. 자동 검사가 놓치는 범주다.
HUMAN_CHECKLIST = (
    "고객사·제품·인명이 **어떤 표기로도** 없다 (약어·이니셜·별칭 포함)",
    '원문 문장이 없다 — 의미를 옮긴 서술도 없다 (`"납기가 촉박함"` 같은 것)',
    "슬롯 **이름** 자체가 정보를 주지 않는다 (`penalty_clause_exists` 같은 것)",
    "값의 **조합**으로 대상이 특정되지 않는다 (업종 + 규모 + 일정)",
    "숫자가 식별에 쓰일 수 없다 (계약 금액·요구사항 번호·날짜)",
    "파일 경로·디렉터리 구조가 없다",
    "질문 문장 자체가 원문을 담고 있지 않다 (관문 ①)",
)


# ══════════════════════════════════════════════════════════════════════
# 생성 (선택)
# ══════════════════════════════════════════════════════════════════════


async def generate(svc: Services) -> tuple[int, list[str]]:
    """시연 대본을 실제로 돌려 감사 DB 를 채운다.

    ⚠️ `send` 까지 부른다. `prepare` 만으로는 감사 레코드가 남지 않고
       (BR-O-03), 레코드가 없으면 덤프할 것이 없다.

    반환: `(전송 건수, 사람이 읽어야 할 메모)`
    """
    notes: list[str] = []

    upload = await svc.documents.upload(GENERATED_UPLOAD)
    notes.append(
        f"업로드: `{upload.document.filename}` → "
        f"{upload.document.tier.label_ko} "
        f"(근거 {len(upload.document.tier_evidence)}건)"
    )

    sent = 0
    try:
        for question, targets in GENERATED_ASKS:
            request = AskRequest(question=question, asker="person:demo", targets=list(targets))
            prepared = await svc.orchestrator.prepare(request)
            ready = [c.envelope_id for c in prepared.calls if c.envelope_id]
            blocked = [c for c in prepared.calls if c.blocked_reason]
            for call in blocked:
                notes.append(
                    f"차단: `{question[:28]}…` → {call.target_entity_id} "
                    f"({call.blocked_reason}) — 감사 레코드 없음"
                )
            if not ready:
                continue
            await svc.orchestrator.send(prepared.request_id, ready, "person:demo")
            sent += len(ready)
    finally:
        # 올린 문서를 남기지 않는다. 남기면 다음 실행에서 `-1` 접미사가 붙고
        # 리포지토리에 검사용 파일이 쌓인다.
        svc.documents.delete(GENERATED_UPLOAD.owner, upload.document.document_id)

    return sent, notes


# ══════════════════════════════════════════════════════════════════════
# 덤프
# ══════════════════════════════════════════════════════════════════════


def leak_report(svc: Services):
    """전수 자동 검사. `demo.py` 의 `leak_sweep` 과 **같은 규칙**을 쓴다."""
    root = Path(svc.cfg.data_root)
    documents = [
        (p.relative_to(root).as_posix(), p.read_text(encoding="utf-8", errors="replace"))
        for p in (root / "corpus").rglob("*")
        if p.is_file()
    ]
    return documents, svc.audit.sweep_for_leaks(
        documents,
        identifiers=[lit for _, lit in svc.data.pseudonyms.all_literals()],
        banned_literals=svc.data.banned.literals,
        banned_patterns=svc.data.banned.patterns,
    )


def section(index: int, rec: AuditRecord, hits: list) -> list[str]:
    """페이로드 1건. **전문을 절단하지 않는다** (BR-U-01 과 같은 원칙)."""
    lines = [
        f"### {index}. `{rec.record_id}` — {rec.tier.label_ko} · {rec.representation.value}",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 시각 | `{rec.at.isoformat()}` |",
        f"| 종류 | `{rec.kind}` |",
        f"| 질문자 | `{rec.actor}` |",
        f"| 대상 | `{rec.target_entity_id}` |",
        f"| 모델 | `{rec.model_id}` |",
        f"| 전송 | `{rec.transport.value}` |",
        f"| 도착지 | `{rec.trusted_zone_llm_base_url}` |",
        f"| 승인 | `{rec.approved_by}` |",
        f"| 검증 | {rec.validation_summary} |",
        f"| 크기 | {rec.size_bytes} bytes |",
        f"| SHA-256 | `{rec.payload_sha256[:16]}…` |",
    ]
    if rec.citation_count is not None:
        lines.append(f"| 인용 | {rec.citation_count}건 |")
    if rec.confidence is not None:
        lines.append(f"| 신뢰도 | {rec.confidence} |")

    lines += [
        "",
        "**경계를 넘은 것 전부:**",
        "",
        "```json",
        json.dumps(rec.payload, ensure_ascii=False, indent=2),
        "```",
        "",
    ]

    if hits:
        lines += ["> 🔴 **자동 검사 히트**", ">"]
        for hit in hits:
            lines.append(f"> - `{hit.kind}` — `{hit.ngram[:80]}` ← `{hit.document_path}`")
        lines.append("")
    else:
        lines += ["> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건", ""]

    lines += [
        f"- [ ] **{index}번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다",
        "",
        "---",
        "",
    ]
    return lines


def render(svc: Services, report, documents: list[tuple[str, str]], notes: list[str]) -> str:
    records = sorted(svc.audit.recent(limit=10_000), key=lambda r: r.at)
    by_record: dict[str, list] = {}
    for hit in (*report.hits, *report.banned_hits):
        by_record.setdefault(hit.record_id, []).append(hit)

    verbatim = [r for r in records if r.representation is Representation.VERBATIM]

    out: list[str] = [
        "# 게이트 G4 — 페이로드 육안 전수 확인",
        "",
        "> 자동 생성: `make eval-dump-payloads`. 손으로 고치지 않는다 —",
        "> 체크박스만 표시한다.",
        "",
        "## 왜 이 문서가 있나",
        "",
        "자동 검사(`sweep_for_leaks`)는 **아는 것만** 잡는다. 목록에 없는 고객사명,",
        "문장을 옮기지 않고 의미만 옮긴 서술, 슬롯 이름 자체가 정보인 경우,",
        "값 조합으로 대상이 특정되는 경우는 사람이 읽어야 보인다.",
        "",
        "그래서 G4 의 기준은 '자동 검사 통과'가 아니라 **'사람이 전부 읽고 통과'** 다.",
        "",
        "## 환경",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| EXAONE 모드 | `{svc.cfg.exaone_mode}` |",
        f"| Agent 전송 | `{svc.cfg.agent_transport.value}` |",
        f"| 신뢰 구역 LLM | `{svc.cfg.trusted_zone_llm_base_url}` |",
        f"| 경계 시뮬레이션 | `{svc.cfg.trust_boundary_simulated}` |",
        f"| 기준 시각 | `{svc.cfg.now().isoformat()}` |",
        f"| 어휘 사전 | `{svc.data.vocab.version}` / `{svc.data.vocab_sha256[:16]}…` |",
        "",
        "## 자동 전수 검사",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 검사한 페이로드 | {report.payloads_scanned}건 |",
        f"| 검사한 문서 | {report.documents_scanned}건 |",
        f"| n-gram 크기 | {report.ngram_size} |",
        f"| 원문 조각 히트 | **{len(report.hits)}건** |",
        f"| 금칙어 히트 | **{len(report.banned_hits)}건** |",
        f"| 소요 | {report.elapsed_seconds}s |",
        "",
    ]

    if notes:
        out += ["## 이번 실행에서 일어난 일", ""]
        out += [f"- {n}" for n in notes]
        out.append("")

    out += [
        "## 사람이 눈으로 찾아야 하는 것",
        "",
        "각 페이로드마다 아래 7항목을 확인한다. 자동 검사가 잡지 못하는 범주다.",
        "",
    ]
    out += [f"{i}. {item}" for i, item in enumerate(HUMAN_CHECKLIST, start=1)]
    out += [
        "",
        "### 이미 확인했고 남기기로 결정한 것",
        "",
        "적어두지 않으면 다음 사람이 같은 것을 결함으로 다시 조사한다.",
        "반대로 적어두면 그 판단을 남이 반박할 수 있다.",
        "",
    ]
    for what, why in KNOWN_RESIDUALS:
        out += [f"- **{what}**", f"  {why}", ""]
    out += [
        "> ⚠️ `VERBATIM` (공개 등급) 페이로드는 원문 전송이 **등급의 정의**다.",
        "> 원문이 있는 것이 정상이며, 확인할 것은 '이 문서가 정말 공개 등급인가'다.",
        f"> 이번 덤프의 `VERBATIM` 건수: **{len(verbatim)}건**",
        "",
        f"## 페이로드 전문 ({len(records)}건)",
        "",
    ]

    if not records:
        out += [
            "감사 레코드가 없다. `--generate` 로 시나리오를 돌린 뒤 다시 덤프한다.",
            "",
        ]
    for i, rec in enumerate(records, start=1):
        out += section(i, rec, by_record.get(rec.record_id, []))

    out += [
        "## 판정",
        "",
        f"- 자동 검사: {'✅ 유출 0건' if report.clean else '🔴 히트 있음'}",
        f"- 육안 확인: 위 {len(records)}건의 체크박스가 모두 표시되면 G4 통과",
        "",
        "확인자: ____________  날짜: ____________",
        "",
        "## 대조 대상 문서",
        "",
        f"전수 검사가 대조한 문서 {len(documents)}건:",
        "",
    ]
    out += [f"- `{path}`" for path, _ in sorted(documents)]
    out.append("")
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════


def _write_dump(out: Path, text: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def reset_audit(cfg: Config) -> list[str]:
    """감사 DB 를 지운다. **명시적으로 요청할 때만** 부른다.

    왜 옵션인가: 기본값은 "DB 에 있는 것 전부"다. 리허설·실측을 반복하면
    레코드가 쌓이고, 그것을 전부 읽는 것이 진짜 전수 확인이다. 지우는 것을
    기본으로 하면 "방금 돌린 것만" 확인하게 되어 전수가 아니게 된다.

    그러나 제출용 덤프를 만들 때는 중복 없는 한 판이 필요하다. 그때만 쓴다.
    감사 DB 는 로컬 데모 데이터이고 `.gitignore` 에 있다.
    """
    removed: list[str] = []
    base = cfg.data_root / "mesh.db"
    for suffix in ("", "-wal", "-shm"):
        path = base.with_name(base.name + suffix)
        if path.exists():
            path.unlink()
            removed.append(path.name)
    return removed


async def main_async(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if args.fresh:
        if not args.generate:
            print("✗ --fresh 는 --generate 와 함께 써야 한다. 지우고 나면 덤프할 것이 없다.")
            return 2
        removed = reset_audit(cfg)
        print(f"감사 DB 초기화: {', '.join(removed) if removed else '(없음)'}")

    svc = Services(cfg)
    notes: list[str] = []
    try:
        if args.generate:
            print("시나리오 실행 중 (감사 DB 를 채운다)…")
            sent, notes = await generate(svc)
            print(f"  전송 {sent}건")

        documents, report = leak_report(svc)
        text = render(svc, report, documents, notes)
    finally:
        await svc.aclose()

    out = Path(args.out)
    # 블로킹 I/O 를 스레드로 넘긴다 (ASYNC240). 덤프는 수백 KB 가 될 수 있다.
    await asyncio.to_thread(_write_dump, out, text)

    print()
    print(f"페이로드      {report.payloads_scanned}건")
    print(f"대조 문서     {report.documents_scanned}건")
    print(f"원문 조각     {len(report.hits)}건")
    print(f"금칙어        {len(report.banned_hits)}건")
    # `--out` 이 저장소 밖일 수 있다 (임시 검사). 그때도 죽지 않는다.
    shown = out.relative_to(REPO) if out.is_relative_to(REPO) else out
    print(f"덤프          {shown}")
    print()

    if report.payloads_scanned == 0:
        print("✗ 덤프할 페이로드가 없다. `--generate` 를 붙여 시나리오를 먼저 돌린다.")
        return 1
    if not report.clean:
        print("🔴 자동 검사에서 히트가 나왔다. 덤프의 🔴 표시를 확인한다.")
        for hit in (*report.hits[:5], *report.banned_hits[:5]):
            print(f"   {hit.kind}: {hit.ngram[:60]}  ({hit.document_path})")
        return 1

    print("✅ 자동 검사 유출 0건.")
    print(f"   G4 는 여기서 끝나지 않는다 — {out.name} 를 열어 {report.payloads_scanned}건을")
    print("   전부 읽고 체크박스를 표시해야 통과다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="G4 페이로드 육안 전수 덤프")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="시연 시나리오(업로드 포함)를 돌려 감사 DB 를 채운 뒤 덤프",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="감사 DB 를 지우고 시작 (--generate 필수). 제출용 한 판을 만들 때",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="출력 경로")
    args = parser.parse_args()
    setup_logging(os.environ.get("MESH_LOG_LEVEL", "WARNING"))
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:  # pragma: no cover
        print("\n중단했습니다.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

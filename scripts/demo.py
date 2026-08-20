#!/usr/bin/env python3
"""3막 시연 스크립트 — 화면 없이 종단 흐름을 보여준다.

    make demo                     # 목업 모드 (네트워크 없이)
    EXAONE_MODE=live make demo     # 실제 EXAONE + Agent

U4 의 화면이 나오기 전에 **경로 전체가 동작함을 눈으로 확인**하는 도구다.
동시에 리허설 대본이기도 하다 — 각 막에서 무엇을 보여줄지가 여기 적혀 있다.

⚠️ 이 스크립트는 HTTP 를 거치지 않고 `Orchestrator` 를 직접 부른다.
   API 표면은 `tests/eval/test_scenarios.py` 가 검사한다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mesh.api_models import AskRequest  # noqa: E402
from mesh.config import Config, setup_logging  # noqa: E402
from mesh.main import Services  # noqa: E402

# ══════════════════════════════════════════════════════════════════════
# 출력
# ══════════════════════════════════════════════════════════════════════

W = 88


def head(text: str) -> None:
    print("\n" + "═" * W)
    print(f"  {text}")
    print("═" * W)


def sub(text: str) -> None:
    print(f"\n── {text} " + "─" * max(0, W - len(text) - 5))


def kv(key: str, value: object) -> None:
    print(f"  {key:22s} {value}")


def note(text: str) -> None:
    print(f"     {text}")


# ══════════════════════════════════════════════════════════════════════
# 시나리오 정의
# ══════════════════════════════════════════════════════════════════════

SCENARIOS: list[dict] = [
    {
        "act": "1막 · 기밀 문서인데 답이 나온다",
        "question": "고객사 요구사항과 우리 SDK 토큰 갱신 방식이 충돌하나요?",
        "targets": ["person:kim"],
        "watch": [
            "질문은 사내인데 근거에 기밀이 있어 호출 전체가 기밀로 상향된다",
            "미리보기에 나가는 것 전부가 보인다. 원문 문장 수는 측정값 0 이다",
            "김책임에게 알림이 가지 않는다",
        ],
    },
    {
        "act": "2막 · 사내 등급은 가명화로 나간다",
        "question": "라벨 불균형을 어떤 기법으로 처리했나요?",
        "targets": ["person:park"],
        "watch": [
            "프로젝트명·인명은 <PROJ_1>·<PERSON_1> 로 치환된다",
            "RandomOverSampler 같은 기술 용어는 남는다 — 치환하면 답이 무너진다",
            "세션이 '지금 학습 실행 중'을 알고 있다",
        ],
    },
    {
        "act": "3막 · 두 사람의 답을 모두 보여준다",
        "question": "왜 세션 바인딩을 넣지 않았나요? 그 결정 배경을 알고 싶습니다",
        "targets": ["person:kim", "person:choi"],
        "watch": [
            "답을 하나 고르지 않는다. 양쪽을 병기하고 판단은 사람에게 남긴다",
            "최민수는 2시간 전 세션이라 신뢰도가 x0.8 되어 미검증 배지가 붙는다",
            "상충 여부를 LLM 으로 판정하지 않는다 — divergent 는 관찰이다",
        ],
    },
    {
        "act": "3막 후속 · 차단되지만 답은 나온다",
        "question": "그때 p99 지연이 얼마였나요?",
        "targets": ["person:kim"],
        "watch": [
            "성능 수치 슬롯이 어휘 사전에 없어 구조 추출이 실패한다",
            "prepare 가 차단과 폴백 답변을 함께 준다 — send 를 부를 필요가 없다",
            "*** 감사 로그에 이 질의의 레코드가 없다 *** 이것이 증거다",
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════════════════


async def run_act(svc: Services, spec: dict, *, auto_approve: bool, show_payload: bool) -> None:
    head(spec["act"])
    kv("질문", spec["question"])
    kv("지목", ", ".join(spec["targets"]))
    print()
    for line in spec["watch"]:
        note(f"• {line}")

    request = AskRequest(question=spec["question"], asker="person:demo", targets=spec["targets"])
    audit_before = svc.audit.count()

    sub("prepare — 변환 · 검증 · 미리보기 (Agent 호출 없음)")
    prepared = await svc.orchestrator.prepare(request)
    kv("agents_notified", prepared.agents_notified)
    if prepared.upgraded_tier:
        kv("등급 상향", f"{prepared.upgraded_tier.label_ko} — {prepared.upgrade_reason}")

    for call in prepared.calls:
        print()
        kv("대상", f"{call.agent_label} [{call.tier.label_ko}]")
        kv("처분", call.disposition)
        if call.preview:
            kv("검증", call.preview.validation_summary)
            kv("표현", call.preview.representation.value)
            kv("크기", f"{call.preview.size_bytes} bytes")
            kv("원문 문장 수", f"{call.preview.verbatim_sentence_count}  (측정값)")
            kv("포함되지 않은 것", ", ".join(call.preview.excluded_categories))
            if show_payload:
                print("\n  ── 경계를 넘는 것 전부 " + "─" * 40)
                for line in call.preview.payload_pretty.splitlines():
                    print(f"  {line}")
        if call.fallback:
            kv("차단 이유", call.blocked_reason)
            print()
            for line in call.fallback.text.splitlines():
                note(line)

    ready = [c.envelope_id for c in prepared.calls if c.envelope_id]
    if not ready:
        sub("send — 호출하지 않는다 (차단 + 폴백이 한 왕복에 끝났다)")
        kv("감사 레코드 증가", svc.audit.count() - audit_before)
        kv("local_queries", svc.audit.local_count())
        return

    if not auto_approve:
        sub("사람 확인")
        # ASYNC250 을 억제한다: 이 스크립트는 시연용 단일 사용자 CLI 이고,
        # **사람이 미리보기를 읽는 시간이 곧 이 프로젝트의 방어 겹**이다 (FR-09).
        # 여기서 블로킹하는 것이 의도다. 서버 경로에는 이 코드가 없다.
        answer = input("  위 내용을 보내시겠습니까? [y/N] ").strip().lower()  # noqa: ASYNC250
        if answer != "y":
            for envelope_id in ready:
                svc.gatekeeper.cache.discard(envelope_id)
            note("취소했습니다. 감사 레코드는 남지 않습니다 (BR-U-03).")
            return

    sub("send — 승인 후 전송")
    result = await svc.orchestrator.send(prepared.request_id, ready, "person:demo")
    kv("처분", result.merged.disposition.value)
    kv("소요", f"{result.elapsed_seconds}s")
    if result.merged.divergent:
        kv("divergent", "True")
        note(result.merged.divergence_note)

    for answer in result.merged.answers:
        print()
        kv("답변", answer.agent_label)
        kv(
            "신뢰도",
            f"{answer.confidence:.2f}"
            + (f"  ({answer.freshness.value})" if answer.freshness else ""),
        )
        kv("외부 Agent", answer.used_external_agent)
        for line in answer.text.splitlines():
            note(line)
        for c in answer.citations:
            marks = [c.tier.label_ko]
            if c.as_of:
                marks.append(str(c.as_of))
            if c.formality == "informal":
                marks.append("비공식")
            note(f"  근거: {c.display_title}  [{' · '.join(marks)}]")
        if answer.unresolved_refs:
            note(f"  ⚠️ 치환되지 않은 기호: {', '.join(answer.unresolved_refs)}")

    if result.escalations:
        sub("에스컬레이션 — 담당자 인박스")
        for item_id in result.escalations:
            item = svc.inbox.get(item_id)
            if item is None:  # pragma: no cover
                continue
            kv("수신", item.owner_entity_id)
            kv("스레드", item.thread_id)
            note(f"요약: {item.draft.summary}")
            for line in item.draft.situation:
                note(f"  · {line}")
            note(f"초안: {item.draft.draft_answer}")
            for line in item.draft.already_answered:
                note(f"  이미 답변됨: {line}")

    sub("감사")
    kv("이 질의의 레코드", svc.audit.count() - audit_before)
    kv(
        "절약 추정",
        f"{result.interrupts_avoided}건 방해 회피 · 약 {result.minutes_saved_estimate}분",
    )


async def show_agents(svc: Services) -> None:
    head("에이전트 목록 — 인증 없이 보이는 화면")
    note("여기 표시되는 것은 세션 원문이 아니라 닫힌 어휘의 주제 라벨이다 (FR-31).")
    print()
    for card in await svc.orchestrator.agent_cards():
        bits = [card.display_name, f"[{card.expertise}]"]
        if card.activity_status:
            bits.append(card.activity_status)
        if card.away_minutes:
            bits.append(f"{card.away_minutes}분 전")
        if card.current_focus_summary:
            bits.append(f"— {card.current_focus_summary}")
        print("  " + " ".join(bits))


async def leak_sweep(svc: Services) -> None:
    head("전수 유출 검사 — 모든 페이로드 × 모든 문서")
    root = Path(svc.cfg.data_root)
    documents = [
        (p.relative_to(root).as_posix(), p.read_text(encoding="utf-8", errors="replace"))
        for p in (root / "corpus").rglob("*")
        if p.is_file()
    ]
    report = svc.audit.sweep_for_leaks(
        documents,
        # 가명화 등급은 식별자 포함 n-gram 만 검사한다 (BR-P-03)
        identifiers=[lit for _, lit in svc.data.pseudonyms.all_literals()],
        banned_literals=svc.data.banned.literals,
        banned_patterns=svc.data.banned.patterns,
    )
    kv("검사한 페이로드", report.payloads_scanned)
    kv("검사한 문서", report.documents_scanned)
    kv("n-gram 크기", report.ngram_size)
    kv("원문 조각 히트", len(report.hits))
    kv("금칙어 히트", len(report.banned_hits))
    kv("소요", f"{report.elapsed_seconds}s")
    print()
    if report.clean:
        note("✅ 유출 0건. 저장된 모든 페이로드에 원문 조각도 금칙어도 없다.")
    else:
        note("🔴 유출이 발견됐다:")
        for hit in (*report.hits[:5], *report.banned_hits[:5]):
            note(f"  {hit.kind}: {hit.ngram[:60]}  ({hit.document_path})")

    sub("원문 문구로 검색해 보기")
    for term in ("REQ-4412", "H社", "12억원", "session_binding"):
        rows = svc.audit.search(term)
        verdict = "0건 — 이 문구는 경계를 넘은 적이 없습니다" if not rows else f"{len(rows)}건"
        kv(term, verdict)


# ══════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════


async def main_async(args: argparse.Namespace) -> int:
    cfg = Config.load()
    head("환경")
    kv("EXAONE 모드", cfg.exaone_mode)
    kv("Agent 전송", cfg.agent_transport.value)
    kv("신뢰 구역 LLM", cfg.trusted_zone_llm_base_url)
    kv("경계 시뮬레이션", cfg.trust_boundary_simulated)
    kv("기준 시각", cfg.now().isoformat())
    if cfg.trust_boundary_simulated:
        print()
        note("⚠️ 신뢰 구역 LLM 이 공개 SaaS 를 가리킨다. 아키텍처가 보장하는 것은")
        note("   '원문이 이 엔드포인트 하나에만 전달된다'이며, 사내망 전환은 이 값만 바꾸면 된다.")

    svc = Services(cfg)
    try:
        await show_agents(svc)
        chosen = SCENARIOS if args.act == 0 else [SCENARIOS[args.act - 1]]
        for spec in chosen:
            await run_act(svc, spec, auto_approve=args.yes, show_payload=not args.no_payload)
        if args.act == 0:
            await leak_sweep(svc)
    finally:
        await svc.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="3막 시연")
    parser.add_argument("--act", type=int, default=0, choices=[0, 1, 2, 3, 4], help="0 = 전체")
    parser.add_argument("--yes", action="store_true", help="사람 확인을 자동 승인")
    parser.add_argument("--no-payload", action="store_true", help="페이로드 전문 생략")
    args = parser.parse_args()

    setup_logging(os.environ.get("MESH_LOG_LEVEL", "WARNING"))
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:  # pragma: no cover
        print("\n중단했습니다.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

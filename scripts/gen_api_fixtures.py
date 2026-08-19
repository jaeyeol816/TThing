#!/usr/bin/env python3
"""U4(C) 화면 선행 개발용 API 목업 픽스처 생성.

**실제 pydantic 모델로 생성한다.** 손으로 JSON 을 쓰면 형태가 실제 응답과
달라지고, 그러면 C 가 Day 4 에 UI 를 다시 만들어야 한다.

    uv run python scripts/gen_api_fixtures.py

생성 위치: data/fixtures/api/
브라우저에서 `?mock` 파라미터로 즉시 전환한다 (재시작 불필요).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mesh.api_models import (  # noqa: E402
    AgentCardView,
    AskResult,
    AuditRowView,
    AuditSearchResult,
    HealthStatus,
    InboxItem,
    MergedAnswer,
    PreparedCall,
    PrepareResult,
    SubQuestionView,
)
from mesh.schemas import (  # noqa: E402
    EXCLUDED_CATEGORIES_DEFAULT,
    CheckResult,
    Citation,
    Disposition,
    EscalationDraft,
    Freshness,
    PreviewCard,
    RehydratedAnswer,
    Representation,
    Tier,
    Transport,
)

OUT = REPO / "data" / "fixtures" / "api"
NOW = datetime.fromisoformat("2026-08-19T14:35:00+09:00")

# 시나리오 1 의 실제 페이로드 (실측 검증에 쓴 것과 동일)
PAYLOAD_S1 = {
    "task": "constraint_conflict_check",
    "domain": "authentication",
    "entities": [
        {
            "ref": "REQ_A",
            "role": "external_requirement",
            "facts": {
                "auth_mechanism_class": "challenge_response",
                "session_binding": "required",
                "credential_reuse_allowed": False,
                "max_session_hours": 8,
            },
        },
        {
            "ref": "COMP_B",
            "role": "our_component",
            "facts": {
                "credential_lifetime_hours": 24,
                "renewal_mode": "background_silent",
                "session_binding": "none",
            },
        },
    ],
    "question_template": "conflict_and_mitigation",
    "answer_format": {"conflict": "bool", "reason": "string", "mitigations": "string[]"},
}

ALL_PASS = tuple(
    CheckResult(stage=s, passed=True)
    for s in ("schema", "vocab", "range", "banned", "ngram", "size")
)


def _preview(payload: dict, tier: Tier, rep: Representation) -> PreviewCard:
    pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    return PreviewCard(
        envelope_id="env_" + "A" * 22,
        tier=tier,
        representation=rep,
        payload_pretty=pretty,
        size_bytes=len(pretty.encode()),
        validation_summary="6/6",
        checks=ALL_PASS,
        excluded_categories=EXCLUDED_CATEGORIES_DEFAULT,
        verbatim_sentence_count=0,
    )


def write(name: str, model) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    payload = model.model_dump(mode="json") if hasattr(model, "model_dump") else model
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  {path.relative_to(REPO)}")


# ══════════════════════════════════════════════════════════════════════
# GET /api/health
# ══════════════════════════════════════════════════════════════════════

health = HealthStatus(
    exaone_mode="mock",
    agent_transport=Transport.MOCK,
    trusted_zone_llm_base_url="https://api.friendli.ai/dedicated/v1",
    trust_boundary_simulated=True,  # UI 헤더가 "경계 시뮬레이션" 표시
    agent_model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    draft_model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    vocab_version="1.0.0",
    vocab_sha256="a3f1" + "0" * 60,
    demo_now_override=NOW,
    disposition_counts={"auto": 14, "unverified": 5, "escalate": 4, "blocked": 1},
)

# ══════════════════════════════════════════════════════════════════════
# GET /api/agents
# ══════════════════════════════════════════════════════════════════════

agents = [
    AgentCardView(
        entity_id="person:kim",
        display_name="김철수 책임",
        expertise="인증 · SSO · SDK 보안",
        activity_status="active",
        question_count_today=3,
        current_focus_summary="인증 관련 작업 중",  # 식별자 제거 요약
        session_as_of=datetime.fromisoformat("2026-08-19T14:31:20+09:00"),
        freshness=Freshness.LIVE,
    ),
    AgentCardView(
        entity_id="person:park",
        display_name="박선영 선임",
        expertise="데이터 파이프라인 · 모델 학습",
        activity_status="active",
        question_count_today=1,
        current_focus_summary="모델 학습 실행 중",
        session_as_of=datetime.fromisoformat("2026-08-19T14:33:05+09:00"),
        freshness=Freshness.LIVE,
    ),
    AgentCardView(
        entity_id="person:choi",
        display_name="최민수 선임",
        expertise="SDK 인증 모듈 · 배포 파이프라인",
        activity_status="away",
        away_minutes=125,
        question_count_today=0,
        current_focus_summary="배포 준비 작업 중",
        session_as_of=datetime.fromisoformat("2026-08-19T12:30:00+09:00"),
        freshness=Freshness.STALE,
    ),
]

# ══════════════════════════════════════════════════════════════════════
# POST /api/ask/prepare — 시나리오 1 (기밀, 검증 통과)
# ══════════════════════════════════════════════════════════════════════

prepare_ready = PrepareResult(
    request_id="req_01JS1",
    upgraded_tier=Tier.SECRET,
    upgrade_reason="질문은 사내이나 동원된 파일 중 기밀이 있어 호출 전체를 기밀로 상향",
    calls=(
        PreparedCall(
            envelope_id="env_" + "A" * 22,
            target_entity_id="person:kim",
            agent_label="김철수 책임의 Agent",
            tier=Tier.SECRET,
            disposition="ready",
            preview=_preview(PAYLOAD_S1, Tier.SECRET, Representation.STRUCTURED),
        ),
    ),
)

# ══════════════════════════════════════════════════════════════════════
# POST /api/ask/prepare — 시나리오 3 후속 (검증 실패 -> 폴백)
# ══════════════════════════════════════════════════════════════════════

prepare_blocked = PrepareResult(
    request_id="req_01JS3B",
    calls=(
        PreparedCall(
            target_entity_id="person:kim",
            agent_label="김철수 책임의 Agent",
            tier=Tier.SECRET,
            disposition="blocked",
            blocked_reason="필수 슬롯 미충족 — 성능 수치 필드가 어휘 사전에 없다",
            fallback=RehydratedAnswer(
                entity_id="person:kim",
                agent_label="김철수 책임의 Agent",
                text=(
                    "정확한 수치는 이 화면에서 제공할 수 없습니다.\n"
                    "확인된 것은 「임계를 넘었다」는 정성적 기록뿐입니다.\n"
                    "수치 열람은 고객 환경 벤치마크 권한이 필요하며, "
                    "김책임에게 확인 요청을 보냈습니다."
                ),
                confidence=0.55,
                citations=(
                    Citation(
                        ref="NOTE_A",
                        display_title="인증 설계 메모",
                        tier=Tier.INTERNAL,
                        as_of=date(2025, 11, 14),
                        formality="informal",
                    ),
                ),
                tier=Tier.SECRET,
                used_external_agent=False,  # -> [사내망 밖으로 나간 것 없음]
            ),
        ),
    ),
)

# ══════════════════════════════════════════════════════════════════════
# POST /api/ask/send — 시나리오 1 자동 응답
# ══════════════════════════════════════════════════════════════════════

send_auto = AskResult(
    request_id="req_01JS1",
    interrupts_avoided=1,
    minutes_saved_estimate=20,
    elapsed_seconds=11.4,
    merged=MergedAnswer(
        disposition=Disposition.AUTO,
        answers=(
            RehydratedAnswer(
                entity_id="person:kim",
                agent_label="김철수 책임의 Agent",
                text=(
                    "충돌합니다. 고객사 H REQ-4412는 인증을 세션에 바인딩하고 "
                    "자격증명 재사용을 금지하며 세션 상한이 8시간입니다. "
                    "SDK v3.2는 토큰 수명이 24시간이고 세션과 무관하게 무음 갱신하므로, "
                    "세션 종료 후에도 토큰이 최대 16시간 유효하게 남습니다. "
                    "이는 재사용 금지 조항에 위배됩니다.\n\n"
                    "완화안\n"
                    " 1. SDK 토큰 수명을 세션 상한(8시간) 이하로 낮춘다\n"
                    " 2. 세션 종료 이벤트에 토큰 무효화를 연동한다\n"
                    " 3. 무음 갱신 시 세션 유효성을 재확인한다"
                ),
                confidence=0.83,
                citations=(
                    Citation(
                        ref="REQ_A",
                        display_title="고객사 H 요구사항명세서",
                        section="§2.2",
                        tier=Tier.SECRET,
                        as_of=date(2026, 7, 15),
                    ),
                    Citation(
                        ref="COMP_B",
                        display_title="SDK 인증 설계 문서",
                        section="§2",
                        tier=Tier.INTERNAL,
                        as_of=date(2026, 8, 19),
                    ),
                ),
                tier=Tier.SECRET,
                used_external_agent=True,
                freshness=Freshness.LIVE,
                session_as_of=datetime.fromisoformat("2026-08-19T14:31:20+09:00"),
            ),
        ),
    ),
)

# ══════════════════════════════════════════════════════════════════════
# POST /api/ask/send — 시나리오 3 병기 (divergent)
# ══════════════════════════════════════════════════════════════════════

send_divergent = AskResult(
    request_id="req_01JS3A",
    elapsed_seconds=13.8,
    merged=MergedAnswer(
        disposition=Disposition.UNVERIFIED,
        divergent=True,
        divergence_note="둘 다 사실일 수 있습니다. 시점이 한 달 차이이고 문서 성격이 다릅니다.",
        answers=(
            RehydratedAnswer(
                entity_id="person:kim",
                agent_label="김철수 책임의 Agent",
                text="성능 문제입니다. 동시 인증 3천 TPS 구간에서 세션 조회 지연이 임계를 넘어 제외했습니다.",
                confidence=0.71,
                citations=(
                    Citation(
                        ref="NOTE_A",
                        display_title="인증 설계 메모",
                        tier=Tier.INTERNAL,
                        as_of=date(2025, 11, 14),
                        formality="informal",
                    ),
                ),
                tier=Tier.INTERNAL,
                used_external_agent=True,
                freshness=Freshness.LIVE,
            ),
            RehydratedAnswer(
                entity_id="person:choi",
                agent_label="최민수 선임의 Agent",
                text=(
                    "호환 문제입니다. 레거시 SSO 게이트웨이가 세션 식별자를 "
                    "downstream 으로 전파하지 않아 바인딩 자체가 불가능했습니다."
                ),
                confidence=0.624,  # 0.78 x 0.8 (STALE 보정)
                citations=(
                    Citation(
                        ref="REVIEW_B",
                        display_title="SDK v3.2 인증 설계 리뷰",
                        section="§2.1",
                        tier=Tier.INTERNAL,
                        as_of=date(2025, 12, 3),
                        formality="official",
                    ),
                ),
                tier=Tier.INTERNAL,
                used_external_agent=True,
                freshness=Freshness.STALE,
                session_as_of=datetime.fromisoformat("2026-08-19T12:30:00+09:00"),
            ),
        ),
    ),
)

# ══════════════════════════════════════════════════════════════════════
# POST /api/ask/send — 시나리오 2 q2 에스컬레이션
# ══════════════════════════════════════════════════════════════════════

send_escalate = AskResult(
    request_id="req_01JS2B",
    escalations=("itm_01JS2B1",),
    elapsed_seconds=10.2,
    merged=MergedAnswer(
        disposition=Disposition.ESCALATE,
        answers=(
            RehydratedAnswer(
                entity_id="person:park",
                agent_label="박선영 선임의 Agent",
                text=(
                    "현재 박선임이 동일 설정으로 학습을 실행 중입니다 "
                    "(14:02 시작, 17:10 완료 예상, cuda:0 점유). "
                    "지금 같은 스크립트를 실행하면 GPU가 충돌할 가능성이 높습니다. "
                    "다만 실행 허가 여부는 제가 판단할 수 없습니다."
                ),
                confidence=0.38,
                citations=(
                    Citation(
                        ref="SESSION_A",
                        display_title="세션 (실시간)",
                        tier=Tier.INTERNAL,
                        as_of=date(2026, 8, 19),
                    ),
                ),
                tier=Tier.INTERNAL,
                used_external_agent=True,
                freshness=Freshness.LIVE,
                session_as_of=datetime.fromisoformat("2026-08-19T14:33:05+09:00"),
            ),
        ),
    ),
)

# ══════════════════════════════════════════════════════════════════════
# POST /api/ask/prepare — 시나리오 2 질문 분해
# ══════════════════════════════════════════════════════════════════════

prepare_decomposed = PrepareResult(
    request_id="req_01JS2",
    decomposed=True,
    calls=(
        PreparedCall(
            envelope_id="env_" + "B" * 22,
            target_entity_id="person:park",
            agent_label="박선영 선임의 Agent",
            tier=Tier.INTERNAL,
            disposition="ready",
            sub_question=SubQuestionView(
                id="q1",
                kind="technique",
                text="전처리 v3의 라벨 불균형 처리 방식",
                tier=Tier.INTERNAL,
            ),
            preview=_preview(
                {
                    "task": "technique_lookup",
                    "domain": "data_pipeline",
                    "entities": [
                        {
                            "ref": "COMP_A",
                            "role": "our_component",
                            "facts": {"sampling_strategy_class": "hybrid"},
                        }
                    ],
                    "question_template": "technique_explanation",
                },
                Tier.INTERNAL,
                Representation.PSEUDONYMIZED,
            ),
        ),
        PreparedCall(
            envelope_id="env_" + "C" * 22,
            target_entity_id="person:park",
            agent_label="박선영 선임의 Agent",
            tier=Tier.INTERNAL,
            disposition="ready",
            sub_question=SubQuestionView(
                id="q2",
                kind="current_state_and_permission",
                text="지금 그 스크립트를 실행해도 되는가",
                tier=Tier.INTERNAL,
            ),
            preview=_preview(
                {
                    "task": "technique_lookup",
                    "domain": "data_pipeline",
                    "entities": [
                        {
                            "ref": "COMP_A",
                            "role": "our_component",
                            "facts": {"resource_contention": "gpu_busy"},
                        }
                    ],
                    "question_template": "technique_explanation",
                },
                Tier.INTERNAL,
                Representation.PSEUDONYMIZED,
            ),
        ),
    ),
)

# ══════════════════════════════════════════════════════════════════════
# GET /api/inbox
# ══════════════════════════════════════════════════════════════════════

inbox = [
    InboxItem(
        item_id="itm_01JS2B1",
        at=datetime.fromisoformat("2026-08-19T14:34:10+09:00"),
        owner_entity_id="person:park",
        asker="person:jung",
        thread_id="req_01JS2",
        question_summary="preprocess_v3 스크립트를 지금 실행해도 되는지",
        tier=Tier.INTERNAL,
        draft=EscalationDraft(
            summary="preprocess_v3 스크립트를 지금 실행해도 되는지",
            situation=(
                "현재 train.py 실행 중 (14:02~, 약 3h 남음, cuda:0)",
                "스크립트는 오늘 13:47에 수정됨",
                "기법 질문(라벨 불균형)은 Agent가 이미 답변함",
            ),
            draft_answer=(
                "지금은 GPU를 점유 중이라 17:10 이후에 실행해 주세요. "
                "급하면 configs/v3.yaml을 복사해서 다른 GPU로 돌리셔도 됩니다."
            ),
            already_answered=("q1 전처리 v3의 라벨 불균형 처리 방식",),
        ),
        citations=(
            Citation(
                ref="SESSION_A",
                display_title="세션 (실시간)",
                tier=Tier.INTERNAL,
                as_of=date(2026, 8, 19),
            ),
        ),
    )
]

# ══════════════════════════════════════════════════════════════════════
# GET /api/audit
# ══════════════════════════════════════════════════════════════════════

audit_row = AuditRowView(
    record_id="aud_01JS1A",
    at=datetime.fromisoformat("2026-08-19T14:33:41+09:00"),
    actor="person:choi",
    target_entity_id="person:kim",
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    transport=Transport.BROKER,
    trusted_zone_llm_base_url="https://api.friendli.ai/dedicated/v1",
    tier=Tier.SECRET,
    representation="structured",
    payload=PAYLOAD_S1,
    payload_sha256="9f2a8c" + "0" * 58,
    size_bytes=1124,
    validation_summary="6/6",
    approved_by="person:choi",
    envelope_id="env_" + "A" * 22,
)

audit_list = AuditSearchResult(rows=(audit_row,), total_records=1)

#: 1막 결정적 장면 ② — 원문 문구 검색 결과 0건
audit_zero = AuditSearchResult(query="REQ-4412", rows=(), total_records=1)


def main() -> None:
    print("API 목업 픽스처 생성 (실제 pydantic 모델 기반):")
    write("GET_api_health", health)
    write("GET_api_agents", {"agents": [a.model_dump(mode="json") for a in agents]})
    write("POST_api_ask_prepare_ready", prepare_ready)
    write("POST_api_ask_prepare_blocked", prepare_blocked)
    write("POST_api_ask_prepare_decomposed", prepare_decomposed)
    write("POST_api_ask_send_auto", send_auto)
    write("POST_api_ask_send_divergent", send_divergent)
    write("POST_api_ask_send_escalate", send_escalate)
    write("GET_api_inbox", {"items": [i.model_dump(mode="json") for i in inbox]})
    write("GET_api_audit", audit_list)
    write("GET_api_audit_zero", audit_zero)
    print(f"\n총 11개 -> {OUT.relative_to(REPO)}/")
    print("브라우저에서 ?mock 파라미터로 전환 (재시작 불필요)")


if __name__ == "__main__":
    main()

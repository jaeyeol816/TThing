"""테스트 대역 — LLM 응답만 흉내 내고 나머지는 실제 코드를 쓴다.

⚠️ **대역이 검증을 우회하지 않는다.** 목업 모드의 원칙과 같다 (FR-48):

    ExaoneClient / BrokerClient   대역
    조립 · 검증 · 감사 · 재수화     **실제 코드**

이 원칙을 깨면 테스트가 통과하는데 실물이 새는 상태가 된다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mesh.schemas import AgentResponse

# ══════════════════════════════════════════════════════════════════════
# EXAONE
# ══════════════════════════════════════════════════════════════════════

#: 실측된 응답 형태. 문서 1건당 한 번씩 온다.
SLOT_REPLY_REQUIREMENT = {
    "auth_mechanism_class": "challenge_response",
    "session_binding": "required",
    "credential_reuse_allowed": "false",
    "max_session_hours": "8 hours",
}
SLOT_REPLY_OUR_COMPONENT = {
    "auth_mechanism_class": "token_bearer",
    "session_binding": "none",
    "credential_lifetime_hours": 24,
    "renewal_mode": "background_silent",
}
SLOT_REPLY_TECHNIQUE = {"sampling_strategy_class": "hybrid"}
SLOT_REPLY_RATIONALE = {"session_binding": "none", "renewal_mode": "background_silent"}


class FakeExaone:
    """`name` 별로 응답을 정한다.

    `name` 으로 분기하는 이유: 한 질의가 `classify` -> `select_paths` ->
    `extract` -> `focus_topic` 순으로 여러 번 부르는데, 큐 방식으로 두면
    호출 순서가 바뀔 때마다 테스트가 깨진다.
    """

    def __init__(
        self,
        *,
        classify: str = "internal",
        selected: Sequence[int] | None = None,
        slots: Sequence[dict] | None = None,
        topic: str = "인증 관련 작업",
        text: str = "신뢰 구역 안에서 만든 답변입니다.",
        fail: dict[str, Exception] | None = None,
    ) -> None:
        self.classify = classify
        self.selected = list(selected) if selected is not None else None
        self.slots = list(slots or [SLOT_REPLY_REQUIREMENT, SLOT_REPLY_OUR_COMPONENT])
        self.topic = topic
        self.text = text
        self.fail = fail or {}
        self.calls: list[str] = []
        self._slot_index = 0

    async def complete_json(self, system, user, *, name="generic", max_tokens=800):
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]
        match name:
            case "classify":
                return {"tier": self.classify, "reason_code": "internal_technical_content"}
            case "select_paths":
                return {"selected": self.selected} if self.selected is not None else {}
            case "focus_topic":
                return {"topic": self.topic}
            case "extract":
                reply = self.slots[min(self._slot_index, len(self.slots) - 1)]
                self._slot_index += 1
                return dict(reply)
        return {}

    async def complete_text(self, system, user, *, name="answer", max_tokens=1200):
        self.calls.append(f"text:{name}")
        if name in self.fail:
            raise self.fail[name]
        return self.text

    async def aclose(self) -> None:
        return None

    def count(self, name: str) -> int:
        return sum(1 for c in self.calls if c == name)


# ══════════════════════════════════════════════════════════════════════
# Broker (경계 밖)
# ══════════════════════════════════════════════════════════════════════


def agent_reply(
    *,
    confidence: float = 0.83,
    citations: Sequence[str] = ("REQ_A", "COMP_A"),
    reason: str = "REQ_A 는 세션 바인딩을 요구하지만 COMP_A 는 적용하지 않는다",
    conflict: bool = True,
) -> AgentResponse:
    return AgentResponse(
        answer={
            "conflict": conflict,
            "reason": reason,
            "mitigations": ["COMP_A 에 세션 바인딩 추가", "토큰 수명 단축"],
        },
        confidence=confidence,
        citations=tuple(citations),
        revalidated=True,
    )


DRAFT_REPLY = AgentResponse(
    answer={
        "summary": "세션 바인딩 요구와 현재 갱신 방식의 정합성 확인이 필요합니다.",
        "situation": ["REQ_A 는 바인딩을 요구합니다"],
        "draft_answer": "바인딩을 추가하고 토큰 수명을 단축하는 방향으로 검토했습니다.",
        "already_answered": [],
    },
    confidence=0.5,
    citations=("REQ_A",),
    revalidated=True,
)


class FakeBroker:
    """모델 ID 로 분기한다 — 본 호출과 초안 호출을 구분해야 한다."""

    def __init__(
        self,
        response: AgentResponse | Exception | Callable[[str], AgentResponse] = None,  # type: ignore[assignment]
        *,
        draft: AgentResponse | Exception | None = None,
        draft_model_id: str = "",
    ) -> None:
        self.response = response if response is not None else agent_reply()
        self.draft = draft if draft is not None else DRAFT_REPLY
        self.draft_model_id = draft_model_id
        self.calls: list[tuple[str, str]] = []

    async def invoke(self, env, system_prompt, model_id):
        self.calls.append((model_id, system_prompt))
        target = (
            self.draft if self.draft_model_id and model_id == self.draft_model_id else self.response
        )
        if isinstance(target, Exception):
            raise target
        if callable(target):
            return target(env.envelope_id)
        return target

    async def aclose(self) -> None:
        return None

    @property
    def main_calls(self) -> int:
        return sum(1 for m, _ in self.calls if not self.draft_model_id or m != self.draft_model_id)

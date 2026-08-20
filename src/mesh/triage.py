"""브로드캐스트 선별 — "이 질문에 내가 답할 수 있는가" 를 각자 판정한다.

──────────────────────────────────────────────────────────────────────
무엇이 바뀌었나
──────────────────────────────────────────────────────────────────────

이전 흐름은 **사람이 먼저 지목**했다. 질문자가 조직도에서 담당자를 고르고
그 사람에게만 질문이 갔다. 그러려면 질문자가 "누가 이걸 아는가" 를 이미
알고 있어야 한다 — 그것을 모르는 것이 이 프로젝트가 풀려는 문제였는데도.

바뀐 흐름은 **질문이 먼저**다.

    질문 → 전원의 Agent 에게 방송 → 각자 "답할 수 있는가" 판정
         → 답할 수 있는 사람만 화면에 남는다 → 그 사람을 눌러 대화

지목은 사라지지 않는다. **지목의 시점이 뒤로 갔다** — 후보가 좁혀진 뒤에
사람이 고른다. 판정은 사람을 대신하지 않고 목록을 줄인다 (FR-29 유지).

──────────────────────────────────────────────────────────────────────
이 모듈이 **보지 않는** 것 — 이 파일의 핵심 규칙
──────────────────────────────────────────────────────────────────────

브로드캐스트는 **문서를 한 글자도 읽지 않는다.**

판정에 쓰는 재료는 이것뿐이다.

    agents.yaml 의 `expertise` · `topics`      본인이 공개하기로 적은 문장
    org.yaml 의 단위 이름                       인증 없이 보이는 조직도
    `AgentCard.current_focus_summary`          이미 게이트키퍼를 지난 라벨

전부 **이미 인증 없이 보이는 화면에 떠 있는 값**이다. 그래서 열 명에게
질문을 뿌려도 새로 노출되는 것이 없다. 만약 판정이 문서 본문을 읽었다면,
"박선영이 이 질문에 답할 수 있다" 는 결과 자체가 박선영의 파일에 무엇이
있는지를 알려주는 채널이 된다. 그것은 게이트키퍼를 우회한 유출이다.

⚠️ **이 모듈은 경계를 넘지 않는다.** EXAONE(신뢰 구역)만 쓰고 감사 레코드도
   만들지 않는다 — 경계를 넘은 것이 없으므로 기록할 것이 없다. 실제 질의는
   사용자가 사람을 고른 뒤 `orchestrator.prepare()` 부터 시작한다.

──────────────────────────────────────────────────────────────────────
판정은 두 겹이다 (등급 판정과 같은 구조)
──────────────────────────────────────────────────────────────────────

    ① 규칙   질문 ↔ topics/expertise/단위 이름의 겹침. 순수 함수. 항상 돈다
    ② 모델   EXAONE 이 **번호와 사유 코드만** 고른다. 실패하면 ①만 쓴다

`classifier.py` 와 방향이 반대인 것에 주의한다. 등급 판정은 둘 중 **높은**
쪽을 택한다 (fail closed — 애매하면 기밀로). 선별은 둘 중 **넓은** 쪽을
택한다 (fail open — 애매하면 후보로 남긴다).

방향이 반대인 이유: 등급을 낮게 잡은 실수는 유출이고, 후보를 좁게 잡은
실수는 **답할 수 있는 사람이 화면에서 사라지는 것**이다. 전자는 되돌릴 수
없고 후자는 사용자가 "전체 보기" 를 누르면 끝난다. 위험이 비대칭이므로
기본값도 비대칭이어야 한다.

⚠️ 모델에게 **자유 문장을 만들게 하지 않는다.** 돌려받는 것은 후보 번호와
   닫힌 집합의 사유 코드뿐이고, 화면에 뜨는 문장은 `REASON_TEMPLATES` 를
   써서 **코드가 조립한다** (extractor 의 슬롯 채우기와 같은 원칙).
   자유 문장을 허용하면 판정 결과에 원문이 섞여 나올 채널이 하나 생긴다.

⚠️ 이 모듈은 L3(변환)이다. 파일을 읽지 않고, `Chunk` 를 만지지 않으며,
   경계 밖 클라이언트를 import 하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from mesh.config import get_logger, log_extra
from mesh.exceptions import ExaoneUnavailable
from mesh.schemas import BannedTerms

if TYPE_CHECKING:  # pragma: no cover
    from mesh.llm.exaone import ExaoneClient

log = get_logger("triage")


# ══════════════════════════════════════════════════════════════════════
# 사유 코드 — 닫힌 집합
# ══════════════════════════════════════════════════════════════════════

ReasonCode = Literal[
    "topic_match",     # 본인이 적어 둔 topics 와 겹친다
    "expertise_match",  # 담당 영역 문장과 겹친다
    "focus_match",     # 지금 하고 있는 일과 겹친다
    "team_context",    # 팀 문맥 — 팀 대표가 답할 수 있는 종류의 질문
    "adjacent",        # 인접 영역. 직접 담당은 아니지만 아는 것이 있다
    "no_match",        # 겹치는 것이 없다
]

#: 화면에 뜰 문장의 **틀**. 모델이 만드는 것은 코드뿐이고 문장은 여기서 나온다.
#: 문구를 고치려면 이 표만 고치면 된다 — 판정 로직을 건드리지 않는다.
REASON_TEMPLATES: dict[str, str] = {
    "topic_match": "{matched} — 담당하는 주제입니다",
    "expertise_match": "담당 영역이 겹칩니다 — {expertise}",
    "focus_match": "지금 관련된 작업을 하고 있습니다",
    "team_context": "{unit} 팀 문맥으로 답할 수 있습니다",
    "adjacent": "직접 담당은 아니지만 인접 영역입니다",
    "no_match": "이 질문과 겹치는 담당 영역이 없습니다",
}

VALID_REASON_CODES = frozenset(REASON_TEMPLATES)


# ══════════════════════════════════════════════════════════════════════
# 점수 가중치 — 한 곳에 모은다
# ══════════════════════════════════════════════════════════════════════

#: 신호별 가중치. 합이 임계값을 넘으면 후보로 남는다.
#:
#: 값의 근거는 "얼마나 본인이 직접 선언한 것인가" 다. `topics` 는 본인이
#: 이 질문을 받겠다고 적어 둔 것이므로 가장 세고, 단위 이름은 같은 팀이면
#: 누구나 걸리므로 가장 약하다.
WEIGHT_TOPIC = 1.0
WEIGHT_EXPERTISE = 0.6
WEIGHT_FOCUS = 0.5
WEIGHT_UNIT = 0.25
WEIGHT_TITLE = 0.3

#: 모델이 고른 후보에 얹는 값. 규칙이 0 이어도 이 값만으로 임계를 넘는다 —
#: 모델은 규칙이 모르는 표현(동의어·풀어 쓴 말)을 잡으라고 있는 것이다.
WEIGHT_MODEL_PICK = 0.8

#: 기본 임계값. `BROADCAST_THRESHOLD` 로 덮을 수 있다.
DEFAULT_THRESHOLD = 0.5

#: 후보로 남길 최대 인원. 넘으면 점수 순으로 자른다.
DEFAULT_MAX_RELEVANT = 6

#: 토큰 최소 길이. 1글자는 아무 문장에나 걸린다.
MIN_TOKEN_LEN = 2

#: 모델에 넘기는 질문 길이 상한. 판정에는 앞부분으로 충분하다.
MAX_QUESTION_CHARS = 600


# ══════════════════════════════════════════════════════════════════════
# 입출력 타입
# ══════════════════════════════════════════════════════════════════════


class Candidate(BaseModel):
    """판정 입력 한 명. **공개 필드만 들어온다.**

    ⚠️ 이 모델에 `knowledge_scope`·`open_paths`·문서 제목을 넣지 않는다.
       넣는 순간 "누가 답할 수 있는가" 라는 결과가 남의 파일 목록을 알려주는
       채널이 된다. 필드를 안 쓰는 것이 아니라 **받지 않는다.**
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str
    display_name: str
    expertise: str = ""
    topics: tuple[str, ...] = ()
    unit_path: tuple[str, ...] = ()
    unit_id: str | None = None
    rank_label: str = ""
    org_title: str = ""
    #: 이미 게이트키퍼를 지난 주제 라벨 (`AgentCard.current_focus_summary`).
    #: 원문 focus 가 아니다.
    focus_label: str = ""
    #: 팀 대표인가 (`org.yaml` 의 `ranks[].leads`). 팀 문맥 질문의 후보가 된다.
    leads: bool = False
    #: 일일 한도를 다 썼거나 세션이 없어 지금은 못 받는 상태.
    available: bool = True


class Verdict(BaseModel):
    """판정 결과 한 명."""

    model_config = ConfigDict(frozen=True)

    entity_id: str
    relevant: bool
    score: float = 0.0
    reason_code: ReasonCode = "no_match"
    #: 화면에 뜨는 문장. **코드가 조립한 것**이며 모델 출력이 아니다.
    reason: str = ""
    #: 무엇이 겹쳤는가. 전부 공개 필드에서 온 말이다.
    matched: tuple[str, ...] = ()
    #: 규칙만인가, 모델이 얹었는가. 화면이 판정 근거를 밝힐 수 있어야 한다.
    decided_by: Literal["rule", "model", "rule+model"] = "rule"


class TriageOutcome(BaseModel):
    """`broadcast` 한 번의 결과 전체."""

    model_config = ConfigDict(frozen=True)

    verdicts: tuple[Verdict, ...] = ()
    threshold: float = DEFAULT_THRESHOLD
    #: 모델 판정이 실제로 돌았는가. 실패하면 화면이 그 사실을 표시한다 —
    #: "규칙만으로 좁혔다" 와 "둘 다 돌았다" 는 신뢰도가 다르다.
    model_used: bool = False
    model_error: str = ""

    @property
    def relevant(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.relevant)


# ══════════════════════════════════════════════════════════════════════
# 토큰화 — 순수 함수
# ══════════════════════════════════════════════════════════════════════

#: 한글·영숫자만 남기고 나머지는 경계로 본다. 형태소 분석기를 쓰지 않는 것이
#: 의도적이다 — 사전 하나가 더 늘면 그것도 관리 대상이 되고, 여기서 필요한
#: 정밀도는 "겹치는가" 수준이다.
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")

#: 조사·접미가 붙은 한국어 토큰을 맞추기 위한 꼬리. 긴 것부터 벗긴다.
_KO_SUFFIXES: tuple[str, ...] = (
    "에서는", "으로는", "에게는", "이라는", "라는", "에서", "으로", "에게",
    "께서", "이나", "거나", "인가", "은가", "는가", "을까", "ㄹ까",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만",
)


def normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def strip_suffix(token: str) -> str:
    """한국어 조사 제거. `라벨을` → `라벨`.

    형태소 분석이 아니라 **꼬리 자르기**다. 과하게 자르면 다른 단어가 되므로
    자른 뒤 길이가 `MIN_TOKEN_LEN` 미만이면 원형을 돌려준다.
    """
    for suffix in _KO_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= MIN_TOKEN_LEN:
            return token[: -len(suffix)]
    return token


def tokens(text: str) -> frozenset[str]:
    out: set[str] = set()
    for raw in _WORD_RE.findall(text.lower()):
        if len(raw) < MIN_TOKEN_LEN:
            continue
        out.add(raw)
        out.add(strip_suffix(raw))
    return frozenset(t for t in out if len(t) >= MIN_TOKEN_LEN)


def compact(text: str) -> str:
    """공백 제거 소문자. 구(phrase) 포함 검사를 위해 쓴다.

    `라벨 불균형` 이라는 topic 이 `라벨불균형을` 이라는 표기와도 맞아야 한다.
    """
    return "".join(_WORD_RE.findall(text.lower()))


def phrase_hits(question: str, phrases: Sequence[str]) -> tuple[str, ...]:
    """질문에 등장하는 구를 돌려준다. 토큰 겹침 + 구 포함을 둘 다 본다."""
    q_tokens = tokens(question)
    q_compact = compact(question)
    hits: list[str] = []
    for phrase in phrases:
        if not phrase.strip():
            continue
        p_compact = compact(phrase)
        if p_compact and p_compact in q_compact:
            hits.append(phrase)
            continue
        p_tokens = tokens(phrase)
        # 구를 이루는 토큰이 **전부** 질문에 있으면 그 구가 나온 것으로 본다.
        if p_tokens and p_tokens <= q_tokens:
            hits.append(phrase)
    return tuple(dict.fromkeys(hits))


def token_overlap(question: str, text: str) -> tuple[str, ...]:
    """질문과 텍스트가 공유하는 토큰."""
    if not text.strip():
        return ()
    return tuple(sorted(tokens(question) & tokens(text)))


# ══════════════════════════════════════════════════════════════════════
# ① 규칙 판정 — 순수 함수
# ══════════════════════════════════════════════════════════════════════


def score_candidate(question: str, cand: Candidate) -> tuple[float, ReasonCode, tuple[str, ...]]:
    """`(점수, 사유 코드, 겹친 말)`. **I/O 없음, 모델 없음.**

    사유 코드는 **가장 강한 신호**를 고른다. 여러 개가 걸려도 화면에는 하나만
    보여준다 — 근거를 나열하면 읽지 않는다.
    """
    score = 0.0
    matched: list[str] = []
    code: ReasonCode = "no_match"

    topic_hits = phrase_hits(question, cand.topics)
    if topic_hits:
        score += WEIGHT_TOPIC * min(len(topic_hits), 3)
        matched += list(topic_hits)
        code = "topic_match"

    expertise_hits = token_overlap(question, cand.expertise)
    if expertise_hits:
        score += WEIGHT_EXPERTISE * min(len(expertise_hits), 3)
        matched += list(expertise_hits)
        if code == "no_match":
            code = "expertise_match"

    if cand.org_title and phrase_hits(question, (cand.org_title,)):
        score += WEIGHT_TITLE
        matched.append(cand.org_title)
        if code == "no_match":
            code = "expertise_match"

    focus_hits = token_overlap(question, cand.focus_label)
    if focus_hits:
        score += WEIGHT_FOCUS
        matched += list(focus_hits)
        if code == "no_match":
            code = "focus_match"

    unit_hits = phrase_hits(question, cand.unit_path)
    if unit_hits:
        score += WEIGHT_UNIT * len(unit_hits)
        matched += list(unit_hits)
        if code == "no_match":
            # 단위 이름만 걸렸다 — 팀 대표면 팀 문맥, 아니면 인접이다.
            code = "team_context" if cand.leads else "adjacent"

    return round(score, 3), code, tuple(dict.fromkeys(matched))


def render_reason(code: ReasonCode, cand: Candidate, matched: Sequence[str]) -> str:
    """화면 문장을 **코드가 조립한다** (모델 출력을 그대로 쓰지 않는다)."""
    template = REASON_TEMPLATES.get(code, REASON_TEMPLATES["no_match"])
    return template.format(
        matched=", ".join(matched[:3]) or cand.expertise or "담당 영역",
        expertise=cand.expertise or "담당 영역",
        unit=cand.unit_path[-1] if cand.unit_path else "소속 팀",
        name=cand.display_name,
    )


def rule_pass(
    question: str,
    candidates: Sequence[Candidate],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Verdict]:
    """규칙만으로 한 번 훑는다. 모델이 없어도 브로드캐스트는 동작해야 한다."""
    out: dict[str, Verdict] = {}
    for cand in candidates:
        score, code, matched = score_candidate(question, cand)
        out[cand.entity_id] = Verdict(
            entity_id=cand.entity_id,
            relevant=cand.available and score >= threshold,
            score=score,
            reason_code=code,
            reason=render_reason(code, cand, matched),
            matched=matched,
            decided_by="rule",
        )
    return out


# ══════════════════════════════════════════════════════════════════════
# ② 모델 판정 — 번호와 사유 코드만 돌려받는다
# ══════════════════════════════════════════════════════════════════════

TRIAGE_SYSTEM = """You route an internal question to the colleagues who can answer it.

You are given a QUESTION and a numbered list of PEOPLE. Each person is described
only by public information: their stated expertise, the topics they declared, their
team path, and an optional topic label of what they are working on right now.

Pick every person who could plausibly answer. Be generous: it is much worse to
drop someone who knows the answer than to include someone who does not. When in
doubt, include.

For each pick you must choose exactly one reason code from this closed set:
  topic_match      the question matches a topic they declared
  expertise_match  the question falls in their stated expertise
  focus_match      the question relates to what they are working on now
  team_context     they lead the team the question belongs to
  adjacent         not their area, but adjacent enough to be useful

Output JSON only, in exactly this shape:
  {"picks": [{"i": <index>, "code": "<reason code>"}, ...]}

Do not invent indices. Do not write prose. Do not add other keys."""


def build_triage_prompt(question: str, candidates: Sequence[Candidate]) -> str:
    """모델에 넘길 사용자 메시지. **공개 필드만 들어간다.**"""
    lines: list[str] = [f"QUESTION:\n{question[:MAX_QUESTION_CHARS]}", "", "PEOPLE:"]
    for i, cand in enumerate(candidates):
        parts = [f"  {i}. expertise: {cand.expertise or '-'}"]
        if cand.topics:
            parts.append(f"topics: {', '.join(cand.topics)}")
        if cand.unit_path:
            parts.append(f"team: {' / '.join(cand.unit_path)}")
        if cand.org_title:
            parts.append(f"role: {cand.org_title}")
        if cand.leads:
            parts.append("leads the team")
        if cand.focus_label:
            parts.append(f"now working on: {cand.focus_label}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def parse_picks(
    raw: object, candidate_count: int
) -> tuple[tuple[int, ReasonCode], ...]:
    """모델 출력 → `(번호, 사유 코드)`. **범위 밖·미등록 코드는 버린다.**

    번호만 받는 이유는 `store.select_paths()` 와 같다 — 문자열을 만들게 하면
    존재하지 않는 것을 만들어낸다. 여기서는 존재하지 않는 사람을 지목한다.
    """
    if not isinstance(raw, dict):
        return ()
    picks = raw.get("picks")
    if not isinstance(picks, list):
        return ()

    out: list[tuple[int, ReasonCode]] = []
    seen: set[int] = set()
    for item in picks:
        if not isinstance(item, dict):
            continue
        index = item.get("i")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not (0 <= index < candidate_count) or index in seen:
            continue
        code = item.get("code")
        if not isinstance(code, str) or code not in VALID_REASON_CODES:
            code = "adjacent"  # 코드를 못 알아들었다고 후보에서 빼지는 않는다
        seen.add(index)
        out.append((index, code))  # type: ignore[arg-type]
    return tuple(out)


async def model_pass(
    question: str,
    candidates: Sequence[Candidate],
    exaone: ExaoneClient,
) -> tuple[tuple[int, ReasonCode], ...]:
    """EXAONE 에게 번호를 고르게 한다. 실패는 호출자가 규칙 결과로 흡수한다."""
    raw = await exaone.complete_json(
        TRIAGE_SYSTEM,
        build_triage_prompt(question, candidates),
        name="triage",
        max_tokens=256,
    )
    return parse_picks(raw, len(candidates))


# ══════════════════════════════════════════════════════════════════════
# 합치기
# ══════════════════════════════════════════════════════════════════════


async def triage(
    question: str,
    candidates: Sequence[Candidate],
    *,
    exaone: ExaoneClient | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_relevant: int = DEFAULT_MAX_RELEVANT,
) -> TriageOutcome:
    """브로드캐스트 판정 전체.

    실패 시 동작: 모델이 죽으면 **규칙 결과를 그대로 쓴다.** 예외를 올리지
    않는다 — 선별은 편의 기능이고, 여기서 막히면 질문 자체를 못 하게 된다.
    등급 판정(`classifier`)과 달라도 되는 이유는 이 판정이 **경계를 넘는
    결정을 하지 않기 때문**이다. 무엇이 나갈지는 게이트키퍼가 다시 정한다.
    """
    verdicts = rule_pass(question, candidates, threshold=threshold)
    by_id = {c.entity_id: c for c in candidates}

    model_used = False
    model_error = ""
    if exaone is not None and candidates:
        try:
            picks = await model_pass(question, candidates, exaone)
            model_used = True
            for index, code in picks:
                cand = candidates[index]
                prev = verdicts[cand.entity_id]
                score = round(prev.score + WEIGHT_MODEL_PICK, 3)
                # 규칙이 이미 사유를 찾았으면 그것을 유지한다 — 규칙 사유는
                # "무엇이 겹쳤는지" 를 가리키고 모델 사유보다 확인 가능하다.
                final_code = prev.reason_code if prev.reason_code != "no_match" else code
                verdicts[cand.entity_id] = prev.model_copy(
                    update={
                        "relevant": cand.available,
                        "score": score,
                        "reason_code": final_code,
                        "reason": render_reason(final_code, cand, prev.matched),
                        "decided_by": "rule+model" if prev.score > 0 else "model",
                    }
                )
        except ExaoneUnavailable as e:
            model_error = str(e)[:120]
            log.warning(
                "브로드캐스트 선별 모델 실패 — 규칙 결과만 쓴다",
                extra=log_extra(reason=model_error, candidates=len(candidates)),
            )
        except Exception as e:  # noqa: BLE001 — 선별 실패가 질문을 막으면 안 된다
            model_error = f"{type(e).__name__}: {e}"[:120]
            log.warning(
                "브로드캐스트 선별 중 예외 — 규칙 결과만 쓴다",
                extra=log_extra(reason=type(e).__name__),
            )

    ordered = sorted(
        verdicts.values(),
        key=lambda v: (-v.score, by_id[v.entity_id].display_name),
    )
    # 상한을 넘으면 점수 낮은 쪽부터 후보에서 뺀다. **목록에서 지우지 않는다** —
    # 화면은 전원을 그리고 관련 없는 사람만 흐리게 만든다.
    kept = 0
    final: list[Verdict] = []
    for verdict in ordered:
        if verdict.relevant:
            if kept >= max_relevant:
                verdict = verdict.model_copy(update={"relevant": False})
            else:
                kept += 1
        final.append(verdict)

    return TriageOutcome(
        verdicts=tuple(final),
        threshold=threshold,
        model_used=model_used,
        model_error=model_error,
    )


# ══════════════════════════════════════════════════════════════════════
# 설정 검증
# ══════════════════════════════════════════════════════════════════════


def validate_topics(
    topics_by_entity: dict[str, Sequence[str]], banned: BannedTerms
) -> tuple[str, ...]:
    """`topics` 에 금칙어가 있으면 그 목록을 돌려준다.

    `topics` 는 브로드캐스트 결과 문장에 그대로 실린다 (`REASON_TEMPLATES`
    의 `{matched}`). 그 화면은 인증 없이 보이므로, 여기에 고객사명을 적으면
    게이트키퍼를 우회한 유출이 된다 — `org.yaml` 을 검사하는 것과 같은 이유다.
    """
    problems: list[str] = []
    for entity_id, topics in topics_by_entity.items():
        for topic in topics:
            hits = banned.hits(topic)
            if hits:
                problems.append(f"{entity_id}: {topic!r} → {sorted(set(hits))}")
    return tuple(problems)

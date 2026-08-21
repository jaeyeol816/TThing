"""게이트키퍼 트레이스 — 파이프라인이 실제로 무엇을 했는지의 기록.

──────────────────────────────────────────────────────────────────────
이것이 무엇이고 무엇이 아닌가
──────────────────────────────────────────────────────────────────────

화면의 말풍선 아래 접혀 있던 "Gatekeeper: 통과" 한 줄을, 단계별로 **열어
볼 수 있는 것**으로 바꾼다. 각 단계는 그 단계가 실제로 손에 쥐고 있던
자료를 보여준다.

    ① 등급 판정   무엇을 보고 몇 등급이라고 했나
    ② 근거 선택   세션의 어떤 문서가 동원됐나 (제목·등급·시점)
    ③ 표현 변환   경계를 넘은 그 JSON 전문 + 치환 기호표
    ④ 검증 6단계  각 단계가 무엇을 검사했고 결과가 무엇인가
    ⑤ 경계 통과   어디로, 어떤 모델에, 몇 바이트가, 어떤 해시로
    ⑥ 재수화      기호 답변 ↔ 복원된 답변 나란히 비교

⚠️ **트레이스는 감사 로그가 아니다.** 감사 로그(`audit.py`)가 법적 증거이고
   이것은 사람이 이해하기 위한 화면이다. 둘을 합치지 않는 이유: 감사는
   영속이고 트레이스는 TTL 로 사라져야 한다 (아래 참조).

──────────────────────────────────────────────────────────────────────
트레이스가 **담지 않는** 것 — 이 파일의 핵심 규칙
──────────────────────────────────────────────────────────────────────

1. **원문을 담지 않는다.**
   트레이스가 보여주는 최대치는 *경계를 넘은 것* 이다. 그보다 많이 보여주면
   트레이스 자체가 권한 우회 통로가 된다. 질문자는 이미 미리보기에서 페이로드
   전문을 봤으므로(BR-U-01) 같은 것을 다시 보는 것은 새로운 노출이 아니다.
   반면 변환 **전** 원문은 질문자가 볼 권한이 없는 남의 파일이다.

2. **`CheckResult.offending` 을 담지 않는다.**
   원문대조(n-gram) 검사가 실패했을 때 `offending` 에는 **원문 5-gram 이 그대로**
   들어 있다. 그것을 화면에 띄우면 "검증이 막았다"고 표시하면서 동시에 막힌
   내용을 보여주는 자기모순이 된다. 건수만 남기고 값은 서버 로그에만 둔다.

3. **매핑표는 답변에 실제로 등장한 기호만 공개한다.**
   `<PERSON_2> → 김철수` 를 전부 보여주면 "김철수가 박선영의 문서에 등장한다"는
   사실이 새어 나간다. 답변에 나온 기호는 이미 재수화된 실제 이름으로
   질문자가 읽은 것이므로 공개해도 새 정보가 아니다. 나머지는 건수만 센다.

이 세 규칙은 주석이 아니라 함수다 — `redact_checks()`, `mapping_rows()`.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mesh.config import get_logger
from mesh.schemas import (
    CheckResult,
    Citation,
    PayloadEnvelope,
    Representation,
    Tier,
    TierDecision,
    ValidationResult,
)

log = get_logger("trace")

PanelKind = Literal["json", "text", "table", "compare", "list", "note"]
StageStatus = Literal["pass", "fail", "warn", "info", "skip", "blocked"]

#: 트레이스 보관 시간. 감사 로그와 달리 **사라져야 한다** — 매핑표 일부를
#: 품고 있고, 화면이 닫힌 뒤에도 남아 있을 이유가 없다.
TRACE_TTL_SECONDS = 30 * 60

#: 동시에 보관할 트레이스 수 상한. 넘으면 오래된 것부터 버린다.
MAX_TRACES = 200

#: 단계 정의. 순서·제목·부제가 **한 곳**에 있다. 단계를 추가하려면
#: 여기에 항목을 더하고 그 단계를 기록하는 쪽에서 `stage_id` 를 쓰면 된다.
STAGE_DEFS: tuple[tuple[str, str, str], ...] = (
    ("classify", "등급 판정", "무엇을 보고 이 등급이라고 했나"),
    ("select", "근거 선택", "세션의 어떤 문서가 동원됐나"),
    ("transform", "비식별 처리", "무엇을 마스킹 했나"),
    ("validate", "검증 6단계", "나가기 전 무엇을 검사했나"),
    ("dispatch", "경계 통과", "어디로 무엇이 나갔나"),
    ("rehydrate", "식별화", "기호를 실제 이름으로 되돌린 결과"),
)

STAGE_ORDER: dict[str, int] = {sid: i for i, (sid, _, _) in enumerate(STAGE_DEFS)}
STAGE_TITLES: dict[str, tuple[str, str]] = {
    sid: (title, subtitle) for sid, title, subtitle in STAGE_DEFS
}


def new_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:20]}"


# ══════════════════════════════════════════════════════════════════════
# 모델
# ══════════════════════════════════════════════════════════════════════


class TraceEvidence(BaseModel):
    """근거 문서 한 건의 **투영**. `Chunk` 가 아니다.

    ⚠️ 이 모듈이 `Chunk` 를 import 하지 않는 이유가 여기 있다.
       `Chunk` 에는 `text`(원문)와 `internal_path`(경로 자체가 정보다, FR-43)가
       들어 있다. 트레이스는 둘 다 화면에 올리지 않으므로 **애초에 받지 않는다.**
       "받아 놓고 안 쓴다" 와 "받지 않는다" 는 다르다 — 앞의 것은 다음 사람이
       한 줄 추가하면 유출이 되고, 뒤의 것은 타입이 막는다.

       `tests/unit/test_import_boundary.py` 가 `Chunk` 를 만질 수 있는 모듈을
       고정해 두었다. 트레이스를 그 목록에 넣어 규칙을 넓히는 대신,
       **투영을 만드는 책임을 이미 원문을 다루는 쪽(orchestrator)에 남긴다.**

    `from_chunk()` 를 여기 두지 않는 것도 같은 이유다 — 그 함수가 있으면
    이 파일이 `Chunk` 를 알아야 한다.
    """

    model_config = ConfigDict(frozen=True)

    #: 화면에 보이는 제목. 경로가 아니다.
    title: str
    #: 절대 경로. UI 에서 경로+제목으로 표시할 때 쓴다 (TR-43 override — 트레이스 전용).
    source_path: str = ""
    tier: Tier | None = None
    #: "note" / "doc" / "code" / "config" / "log" 등. 사람이 읽을 종류 표시.
    source_kind: str = ""
    #: 문서 시점. 없으면 빈 문자열 (화면은 "—" 로 그린다).
    as_of: str = ""
    #: 분량. 원문이 아니라 **크기**다.
    chars: int = 0
    truncated: bool = False
    #: 등급 판정 근거. `TierDecision` 을 그대로 넣지 않고 필요한 것만 편다.
    #:
    #: ⚠️ 원본 그대로 넣어도 된다. 화면에 나가기 전 `redact_reasons()` 가
    #:    매치된 값(경로·금칙어)을 걷어낸다 — 호출자가 잊어도 새지 않는다.
    rule_tier: Tier | None = None
    exaone_tier: Tier | None = None
    exaone_note: str = ""
    #: 몇 번 규칙에서 확정됐는가 (1~6). 판정 과정을 단계별로 펼칠 때 쓴다.
    rule_number: int | None = None
    #: EXAONE 을 부르지 않았는가 / 불렀는데 실패했는가.
    exaone_skipped: bool = False
    exaone_failed: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def effective_tier(self) -> Tier:
        return self.tier or Tier.INTERNAL


class TraceRow(BaseModel):
    """표 한 줄. 셀 수는 `TracePanel.columns` 와 같아야 한다."""

    model_config = ConfigDict(frozen=True)

    cells: tuple[str, ...]
    status: Literal["pass", "fail", "warn", "info"] = "info"


class TracePanel(BaseModel):
    """단계 하나가 보여주는 자료 한 덩어리.

    ⚠️ 종류가 6개뿐이고 전부 **텍스트**다. HTML 을 담는 필드가 없다 —
       화면이 `innerHTML` 을 쓸 수 없게 하는 타입 수준 장치다 (BR-U-12).
    """

    model_config = ConfigDict(frozen=True)

    panel_id: str
    label: str
    kind: PanelKind
    caption: str = ""

    text: str | None = None
    json_text: str | None = None
    columns: tuple[str, ...] = ()
    rows: tuple[TraceRow, ...] = ()
    items: tuple[str, ...] = ()

    # compare 전용
    before_label: str = ""
    before_text: str | None = None
    after_label: str = ""
    after_text: str | None = None

    #: 규칙에 따라 가려진 항목 수. 0 이 아니면 화면이 "N건 비공개" 를 그린다.
    #: 숨겼다는 사실 자체를 숨기지 않는다.
    redacted_count: int = 0

    #: `json` 패널에서 **눈에 띄게 칠할 문자열**. 치환으로 들어간 기호들이다.
    #:
    #: 값이 아니라 **기호**만 담긴다 (`<PERSON_1>`, `COMP_A`). 실제 이름을 여기
    #: 넣으면 화면 강조가 매핑표를 우회해 값을 공개하는 셈이 된다.
    highlight: tuple[str, ...] = ()


class TraceStage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage_id: str
    order: int
    title: str
    subtitle: str = ""
    status: StageStatus = "info"
    summary: str = ""
    #: 이 단계에서 신뢰 경계를 넘는가. 화면이 이 위아래로 선을 긋는다.
    crosses_boundary: bool = False
    elapsed_ms: int | None = None
    panels: tuple[TracePanel, ...] = ()


class GatekeeperTrace(BaseModel):
    """한 사람에 대한 한 질문의 처리 경과 전체."""

    model_config = ConfigDict(frozen=True)

    trace_id: str
    request_id: str
    entity_id: str
    agent_label: str = ""
    question: str = ""
    tier: Tier | None = None
    representation: Representation | None = None
    stages: tuple[TraceStage, ...] = ()
    crossed_boundary: bool = False
    created_at: datetime | None = None

    @property
    def stage(self, ) -> dict[str, TraceStage]:  # pragma: no cover — 편의 조회
        return {s.stage_id: s for s in self.stages}


# ══════════════════════════════════════════════════════════════════════
# 가리기 규칙 (주석이 아니라 함수다)
# ══════════════════════════════════════════════════════════════════════


def redact_checks(result: ValidationResult | None) -> tuple[tuple[CheckResult, int], ...]:
    """`(검사결과, 가려진 offending 건수)` 쌍. 값 자체는 절대 나가지 않는다."""
    if result is None:
        return ()
    return tuple((c, len(c.offending)) for c in result.checks)


#: 판정 사유의 **범주 표시**. `classifier` 가 만드는 사유 문자열은 서버 로그용이라
#: 매치된 값을 그대로 품는다 — `경로 규칙 'person_kim/data/customer-H/**' 매치`,
#: `금칙어 '하나텔'` 처럼.
#:
#: 🔴 그 값이 화면에 뜨면 트레이스가 **유출 채널이 된다.** 내부 경로는 FR-43 이
#:    금지하는 것이고, 금칙어 리터럴은 애초에 그것을 막으려고 만든 목록이다.
#:    "왜 기밀인가" 에 답하는 데 필요한 것은 **어떤 규칙이 걸렸는가**이지
#:    무엇과 일치했는가가 아니다.
_REASON_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("사내 경로 규칙", "사내 경로 규칙 매치 (경로는 표시하지 않는다)"),
    ("경로 규칙", "경로 규칙 매치 (경로는 표시하지 않는다)"),
    ("금칙어 패턴", "금칙어 패턴 매치 (패턴은 표시하지 않는다)"),
    ("금칙어", "금칙어 리터럴 매치 (해당 단어는 표시하지 않는다)"),
    ("헤더 표기", "문서 헤더의 등급 표기"),
)

#: 값을 품고 있다는 신호. 여기 걸리는데 범주를 못 찾으면 통째로 가린다.
_VALUE_MARKERS = ("'", '"', "/", "\\", "`")


def redact_reasons(reasons: Iterable[str]) -> tuple[str, ...]:
    """판정 사유에서 **매치된 값**을 걷어내고 범주만 남긴다.

    모르는 형태의 사유가 값을 품고 있으면(따옴표·슬래시) 통째로 가린다 —
    새 규칙이 추가됐을 때 그 사유가 검사 없이 화면에 뜨는 것을 막는다
    . 목록에 없는 것은 통과시키지 않는다.
    """
    out: list[str] = []
    for reason in reasons:
        text = reason.strip()
        if not text:
            continue
        for prefix, label in _REASON_CATEGORIES:
            if text.startswith(prefix):
                text = label
                break
        else:
            if any(marker in text for marker in _VALUE_MARKERS):
                text = "규칙 매치 (상세는 서버 로그에만 남는다)"
        if text not in out:
            out.append(text)
    return tuple(out)


def mapping_rows(
    mapping: dict[str, str] | None,
    *,
    visible_in: str = "",
) -> tuple[tuple[str, str, bool], ...]:
    """`(기호, 값, 공개여부)`.

    `visible_in` 에 등장하지 않는 기호의 값은 돌려주지 않는다 —
    파일 §규칙 3. 값을 지우는 것이 아니라 **애초에 담지 않는다.**
    """
    if not mapping:
        return ()
    out: list[tuple[str, str, bool]] = []
    for symbol in sorted(mapping, key=len, reverse=True):
        shown = bool(visible_in) and symbol in visible_in
        out.append((symbol, mapping[symbol] if shown else "", shown))
    return tuple(out)


# ══════════════════════════════════════════════════════════════════════
# 등급 판정 과정 — 검증 6단계와 같은 모양으로 펼친다
# ══════════════════════════════════════════════════════════════════════
#
# `classifier.rule_tier()` 는 **앞에서 걸리면 뒤를 보지 않는다.** 그래서
# "몇 번에서 확정됐는가" 하나로 여섯 단계 전체의 상태가 정해진다.
#
#     확정 번호보다 앞  ->  검사했고 **해당 없음**
#     확정 번호         ->  **여기서 걸렸다**
#     확정 번호보다 뒤  ->  **검사하지 않았다** (앞에서 이미 정해졌다)
#
# 이 사실을 화면이 그대로 보여주는 것이 중요하다. 여섯 줄 중 하나만 색이
# 다르면, 왜 그 등급인지가 문장이 아니라 **표**로 읽힌다.

#: (번호, 이름, 무엇을 보는가, 걸리면 어떤 등급인가)
CLASSIFY_RULES: tuple[tuple[int, str, str, str], ...] = (
    (1, "경로 규칙 (기밀)", "파일이 기밀 디렉터리 아래에 있는가", "기밀"),
    (2, "금칙어 리터럴", "본문에 고객사명·인증방식명이 있는가", "기밀"),
    (3, "금칙어 정규식", "본문에 계약번호·요구사항번호·금액이 있는가", "기밀"),
    (4, "헤더 등급 표기", "작성자가 문서 머리에 등급을 적었는가", "표기된 등급"),
    (5, "경로 규칙 (사내)", "파일이 사내 디렉터리 아래에 있는가", "사내"),
    (6, "기본값", "위 어디에도 걸리지 않았다", "사내"),
)


def rule_step_rows(
    *,
    rule_number: int | None,
    rule_tier: Tier | None,
    final_tier: Tier,
    exaone_tier: Tier | None,
    exaone_skipped: bool,
    exaone_failed: bool,
    reasons: Iterable[str],
) -> tuple[TraceRow, ...]:
    """규칙 6단계 + EXAONE + 최종을 한 표로 편다.

    ⚠️ `reasons` 는 `redact_reasons()` 를 통과시킨다 — 매치된 경로·금칙어가
       화면에 뜨면 이 표가 유출 채널이 된다 (파일 머리말 §④).
    """
    hit_reason = "; ".join(redact_reasons(reasons)) or "—"
    rows: list[TraceRow] = []

    for number, name, looks_at, outcome in CLASSIFY_RULES:
        if rule_number is None:
            verdict, detail, status = "—", looks_at, "info"
        elif number < rule_number:
            verdict, detail, status = "해당 없음", looks_at, "pass"
        elif number == rule_number:
            verdict = "걸림"
            detail = hit_reason
            # 걸린 것이 나쁜 것은 아니다 — 기밀로 확정됐을 때만 주의색을 준다.
            status = "warn" if (rule_tier or final_tier) is Tier.SECRET else "pass"
        else:
            verdict, detail, status = "검사 안 함", f"{rule_number}번에서 확정됐다", "info"
        rows.append(
            TraceRow(cells=(f"{number}. {name}", verdict, detail, outcome), status=status)
        )

    if exaone_skipped:
        ex_verdict = "생략"
        ex_detail = (
            "규칙이 이미 기밀이다 — 더 높은 등급이 없으므로 왕복을 절약한다 (BR-C-02)"
            if (rule_tier is Tier.SECRET)
            else "보조 판정이 꺼져 있다 (오프라인 · 게이트 측정)"
        )
        ex_status = "info"
    elif exaone_failed:
        ex_verdict, ex_detail, ex_status = (
            "실패",
            "판정하지 못했다 → 기밀로 간주한다",
            "warn",
        )
    elif exaone_tier is not None:
        ex_verdict, ex_detail, ex_status = (
            exaone_tier.label_ko,
            "사전에 정의되지 않은 형태의 기밀을 식별한다",
            "pass",
        )
    else:
        ex_verdict, ex_detail, ex_status = "—", "부르지 않았다", "info"

    rows.append(
        TraceRow(cells=("EXAONE 보조 판정", ex_verdict, ex_detail, "더 높으면 상향"),
                 status=ex_status)
    )
    rows.append(
        TraceRow(
            cells=(
                "최종",
                final_tier.label_ko,
                f"max(규칙 {(rule_tier or final_tier).label_ko}, "
                f"EXAONE {exaone_tier.label_ko if exaone_tier else '—'})",
                "둘 중 높은 쪽",
            ),
            status="warn" if final_tier is Tier.SECRET else "pass",
        )
    )
    return tuple(rows)


#: 기호의 앞부분 -> 무엇을 가린 것인가. 값을 말하지 않고 **범주**만 말한다.
_SYMBOL_KINDS: tuple[tuple[str, str], ...] = (
    ("PERSON", "사람 이름"),
    ("PROJ", "프로젝트명"),
    ("SYS", "시스템명"),
    ("TEAM", "팀 이름"),
    ("REQ", "요구사항 문서 참조"),
    ("COMP", "우리 구성요소 문서 참조"),
    ("DOC", "문서 참조"),
)


def symbol_kind(symbol: str) -> str:
    """`<PERSON_1>` -> "사람 이름". 값을 말하지 않고 범주만 말한다."""
    bare = symbol.strip("<>").upper()
    for prefix, label in _SYMBOL_KINDS:
        if bare.startswith(prefix):
            return label
    return "치환된 식별자"


# ══════════════════════════════════════════════════════════════════════
# 기록기
# ══════════════════════════════════════════════════════════════════════


class TraceRecorder:
    """단계를 쌓아 올리는 가변 빌더.

    `prepare` 가 ①~④ 를, `send` 가 ⑤~⑥ 을 채운다. 두 HTTP 왕복에 걸쳐
    같은 기록기를 이어 쓰므로 `PendingCall` 이 이것을 들고 있는다.

    ⚠️ 기록 실패가 질의를 죽이면 안 된다. 모든 `add_*` 는 예외를 삼키고
       로그만 남긴다 — 트레이스는 설명이지 기능이 아니다.
    """

    def __init__(self, *, request_id: str, entity_id: str, question: str) -> None:
        self.trace_id = new_trace_id()
        self.request_id = request_id
        self.entity_id = entity_id
        self.question = question
        self.agent_label = ""
        self.tier: Tier | None = None
        self.representation: Representation | None = None
        self.crossed_boundary = False
        self._stages: dict[str, TraceStage] = {}
        self._marks: dict[str, float] = {}

    # ── 시간 ─────────────────────────────────────────────────────────

    def mark(self, stage_id: str) -> None:
        self._marks[stage_id] = time.monotonic()

    def _elapsed(self, stage_id: str) -> int | None:
        started = self._marks.pop(stage_id, None)
        if started is None:
            return None
        return int((time.monotonic() - started) * 1000)

    # ── 저수준 ───────────────────────────────────────────────────────

    def put(
        self,
        stage_id: str,
        *,
        status: StageStatus = "info",
        summary: str = "",
        panels: Sequence[TracePanel] = (),
        crosses_boundary: bool = False,
    ) -> None:
        try:
            title, subtitle = STAGE_TITLES.get(stage_id, (stage_id, ""))
            self._stages[stage_id] = TraceStage(
                stage_id=stage_id,
                order=STAGE_ORDER.get(stage_id, 99),
                title=title,
                subtitle=subtitle,
                status=status,
                summary=summary,
                crosses_boundary=crosses_boundary,
                elapsed_ms=self._elapsed(stage_id),
                panels=tuple(panels),
            )
        except Exception:  # noqa: BLE001 — 트레이스가 질의를 죽이면 안 된다
            log.exception("트레이스 기록 실패 — 무시한다", extra={"stage": stage_id})

    @property
    def has_blocked(self) -> bool:
        """어느 단계에서든 이미 "여기서 멈췄다" 를 기록했는가.

        호출자가 같은 사실을 두 번 적지 않게 한다 — 차단 사유가 두 개면
        읽는 사람은 어느 것이 진짜인지 모른다.
        """
        return any(stage.status == "blocked" for stage in self._stages.values())

    def build(self) -> GatekeeperTrace:
        stages = tuple(sorted(self._stages.values(), key=lambda s: s.order))
        return GatekeeperTrace(
            trace_id=self.trace_id,
            request_id=self.request_id,
            entity_id=self.entity_id,
            agent_label=self.agent_label,
            question=self.question,
            tier=self.tier,
            representation=self.representation,
            stages=stages,
            crossed_boundary=self.crossed_boundary,
            created_at=datetime.now().astimezone(),
        )

    # ── ① 등급 판정 ──────────────────────────────────────────────────

    def add_classify(
        self,
        *,
        question_decision: TierDecision,
        evidence: Sequence[TraceEvidence],
        effective: Tier,
    ) -> None:
        rows: list[TraceRow] = [
            TraceRow(
                cells=(
                    "질문 문장",
                    question_decision.rule_tier.label_ko,
                    _tier_or_dash(question_decision.exaone_tier, question_decision),
                    question_decision.tier.label_ko,
                ),
                status="warn" if question_decision.tier is Tier.SECRET else "info",
            )
        ]
        for item in evidence:
            # ⚠️ 경로는 트레이스에 싣지 않는다 (FR-43). 경로 자체가 정보다 —
            #    `customer-H/` 같은 세그먼트가 고객사명을 드러낸다. 제목만 보여준다.
            rows.append(
                TraceRow(
                    cells=(
                        item.title,
                        item.rule_tier.label_ko if item.rule_tier else "—",
                        item.exaone_tier.label_ko if item.exaone_tier else (item.exaone_note or "—"),
                        item.effective_tier.label_ko,
                    ),
                    status="warn" if item.effective_tier is Tier.SECRET else "info",
                )
            )

        self.tier = effective

        # 대상마다 **판정 과정 표**를 하나씩 만든다. 검증 6단계와 같은 모양이다 —
        # 왜 그 등급인지가 문장이 아니라 표로 읽혀야 한다.
        panels: list[TracePanel] = [
            TracePanel(
                panel_id="classify-summary",
                label="한눈에",
                kind="table",
                caption=(
                    "규칙과 EXAONE 중 **더 높은 쪽**을 택한다 (BR-C-03). 애매하면 항상 높은 "
                    "등급으로 — 낮게 잡은 실수는 유출이고 높게 잡은 실수는 불편이다. "
                    "질의 전체의 등급은 여기 나온 것 중 **가장 높은 것**이 된다."
                ),
                columns=("대상", "규칙", "EXAONE", "최종"),
                rows=tuple(rows),
            ),
            TracePanel(
                panel_id="classify-question-steps",
                label="질문 문장 — 판정 과정",
                kind="table",
                caption=(
                    "규칙은 **앞에서 걸리면 뒤를 보지 않는다.** 그래서 걸린 번호 하나로 "
                    "여섯 단계 전체의 상태가 정해진다. 질문도 판정 대상인 이유: 지식을 "
                    "아무리 잘 막아도 질문 문장이 기밀을 담고 있으면 그대로 새어 나간다."
                ),
                columns=("검사", "결과", "내용", "걸리면"),
                rows=rule_step_rows(
                    rule_number=question_decision.rule_number,
                    rule_tier=question_decision.rule_tier,
                    final_tier=question_decision.tier,
                    exaone_tier=question_decision.exaone_tier,
                    exaone_skipped=question_decision.exaone_skipped,
                    exaone_failed=question_decision.exaone_failed,
                    reasons=question_decision.reasons,
                ),
            ),
        ]

        for index, item in enumerate(evidence):
            panels.append(
                TracePanel(
                    panel_id=f"classify-steps-{index}",
                    label=f"{item.title} — 판정 과정",
                    kind="table",
                    caption=(
                        "**매치된 값은 싣지 않는다.** 1번에 걸렸다면 경로가, 2번에 걸렸다면 "
                        "금칙어가 화면에 뜨게 되는데, 그 둘이야말로 이 시스템이 내보내지 "
                        "않으려는 것이다. 어떤 규칙이 걸렸는지까지만 말한다."
                    ),
                    columns=("검사", "결과", "내용", "걸리면"),
                    rows=rule_step_rows(
                        rule_number=item.rule_number,
                        rule_tier=item.rule_tier,
                        final_tier=item.effective_tier,
                        exaone_tier=item.exaone_tier,
                        exaone_skipped=item.exaone_skipped,
                        exaone_failed=item.exaone_failed,
                        reasons=item.reasons,
                    ),
                )
            )

        panels.append(
            TracePanel(
                panel_id="classify-note",
                label="이 등급이 뜻하는 것",
                kind="note",
                text=_tier_meaning(effective),
            )
        )

        self.put(
            "classify",
            status="pass",
            summary=f"최종 {effective.label_ko} — 근거 {len(evidence)}건 판정",
            panels=tuple(panels),
        )

    # ── ② 근거 선택 ──────────────────────────────────────────────────

    def add_select(
        self,
        *,
        candidate_count: int,
        evidence: Sequence[TraceEvidence],
        selected_by_model: bool,
    ) -> None:
        rows = tuple(
            TraceRow(
                cells=(
                    item.title,
                    item.effective_tier.label_ko,
                    item.source_kind or "—",
                    item.as_of or "—",
                    "잘림" if item.truncated else f"{item.chars:,}자",
                ),
                status="warn" if item.effective_tier is Tier.SECRET else "info",
            )
            for item in evidence
        )
        self.put(
            "select",
            status="pass",
            summary=f"후보 {candidate_count}건 중 {len(evidence)}건 사용",
            panels=(
                TracePanel(
                    panel_id="select-table",
                    label="선택된 근거",
                    kind="table",
                    caption=(
                        "세션이 좁혀 둔 후보에서만 고른다 — 전역 스캔은 하지 않는다 (BR-S-01). "
                        "**내부 경로는 표시하지 않는다** — 경로 자체가 정보이기 때문이다 (FR-43)."
                    ),
                    columns=("문서", "등급", "종류", "시점", "분량"),
                    rows=rows,
                ),
                # TracePanel(
                #     panel_id="select-how",
                #     label="고른 방법",
                #     kind="note",
                #     text=(
                #         "EXAONE 이 **경로와 제목만 보고** 인덱스를 골랐다. 본문은 넘기지 않는다 — "
                #         "아직 어떤 파일이 기밀인지 모르는 시점이기 때문이다 (BR-S-02)."
                #         if selected_by_model
                #         else "후보가 하나이거나 판정이 실패해 후보 전체를 읽었다 (fail closed 방향)."
                #     ),
                # ),
            ),
        )

    # ── ③ 표현 변환 ──────────────────────────────────────────────────

    def add_transform(
        self,
        *,
        env: PayloadEnvelope,
        mapping_table: dict[str, str] | None,
        extraction_note: str = "",
    ) -> None:
        """⚠️ `schemas.Mapping` 이 아니라 **평범한 dict** 를 받는다.

        `Mapping` 은 직렬화·복사·pickle 이 타입 수준에서 금지된 객체다
        (BR-G-09). 그 객체를 이 모듈까지 흘려보내면 "복사할 수 없다"는 보증을
        가진 값이 화면 빌더의 손에 들어온다. 호출자가 **자기 책임으로** 사본을
        떠서 넘기게 하면, 매핑을 다루는 모듈이 늘어나지 않는다
        (`test_import_boundary.py::test_only_designated_modules_handle_mapping`).
        """
        self.representation = env.representation
        table = dict(mapping_table or {})

        # ⚠️ 여기서 값을 보여주지 않는 이유는 "답변에 없어서" 가 **아니다.**
        #    이 시점에는 답변이 아직 오지도 않았다 — 경계를 넘기 직전이다.
        #    (예전 문구가 "답변에 등장하지 않음" 이었고, 그것은 ⑥ 재수화의
        #     규칙을 여기에 잘못 옮긴 것이었다.)
        #
        #    진짜 이유: 이 표에는 답변에 쓰이지 **않을** 문서 제목과 인명까지
        #    들어 있다. 전부 펼치면 "그 사람 파일에 무엇이 있는지" 가 새어 나간다.
        #    그래서 여기서는 **무엇을 가린 것인지(범주)** 까지만 말하고,
        #    실제 값은 ⑥ 재수화에서 **답변에 등장한 것만** 연다.
        rows = tuple(
            TraceRow(
                cells=(symbol, symbol_kind(symbol), "⑥ 식별화 — 답변에 등장하면 공개"),
                status="info",
            )
            for symbol in sorted(table, key=len, reverse=True)
        )
        self.put(
            "transform",
            status="pass",
            summary=f"{_representation_label(env.representation)} · {env.size_bytes:,}B",
            panels=(
                TracePanel(
                    panel_id="transform-payload",
                    label="비식별화 결과 (전문)",
                    kind="json",
                    caption=(
                        _representation_caption(env.representation)
                        + (
                            "  **붉게 칠한 것이 치환된 자리다** — 그 자리에 원래 있던 값은 "
                            "경계를 넘지 않았고, 신뢰 구역의 매핑표에만 있다."
                            if table
                            else ""
                        )
                    ),
                    json_text=json.dumps(env.payload, ensure_ascii=False, indent=2),
                    # 화면이 칠할 것은 **기호**뿐이다. 실제 이름을 여기 넣으면
                    # 강조가 매핑표를 우회해 값을 공개하는 셈이 된다.
                    highlight=tuple(sorted(table, key=len, reverse=True)),
                ),
                TracePanel(
                    panel_id="transform-mapping",
                    label=f"치환 기호표 ({len(table)}개)",
                    kind="table",
                    caption=(
                        "기호 ↔ 실제 이름의 대응표. **앱 메모리에만 있고 응답 후 폐기된다** "
                        "(BR-G-09).\n\n여기서 값을 감추는 이유는 '답변에 없어서' 가 아니다 — "
                        "**이 시점에는 답변이 아직 오지 않았다.** 이 표에는 답변에 쓰이지 않을 "
                        "문서 제목과 인명까지 들어 있어서, 전부 펼치면 그 사람의 파일에 무엇이 "
                        "있는지가 새어 나간다. 실제 값은 **⑥ 식별화에서 답변에 등장한 것만** 연다."
                    ),
                    columns=("기호", "무엇을 가렸나", "값이 열리는 시점"),
                    rows=rows,
                    redacted_count=len(table),
                ),
                # TracePanel(
                #     panel_id="transform-how",
                #     label="어떻게 만들었나",
                #     kind="note",
                #     text=extraction_note or _representation_how(env.representation),
                # ),
            ),
        )

    # ── ④ 검증 ───────────────────────────────────────────────────────

    def add_validate(self, *, result: ValidationResult | None, verbatim_count: int) -> None:
        pairs = redact_checks(result)
        rows = tuple(
            TraceRow(
                cells=(
                    _stage_label(check.stage),
                    "통과" if check.passed else "차단",
                    check.detail or "—",
                    f"{hidden}건 비공개" if hidden else "—",
                ),
                status="pass" if check.passed else "fail",
            )
            for check, hidden in pairs
        )
        hidden_total = sum(h for _, h in pairs)
        passed = result.passed if result else False
        self.put(
            "validate",
            status="pass" if passed else "fail",
            summary=(result.summary if result else "미실행") + (" 통과" if passed else " 차단"),
            panels=(
                TracePanel(
                    panel_id="validate-table",
                    label="검증 6단계",
                    kind="table",
                    caption=(
                        "첫 실패에서 멈추지 않고 전부 수집한다 — 사람이 볼 진단이 완전해야 한다. "
                        "**걸린 원문 조각은 화면에 싣지 않는다**: 검증이 막은 내용을 "
                        "막았다는 화면에서 보여주면 자기모순이다."
                    ),
                    columns=("단계", "결과", "내용", "가려진 것"),
                    rows=rows,
                    redacted_count=hidden_total,
                ),
                TracePanel(
                    panel_id="validate-verbatim",
                    label="원문 문장 수 (측정값)",
                    kind="note",
                    text=(
                        f"{verbatim_count}개. 이 숫자는 주장이 아니라 페이로드와 원문을 "
                        "대조해 **계산한 결과**다. 기밀 등급에서 0 이 아니면 전송되지 않는다."
                    ),
                ),
            ),
        )

    # ── ⑤ 경계 통과 ──────────────────────────────────────────────────

    def add_dispatch(
        self,
        *,
        env: PayloadEnvelope,
        transport: str,
        model_id: str,
        approved_by: str,
        endpoint: str = "",
        sent: bool = True,
        note: str = "",
        latency_ms: int | None = None,
        usage: dict[str, object] | None = None,
        failed: bool = False,
    ) -> None:
        self.crossed_boundary = sent and not failed
        rows: list[TraceRow] = [
            TraceRow(
                cells=("전송 여부", "비식별화 후 전송됨" if sent else "전송되지 않음"),
                status="warn" if sent else "pass",
            ),
            TraceRow(cells=("경로", transport)),
            TraceRow(cells=("모델", model_id)),
            TraceRow(cells=("엔드포인트", endpoint or "(전송되지 않음)")),
            TraceRow(cells=("승인자", approved_by)),
            TraceRow(cells=("크기", f"{env.size_bytes:,} bytes")),
        ]

        # 왕복 시간. 사람이 기다린 시간의 대부분이 여기다 — 어느 단계가 느린지
        # 물으면 답할 수 있어야 한다.
        if latency_ms is not None:
            rows.append(
                TraceRow(
                    cells=("Agent 응답 시간", f"{latency_ms:,} ms ({latency_ms / 1000:.2f}초)"),
                    status="warn" if latency_ms >= 10_000 else "pass",
                )
            )
        if failed:
            rows.append(
                TraceRow(cells=("결과", "호출 실패 — 신뢰 구역 안에서 답했다"), status="fail")
            )
        if usage:
            tokens = ", ".join(
                f"{k} {v}" for k, v in usage.items() if isinstance(v, int | float)
            )
            if tokens:
                rows.append(TraceRow(cells=("토큰 사용량", tokens)))

        rows.append(TraceRow(cells=("페이로드 해시", env.payload_sha256[:32] + "…")))
        rows.append(TraceRow(cells=("봉투 ID", env.envelope_id)))
        rows = tuple(rows)  # type: ignore[assignment]
        took = f" · {latency_ms:,}ms" if latency_ms is not None else ""
        self.put(
            "dispatch",
            status="fail" if failed else ("warn" if sent else "pass"),
            summary=(
                ("경계 통과 — " + transport + took) if sent else "경계를 넘지 않음"
            ),
            crosses_boundary=sent,
            panels=(
                TracePanel(
                    panel_id="dispatch-table",
                    label="경계에서 일어난 일",
                    kind="table",
                    caption=(
                        "이 기록은 호출 **직전**에 남는다 (BR-A-01). 호출이 실패해도 "
                        "'나갔다'는 사실은 감사 로그에 남아 있다."
                    ),
                    columns=("항목", "값"),
                    rows=rows,
                ),
            ),
        )

    # ── ⑥ 재수화 ─────────────────────────────────────────────────────

    def add_rehydrate(
        self,
        *,
        masked_text: str,
        rehydrated_text: str,
        mapping_table: dict[str, str] | None,
        unresolved: Sequence[str] = (),
        citations: Sequence[Citation] = (),
        confidence: float | None = None,
    ) -> None:
        rows: list[TraceRow] = []
        table = mapping_table or {}
        for symbol, value, shown in mapping_rows(mapping_table, visible_in=masked_text):
            if shown:
                rows.append(TraceRow(cells=(symbol, value), status="pass"))
            else:
                # PoC: 답변에 등장하지 않아 원래는 건수만 세던 기호도 확인용으로
                # 값을 함께 보여준다.
                # ⚠️ 운영 전환 시 되돌린다 — 질문자가 볼 권한이 없을 수 있는 값이다
                #    (파일 §규칙 3). mapping_rows() 자체는 그대로 두고 여기서만 연다.
                rows.append(
                    TraceRow(
                        cells=(symbol, f"{table.get(symbol, '')}"),
                        status="warn",
                    )
                )

        panels: list[TracePanel] = [
            TracePanel(
                panel_id="rehydrate-compare",
                label="기호 답변 ↔ 복원된 답변",
                kind="compare",
                caption=(
                    "왼쪽은 경계 **밖** 모델이 만든 그대로다 — 실제 이름을 본 적이 없다. "
                    "오른쪽은 신뢰 구역 안에서 기호를 실제 이름으로 되돌린 것이다. "
                    "식별화는 **순수 문자열 치환**이고 모델을 쓰지 않는다 (FR-13)."
                ),
                before_label="경계 밖에서 받은 것 (비식별 기호)",
                before_text=masked_text or "(기호가 남지 않은 답변)",
                after_label="신뢰 구역에서 식별화한 것",
                after_text=rehydrated_text,
            ),
            TracePanel(
                panel_id="rehydrate-mapping",
                label=f"되돌린 기호 ({len(rows)}개)",
                kind="table",
                caption=(
                    "답변에 등장한 기호는 실제 값으로 복원해 보여줍니다. "
                    "PoC 확인용으로, 답변에 등장하지 않아 원래는 건수만 세던 기호도 "
                    "값을 함께 표시합니다 (운영에서는 건수만)."
                ),
                columns=("기호", "되돌린 값"),
                rows=tuple(rows),
            ),
        ]
        if citations:
            panels.append(
                TracePanel(
                    panel_id="rehydrate-citations",
                    label="인용",
                    kind="table",
                    caption="매핑에 없는 ref 로 온 인용은 버린다 — 근거 없는 인용을 띄우지 않는다 (BR-G-10).",
                    columns=("기호", "문서", "등급", "시점"),
                    rows=tuple(
                        TraceRow(
                            cells=(
                                c.ref,
                                c.display_title,
                                c.tier.label_ko,
                                c.as_of.isoformat() if c.as_of else "—",
                            )
                        )
                        for c in citations
                    ),
                )
            )
        if unresolved:
            panels.append(
                TracePanel(
                    panel_id="rehydrate-unresolved",
                    label=f"되돌리지 못한 기호 ({len(unresolved)}개)",
                    kind="list",
                    caption=(
                        "매핑에 없는 기호는 **치환하지 않고 그대로 남긴다** (BR-G-10). "
                        "프롬프트 인젝션으로 임의 문자열을 치환시키는 것을 막는다."
                    ),
                    items=tuple(unresolved),
                )
            )

        self.put(
            "rehydrate",
            status="warn" if unresolved else "pass",
            summary=(
                f"기호 {len(rows)}개 복원"
                + (f" · 미복원 {len(unresolved)}개" if unresolved else "")
                + (f" · 신뢰도 {confidence:.2f}" if confidence is not None else "")
            ),
            panels=tuple(panels),
        )

    # ── 차단 경로 ────────────────────────────────────────────────────

    def add_blocked(self, *, stage_id: str, reason: str, detail: str = "") -> None:
        """경계를 넘지 못하고 신뢰 구역 안에서 답한 경우."""
        self.crossed_boundary = False
        self.put(
            stage_id,
            status="blocked",
            summary=reason,
            panels=(
                TracePanel(
                    panel_id=f"{stage_id}-blocked",
                    label="비식별 처리 완료",
                    kind="note",
                    text=detail
                    or (
                        f"{reason}. 경계 밖 Agent 를 부르지 않고 신뢰 구역 안에서 답했다 — "
                        "실패의 모든 경로가 '더 안전한 쪽'으로 귀결된다."
                    ),
                ),
            ),
        )


# ══════════════════════════════════════════════════════════════════════
# 보관
# ══════════════════════════════════════════════════════════════════════


class TraceStore:
    """TTL 이 있는 메모리 보관소.

    ⚠️ 파일로 쓰지 않는다. 트레이스는 매핑표의 일부(답변에 등장한 기호)를
       품고 있고, 그것이 디스크에 남으면 `Mapping` 이 직렬화 불가인 이유
       (BR-G-09) 를 우회하는 셈이 된다.
    """

    def __init__(self, *, ttl_seconds: int = TRACE_TTL_SECONDS, max_items: int = MAX_TRACES) -> None:
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._items: dict[str, tuple[float, GatekeeperTrace]] = {}

    def put(self, trace: GatekeeperTrace) -> str:
        self.sweep()
        if len(self._items) >= self.max_items:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)
        self._items[trace.trace_id] = (time.monotonic(), trace)
        return trace.trace_id

    def get(self, trace_id: str) -> GatekeeperTrace | None:
        self.sweep()
        hit = self._items.get(trace_id)
        return hit[1] if hit else None

    def sweep(self) -> int:
        now = time.monotonic()
        dead = [k for k, (at, _) in self._items.items() if now - at > self.ttl]
        for k in dead:
            self._items.pop(k, None)
        return len(dead)

    def __len__(self) -> int:
        return len(self._items)


# ══════════════════════════════════════════════════════════════════════
# 라벨 (화면 문구를 한 곳에 모은다)
# ══════════════════════════════════════════════════════════════════════

_STAGE_LABELS = {
    "schema": "① 스키마",
    "vocab": "② 어휘",
    "range": "③ 범위",
    "banned": "④ 금칙어",
    "ngram": "⑤ 원문대조",
    "size": "⑥ 크기",
}


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _tier_or_dash(tier: Tier | None, decision: TierDecision | None) -> str:
    if tier is not None:
        return tier.label_ko
    if decision is None:
        return "—"
    if decision.exaone_skipped:
        return "생략 (규칙이 이미 기밀)"
    if decision.exaone_failed:
        return "실패 → 기밀 간주"
    return "—"


def _tier_meaning(tier: Tier) -> str:
    return {
        Tier.SECRET: (
            "기밀 — 원문이 경계를 넘지 못한다. 어휘 사전 안의 값만으로 구조를 조립해 "
            "보낸다. 자유 문장이 들어갈 자리가 페이로드에 없다."
        ),
        Tier.INTERNAL: (
            "사내 — 문장은 유지하고 식별자만 기호로 바꾼다. 기술 용어는 치환하지 않는다 "
            "(치환하면 답변 품질이 무너진다)."
        ),
        Tier.OPEN: "공개 — 변환하지 않는다. 명시적 공개 표기가 있는 문서만 이 등급이 된다.",
    }[tier]


def _representation_label(rep: Representation) -> str:
    return {
        Representation.STRUCTURED: "구조 추출 (원문 0개)",
        Representation.PSEUDONYMIZED: "가명화 (식별자 치환)",
        Representation.VERBATIM: "원문 그대로",
    }.get(rep, str(rep))


def _representation_caption(rep: Representation) -> str:
    return {
        Representation.STRUCTURED: (
            "**구조화된 JSON 이다. 자연어 문장이 아니다.** 값은 전부 `vocab.json` 의 "
            "닫힌 어휘에서 왔고, 코드가 스키마를 순회하며 조립했다 — 모델의 출력을 "
            "순회한 것이 아니다. 그래서 '검사를 잊어서 새는' 경로가 없다."
        ),
        Representation.PSEUDONYMIZED: (
            "**자연어 문장이 그대로 있는 JSON 이다.** 사내 등급은 문장을 유지하고 "
            "식별자만 `<PERSON_1>` 같은 기호로 바꾼다. 그래서 '원문 문장 없음'을 "
            "약속하지 않는다 — 약속할 수 있는 것은 금칙어와 치환이 보장하는 범주뿐이다."
        ),
        Representation.VERBATIM: (
            "**원문 그대로다.** 공개 등급의 정의가 그것이다. 이 등급은 경로와 헤더에 "
            "명시적 공개 표기가 있는 문서에만 붙는다."
        ),
    }.get(rep, "")


def _representation_how(rep: Representation) -> str:
    return {
        Representation.STRUCTURED: (
            "EXAONE 에게 **슬롯을 채우게** 했다. JSON 전체를 만들게 하지 않았다. "
            "그다음 코드가 스키마의 슬롯 목록을 순회하며 값이 있는 것만 담았다. "
            "모델이 만든 미등록 키는 가장 이른 지점에서 버려진다."
        ),
        Representation.PSEUDONYMIZED: (
            "`pseudonyms.json` 의 리터럴을 긴 것부터 기호로 치환했다. "
            "`technical_terms` 에 있는 기술 용어는 치환하지 않는다."
        ),
        Representation.VERBATIM: "변환하지 않았다.",
    }.get(rep, "")


"""재수화 — 기호를 실제 이름으로 되돌린다 (FR-13, BR-G-10, BR-P-04).

관문 ③이다. Agent 응답은 `COMP_A`·`<PROJ_1>` 같은 기호로 되어 있고,
실제 이름은 **신뢰 구역 안에서만** 되돌린다.

⚠️ 순수 문자열 치환이다. LLM 을 쓰지 않는다.
   재수화에 모델을 끼우면 매핑 테이블이 다시 경계를 넘어야 한다.

⚠️ 이 모듈은 L1(지원)이다. `Mapping` 과 문자열만 다루고 I/O 가 없다.

──────────────────────────────────────────────────────────────────────
매핑에 없는 기호는 치환하지 않는다 (BR-G-10)
──────────────────────────────────────────────────────────────────────

Agent 응답을 신뢰하지 않는다. 응답에 `<SYS_9>` 가 있고 매핑에 그 키가 없으면
**기호를 그대로 남긴다.** 프롬프트 인젝션으로 임의 문자열을 치환시키는 것을
막는다. 치환되지 않은 기호는 `unresolved_refs` 로 올려 UI 가 경고를 띄운다.

"매핑에 없으니 적당히 지운다"도 하지 않는다 — 지우면 사용자가 문장이
불완전해진 것을 알 수 없다. 남겨서 보이게 한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from mesh.schemas import Mapping

#: 응답에 나타날 수 있는 기호 두 형태.
#:   `<PROJ_1>`  가명화 placeholder
#:   `COMP_A`    ref 라벨 (단어 경계로 감싼다 — 일반 영문 단어를 잡지 않게)
_PLACEHOLDER_RE = re.compile(r"<[A-Z]{2,8}_\d{1,3}>")
_REF_LABEL_RE = re.compile(r"\b[A-Z]{2,8}_[A-Z]\b")

#: 재수화 대상 문자열이 담기는 응답 필드. 중첩 구조도 재귀 순회한다.
MAX_DEPTH = 8


def symbols_in(text: str) -> tuple[str, ...]:
    """텍스트에 남아 있는 기호 전체."""
    return tuple(sorted(set(_PLACEHOLDER_RE.findall(text)) | set(_REF_LABEL_RE.findall(text))))


def rehydrate_text(text: str, mapping: Mapping) -> tuple[str, tuple[str, ...]]:
    """치환한 문자열과 **치환되지 않은 기호** 목록을 반환한다.

    ⚠️ 긴 키부터 치환한다 (BR-P-04). `<SYS_1>` 과 `<SYS_11>` 이 함께 있을 때
       짧은 키를 먼저 치환하면 `<SYS_11>` 이 `실제이름1>` 로 망가진다.
    """
    out = text
    for key in mapping.keys_longest_first():
        value = mapping.table[key]
        if key in out:
            out = out.replace(key, value)
    return out, symbols_in(out)


def rehydrate_obj(obj: object, mapping: Mapping, _depth: int = 0) -> tuple[object, tuple[str, ...]]:
    """dict/list/str 을 재귀적으로 재수화한다.

    Agent 응답의 `answer` 는 스키마마다 형태가 다르다
    (`{"conflict": bool, "reason": str, "mitigations": [str]}`).
    필드 이름을 하드코딩하면 새 task 를 추가할 때 재수화가 조용히 빠진다.
    """
    if _depth > MAX_DEPTH:
        return obj, ()
    if isinstance(obj, str):
        return rehydrate_text(obj, mapping)
    if isinstance(obj, dict):
        out: dict[object, object] = {}
        unresolved: set[str] = set()
        for k, v in obj.items():
            rv, u = rehydrate_obj(v, mapping, _depth + 1)
            out[k] = rv
            unresolved |= set(u)
        return out, tuple(sorted(unresolved))
    if isinstance(obj, list | tuple):
        items: list[object] = []
        unresolved = set()
        for v in obj:
            rv, u = rehydrate_obj(v, mapping, _depth + 1)
            items.append(rv)
            unresolved |= set(u)
        return items, tuple(sorted(unresolved))
    return obj, ()


def rehydrate_response(answer: dict, mapping: Mapping) -> tuple[dict, tuple[str, ...]]:
    """`AgentResponse.answer` 전체를 재수화한다."""
    out, unresolved = rehydrate_obj(answer, mapping)
    if not isinstance(out, dict):  # pragma: no cover — 입력이 dict 다
        return {}, unresolved
    return out, unresolved


def answer_to_text(answer: dict) -> str:
    """구조 응답을 사람이 읽을 문장으로 편다.

    Agent 는 `answer_format` 에 따라 dict 를 반환한다. UI 는 문장을 원한다.
    **여기서 LLM 을 쓰지 않는다** — 한 번 더 모델을 끼우면 재수화된 실제 이름이
    또 어딘가로 나갈 경로가 생긴다.

    키 이름을 그대로 쓰지 않고 한국어 라벨로 바꾸되, 매핑에 없는 키는
    키 이름을 노출한다 (숨기면 답변이 사라진 것처럼 보인다).
    """
    labels = {
        "conflict": "충돌 여부",
        "divergent": "관찰된 차이",
        "reason": "이유",
        "rationale": "근거",
        "technique": "기법",
        "mitigations": "대응 방안",
        "tradeoffs": "트레이드오프",
        "text": "",
    }
    lines: list[str] = []
    for key, value in answer.items():
        label = labels.get(key, key)
        rendered = _render_value(value)
        if not rendered:
            continue
        lines.append(f"{label}: {rendered}" if label else rendered)
    return "\n".join(lines)


def _render_value(value: object) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, list | tuple):
        parts = [_render_value(v) for v in value]
        return "\n" + "\n".join(f"  - {p}" for p in parts if p)
    if value is None:
        return ""
    return str(value).strip()


def unresolved_warning(unresolved: Sequence[str]) -> str:
    """UI 경고 문구. 빈 목록이면 빈 문자열."""
    if not unresolved:
        return ""
    return (
        f"Agent 응답에 매핑되지 않은 참조 기호 {len(unresolved)}개가 있다: "
        f"{', '.join(unresolved)}. 치환하지 않고 그대로 남겼다 (BR-G-10)."
    )

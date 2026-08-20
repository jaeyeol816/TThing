"""가명화 — 사내(INTERNAL) 등급의 표현 변환 (BR-P-01 ~ BR-P-03).

기밀 등급은 기술 내용 자체가 비밀이라 구조 추출로 간다. 사내 등급은
**기술 내용이 남아도 괜찮고 식별자만 문제**다. 그래서 치환으로 충분하다.

    "atlas_ml 파이프라인은 RandomOverSampler(sampling_strategy=0.5) 로 …"
      -> "<PROJ_1> 파이프라인은 RandomOverSampler(sampling_strategy=0.5) 로 …"

──────────────────────────────────────────────────────────────────────
기술 용어를 치환하면 답변이 무너진다
──────────────────────────────────────────────────────────────────────

    나쁜 예   <TERM_1>(<TERM_2>=0.5) 로 <TERM_3> 를 <TERM_4>
    좋은 예   RandomOverSampler(sampling_strategy=0.5) 로 소수 클래스를 오버샘플링

`<TERM_1>` 이 오버샘플링인지 Claude 가 알 수 없다. `technical_terms` 는
**명시적 허용 목록**(frozenset)이고, 목록에 없는 대문자 고유명사는
치환하는 쪽으로 기울인다 — 애매하면 더 안전한 쪽 (BR-P-01).

──────────────────────────────────────────────────────────────────────
왜 `banned.json` 과 반대 성격인가
──────────────────────────────────────────────────────────────────────

    banned.json       걸리면 SECRET 상향 + 전송 차단   고객사명·계약번호·금액
    pseudonyms.json   치환하고 경계를 넘게 허용        프로젝트명·시스템명·인명

두 목록을 섞으면 사내 문서가 전부 SECRET 으로 오분류되고 **가명화 경로가
아예 실행되지 않는다.** v1.0.0 에서 실제로 발생해 정확도가 55% 로 떨어졌다.
`DataBundle._check_lists_are_disjoint()` 가 로드 시점에 거부한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mesh.config import get_logger, log_extra
from mesh.exceptions import ExaoneUnavailable
from mesh.schemas import Mapping, PseudonymTargets

if TYPE_CHECKING:  # pragma: no cover
    from mesh.llm.exaone import ExaoneClient

log = get_logger("pseudonymizer")

#: 사내 등급에서 한 문서에 담을 본문 길이 상한.
#: 가명화는 원문 대부분을 유지하므로 크기 상한(검증 6단계)에 걸리기 쉽다.
EXCERPT_MAX_CHARS = 3000

# ══════════════════════════════════════════════════════════════════════
# 보수적 마스킹 — 목록에 없어도 "식별자처럼 생긴 것"을 함께 가린다 (A + B)
# ══════════════════════════════════════════════════════════════════════
#
# 리터럴 목록 치환만으로는 목록에 없는 새 식별자·날짜·수치가 그대로 나간다.
# 아래 정규식은 **모양으로** 그런 토큰을 잡아 함께 가린다. 원칙:
#
#   - 기술 용어·수식 파라미터는 건드리지 않는다. 그래서 수치는 **단위가 붙은
#     것만** 잡는다. `sampling_strategy=0.5` 의 `0.5` 는 단위가 없어 남는다.
#   - 버전은 **점이 있는** 것만 (`v3.2` O, `v3` X — `v3` 은 단어 일부일 수 있다).
#   - 전부 매핑에 넣어 **가역적**이다. 답변으로 돌아오면 신뢰 구역에서 되돌린다.
#   - 번호는 텍스트 순서와 무관하게(길이·사전순) 배정해 결정론을 지킨다.
#
# ⚠️ placeholder 접두사(DATE/VER/NUM/CODE)는 pseudonyms.json 의 접두사
#    (PROJ/SYS/PERSON …)와 겹치지 않아야 매핑이 충돌하지 않는다.
_HEURISTIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 날짜 (B) — 특정 시점은 일정·계획을 노출한다
    ("DATE", re.compile(r"\d{4}-\d{2}-\d{2}")),
    ("DATE", re.compile(r"\d{4}\.\d{1,2}\.\d{1,2}")),
    ("DATE", re.compile(r"\d{4}년\s?\d{1,2}월(?:\s?\d{1,2}일)?")),
    ("DATE", re.compile(r"[1-4]분기")),
    # 버전 (A) — 점이 있는 것만
    ("VER", re.compile(r"\bv\d+(?:\.\d+)+\b")),
    # 코드/식별자 (A) — 대문자약어-숫자
    ("CODE", re.compile(r"\b[A-Z]{2,6}-\d{2,}\b")),
    # 수치 + 단위 (B) — 단위가 붙은 것만. 파라미터(0.5, 42)는 단위가 없어 제외
    ("NUM", re.compile(r"\d+(?:\.\d+)?\s?(?:억원|만원|시간|개월|주일|일|분)")),
)


def _mask_by_regex(
    bodies: list[str], targets: PseudonymTargets
) -> tuple[dict[str, str], list[str]]:
    """정규식으로 잡은 토큰을 placeholder 로 치환한다 (A + B).

    반환: `(placeholder -> 원문 매핑, 치환된 본문들)`.

    결정론: 카테고리별로 원문을 **길이 내림차순·사전순**으로 정렬해 번호를
    매긴다 — 텍스트 순서가 바뀌어도 같은 매핑이 나온다.
    """
    # 카테고리 -> 등장한 원문 집합
    found: dict[str, set[str]] = {}
    for prefix, pattern in _HEURISTIC_PATTERNS:
        for body in bodies:
            for m in pattern.findall(body):
                match = m if isinstance(m, str) else m[0]
                match = match.strip()
                if not match or targets.is_technical(match):
                    continue
                found.setdefault(prefix, set()).add(match)

    table: dict[str, str] = {}
    assigned: dict[str, str] = {}  # 원문 -> placeholder
    for prefix in sorted(found):
        ordered = sorted(found[prefix], key=lambda s: (-len(s), s))
        for i, literal in enumerate(ordered, start=1):
            placeholder = f"<{prefix}_{i}>"
            assigned[literal] = placeholder
            table[placeholder] = literal

    # 긴 원문부터 치환 — 짧은 것이 긴 것의 일부를 먼저 깨는 것을 막는다.
    out: list[str] = []
    for body in bodies:
        for literal in sorted(assigned, key=len, reverse=True):
            body = body.replace(literal, assigned[literal])
        out.append(body)
    return table, out


@dataclass(frozen=True, slots=True)
class PseudonymResult:
    """치환 결과.

    `identifiers` 는 **치환 대상 전체**다 (실제로 치환된 것만이 아니다).
    검증 5단계가 이 목록으로 "식별자를 포함한 원문 n-gram" 을 만들어
    **가명화가 놓친 표기 변형**을 잡는다. 실제 치환분만 넘기면
    놓친 것을 검사할 방법이 사라진다 (BR-P-03).
    """

    texts: tuple[str, ...]
    mapping: Mapping
    identifiers: tuple[str, ...]
    substituted: tuple[str, ...]
    truncated: bool


def apply(texts: Sequence[str], targets: PseudonymTargets) -> PseudonymResult:
    """식별자만 placeholder 로 치환한다.

    ⚠️ **긴 리터럴부터 치환한다** (`all_literals()` 가 그 순서로 반환).
       `atlas-ml` 을 먼저 치환하면 `atlas-ml-core` 가 `<PROJ_1>-core` 로 망가진다.
       같은 이유로 재수화도 긴 키부터다 (BR-P-04).

    ⚠️ placeholder 번호는 **리터럴 길이 순으로 먼저 배정**하고 그 다음에 치환한다.
       문서 순서에 따라 번호가 흔들리면 한 질의 안에서 일관성이 깨지고
       Claude 가 관계를 추론하지 못한다 (BR-P-02, PB-6).
    """
    pairs = targets.all_literals()

    counters: dict[str, int] = {}
    assigned: dict[str, str] = {}  # 리터럴 -> placeholder
    table: dict[str, str] = {}  # placeholder -> 리터럴

    for prefix, literal in pairs:
        if literal in assigned:
            continue
        if targets.is_technical(literal):
            # 방어: 치환 대상과 기술 용어에 동시에 등장한 항목.
            # DataBundle 이 로드 시점에 거부하지만, 직접 호출되는 경로도 막는다.
            log.warning(
                "치환 대상이 기술 용어 목록에도 있다 — 치환하지 않는다",
                extra=log_extra(prefix=prefix),
            )
            continue
        if not any(literal in t for t in texts):
            continue  # 등장하지 않는 대상에 번호를 낭비하지 않는다
        counters[prefix] = counters.get(prefix, 0) + 1
        placeholder = f"<{prefix}_{counters[prefix]}>"
        assigned[literal] = placeholder
        table[placeholder] = literal

    truncated = False
    out: list[str] = []
    for text in texts:
        body = text[:EXCERPT_MAX_CHARS]
        truncated = truncated or len(text) > EXCERPT_MAX_CHARS
        for _, literal in pairs:
            placeholder = assigned.get(literal)
            if placeholder:
                body = body.replace(literal, placeholder)
        out.append(body)

    # ── 휴리스틱 패스 (A + B) — 목록에 없어도 식별자·날짜·수치를 가린다 ──
    #    리터럴 치환 뒤에 돈다. 이미 <PROJ_1> 같은 placeholder 는 정규식이
    #    잡지 않으므로 이중 치환이 없다.
    regex_table, out = _mask_by_regex(out, targets)
    table.update(regex_table)

    return PseudonymResult(
        texts=tuple(out),
        mapping=Mapping(table=table),
        # identifiers 는 검증 5단계의 안전망이다 — 리터럴 목록 전체 + 정규식이
        # 새로 가린 원문. 놓친 표기 변형을 n-gram 으로 잡는 데 쓴다 (BR-P-03).
        identifiers=tuple(lit for _, lit in pairs) + tuple(regex_table.values()),
        # substituted 는 **리터럴 목록에서** 실제 배정된 것만. (정규식/EXAONE 은 별개)
        substituted=tuple(assigned),
        truncated=truncated,
    )


# ══════════════════════════════════════════════════════════════════════
# EXAONE 기반 확장 마스킹 (C) — 신뢰 구역 안에서 span 을 제안받는다
# ══════════════════════════════════════════════════════════════════════
#
# 정규식(A+B)은 모양이 뚜렷한 것만 잡는다. EXAONE 은 문맥을 읽고 "이건
# 사람·고객사·제품·코드명 같다" 는 span 을 **제안**한다. 무엇이 실제로 가려질지는
# 코드가 정한다 — 제안 span 은 원문의 부분문자열이어야 하고(가역), 기술 용어가
# 아니어야 하며, 길이 제한을 넘지 않아야 한다. 이 호출은 **신뢰 구역(EXAONE)**
# 안에서만 일어나므로 원문이 경계를 넘지 않는다.
#
# best-effort 다. EXAONE 이 없거나 실패하면 정규식까지만 적용한 결과를 그대로 쓴다.

_EXAONE_SPAN_SYSTEM = (
    "You are a conservative de-identifier working INSIDE a trusted zone.\n"
    "The text may already have some identifiers replaced with <TOKEN> placeholders.\n"
    "Find ADDITIONAL verbatim substrings that should be masked because they identify a\n"
    "specific person, customer, organization, project, system, product, or code name,\n"
    "or reveal an unusual specific identifier.\n"
    "\n"
    'Output JSON only: {"spans": ["...", ...]}.\n'
    "Rules:\n"
    "  - Each span MUST be copied character-for-character from the text.\n"
    "  - Do NOT include general or technical terms, common words, verbs, units,\n"
    "    whole sentences, or anything already inside <>.\n"
    "  - Prefer proper nouns and code-like names. When unsure, include it (conservative).\n"
    "  - Return an empty list if nothing else needs masking."
)

#: EXAONE 제안 span 의 길이 상한. 이보다 길면 문장을 통째로 가리려는 것이라 버린다.
_EXAONE_SPAN_MAX_CHARS = 40
#: 한 번에 받는 span 개수 상한.
_EXAONE_SPAN_MAX = 20


async def _exaone_spans(bodies: Sequence[str], exaone: ExaoneClient) -> list[str]:
    """EXAONE 이 제안하는 추가 마스킹 span 목록. 실패하면 예외를 올린다."""
    joined = "\n\n".join(f"[{i + 1}] {b}" for i, b in enumerate(bodies))
    raw = await exaone.complete_json(
        _EXAONE_SPAN_SYSTEM, f"TEXT:\n{joined}", name="pseudonym_spans", max_tokens=400
    )
    spans = raw.get("spans") if isinstance(raw, dict) else None
    if not isinstance(spans, list):
        return []
    return [s for s in spans if isinstance(s, str)]


async def apply_conservative(
    texts: Sequence[str], targets: PseudonymTargets, exaone: ExaoneClient
) -> PseudonymResult:
    """리터럴 + 정규식(A+B) + EXAONE span(C) 을 모두 적용한 보수적 가명화.

    `apply()` 로 A+B 까지 처리한 뒤, EXAONE 이 제안한 span 을 코드가 검증해
    추가로 가린다. EXAONE 이 없거나 실패하면 `apply()` 결과를 그대로 돌려준다.
    """
    base = apply(texts, targets)
    try:
        proposals = await _exaone_spans(base.texts, exaone)
    except ExaoneUnavailable as e:
        log.info("EXAONE span 제안 건너뜀 — 정규식까지만 적용", extra=log_extra(reason=str(e)))
        return base
    except Exception:  # noqa: BLE001 — best-effort. 실패해도 A+B 결과는 유효하다
        log.warning("EXAONE span 제안 중 예상치 못한 오류 — 정규식까지만 적용")
        return base

    if not proposals:
        return base

    # ── 제안 span 검증: 무엇이 경계를 넘을 수 있는지는 코드가 정한다 ──
    bodies = list(base.texts)
    joined = "\n".join(bodies)
    valid: set[str] = set()
    for span in proposals:
        s = span.strip()
        if not (2 <= len(s) <= _EXAONE_SPAN_MAX_CHARS):
            continue
        if "<" in s or ">" in s:  # 이미 치환된 placeholder 는 건드리지 않는다
            continue
        if targets.is_technical(s):  # 기술 용어는 가리지 않는다 (답변이 무너진다)
            continue
        if s not in joined:  # 원문의 부분문자열이어야 가역적이다
            continue
        valid.add(s)
        if len(valid) >= _EXAONE_SPAN_MAX:
            break

    if not valid:
        return base

    # 결정론적 번호 배정 (길이 내림차순·사전순) + 긴 것부터 치환.
    table = dict(base.mapping.table)
    assigned: dict[str, str] = {}
    for i, literal in enumerate(sorted(valid, key=lambda s: (-len(s), s)), start=1):
        placeholder = f"<CTX_{i}>"
        assigned[literal] = placeholder
        table[placeholder] = literal

    out: list[str] = []
    for body in bodies:
        for literal in sorted(assigned, key=len, reverse=True):
            body = body.replace(literal, assigned[literal])
        out.append(body)

    log.info("EXAONE 확장 마스킹 적용 (C)", extra=log_extra(spans=len(assigned)))
    return PseudonymResult(
        texts=tuple(out),
        mapping=Mapping(table=table),
        identifiers=base.identifiers + tuple(assigned),
        substituted=base.substituted,
        truncated=base.truncated,
    )


def merge_mappings(*mappings: Mapping) -> Mapping:
    """ref 매핑과 placeholder 매핑을 합친다.

    사내 등급 페이로드는 둘 다 갖는다 — `COMP_A` 는 근거 문서를 가리키고
    `<PROJ_1>` 은 본문 안의 프로젝트명을 가리킨다. 재수화는 한 테이블로 처리한다.

    키가 충돌하면 예외를 던진다. 조용히 덮어쓰면 재수화가 틀린 이름을 남긴다.
    """
    out: dict[str, str] = {}
    for m in mappings:
        for k, v in m.table.items():
            if k in out and out[k] != v:
                raise ValueError(f"매핑 키 충돌: {k!r}")
            out[k] = v
    return Mapping(table=out)

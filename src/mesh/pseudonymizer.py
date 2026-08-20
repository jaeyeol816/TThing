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

    return PseudonymResult(
        texts=tuple(out),
        mapping=Mapping(table=table),
        identifiers=tuple(lit for _, lit in pairs),
        substituted=tuple(assigned),
        truncated=truncated,
    )


# ══════════════════════════════════════════════════════════════════════
# EXAONE 기반 확장 마스킹 — 신뢰 구역 안에서 span 을 제안받는다
# ══════════════════════════════════════════════════════════════════════
#
# EXAONE 이 원문을 읽고 경계 밖으로 나가면 안 되는 모든 span 을 제안한다.
# 코드는 제안을 검증(원문 부분문자열·길이 제한)하고 <CTX_n> 으로 치환한다.
# 무엇이 경계를 넘을 수 있는지는 코드가 최종 확정한다.
#
# best-effort 다. EXAONE 이 없거나 실패하면 리터럴 치환(`apply()`)까지만 적용.

_EXAONE_SPAN_SYSTEM = """\
You are a de-identifier. The text is a company-internal document about to cross a security \
boundary. Your job: find every verbatim substring that could reveal internal information, \
and return it as a list so the caller can replace it with a placeholder.

MASK ALL OF THESE (be aggressive — when in doubt, include it):
  1. Person names, team names, org names, customer/partner names
  2. Project names, product names, system names, service names
  3. Internal code names, labels, experiment names
  4. File paths, directory names, repo names, table names, column names
  5. camelCase identifiers  (e.g. validAuc, sessionLogs, tokenLifetime)
  6. snake_case identifiers (e.g. valid_auc, session_logs, n_estimators)
  7. Numeric values that reveal specifics: measurements, counts, thresholds, \
hyperparameters, metric values, ratios, ranges (e.g. 0.8923, 388721, 400, 0.05)
  8. Dates, version strings, build numbers (e.g. 2026-08-19, v3.2, round 275)
  9. Anything already inside <PLACEHOLDER> — skip those, they are already masked

DO NOT MASK:
  - Well-known open-source algorithm/framework names where masking loses meaning
    (e.g. RandomOverSampler, gradient boosting, XGBoost, LightGBM, BERT, CNN, OAuth, TLS, JWT, GPU)
  - Generic Korean/English words: verbs, adjectives, common nouns (모델, 데이터, 학습, 결과 etc.)
  - Do NOT leave out parameter names and their values — those ARE internal configuration

Output ONLY a JSON object, no prose:
{"spans": ["exact substring 1", "exact substring 2", ...]}
Each string MUST appear verbatim in the input text. Return [] if nothing to mask.\
"""

#: EXAONE 제안 span 의 길이 상한.
_EXAONE_SPAN_MAX_CHARS = 80
#: 한 번에 받는 span 개수 상한.
_EXAONE_SPAN_MAX = 40


async def _exaone_spans(bodies: Sequence[str], exaone: ExaoneClient) -> list[str]:
    """EXAONE 이 제안하는 추가 마스킹 span 목록. 실패하면 예외를 올린다."""
    joined = "\n\n".join(f"[{i + 1}] {b}" for i, b in enumerate(bodies))
    raw = await exaone.complete_json(
        _EXAONE_SPAN_SYSTEM, f"TEXT:\n{joined}", name="pseudonym_spans", max_tokens=800
    )
    spans = raw.get("spans") if isinstance(raw, dict) else None
    if not isinstance(spans, list):
        return []
    return [s for s in spans if isinstance(s, str)]


async def apply_conservative(
    texts: Sequence[str], targets: PseudonymTargets, exaone: ExaoneClient
) -> PseudonymResult:
    """리터럴 치환 후 EXAONE 이 span 을 제안해 추가로 가린다.

    리터럴 목록(pseudonyms.json)으로 먼저 치환하고, EXAONE 에게 "나머지 중에
    가려야 할 것"을 물어본다. camelCase/snake_case 식별자, 수치, 날짜, 경로 등
    정규식으로 잡기 어려운 것까지 문맥 기반으로 가린다.

    EXAONE 이 없거나 실패하면 리터럴 치환 결과를 그대로 돌려준다 (best-effort).
    """
    base = apply(texts, targets)
    try:
        proposals = await _exaone_spans(base.texts, exaone)
    except ExaoneUnavailable as e:
        log.info("EXAONE span 제안 건너뜀 — 리터럴 치환까지만 적용", extra=log_extra(reason=str(e)))
        return base
    except Exception:  # noqa: BLE001 — best-effort. 실패해도 리터럴 결과는 유효하다
        log.warning("EXAONE span 제안 중 예상치 못한 오류 — 리터럴 치환까지만 적용")
        return base

    if not proposals:
        return base

    # ── 제안 span 검증: 무엇이 경계를 넘을 수 있는지는 코드가 최종 확정한다 ──
    bodies = list(base.texts)
    joined = "\n".join(bodies)
    valid: set[str] = set()
    for span in proposals:
        s = span.strip()
        if not (2 <= len(s) <= _EXAONE_SPAN_MAX_CHARS):
            continue
        if "<" in s or ">" in s:  # 이미 치환된 placeholder 는 건드리지 않는다
            continue
        if targets.is_technical(s):  # technical_terms 는 가리지 않는다 (답변이 무너진다)
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

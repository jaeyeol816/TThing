"""검증 6단계 — 순수 함수 (BR-V-00 ~ BR-V-06).

⚠️ **이 모듈에는 I/O·전역 상태·설정 참조가 없다.**
   U5 Lambda 가 이 파일과 `vocab.json` 을 그대로 번들해 1~4·6단계를 재실행한다
   (BR-V-07). import 가 늘어나면 번들이 깨지고 재검증 겹이 사라진다.

   허용 import: 표준 라이브러리 + `mesh.schemas` (타입 계약).
   `mesh.config` 를 import 하지 않는다 — 설정을 읽으면 순수성이 깨지고
   Lambda 가 환경변수에 의존하게 된다. 필요한 값은 모두 인자로 받는다.

   `tests/unit/test_validator.py::test_validator_is_pure` 가 ast 로 강제한다.

──────────────────────────────────────────────────────────────────────
검사는 등급에 따라 다르다 (중요)
──────────────────────────────────────────────────────────────────────

데모에서 반드시 나오는 질문: "사내 등급은 원문이 나가는데 왜 6/6 통과인가?"

등급의 정의가 다르므로 검사도 다르다.

  | 단계 | STRUCTURED (기밀)    | PSEUDONYMIZED (사내)        | VERBATIM (공개) |
  |------|----------------------|-----------------------------|-----------------|
  | 1 스키마 | 슬롯 ∪ 구조키만    | + `excerpts` 허용           | + `excerpts`    |
  | 2 어휘   | **모든 문자열**    | `excerpts` 내부 제외        | 동일            |
  | 3 범위   | 동일               | 동일                        | 동일            |
  | 4 금칙어 | 동일               | 동일 (여기가 사내의 하한선) | 동일            |
  | 5 원문   | 원문 5-gram 0건    | **식별자 포함 5-gram 0건**  | 검사 불가(정의) |
  | 6 크기   | 2KB                | 2KB x 8                     | 2KB x 8         |

즉 `PSEUDONYMIZED` 에서 검사하는 것은 "원문이 나갔는가"가 아니라
**"식별자가 나갔는가"** 다 (BR-P-03). `VERBATIM` 은 원문 전송이 등급의 정의라서
5단계를 적용할 수 없고, 그 사실을 `CheckResult.detail` 에 명시한다 —
조용히 통과시키지 않는다.

`STRUCTURED` 에서만 `excerpts` 키를 금지하는 것이 기밀 등급의 "원문 0개"를
구조적으로 보장한다. 텍스트를 담을 키가 화이트리스트에 없다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Sequence

from mesh.schemas import (
    EXCLUDED_CATEGORIES_DEFAULT,
    STRUCTURAL_KEYS,
    BannedTerms,
    CheckResult,
    Representation,
    SlotDef,
    TaskSchema,
    ValidationResult,
    Vocabulary,
)

# ══════════════════════════════════════════════════════════════════════
# 상수
# ══════════════════════════════════════════════════════════════════════

#: 가명화·공개 등급에서만 허용되는 텍스트 키. `STRUCTURED` 에서는 금지된다.
EXCERPTS_KEY = "excerpts"

#: 텍스트를 담는 표현. 크기 상한과 어휘 검사가 달라진다.
TEXT_REPRESENTATIONS: frozenset[Representation] = frozenset(
    {Representation.PSEUDONYMIZED, Representation.VERBATIM}
)

#: 텍스트 표현의 크기 배수. 2KB 는 구조 페이로드용 상한이다
#: ("자유 텍스트가 섞였다는 신호"). 가명화 본문에는 그 논리가 적용되지 않는다.
TEXT_SIZE_FACTOR = 8

#: 자동 생성 ref 라벨 (`REQ_A`). 실제 이름과 무관하므로 어휘 사전에 없다.
REF_LABEL_RE = re.compile(r"^[A-Z]{2,8}_[A-Z]$")

#: 가명화 placeholder (`<PROJ_1>`).
PLACEHOLDER_RE = re.compile(r"^<[A-Z]{2,8}_\d{1,3}>$")

#: 문장 분리 — `verbatim_sentence_count` 측정용.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?。\n]+")

_WS_RE = re.compile(r"\s+")

ALL_STAGES: tuple[str, ...] = ("schema", "vocab", "range", "banned", "ngram", "size")


# ══════════════════════════════════════════════════════════════════════
# 텍스트 정규화와 n-gram — 로컬과 Lambda 가 같은 값을 계산해야 한다
# ══════════════════════════════════════════════════════════════════════


def normalize_text(s: str) -> str:
    """5-gram 대조의 전처리: 공백 축약 + 소문자화.

    공백만 바꿔 우회하는 것을 막는다 (BR-V-05).
    이 함수의 정의가 바뀌면 로컬과 Lambda 의 판정이 갈린다 — 함부로 고치지 않는다.
    """
    return _WS_RE.sub(" ", s).strip().lower()


def ngram_set(text: str, n: int) -> frozenset[str]:
    """공백 토큰 기준 n-gram 집합.

    한국어는 형태소 분리 없이 공백 토큰을 쓴다. 5-gram 이면 우연 일치가 거의 없다.

    ⚠️ **토큰이 n 개보다 적으면 전체를 하나의 gram 으로 취급한다.**
       그러지 않으면 짧은 원문이 검사를 통째로 빠져나간다 —
       "세션 바인딩 필수" 세 단어짜리 원문이 5-gram 으로는 빈 집합이 된다.

    한계 (문서화): 모델이 원문을 **의역**하면 n-gram 으로 잡히지 않는다.
    그건 어휘 사전(2단계)이 막는 영역이고 n-gram 은 축자 인용만 잡는다.
    두 겹이 함께 필요한 이유다.
    """
    if n < 1:
        raise ValueError(f"n 은 1 이상이어야 한다: {n}")
    toks = normalize_text(text).split()
    if not toks:
        return frozenset()
    if len(toks) <= n:
        return frozenset({" ".join(toks)})
    return frozenset(" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1))


def sentences(text: str, *, min_tokens: int = 4) -> tuple[str, ...]:
    """정규화된 문장 목록. 너무 짧은 조각은 우연 일치가 많아 제외한다."""
    out = []
    for raw in _SENTENCE_SPLIT_RE.split(text):
        norm = normalize_text(raw)
        if len(norm.split()) >= min_tokens:
            out.append(norm)
    return tuple(out)


# ══════════════════════════════════════════════════════════════════════
# 페이로드 순회
# ══════════════════════════════════════════════════════════════════════


def _walk(
    obj: object, path: str = "", *, skip_keys: frozenset[str] = frozenset()
) -> Iterator[tuple[str, object]]:
    """`(경로, 값)` 을 재귀적으로 방출. `skip_keys` 하위는 들어가지 않는다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{path}.{k}" if path else str(k)
            if k in skip_keys:
                continue
            yield child, v
            yield from _walk(v, child, skip_keys=skip_keys)
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj):
            child = f"{path}[{i}]"
            yield child, v
            yield from _walk(v, child, skip_keys=skip_keys)


def all_keys(payload: object) -> tuple[str, ...]:
    """페이로드 어디에든 등장하는 dict 키 전체."""
    out: set[str] = set()
    stack: list[object] = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            out |= {str(k) for k in cur}
            stack += list(cur.values())
        elif isinstance(cur, list | tuple):
            stack += list(cur)
    return tuple(sorted(out))


def all_strings(payload: object, *, skip_keys: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """페이로드의 문자열 **값** 전체 (키는 제외)."""
    out: list[str] = []
    for _, v in _walk(payload, skip_keys=skip_keys):
        if isinstance(v, str):
            out.append(v)
    return tuple(out)


def slot_entries(payload: object, schema: TaskSchema) -> tuple[tuple[str, str, object], ...]:
    """`(경로, 슬롯 이름, 값)` 전체.

    `facts` 안에 있든 최상위에 있든 찾는다 — 배치 위치가 아니라 값을 검사한다.

    ⚠️ **경로별로 반환한다.** 이름별 dict 로 뭉치면 `facts.REQ_A.session_binding`
       과 `facts.COMP_A.session_binding` 중 하나만 검사된다. 두 근거가 같은
       슬롯에 다른 값을 갖는 것이 바로 `constraint_conflict_check` 의 본질이라
       둘 다 검사해야 한다.
    """
    names = schema.slot_names
    found: list[tuple[str, str, object]] = []
    for path, v in _walk(payload):
        key = path.rsplit(".", 1)[-1]
        if key in names and not isinstance(v, dict | list | tuple):
            found.append((path, key, v))
    return tuple(found)


def slot_names_present(payload: object, schema: TaskSchema) -> frozenset[str]:
    """페이로드에 값이 채워진 슬롯 이름 집합."""
    return frozenset(name for _, name, _ in slot_entries(payload, schema))


def payload_text(payload: object) -> str:
    """검사용 평탄화 문자열. 키와 값을 모두 포함한다.

    `json.dumps` 를 쓰는 이유: 사람이 읽을 형태가 아니라 **빠뜨림 없는** 형태가
    필요하다. 직접 순회하면 새 컨테이너 타입에서 누락이 생긴다.

    ⚠️ **`json.dumps` 만으로는 5-gram 대조를 우회할 수 있다.**
       문자열 값 안의 실제 개행·탭이 `\\n`·`\\t` 라는 **두 글자**로 직렬화되어
       공백 정규화(`normalize_text`)를 빠져나간다.

           원문      "세션 최대 유지시간은 여덟 시간으로 제한한다"
           페이로드  "세션    최대\\n유지시간은 여덟 시간으로 제한한다"
           dumps 후  "세션 최대\\\\n유지시간은 …"   <- 토큰이 "최대\\n유지시간은" 이 되어
                                                    5-gram 이 어긋난다

       그래서 원시 문자열 값을 **이스케이프 없이** 한 번 더 이어 붙인다.
       구조 부분(키·숫자)과 본문 부분을 모두 검사 대상에 넣는 것이 목적이다.
    """
    structural = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    raw = " ".join(all_strings(payload))
    return f"{structural}\n{raw}" if raw else structural


def payload_bytes(payload: object) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# ══════════════════════════════════════════════════════════════════════
# 1단계 · 스키마 (BR-V-01)
# ══════════════════════════════════════════════════════════════════════


def allowed_keys(schema: TaskSchema, representation: Representation) -> frozenset[str]:
    """페이로드에 등장할 수 있는 키 전체.

    `answer_format` 의 키(`conflict`, `reason`, ...)는 **동결된 스키마에서** 오므로
    허용한다. 모델이 만든 것이 아니다.
    """
    keys = STRUCTURAL_KEYS | schema.slot_names | frozenset(schema.answer_format)
    if representation in TEXT_REPRESENTATIONS:
        keys = keys | {EXCERPTS_KEY}
    return keys


def check_schema(
    payload: object, schema: TaskSchema, representation: Representation
) -> CheckResult:
    """정의된 키만 있는가.

    `STRUCTURED` 에서 `excerpts` 를 금지하는 것이 기밀 등급의 핵심 보장이다 —
    **텍스트를 담을 키가 화이트리스트에 존재하지 않는다.**

    `excerpts` 는 `{ref_label: text}` 형태다. 그 키는 자동 생성 ref 라벨이므로
    스키마에 없다 — 형식 정규식으로만 허용하고, 다른 이름은 거부한다.
    본문을 ref 로 묶는 이유: Agent 가 어느 근거에서 나온 말인지 인용할 수 있어야
    재수화와 인용 검사가 성립한다.
    """
    if not isinstance(payload, dict):
        return CheckResult(
            stage="schema",
            passed=False,
            detail=f"페이로드가 객체가 아니다: {type(payload).__name__}",
        )

    ok = allowed_keys(schema, representation)
    text_ok = representation in TEXT_REPRESENTATIONS
    bad: list[str] = []
    # `facts` 는 `{ref_label: {slot: value}}` 형태다. ref 라벨은 자동 생성물이라
    # 스키마에 없으므로 형식 정규식으로 허용한다. 라벨에는 정보가 없다 (BR-E-04).

    if EXCERPTS_KEY in payload:
        if not text_ok:
            bad.append(f"{EXCERPTS_KEY} — 기밀 등급에는 텍스트 키가 없다")
        else:
            block = payload[EXCERPTS_KEY]
            if not isinstance(block, dict):
                bad.append(f"{EXCERPTS_KEY}: 객체가 아니다 ({type(block).__name__})")
            else:
                bad += [f"{EXCERPTS_KEY}.{k}" for k in block if not REF_LABEL_RE.match(str(k))]

    rest = {k: v for k, v in payload.items() if k != EXCERPTS_KEY}
    keys = all_keys(rest)
    bad += sorted(k for k in keys if k not in ok and not REF_LABEL_RE.match(k))

    if bad:
        return CheckResult(
            stage="schema",
            passed=False,
            detail=f"미등록 키 {len(bad)}개",
            offending=tuple(bad),
        )
    return CheckResult(stage="schema", passed=True, detail=f"키 {len(keys)}개 전부 등록됨")


# ══════════════════════════════════════════════════════════════════════
# 2단계 · 어휘 (BR-V-02)
# ══════════════════════════════════════════════════════════════════════


def allowed_strings(schema: TaskSchema, vocab: Vocabulary) -> frozenset[str]:
    """페이로드에 등장할 수 있는 문자열 **값** 전체.

    이 집합이 "경계 밖으로 나갈 수 있는 값"의 정의다 (설계 §3.3).
    """
    out: set[str] = set()
    out |= set(vocab.tasks)
    out |= set(vocab.domains)
    out |= set(vocab.question_templates)
    out |= set(schema.entity_roles)
    out |= set(schema.answer_format.values())
    for slot in schema.slots:
        if slot.kind == "enum" and slot.allowed:
            out |= set(slot.allowed)
    return frozenset(out)


def check_vocab(
    payload: object, schema: TaskSchema, vocab: Vocabulary, representation: Representation
) -> CheckResult:
    """모든 문자열 값이 어휘 사전 안에 있는가.

    ref 라벨(`REQ_A`)과 placeholder(`<PROJ_1>`)는 자동 생성물이라 사전에 없다 —
    형식 정규식으로 허용한다. 형식을 만족하지 않는 문자열은 전부 위반이다.

    `PSEUDONYMIZED`/`VERBATIM` 은 `excerpts` 내부를 제외한다. 그 등급의 정의가
    "어휘 제한"이 아니라 "식별자 제거"이기 때문이다 — 대신 4·5단계가 하한선이다.
    """
    skip = frozenset({EXCERPTS_KEY}) if representation in TEXT_REPRESENTATIONS else frozenset()
    ok = allowed_strings(schema, vocab)

    bad = [
        s
        for s in all_strings(payload, skip_keys=skip)
        if s not in ok and not REF_LABEL_RE.match(s) and not PLACEHOLDER_RE.match(s)
    ]
    if bad:
        return CheckResult(
            stage="vocab",
            passed=False,
            detail=f"어휘 사전 밖의 값 {len(bad)}개",
            offending=tuple(sorted(set(bad))[:20]),
        )
    scope = "excerpts 제외" if skip else "전체"
    return CheckResult(stage="vocab", passed=True, detail=f"문자열 값 전부 in-vocab ({scope})")


# ══════════════════════════════════════════════════════════════════════
# 3단계 · 범위 (BR-V-03)
# ══════════════════════════════════════════════════════════════════════


def check_ranges(payload: object, schema: TaskSchema) -> CheckResult:
    """정수가 `[min, max]` 안에 있고 타입이 맞는가.

    `bool` 슬롯에 정수가 오거나 `int` 슬롯에 `True` 가 오는 것도 위반이다 —
    파이썬에서 `isinstance(True, int)` 가 참이라 조용히 통과할 수 있다.
    """
    bad: list[str] = []
    for path, name, value in slot_entries(payload, schema):
        slot = schema.slot(name)
        if slot is None:  # pragma: no cover — 1단계가 먼저 잡는다
            continue
        match slot.kind:
            case "int":
                if isinstance(value, bool) or not isinstance(value, int):
                    bad.append(f"{path}: int 가 아니다 ({type(value).__name__})")
                elif (
                    slot.min is not None
                    and slot.max is not None
                    and not (slot.min <= value <= slot.max)
                ):
                    bad.append(f"{path}={value} 범위 밖 [{slot.min}, {slot.max}]")
            case "bool":
                if not isinstance(value, bool):
                    bad.append(f"{path}: bool 이 아니다 ({type(value).__name__})")
            case "enum":
                if not isinstance(value, str):
                    bad.append(f"{path}: 문자열이 아니다 ({type(value).__name__})")

    if bad:
        return CheckResult(
            stage="range", passed=False, detail=f"범위·타입 위반 {len(bad)}건", offending=tuple(bad)
        )
    return CheckResult(stage="range", passed=True, detail="숫자·타입 전부 범위 안")


# ══════════════════════════════════════════════════════════════════════
# 4단계 · 금칙어 (BR-V-04)
# ══════════════════════════════════════════════════════════════════════


def check_banned(payload: object, banned: BannedTerms) -> CheckResult:
    """금칙어 0건인가. **모든 등급에 동일하게 적용된다.**

    사내 등급의 하한선이 여기다. 가명화가 실패해도 고객사명·계약번호·금액은
    이 단계에서 걸린다.
    """
    hits = banned.hits(payload_text(payload))
    if hits:
        return CheckResult(
            stage="banned",
            passed=False,
            detail=f"금칙어 {len(hits)}건",
            offending=tuple(sorted(set(hits))),
        )
    return CheckResult(stage="banned", passed=True, detail="금칙어 0건")


# ══════════════════════════════════════════════════════════════════════
# 5단계 · 원문 대조 (BR-V-05, BR-P-03) — 가장 강력한 검사
# ══════════════════════════════════════════════════════════════════════


def _grams_to_check(
    originals: Sequence[str],
    *,
    n: int,
    identifiers: Sequence[str],
    identifier_only: bool,
    n_internal: int,
) -> frozenset[str]:
    """검사 대상 n-gram 집합을 만든다.

    `identifier_only=True` (가명화 등급)면 **식별자를 포함한 gram 만** 남긴다.
    3-gram 도 함께 본다 — 한국어는 공백이 적어 5-gram 이 긴 구간을 덮고
    탐지가 느슨해진다 (`NGRAM_SIZE_INTERNAL`).
    """
    grams: set[str] = set()
    for text in originals:
        grams |= ngram_set(text, n)
        if identifier_only:
            grams |= ngram_set(text, n_internal)

    if not identifier_only:
        return frozenset(grams)

    low = [i.lower() for i in identifiers if i.strip()]
    return frozenset(g for g in grams if any(tok in g for tok in low))


def check_no_source_ngram(
    payload: object,
    originals: Sequence[str],
    *,
    representation: Representation,
    n: int = 5,
    identifiers: Sequence[str] = (),
    n_internal: int = 3,
) -> CheckResult:
    """원문 조각이 페이로드에 있는가.

    > 원문 문장이 한 조각이라도 페이로드에 있으면 기계적으로 잡힌다.
    > 모델이 무엇을 하든.

    `VERBATIM` 은 **원문 전송이 등급의 정의**라서 이 검사를 적용할 수 없다.
    통과시키되 그 사실을 `detail` 에 남긴다 — 조용히 넘기지 않는다.
    """
    if representation is Representation.VERBATIM:
        return CheckResult(
            stage="ngram",
            passed=True,
            detail="공개 등급: 원문 그대로 전송이 등급의 정의다 (검사 미적용)",
        )

    identifier_only = representation is Representation.PSEUDONYMIZED
    grams = _grams_to_check(
        originals,
        n=n,
        identifiers=identifiers,
        identifier_only=identifier_only,
        n_internal=n_internal,
    )
    blob = normalize_text(payload_text(payload))
    hits = sorted(g for g in grams if g in blob)

    kind = "식별자 포함 n-gram" if identifier_only else f"원문 {n}-gram"
    if hits:
        return CheckResult(
            stage="ngram",
            passed=False,
            detail=f"{kind} {len(hits)}건 발견",
            offending=tuple(hits[:10]),
        )
    return CheckResult(stage="ngram", passed=True, detail=f"{kind} 0건 (대조 {len(grams)}개)")


# ══════════════════════════════════════════════════════════════════════
# 6단계 · 크기 (BR-V-06)
# ══════════════════════════════════════════════════════════════════════


def size_limit(representation: Representation, max_bytes: int) -> int:
    """구조 페이로드는 `max_bytes`, 텍스트 표현은 그 `TEXT_SIZE_FACTOR` 배.

    2KB 상한의 근거는 "초과는 자유 텍스트가 섞였다는 신호"다. 가명화 본문에는
    그 논리가 성립하지 않으므로 같은 숫자를 쓰면 정상 동작이 차단된다.
    """
    if representation in TEXT_REPRESENTATIONS:
        return max_bytes * TEXT_SIZE_FACTOR
    return max_bytes


def check_size(payload: object, representation: Representation, max_bytes: int) -> CheckResult:
    limit = size_limit(representation, max_bytes)
    size = payload_bytes(payload)
    if size > limit:
        return CheckResult(stage="size", passed=False, detail=f"{size} bytes > 상한 {limit} bytes")
    return CheckResult(stage="size", passed=True, detail=f"{size}/{limit} bytes")


# ══════════════════════════════════════════════════════════════════════
# 통합 (BR-V-00)
# ══════════════════════════════════════════════════════════════════════


def validate(
    payload: object,
    *,
    schema: TaskSchema,
    vocab: Vocabulary,
    banned: BannedTerms,
    originals: Sequence[str],
    representation: Representation,
    max_bytes: int = 2048,
    ngram_size: int = 5,
    ngram_size_internal: int = 3,
    identifiers: Sequence[str] = (),
) -> ValidationResult:
    """6단계를 순서대로 실행하되 **첫 실패에서 멈추지 않는다** (BR-V-00).

    이유 둘: 사람이 볼 진단이 완전해야 하고, `PreviewCard` 에 `6/6` 을 표시해야 한다.
    조기 반환을 넣으면 "5/6 실패"만 보이고 나머지 상태를 알 수 없다.
    """
    checks = (
        check_schema(payload, schema, representation),
        check_vocab(payload, schema, vocab, representation),
        check_ranges(payload, schema),
        check_banned(payload, banned),
        check_no_source_ngram(
            payload,
            originals,
            representation=representation,
            n=ngram_size,
            identifiers=identifiers,
            n_internal=ngram_size_internal,
        ),
        check_size(payload, representation, max_bytes),
    )
    return ValidationResult(checks=checks)


def revalidate_without_originals(
    payload: object,
    *,
    schema: TaskSchema,
    vocab: Vocabulary,
    banned: BannedTerms,
    representation: Representation,
    max_bytes: int = 2048,
) -> ValidationResult:
    """U5 Lambda 용 재검증 — 1~4·6단계만 (BR-V-07).

    5단계는 **원문이 클라우드에 없으므로 재실행할 수 없다.** 이건 한계이고
    숨기지 않는다. `CheckResult.detail` 에 그 사실을 남겨 재검증 결과를 읽는
    사람이 무엇이 확인되지 않았는지 알 수 있게 한다.
    """
    checks = (
        check_schema(payload, schema, representation),
        check_vocab(payload, schema, vocab, representation),
        check_ranges(payload, schema),
        check_banned(payload, banned),
        CheckResult(
            stage="ngram",
            passed=True,
            detail="브로커 재검증: 원문이 클라우드에 없어 대조 불가 (로컬에서만 수행됨)",
        ),
        check_size(payload, representation, max_bytes),
    )
    return ValidationResult(checks=checks)


# ══════════════════════════════════════════════════════════════════════
# 미리보기용 측정
# ══════════════════════════════════════════════════════════════════════


def verbatim_sentence_count(
    payload: object,
    originals: Sequence[str],
    *,
    representation: Representation,
    identifiers: Sequence[str] = (),
) -> int:
    """페이로드에 남은 "이 등급에서 나가면 안 되는 원문 문장" 개수.

    `PreviewCard` 에 **측정값**으로 표시한다 — "원문 0개"가 주장이 아니라
    계산 결과가 된다 (FR-41).

    등급별로 세는 대상이 다르다:
      STRUCTURED     원문 문장이 하나라도 있으면 센다
      PSEUDONYMIZED  **식별자가 남아 있는** 원문 문장만 센다 (BR-P-03)
      VERBATIM       0 (원문 전송이 정의다)

    그래서 이 값은 어느 등급에서도 0 이어야 한다.
    """
    if representation is Representation.VERBATIM:
        return 0

    blob = normalize_text(payload_text(payload))
    low = [i.lower() for i in identifiers if i.strip()]
    count = 0
    for text in originals:
        for sent in sentences(text):
            if sent not in blob:
                continue
            if representation is Representation.PSEUDONYMIZED and not any(
                tok in sent for tok in low
            ):
                continue
            count += 1
    return count


def excluded_categories(payload: object, banned: BannedTerms) -> tuple[str, ...]:
    """`PreviewCard` 의 "포함되지 않은 것" 목록 (BR-U-02).

    있는 것을 보여주는 것보다 없는 것을 보여주는 게 설득력이 있다.
    실제로 없음을 확인한 항목만 반환한다 — 확인하지 않은 것을 "없다"고 쓰면
    미리보기가 거짓이 된다.
    """
    if banned.hits(payload_text(payload)):
        return ()
    return EXCLUDED_CATEGORIES_DEFAULT


# ══════════════════════════════════════════════════════════════════════
# 진단 보조
# ══════════════════════════════════════════════════════════════════════


def failed_stages(result: ValidationResult) -> tuple[str, ...]:
    return tuple(c.stage for c in result.checks if not c.passed)


def missing_required_slots(payload: object, schema: TaskSchema) -> frozenset[str]:
    """필수 슬롯 미충족 목록. `ExtractionFailed` 판정의 근거 (BR-E-03).

    **근거 전체에서** 채워지면 충족이다 — 한 문서가 모든 사실을 담고 있어야
    하는 것은 아니다. 시나리오 1 에서 세션 최대시간은 고객사 문서에만 있고
    토큰 수명은 자사 문서에만 있다.
    """
    return schema.required_slots - slot_names_present(payload, schema)


def slot_kinds(slots: Iterable[SlotDef]) -> dict[str, str]:
    """진단 출력용."""
    return {s.name: s.kind for s in slots}

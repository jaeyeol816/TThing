"""Hypothesis 도메인 생성기 (PBT-07).

`adversarial_raw()` 가 이 파일의 핵심이다.

원시 타입 생성기(`st.dictionaries(st.text(), st.text())`)만으로는 화이트리스트
조립의 실효성을 시험할 수 없다. 임의의 문자열 키는 슬롯 이름과 거의 겹치지
않으므로 `assemble()` 이 빈 dict 를 반환하고, 테스트가 **아무 일도 하지 않는데
통과한다.** 그래서 실측에서 관찰된 모델의 실패 방식을 그대로 생성한다:

  - 스키마에 없는 키 (`max_session_duration`)
  - 어휘 사전 밖의 값 (`"challenge-response"` 하이픈 변형)
  - 자유 문자열 (`"8 hours"`, 원문 문장 조각)
  - 중첩 구조 (`{"facts": {"nested": {...}}}`)
  - 타입 불일치 (`"false"`, `8.0`, `null`)
  - `__unknown__` 혼재

이 생성기가 없으면 PB-3/PB-4/PB-5 는 형식적인 테스트가 된다.
"""

from __future__ import annotations

from datetime import date

from hypothesis import strategies as st

from mesh.extractor import UNKNOWN
from mesh.schemas import Chunk, PseudonymTargets, SlotDef, TaskSchema, Tier

# ══════════════════════════════════════════════════════════════════════
# 기본
# ══════════════════════════════════════════════════════════════════════


def tiers() -> st.SearchStrategy[Tier]:
    return st.sampled_from(list(Tier))


#: 슬롯·enum 값의 형태. 실제 어휘 사전처럼 **공백 없는 짧은 토큰**이다.
#:
#: ⚠️ 이것이 설계 제약이기도 하다. 어휘 사전 값에 공백이 들어가면 그 값이
#:    원문 5-gram 과 우연히 일치할 수 있다. 값은 항상 단일 토큰이어야 한다.
_TOKEN = st.from_regex(r"\A[a-z][a-z0-9_]{2,20}\Z", fullmatch=True)


def slot_names() -> st.SearchStrategy[str]:
    return _TOKEN


def enum_slots(name: str | None = None) -> st.SearchStrategy[SlotDef]:
    return st.builds(
        SlotDef,
        name=st.just(name) if name else slot_names(),
        kind=st.just("enum"),
        allowed=st.lists(_TOKEN, min_size=1, max_size=6, unique=True).map(tuple),
        required=st.booleans(),
    )


def int_slots(name: str | None = None) -> st.SearchStrategy[SlotDef]:
    @st.composite
    def _build(draw) -> SlotDef:
        low = draw(st.integers(min_value=0, max_value=100))
        high = draw(st.integers(min_value=low, max_value=8760))
        return SlotDef(
            name=name or draw(slot_names()),
            kind="int",
            min=low,
            max=high,
            required=draw(st.booleans()),
        )

    return _build()


def bool_slots(name: str | None = None) -> st.SearchStrategy[SlotDef]:
    return st.builds(
        SlotDef,
        name=st.just(name) if name else slot_names(),
        kind=st.just("bool"),
        required=st.booleans(),
    )


def slot_defs() -> st.SearchStrategy[SlotDef]:
    return st.one_of(enum_slots(), int_slots(), bool_slots())


@st.composite
def task_schemas(draw, *, min_slots: int = 1, max_slots: int = 8) -> TaskSchema:
    """유효한 `TaskSchema`. 슬롯 이름이 유일하고 구조 키와 겹치지 않는다."""
    from mesh.schemas import STRUCTURAL_KEYS

    count = draw(st.integers(min_value=min_slots, max_value=max_slots))
    names = draw(
        st.lists(slot_names(), min_size=count, max_size=count, unique=True).filter(
            lambda ns: not (set(ns) & STRUCTURAL_KEYS)
        )
    )
    slots = [draw(st.one_of(enum_slots(n), int_slots(n), bool_slots(n))) for n in names]
    roles = draw(st.lists(_TOKEN, min_size=1, max_size=3, unique=True))
    answer_keys = draw(
        st.lists(_TOKEN, min_size=1, max_size=3, unique=True).filter(
            lambda ks: not (set(ks) & set(names)) and not (set(ks) & STRUCTURAL_KEYS)
        )
    )
    return TaskSchema(
        schema_id=draw(_TOKEN),
        domain=draw(_TOKEN),
        question_template=draw(_TOKEN),
        answer_format={k: "string" for k in answer_keys},
        entity_roles=tuple(roles),
        slots=tuple(slots),
    )


# ══════════════════════════════════════════════════════════════════════
# 한국어 · 기술 텍스트
# ══════════════════════════════════════════════════════════════════════

_KO_WORDS = (
    "인증",
    "세션",
    "바인딩",
    "토큰",
    "갱신",
    "만료",
    "요구사항",
    "설계",
    "문서",
    "고객사",
    "계약",
    "금액",
    "담당자",
    "일정",
    "성능",
    "지연",
    "처리량",
    "학습",
    "전처리",
    "불균형",
    "오버샘플링",
    "파이프라인",
    "재사용",
    "금지",
    "허용",
    "제한한다",
    "적용하지",
    "않는다",
    "확인했다",
    "검토",
    "리뷰",
    "메모",
)
_EN_TERMS = (
    "RandomOverSampler",
    "balanced_subsample",
    "sampling_strategy",
    "class_weight",
    "EAP-AKA",
    "OAuth",
    "SAML",
    "mTLS",
    "JWT",
    "SSO",
    "p99",
    "TPS",
    "SDK",
    "background_silent",
    "session_binding",
    "REQ-4412",
    "CTR-204817",
)
_CODE_BITS = (
    "sampling_strategy=0.5",
    "random_state=42",
    "max_depth=6",
    "lr=0.05",
    "timeout=10s",
    "n_estimators=400",
)
_NUMBERS = ("8", "24", "840", "3120", "12억원", "3천만원", "1,200,000원", "0.5", "42")


def korean_technical_text(*, min_tokens: int = 1, max_tokens: int = 40) -> st.SearchStrategy[str]:
    """한글 + 영문 기술어 + 숫자 + 코드가 섞인 실전 형태의 텍스트.

    순수 `st.text()` 보다 이 프로젝트의 실제 입력에 가깝다. 공백 토큰 기준
    n-gram 을 쓰므로 **토큰 경계가 있는** 텍스트로 시험해야 의미가 있다.
    """
    token = st.sampled_from(_KO_WORDS + _EN_TERMS + _CODE_BITS + _NUMBERS)
    return st.lists(token, min_size=min_tokens, max_size=max_tokens).map(" ".join)


def source_texts() -> st.SearchStrategy[str]:
    """원문 후보. 문장 구분과 개행을 섞는다 — 개행은 실측된 우회 채널이다."""
    return st.lists(
        korean_technical_text(min_tokens=3, max_tokens=15),
        min_size=1,
        max_size=4,
    ).map(lambda parts: ". ".join(parts))


# ══════════════════════════════════════════════════════════════════════
# Chunk
# ══════════════════════════════════════════════════════════════════════

_PATH_BY_TIER = {
    Tier.SECRET: "corpus/customer-H/{n}.md",
    Tier.INTERNAL: "corpus/kim/docs/{n}.md",
    Tier.OPEN: "corpus/public/{n}.md",
}


@st.composite
def chunks(draw, *, tier: Tier | None = None, count: int = 1) -> list[Chunk]:
    """`tier` 와 경로의 상관관계를 유지한다 — 실제 코퍼스와 같은 형태여야
    등급 판정 경로를 시험할 수 있다."""
    out: list[Chunk] = []
    for i in range(count):
        t = tier or draw(tiers())
        out.append(
            Chunk(
                chunk_id=f"c_{i}_{draw(st.integers(0, 9999))}",
                entity_id="person:kim",
                text=draw(source_texts()),
                tier=t,
                display_title=f"문서 {i}",
                internal_path=_PATH_BY_TIER[t].format(n=i),
                as_of=draw(st.one_of(st.none(), st.just(date(2026, 7, 15)))),
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════
# 페이로드
# ══════════════════════════════════════════════════════════════════════


@st.composite
def valid_facts(draw, schema: TaskSchema) -> dict[str, object]:
    """스키마를 만족하는 `facts`. 검증 전 단계가 통과함을 확인하는 데 쓴다."""
    out: dict[str, object] = {}
    for slot in schema.slots:
        if not slot.required and draw(st.booleans()):
            continue
        match slot.kind:
            case "enum":
                out[slot.name] = draw(st.sampled_from(list(slot.allowed or ("x",))))
            case "int":
                out[slot.name] = draw(st.integers(min_value=slot.min or 0, max_value=slot.max or 1))
            case "bool":
                out[slot.name] = draw(st.booleans())
    return out


# ══════════════════════════════════════════════════════════════════════
# ⚠️ 핵심 생성기
# ══════════════════════════════════════════════════════════════════════


@st.composite
def adversarial_raw(draw, schema: TaskSchema, *, source: str | None = None) -> dict:
    """모델이 반환할 수 있는 **최악의** 응답.

    실측에서 관찰된 실패 방식을 그대로 섞는다. 원문 조각을 슬롯 값에 넣는
    경우가 특히 중요하다 — 그게 PB-5 가 잡아야 하는 유출 형태다.
    """
    text = source if source is not None else draw(source_texts())
    fragments = text.split()

    def fragment() -> str:
        if not fragments:
            return text
        start = draw(st.integers(min_value=0, max_value=max(0, len(fragments) - 1)))
        length = draw(st.integers(min_value=1, max_value=12))
        return " ".join(fragments[start : start + length])

    raw: dict = {}

    # ① 정상값
    for slot in schema.slots:
        if draw(st.booleans()):
            continue
        match slot.kind:
            case "enum":
                raw[slot.name] = draw(st.sampled_from(list(slot.allowed or ("x",))))
            case "int":
                raw[slot.name] = draw(st.integers(min_value=slot.min or 0, max_value=slot.max or 1))
            case "bool":
                raw[slot.name] = draw(st.booleans())

    # ② 실측된 타입 불일치 · 자유 문자열 · 원문 조각
    for slot in schema.slots:
        if not draw(st.booleans()):
            continue
        raw[slot.name] = draw(
            st.one_of(
                st.just(UNKNOWN),
                st.just("false"),
                st.just("true"),
                st.just("8 hours"),
                st.just("2026-07-15"),
                st.just(8.0),
                st.just(8.5),
                st.none(),
                st.just([]),
                st.just({}),
                st.builds(fragment),
                st.text(max_size=40),
                st.integers(min_value=-(10**6), max_value=10**6),
                # enum 의 하이픈 변형 — 실측된 유사값
                st.sampled_from(list(slot.allowed or ("x",))).map(lambda v: v.replace("_", "-")),
                st.sampled_from(list(slot.allowed or ("x",))).map(str.upper),
            )
        )

    # ③ 스키마에 없는 키 (실측: max_session_duration, credential_reuse)
    for key in draw(
        st.lists(
            st.one_of(
                st.just("max_session_duration"),
                st.just("credential_reuse"),
                st.just("고객사"),
                st.just("원문"),
                st.just("reasoning"),
                st.text(min_size=1, max_size=20),
            ),
            max_size=5,
        )
    ):
        raw[key] = draw(st.one_of(st.builds(fragment), st.just(text), st.text(max_size=50)))

    # ④ 중첩 구조
    if draw(st.booleans()):
        raw["facts"] = {"nested": {"deep": text}}
    if draw(st.booleans()) and schema.slots:
        raw[schema.slots[0].name] = {"value": fragment()}

    return raw


# ══════════════════════════════════════════════════════════════════════
# 가명화
# ══════════════════════════════════════════════════════════════════════

_IDENTIFIERS = {
    "PROJ": ("atlas-ml", "atlas_ml", "AtlasML", "sdk-core"),
    "SYS": ("Nova 게이트웨이", "NovaGW"),
    "PERSON": ("김철수", "박선영", "최민수"),
}


def pseudonym_targets() -> st.SearchStrategy[PseudonymTargets]:
    return st.builds(
        PseudonymTargets,
        targets=st.just({k: v for k, v in _IDENTIFIERS.items()}),
        technical_terms=st.just(frozenset({"RandomOverSampler", "OAuth", "SSO"})),
    )


@st.composite
def identifier_texts(draw) -> tuple[str, PseudonymTargets]:
    """식별자가 섞인 텍스트와 그 치환 목록.

    같은 식별자가 여러 번 등장하게 만들어 placeholder 일관성(PB-6)을 시험한다.
    """
    targets = draw(pseudonym_targets())
    pool = [lit for lits in targets.targets.values() for lit in lits]
    chosen = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=8))
    filler = draw(st.lists(st.sampled_from(_KO_WORDS), min_size=1, max_size=10))

    parts: list[str] = []
    for i, token in enumerate(chosen):
        parts.append(token)
        if i < len(filler):
            parts.append(filler[i])
    return " ".join(parts), targets

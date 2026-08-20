"""타입 계약 — 3인의 공유 계약.

⚠️ Day 1 종료 시 동결. 이후 변경은 3인 합의로만 (NFR-M-02).

이 모듈의 설계 원칙: **불변식을 문서가 아니라 타입으로 표현한다.**
문서로 쓴 규칙은 5일 동안 3명이 작업하면 지켜지지 않는다.

  | 불변식                  | 타입 표현                                    |
  |-------------------------|----------------------------------------------|
  | 한 호출에 한 등급       | AgentCall.tier: Tier  (list[Tier] 이 아니다) |
  | 페이로드에 원문 없음    | PayloadEnvelope 에 text 필드 부재            |
  | 매핑 비영속             | Mapping.__getstate__ -> TypeError            |
  | 인용에 경로 없음        | Citation 에 internal_path 필드 부재          |
  | 등급 상향이 max()       | Tier.__lt__ 구현                             |
  | 자유 문자열 슬롯 금지   | SlotDef.kind: Literal["enum","int","bool"]   |

CHANGELOG
---------
2026-08-19  v1.0.0  초기 동결. (A)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ══════════════════════════════════════════════════════════════════════
# 열거형
# ══════════════════════════════════════════════════════════════════════


class Tier(StrEnum):
    """보안 등급.

    등급은 "외부 AI를 쓸 수 있나 없나"를 정하지 않는다.
    "어떤 표현으로 나가나"를 정한다. 모든 등급이 외부 추론의 도움을 받을 수 있다.

    ⚠️ 4개 비교 메서드를 **명시적으로** 구현한다.

       StrEnum 은 str 을 상속하므로 기본 비교가 알파벳 순이다.
       그러면 max(Tier.INTERNAL, Tier.OPEN) == Tier.OPEN 이 되어
       등급 상향(FR-11)이 조용히 유출로 바뀐다.

       functools.total_ordering 으로는 부족하다. 실측 확인:
       __lt__ 만 정의하면 total_ordering 이 __gt__ 를 주입하지 못하고
       str.__gt__ 가 남는다. max() 는 __gt__ 를 쓰므로 여전히 알파벳 순이다.

       tests/unit/test_tier_order.py 가 모든 순서 조합을 검사한다.
    """

    OPEN = "open"
    INTERNAL = "internal"
    SECRET = "secret"  # noqa: S105  — 보안 등급 이름이다. 비밀번호가 아니다

    @property
    def rank(self) -> int:
        return _TIER_RANK[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Tier):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Tier):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Tier):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Tier):
            return NotImplemented
        return self.rank >= other.rank

    @property
    def label_ko(self) -> str:
        return {"open": "공개", "internal": "사내", "secret": "기밀"}[self.value]


_TIER_RANK = {"open": 0, "internal": 1, "secret": 2}


class Freshness(StrEnum):
    """세션 신선도. 오래된 세션이 틀린 실시간 정보를 주는 것을 막는다 (FR-19)."""

    LIVE = "live"  # < SESSION_STALE_MINUTES        보정 없음
    STALE = "stale"  # < 24h    신뢰도 x0.8 + 시각 명시
    EXPIRED = "expired"  # >= 24h   실시간 주장에서 제외 (파일은 계속 읽는다)


class Disposition(StrEnum):
    """신뢰도 분기 결과 (FR-34, FR-35)."""

    AUTO = "auto"  # 신뢰도 >= 0.75 & 인용 >= 1
    UNVERIFIED = "unverified"  # 0.45 ~ 0.75  -> 미검증 배지
    ESCALATE = "escalate"  # < 0.45 또는 인용 0개
    BLOCKED = "blocked"  # 검증 실패 -> 신뢰 구역 내 답변


class Transport(StrEnum):
    """Agent 호출 경로 (FR-49)."""

    BROKER = "broker"  # Lambda 경유. 재검증 2겹 + 지울 수 없는 감사
    DIRECT = "direct"  # 노트북에서 Bedrock 직접. CDK 없이 동작
    MOCK = "mock"  # 픽스처 재생. 네트워크 없이 동작


class Representation(StrEnum):
    """경계 밖으로 나가는 표현 형태."""

    STRUCTURED = "structured"  # 기밀 — 구조 페이로드 (원문 0개)
    PSEUDONYMIZED = "pseudonymized"  # 사내 — 식별자만 치환
    VERBATIM = "verbatim"  # 공개 — 원문 그대로


SourceKind = Literal[
    "design_doc", "minutes", "note", "script", "config", "run_log", "spec", "benchmark"
]
Formality = Literal["official", "informal"]
EntityId = str  # "person:kim" — 형식은 ENTITY_ID_RE 로 검증

#: pydantic `Field(pattern=...)` 는 문자열을 요구하므로 상수와 컴파일본을 함께 둔다.
ENTITY_ID_PATTERN = r"^person:[a-z0-9_]{1,32}$"
ENVELOPE_ID_PATTERN = r"^env_[A-Za-z0-9]{20,32}$"

ENTITY_ID_RE = re.compile(ENTITY_ID_PATTERN)
ENVELOPE_ID_RE = re.compile(ENVELOPE_ID_PATTERN)


# ══════════════════════════════════════════════════════════════════════
# 지식
# ══════════════════════════════════════════════════════════════════════


class Chunk(BaseModel):
    """읽은 파일 한 조각.

    display_title / internal_path 분리가 FR-43(인용이 권한을 우회하지 않는다)의
    구현 지점이다. 경로 자체가 정보를 준다 — corpus/customer-H/ 는 고객사명을
    그대로 노출한다.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    entity_id: EntityId
    text: str  # ⚠️ 원문. 경계를 넘지 않는다
    tier: Tier | None = None  # None = 아직 미판정 (Store 는 채우지 않는다)
    display_title: str  # UI 표시용
    internal_path: str  # ⚠️ 로컬 전용. API 응답에 넣지 않는다
    section: str | None = None
    as_of: date | None = None
    formality: Formality = "official"
    source_kind: SourceKind = "design_doc"
    truncated: bool = False


# ══════════════════════════════════════════════════════════════════════
# task 스키마와 슬롯 — 화이트리스트의 정의
# ══════════════════════════════════════════════════════════════════════

DROP = object()  # coerce() 가 "이 값은 버린다"를 표현하는 센티널


class SlotDef(BaseModel):
    """구조 추출의 슬롯 하나.

    ⚠️ kind 가 enum / int / bool 세 가지뿐인 것이 핵심 설계다.
       자유 문자열 슬롯(kind="str")을 의도적으로 만들지 않았다.
       자유 문자열을 허용하면 원문이 새어나갈 채널이 생긴다.

       새 task 에 자유 문자열이 필요해 보이면, 그건 그 task 가 이 방식에
       맞지 않는다는 신호다 (NFR-M-03).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["enum", "int", "bool"]
    allowed: tuple[str, ...] | None = None
    min: int | None = None
    max: int | None = None
    required: bool = True
    description: str = ""

    @model_validator(mode="after")
    def _check_kind_constraints(self) -> SlotDef:
        if self.kind == "enum" and not self.allowed:
            raise ValueError(f"slot {self.name!r}: enum 슬롯은 allowed 를 선언해야 한다")
        if self.kind == "int" and (self.min is None or self.max is None):
            raise ValueError(f"slot {self.name!r}: int 슬롯은 min/max 를 선언해야 한다")
        if self.kind != "enum" and self.allowed:
            raise ValueError(f"slot {self.name!r}: {self.kind} 슬롯에 allowed 는 무의미하다")
        return self


class TaskSchema(BaseModel):
    """구조 추출 task 하나. slot_names 가 화이트리스트 조립의 기준이다."""

    model_config = ConfigDict(frozen=True)

    schema_id: str
    domain: str
    question_template: str
    answer_format: dict[str, str]
    entity_roles: tuple[str, ...]
    slots: tuple[SlotDef, ...]

    @property
    def slot_names(self) -> frozenset[str]:
        """extractor.assemble() 이 이 집합만 페이로드에 넣는다."""
        return frozenset(s.name for s in self.slots)

    @property
    def required_slots(self) -> frozenset[str]:
        return frozenset(s.name for s in self.slots if s.required)

    def slot(self, name: str) -> SlotDef | None:
        for s in self.slots:
            if s.name == name:
                return s
        return None


#: 페이로드에 허용되는 구조 키 (슬롯 이름 외에).
#: validator.check_schema() 가 slot_names 와 이 집합의 합집합을 허용한다.
STRUCTURAL_KEYS = frozenset(
    {"task", "domain", "entities", "ref", "role", "facts", "question_template", "answer_format"}
)


class Vocabulary(BaseModel):
    """data/vocab.json 의 로드 결과. 나갈 수 있는 값의 전체 집합."""

    model_config = ConfigDict(frozen=True)

    version: str
    slots: dict[str, SlotDef]
    tasks: tuple[str, ...]
    domains: tuple[str, ...]
    question_templates: tuple[str, ...]
    entity_roles: tuple[str, ...]
    task_schemas: dict[str, TaskSchema]

    @classmethod
    def load(cls, path: Path) -> Vocabulary:
        raw = json.loads(path.read_text(encoding="utf-8"))
        slots = {
            name: SlotDef(name=name, **{k: v for k, v in spec.items() if not k.startswith("_")})
            for name, spec in raw["slots"].items()
        }
        # 슬롯 이름과 구조 키가 겹치면 검증 1단계가 모호해진다.
        overlap = STRUCTURAL_KEYS & set(slots)
        if overlap:
            raise ValueError(f"슬롯 이름이 구조 키와 겹친다: {sorted(overlap)}")

        all_roles = tuple(raw["entity_roles"])
        schemas: dict[str, TaskSchema] = {}
        for sid, spec in raw["task_schemas"].items():
            missing = set(spec["slots"]) - set(slots)
            if missing:
                raise ValueError(f"task_schema {sid!r} 가 미정의 슬롯 참조: {sorted(missing)}")
            bad_roles = set(spec["entity_roles"]) - set(all_roles)
            if bad_roles:
                raise ValueError(f"task_schema {sid!r} 가 미등록 role 참조: {sorted(bad_roles)}")
            schemas[sid] = TaskSchema(
                schema_id=sid,
                domain=spec["domain"],
                question_template=spec["question_template"],
                answer_format=spec["answer_format"],
                entity_roles=tuple(spec["entity_roles"]),
                slots=tuple(slots[n] for n in spec["slots"]),
            )
        return cls(
            version=raw["version"],
            slots=slots,
            tasks=tuple(raw["tasks"]),
            domains=tuple(raw["domains"]),
            question_templates=tuple(raw["question_templates"]),
            entity_roles=all_roles,
            task_schemas=schemas,
        )

    def enum_values(self, slot_name: str) -> frozenset[str]:
        s = self.slots.get(slot_name)
        return frozenset(s.allowed or ()) if s else frozenset()


class BannedTerms(BaseModel):
    """금칙어. 등급 판정 규칙 3·4번과 검증 4단계에서 함께 쓰인다."""

    model_config = ConfigDict(frozen=True)

    literals: tuple[str, ...]
    patterns: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> BannedTerms:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(literals=tuple(raw["literals"]), patterns=tuple(raw["patterns"]))

    def compiled(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(p, re.IGNORECASE) for p in self.patterns)

    def hits(self, text: str) -> tuple[str, ...]:
        """걸린 항목을 반환. 로컬 진단 전용 — 경계 밖 응답에 넣지 않는다."""
        low = text.lower()
        found = [lit for lit in self.literals if lit.lower() in low]
        found += [p.pattern for p in self.compiled() if p.search(text)]
        return tuple(found)


#: 문서 헤더의 등급 표기 -> Tier (등급 판정 규칙 2번).
DEFAULT_HEADER_MARKERS: dict[str, Tier] = {
    "기밀": Tier.SECRET,
    "secret": Tier.SECRET,
    "confidential": Tier.SECRET,
    "사내": Tier.INTERNAL,
    "internal": Tier.INTERNAL,
    "공개": Tier.OPEN,
    "open": Tier.OPEN,
    "public": Tier.OPEN,
}


class PseudonymTargets(BaseModel):
    """가명화 대상과 기술 용어 (BR-P-01).

    ⚠️ `BannedTerms` 와 **정반대 성격**이다:
         BannedTerms       걸리면 SECRET 상향 + 전송 차단  (고객사명·계약번호·금액)
         PseudonymTargets  치환하고 경계를 넘게 허용       (프로젝트명·시스템명·인명)

    두 목록을 섞으면 사내 문서가 전부 SECRET 으로 오분류되고
    가명화 경로가 아예 실행되지 않는다 (v1.0.0 에서 실제로 발생).
    """

    model_config = ConfigDict(frozen=True)

    #: 카테고리 접두사 -> 치환 대상 리터럴. 예: {"PROJ": ("atlas-ml", ...)}
    targets: dict[str, tuple[str, ...]]
    #: 절대 치환하지 않는 기술 용어. 치환하면 답변 품질이 무너진다
    technical_terms: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> PseudonymTargets:
        raw = json.loads(path.read_text(encoding="utf-8"))
        targets = {prefix: tuple(spec["literals"]) for prefix, spec in raw["targets"].items()}
        return cls(
            targets=targets,
            technical_terms=frozenset(raw["technical_terms"]),
        )

    def all_literals(self) -> tuple[tuple[str, str], ...]:
        """`(접두사, 리터럴)` 쌍. **긴 리터럴부터** 반환한다 —
        `atlas-ml` 과 `atlas-ml-core` 가 함께 있을 때 짧은 것을 먼저 치환하면
        망가진다 (BR-P-04 와 같은 이유)."""
        pairs = [(p, lit) for p, lits in self.targets.items() for lit in lits]
        return tuple(sorted(pairs, key=lambda x: len(x[1]), reverse=True))

    def is_technical(self, token: str) -> bool:
        return token in self.technical_terms


class ClassificationRules(BaseModel):
    """등급 판정 규칙 (BR-C-03).

    ⚠️ default_tier 가 INTERNAL 인 것이 의도적이다.
       판정 못 한 문서가 OPEN 으로 흘러가면 원문이 그대로 나간다.
       OPEN 은 명시적 표기가 있는 문서만 받는다.

    금칙어를 여기 담는 이유: 판정 규칙 3번(리터럴)과 4번(정규식)이 금칙어를
    쓴다. 검증 4단계와 같은 사전을 공유해야 판정과 차단이 갈리지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    banned: BannedTerms
    secret_path_globs: tuple[str, ...] = ("corpus/customer-*/**", "**/benchmark/**")
    open_path_globs: tuple[str, ...] = ("corpus/public/**",)
    internal_path_globs: tuple[str, ...] = ("corpus/**",)
    header_markers: dict[str, Tier] = Field(default_factory=lambda: dict(DEFAULT_HEADER_MARKERS))
    default_tier: Tier = Tier.INTERNAL


class TierDecision(BaseModel):
    """등급 판정 결과. 왜 그 등급인지를 사람이 읽을 수 있게 남긴다 —
    데모 중 "왜 이게 기밀로 나왔지?"에 즉시 답해야 한다."""

    model_config = ConfigDict(frozen=True)

    tier: Tier
    rule_tier: Tier
    exaone_tier: Tier | None = None
    reasons: tuple[str, ...] = ()
    exaone_skipped: bool = False  # 규칙이 이미 SECRET
    exaone_failed: bool = False  # 실패 -> SECRET 으로 간주됨


# ══════════════════════════════════════════════════════════════════════
# 매핑 테이블 — 영속화 불가
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Mapping:
    """ref/placeholder -> 실제 이름. 앱 메모리에만 존재하고 응답 후 폐기 (BR-G-09).

    pydantic BaseModel 이 아니라 dataclass 인 것이 의도적이다.
    pydantic 모델은 model_dump() 로 쉽게 dict 가 되고, 그 dict 가 로그·응답에
    실려 나갈 수 있다. 여기서는 직렬화를 타입 수준에서 차단한다.

    매핑이 유출되면 과거의 모든 감사 로그가 복호화된다.
    """

    table: dict[str, str]

    def __getstate__(self) -> object:
        raise TypeError("Mapping 은 직렬화·영속화할 수 없다 (BR-G-09)")

    def __reduce__(self) -> object:
        raise TypeError("Mapping 은 pickle 할 수 없다 (BR-G-09)")

    def __deepcopy__(self, memo: dict) -> object:
        raise TypeError("Mapping 은 복사할 수 없다 (BR-G-09)")

    def __repr__(self) -> str:
        return f"Mapping(<{len(self.table)} entries redacted>)"

    def get(self, ref: str) -> str | None:
        return self.table.get(ref)

    def keys_longest_first(self) -> list[str]:
        """긴 키부터 치환해 부분 일치 사고를 막는다 (BR-P-04).
        <SYS_1> 과 <SYS_11> 이 함께 있을 때 짧은 키를 먼저 치환하면 망가진다."""
        return sorted(self.table, key=len, reverse=True)

    @staticmethod
    def empty() -> Mapping:
        return Mapping(table={})


# ══════════════════════════════════════════════════════════════════════
# 검증
# ══════════════════════════════════════════════════════════════════════

ValidationStage = Literal["schema", "vocab", "range", "banned", "ngram", "size"]

STAGE_LABELS_KO: dict[str, str] = {
    "schema": "스키마",
    "vocab": "어휘",
    "range": "범위",
    "banned": "금칙어",
    "ngram": "원문대조",
    "size": "크기",
}


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: ValidationStage
    passed: bool
    detail: str = ""
    offending: tuple[str, ...] = ()  # ⚠️ 로컬 진단 전용. 브로커 응답에 넣지 않는다


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def summary(self) -> str:
        return f"{sum(c.passed for c in self.checks)}/{len(self.checks)}"

    @property
    def first_failed_stage(self) -> ValidationStage | None:
        for c in self.checks:
            if not c.passed:
                return c.stage
        return None


# ══════════════════════════════════════════════════════════════════════
# 페이로드 — 경계를 넘는 것
# ══════════════════════════════════════════════════════════════════════


class PayloadEnvelope(BaseModel):
    """경계를 넘을 후보.

    ⚠️ mapping 필드가 없는 것이 설계다. 매핑은 서버 메모리 캐시에서
       envelope_id 로 별도 관리한다. 그래야 model_dump() 가 실수로
       매핑을 함께 직렬화하지 않는다.

    ⚠️ text / raw / original 같은 원문 필드가 없다. 타입 수준에서
       원문이 이 객체에 담기지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    envelope_id: str
    tier: Tier  # ⚠️ 단일값. 등급 혼합 페이로드는 생성되지 않는다 (BR-G-08)
    task_schema_id: str
    payload: dict
    representation: Representation
    validation: ValidationResult | None = None
    payload_sha256: str
    size_bytes: int

    @field_validator("envelope_id")
    @classmethod
    def _check_envelope_id(cls, v: str) -> str:
        if not ENVELOPE_ID_RE.match(v):
            raise ValueError(f"envelope_id 형식 위반: {v!r}")
        return v


class PreviewCard(BaseModel):
    """사람 확인용 (FR-09, FR-41). 4번째 방어 겹.

    verbatim_sentence_count 를 측정값으로 표시한다 —
    "원문 0개"가 주장이 아니라 계산 결과가 된다.
    """

    envelope_id: str
    tier: Tier
    representation: Representation
    payload_pretty: str  # 들여쓴 JSON 전문. 생략 금지
    size_bytes: int
    validation_summary: str  # "6/6"
    checks: tuple[CheckResult, ...]
    excluded_categories: tuple[str, ...]
    verbatim_sentence_count: int  # 측정값. 항상 0 이어야 한다

    @property
    def stage_labels(self) -> dict[str, str]:
        return STAGE_LABELS_KO


#: PreviewCard 의 "포함되지 않은 것" 목록 (BR-U-02).
#: 있는 것을 보여주는 것보다 없는 것을 보여주는 게 설득력이 있다.
#:
#: ⚠️ **표현마다 다르다.** 하나의 목록을 모든 표현에 쓰면 미리보기가 거짓말을 한다
#:    (G4 육안 확인이 찾은 결함). `EXCLUDED_CATEGORIES_BY_REPRESENTATION` 을 쓴다.
EXCLUDED_CATEGORIES_DEFAULT: tuple[str, ...] = (
    "고객사명",
    "제품명",
    "버전",
    "요구사항 번호",
    "원문 문장",
    "담당자",
    "일정",
    "금액",
)

#: 표현별로 **실제로 없음이 보장되는** 범주.
#:
#: ──────────────────────────────────────────────────────────────────
#: 왜 표현마다 달라야 하나 — G4 육안 확인이 찾은 결함
#: ──────────────────────────────────────────────────────────────────
#:
#: 처음에는 세 표현 모두에 위의 8개 목록을 보여줬다. 자동 검사는 통과했다.
#: 그런데 사내 등급 페이로드를 눈으로 읽어 보니 이렇게 적혀 있었다.
#:
#:     "title: SDK v3.2 인증 설계 리뷰 … as_of: 2025-12-03 …"
#:
#: 화면은 그 순간 "제품명 · 버전 · 일정 · 원문 문장은 포함되지 않았습니다"
#: 라고 표시하고 있었다. **셋 다 거짓이다.**
#:
#: 가명화(사내)는 원문 문장을 유지하는 것이 정의다 — 식별자만 바꾼다.
#: 그러니 "원문 문장 없음"을 약속할 수 없다. 약속할 수 있는 것은
#: 금칙어 검사가 보장하는 것(고객사명·요구사항 번호·금액)과
#: 치환이 보장하는 것(담당자·프로젝트명·경로)뿐이다.
#:
#: 이 결함이 나쁜 이유는 유출이 아니라 **거짓 보증**이라는 데 있다.
#: 사용자는 이 목록을 읽고 [전송] 을 누른다. 목록이 틀리면 사용자의 판단
#: 근거가 틀린 것이고, 그러면 "사람이 확인한다"는 방어 겹이 무의미해진다.
EXCLUDED_CATEGORIES_BY_REPRESENTATION: dict[str, tuple[str, ...]] = {
    # 코드가 닫힌 어휘에서 조립한다. 자유 문장이 들어갈 자리가 없다.
    "structured": EXCLUDED_CATEGORIES_DEFAULT,
    # 원문 문장은 남는다. 식별자만 바뀐다 (BR-P-01).
    "pseudonymized": ("고객사명", "요구사항 번호", "금액", "담당자", "사내 경로"),
    # 원문 전송이 이 등급의 정의다. 없음을 약속할 것이 없다.
    "verbatim": (),
}


# ══════════════════════════════════════════════════════════════════════
# Agent
# ══════════════════════════════════════════════════════════════════════


class Persona(BaseModel):
    """Agent 설정. 사람마다 다른 것은 이것뿐이고 구현은 하나다 (FR-23).
    에이전트 추가는 agents.yaml 에 항목 하나 더하는 것으로 끝난다."""

    model_config = ConfigDict(frozen=True)

    entity_id: EntityId
    display_name: str  # "김철수 책임"
    expertise: str  # 본인 작성. 항상 공개
    persona_prompt: str
    knowledge_scope: tuple[str, ...]
    escalation_inbox: str
    daily_limit: int = 50

    @property
    def agent_label(self) -> str:
        """1인칭으로 사람인 척하지 않는다 (FR-26)."""
        return f"{self.display_name}의 Agent"


class AgentCall(BaseModel):
    """Agent 호출 하나. tier 가 단일값이라 등급 혼합이 타입 수준에서 불가능하다."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    entity_id: EntityId
    tier: Tier  # ⚠️ 단일값
    task_schema_id: str
    sub_question_id: str | None = None
    chunk_ids: tuple[str, ...] = ()


class AgentResponse(BaseModel):
    """Agent 응답. ref 기반이다 — 실제 이름은 신뢰 구역 안에서 되돌린다."""

    answer: dict
    confidence: float = Field(ge=0.0, le=1.0)
    citations: tuple[str, ...] = ()  # ref 라벨
    usage: dict | None = None
    revalidated: bool = False  # 브로커가 재검증했는가. False 면 로컬이 거부
    vocab_sha256: str | None = None


class Citation(BaseModel):
    """UI 로 나가는 인용.

    ⚠️ internal_path 필드가 없다 (FR-43). 표시할 방법이 구조적으로 없다.
       경로 자체가 정보를 준다 — corpus/customer-H/ 는 고객사명을 노출한다.
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    display_title: str
    section: str | None = None
    tier: Tier
    as_of: date | None = None
    formality: Formality = "official"


class RehydratedAnswer(BaseModel):
    """재수화된 최종 답변. 사용자가 보는 것."""

    entity_id: EntityId
    agent_label: str  # "김철수 책임의 Agent"
    text: str  # 실제 이름으로 치환됨
    confidence: float = Field(ge=0.0, le=1.0)
    citations: tuple[Citation, ...] = ()
    tier: Tier
    used_external_agent: bool  # False -> "[사내망 밖으로 나간 것 없음]"
    freshness: Freshness | None = None
    session_as_of: datetime | None = None
    unresolved_refs: tuple[str, ...] = ()  # 매핑에 없어 치환 못 한 ref (BR-G-10)


class EscalationDraft(BaseModel):
    """담당자에게 넘길 것. 전달이 아니라 가공이다 —
    질문 원문만 던지면 알림이 하나 늘 뿐이다."""

    summary: str
    situation: tuple[str, ...]
    draft_answer: str
    already_answered: tuple[str, ...] = ()


# ══════════════════════════════════════════════════════════════════════
# 감사
# ══════════════════════════════════════════════════════════════════════


class AuditRecord(BaseModel):
    """경계를 넘은 것의 기록.

    trusted_zone_llm_base_url 을 매 질의 기록하는 이유:
    이 프로젝트의 신뢰 경계는 설정값이다. 설정값이 경계를 정한다면
    그 설정값도 감사 대상이어야 한다. "원문이 어디로 갔는지"가 로그로 증명된다.

    기록하지 않는 것: 원문(Chunk.text), 매핑 테이블, API 키,
    EXAONE reasoning*, HTTP 요청 헤더.
    """

    record_id: str
    at: datetime
    kind: Literal["request", "result"] = "request"
    actor: EntityId
    target_entity_id: EntityId
    model_id: str
    transport: Transport
    trusted_zone_llm_base_url: str
    tier: Tier
    representation: Representation
    payload: dict  # 전문 보관 (이미 sanitize 됨)
    payload_sha256: str
    size_bytes: int
    validation_summary: str
    approved_by: EntityId
    envelope_id: str
    vocab_sha256: str | None = None
    confidence: float | None = None
    citation_count: int | None = None
    usage: dict | None = None


class LeakHit(BaseModel):
    record_id: str
    document_path: str
    ngram: str
    kind: Literal["ngram", "banned_literal", "banned_pattern"]


class LeakReport(BaseModel):
    """유출 전수 검사 결과. 샘플 데이터라서 가능한 검증이다."""

    payloads_scanned: int
    documents_scanned: int
    ngram_size: int
    hits: tuple[LeakHit, ...] = ()
    banned_hits: tuple[LeakHit, ...] = ()
    elapsed_seconds: float = 0.0

    @property
    def clean(self) -> bool:
        return not self.hits and not self.banned_hits


# ══════════════════════════════════════════════════════════════════════
# 지식 저장소 (U2 소유이지만 타입 계약이라 여기 둔다)
# ══════════════════════════════════════════════════════════════════════


class RunInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    cmd: str
    started_at: datetime
    status: Literal["running", "done", "failed"]
    eta: datetime | None = None
    gpu: str | None = None
    log: str | None = None  # ${MESH_DATA_ROOT} 상대 경로


class EditInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    at: datetime


class DatasetInfo(BaseModel):
    """파생 데이터도 등급을 갖는다. 고객 로그에서 파생된 데이터셋은 secret."""

    model_config = ConfigDict(frozen=True)

    path: str
    rows: int | None = None
    derived_from: str | None = None
    tier: Tier = Tier.INTERNAL


class VerifiedQA(BaseModel):
    """승인된 Q&A.

    ⚠️ tier 보존이 설계 결정이다 (Round 2 Q14).
       승인된 답변은 사람이 검토했지만 여전히 사내/기밀 내용을 담을 수 있다.
       이후 Agent 호출에 동원될 때 다른 지식과 똑같이 게이트키퍼를 통과한다.

       "사람이 승인했으니 그대로 내보내도 된다"가 되면, 설계 §3.8이 금지한
       논리("구조 추출을 거쳤으니 무엇이든 보내도 된다")와 같은 구멍이 생긴다.
    """

    model_config = ConfigDict(frozen=True)

    qa_id: str
    question: str
    answer: str
    tier: Tier
    verified_by: EntityId
    verified_at: datetime
    confidence: float = 0.95
    citations: tuple[str, ...] = ()


class Session(BaseModel):
    """사람의 작업 상태. 인덱스는 항상 뒤처지지만 세션은 실시간이다.

    ⚠️ focus / summary 는 원문 취급이다. "고객사 H 인증 요구사항 검토"에는
       고객사명이 있다. 이 값이 에이전트 목록 화면(인증 없이 보이는 화면)에
       그대로 뜨면 게이트키퍼를 우회한 유출이다 (FR-31).
    """

    model_config = ConfigDict(frozen=True)

    entity_id: EntityId
    updated_at: datetime
    focus: str  # ⚠️ 원문
    summary: str  # ⚠️ 원문
    open_paths: tuple[str, ...] = ()
    recent_edits: tuple[EditInfo, ...] = ()
    recent_runs: tuple[RunInfo, ...] = ()
    datasets: tuple[DatasetInfo, ...] = ()
    verified_qa: tuple[VerifiedQA, ...] = ()


class Disclose(BaseModel):
    """무엇을 공개할지. 본인이 정한다 (Round 2 Q13).

    ⚠️ expertise 가 Literal[True] 인 것이 타입 수준 결정이다.
       담당 영역은 본인이 작성한 자기소개이므로 항상 공개해도 안전하고,
       이걸 끄면 지목이 불가능해진다.
    """

    model_config = ConfigDict(frozen=True)

    expertise: Literal[True] = True
    activity_status: bool = False
    question_count_today: bool = False
    current_focus: bool = False


class AgentConfig(BaseModel):
    """config/agents.yaml 의 항목 하나."""

    model_config = ConfigDict(frozen=True)

    entity_id: EntityId
    display_name: str
    expertise: str
    persona_prompt: str
    knowledge_scope: tuple[str, ...]
    escalation_inbox: str
    daily_limit: int = 50
    disclose: Disclose = Disclose()

    def to_persona(self) -> Persona:
        return Persona(
            entity_id=self.entity_id,
            display_name=self.display_name,
            expertise=self.expertise,
            persona_prompt=self.persona_prompt,
            knowledge_scope=self.knowledge_scope,
            escalation_inbox=self.escalation_inbox,
            daily_limit=self.daily_limit,
        )


class AgentCard(BaseModel):
    """지목 목록에 나가는 것.

    ⚠️ current_focus_summary 는 Session.focus 와 다른 필드다.
       세션 원문이 아니라 식별자를 제거한 요약이며, 그 변환도
       게이트키퍼를 통과한다 (FR-31).

    disclose 가 꺼진 필드는 None 이고, UI 는 "비공개"라고 표시하지 않고
    아예 렌더하지 않는다 — "비공개"라는 표시 자체가 정보이기 때문이다.
    """

    entity_id: EntityId
    display_name: str
    expertise: str
    activity_status: Literal["active", "away", "offline"] | None = None
    away_minutes: int | None = None
    question_count_today: int | None = None
    current_focus_summary: str | None = None
    session_as_of: datetime | None = None
    freshness: Freshness | None = None
    daily_limit_reached: bool = False
    #: 이 사람이 어느 컴퓨터에 있는가. `None` 이면 **이 컴퓨터**다.
    #:
    #: 화면이 이 값으로 "이 컴퓨터" / 노드 이름 배지를 그린다. 사용자가
    #: 남의 컴퓨터에 질문을 보내고 있다는 사실을 모르면 안 된다 — 답변에는
    #: 재수화된 실제 이름이 들어오고, 그것이 LAN 을 건너온 것이기 때문이다.
    #:
    #: 값을 채우는 곳은 **그 노드의 `/api/peer/agents` 라우트**다. 질문자 쪽에서
    #: 채우면 URL 밖에 모르고, 사람이 읽을 이름이 필요하다.
    node_name: str | None = None

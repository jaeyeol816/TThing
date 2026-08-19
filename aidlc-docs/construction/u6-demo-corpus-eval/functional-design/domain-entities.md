# U6 — Domain Entities

데이터 파일과 평가 하네스. 런타임 코드가 거의 없다.

---

## 1. `data/vocab.json` — 어휘 사전 ⚠️ Day 1 동결

이 설계의 심장이다. **나갈 수 있는 값의 전체 집합이 여기서 확정된다.**

```json
{
  "version": "1.0.0",
  "slots": {
    "auth_mechanism_class": {
      "kind": "enum", "required": true,
      "allowed": ["password", "challenge_response", "certificate", "biometric", "token_bearer"],
      "description": "authentication mechanism family"
    },
    "session_binding": {
      "kind": "enum", "required": true,
      "allowed": ["required", "optional", "none"],
      "description": "whether credentials are bound to a session"
    },
    "renewal_mode": {
      "kind": "enum", "required": false,
      "allowed": ["explicit", "background_silent", "none"],
      "description": "how credentials are renewed"
    },
    "credential_reuse_allowed": { "kind": "bool", "required": false },
    "max_session_hours":         { "kind": "int", "min": 0, "max": 8760, "required": false },
    "credential_lifetime_hours": { "kind": "int", "min": 0, "max": 8760, "required": false },
    "role": {
      "kind": "enum", "required": true,
      "allowed": ["external_requirement", "our_component", "constraint", "goal"]
    },
    "sampling_strategy_class": {
      "kind": "enum", "required": false,
      "allowed": ["oversample", "undersample", "class_weight", "hybrid", "none"],
      "description": "label imbalance handling family"
    },
    "resource_contention": {
      "kind": "enum", "required": false,
      "allowed": ["gpu_busy", "gpu_free", "unknown"]
    }
  },
  "tasks": ["constraint_conflict_check", "technique_lookup", "rationale_lookup"],
  "domains": ["authentication", "data_pipeline", "deployment"],
  "question_templates": ["conflict_and_mitigation", "technique_explanation", "design_rationale"],
  "task_schemas": {
    "constraint_conflict_check": {
      "domain": "authentication",
      "question_template": "conflict_and_mitigation",
      "entity_roles": ["external_requirement", "our_component"],
      "slots": ["auth_mechanism_class","session_binding","credential_reuse_allowed",
                "max_session_hours","credential_lifetime_hours","renewal_mode"],
      "answer_format": { "conflict": "bool", "reason": "string", "mitigations": "string[]" }
    },
    "technique_lookup": {
      "domain": "data_pipeline",
      "question_template": "technique_explanation",
      "entity_roles": ["our_component"],
      "slots": ["sampling_strategy_class"],
      "answer_format": { "technique": "string", "rationale": "string" }
    },
    "rationale_lookup": {
      "domain": "authentication",
      "question_template": "design_rationale",
      "entity_roles": ["our_component", "constraint"],
      "slots": ["session_binding", "renewal_mode"],
      "answer_format": { "rationale": "string", "tradeoffs": "string[]" }
    }
  },
  "_intentionally_absent": [
    "금액 · 계약금액 · 단가 — 함정 문서의 판별 기준",
    "계약번호 · 요구사항번호 (REQ-nnnn) — 식별자",
    "인명 · 고객사명 · 제품명 · 버전",
    "p99_latency_ms · throughput_tps · 성능 수치 일반",
    "일정 · 마일스톤 날짜",
    "",
    "성능 수치를 넣지 않은 것은 실수가 아니다.",
    "시나리오 3의 폴백이 정확히 여기서 발생한다 (FR-54).",
    "새 task 를 추가할 때 이 목록을 먼저 읽어라 (NFR-M-03).",
    "자유 문자열 슬롯(kind='str')은 존재하지 않으며 추가하지 않는다."
  ]
}
```

**`_intentionally_absent`가 실행되는 문서다.** 새 task를 추가하려는 개발자가 이 파일을 열면 무엇을 넣지 말아야 하는지 먼저 읽는다.

**슬롯 `kind`가 `enum`/`int`/`bool` 세 가지뿐이다.** 자유 문자열이 없으므로 원문이 새어나갈 채널이 없다.

---

## 2. `data/labels.json` — 등급 정답 라벨

```json
{
  "version": "1.0.0",
  "labels": [
    { "path": "corpus/customer-H/req-spec-2026H.md",       "tier": "secret",
      "reason": "고객사 요구사항명세서. 경로 규칙 + 헤더 등급" },
    { "path": "corpus/customer-H/meeting-2026-07.md",      "tier": "secret",
      "reason": "고객사 협의 기록" },
    { "path": "corpus/customer-H/benchmark-prod-2025-11.md","tier": "secret",
      "reason": "고객 환경 실측. 시나리오 3 폴백 유발" },
    { "path": "corpus/kim/docs/auth-design.md",            "tier": "internal" },
    { "path": "corpus/kim/notes/2025-11-auth.md",          "tier": "internal",
      "note": "시나리오 3 김책임 근거. 비공식" },
    { "path": "corpus/choi/docs/auth-review.md",           "tier": "internal",
      "note": "시나리오 3 최민수 근거. 공식. 김책임 메모와 엇갈린다" },
    { "path": "corpus/park/scripts/preprocess_v3.py",      "tier": "internal",
      "note": "시나리오 2 기법 근거" },
    { "path": "corpus/kim/docs/sdk-pricing-tiers.md",      "tier": "secret",
      "reason": "⚠️ 함정 문서 — 겉보기엔 일반 설계 문서인데 고객사 단가가 섞임",
      "trap": true },
    { "path": "corpus/public/oauth-rfc-summary.md",        "tier": "open" }
  ]
}
```

**`trap: true` 항목이 분류기 검증의 핵심이다.**
경로가 `customer-*/`가 아니고 헤더 등급 표기도 없다. **본문의 금액 패턴**만이 단서다. 규칙 4번(금칙어 정규식)이 이걸 잡아야 한다 (BR-C-04).

평가 하네스가 `trap` 문서를 별도로 리포트한다 — 놓치면 가장 위험한 오분류다.

---

## 3. `data/banned.json` — 금칙어

```json
{
  "version": "1.0.0",
  "literals": [
    "H社", "고객사 H", "Customer H", "HanaTel", "하나텔",
    "Nova 게이트웨이", "Nova Gateway", "atlas-ml", "atlas_ml"
  ],
  "patterns": [
    "REQ-\\d{4}",
    "CTR-\\d{6}",
    "SKU-[A-Z]{2}\\d{4}",
    "\\d+\\s*억\\s*원?",
    "\\d{1,3}(,\\d{3})+\\s*원",
    "USD\\s*[\\d,]+",
    "\\$\\s?[\\d,]{4,}"
  ],
  "_notes": [
    "literals: 부분 문자열 매치, 대소문자 무시",
    "patterns: 정규식. 금액 패턴이 함정 문서를 잡는다 (BR-C-04)",
    "이 목록은 검증 4단계와 등급 판정 규칙 3·4번에 둘 다 쓰인다",
    "가명화 대상 목록과 다르다 — 가명화는 치환하고, 여기 걸리면 차단한다"
  ]
}
```

**금액 패턴이 함정 문서 탐지의 유일한 수단이다.** `\d+억`, `1,200,000원`, `USD 50,000`을 모두 잡도록 여러 패턴을 둔다.

한국어 금액 표기가 다양하므로(`12억`, `12억원`, `12 억 원`) 공백을 허용하는 패턴으로 쓴다.

---

## 4. `data/questions.json` — 데모 질문

```json
{
  "version": "1.0.0",
  "scenarios": [
    {
      "id": "s1", "act": 1, "title": "기밀 자료에 외부 AI를 쓴다",
      "asker": "person:choi",
      "targets": ["person:kim"],
      "question": "고객사 인증 요구사항이 우리 SDK 토큰 갱신 주기랑 충돌하나요?",
      "expect": {
        "tier": "secret", "representation": "structured",
        "validation": "6/6", "disposition": "auto",
        "verbatim_sentence_count": 0,
        "task_schema_id": "constraint_conflict_check",
        "audit_record": true,
        "answer_contains": ["충돌", "완화"],
        "answer_must_not_contain": ["REQ_A", "COMP_B"]
      },
      "leak_probes": ["REQ-4412", "EAP-AKA", "H社", "12억"]
    },
    {
      "id": "s2a", "act": 2, "title": "기법 질문 — 사내 가명화",
      "asker": "person:jung", "targets": ["person:park"],
      "question": "박선임님 전처리 v3에서 라벨 불균형 어떻게 처리하셨어요?",
      "expect": { "tier": "internal", "representation": "pseudonymized",
                  "disposition": "auto",
                  "answer_contains": ["RandomOverSampler", "balanced_subsample"],
                  "answer_must_not_contain": ["<PROJ_1>", "atlas_ml"] }
    },
    {
      "id": "s2b", "act": 2, "title": "허락 질문 — 에스컬레이션",
      "asker": "person:jung", "targets": ["person:park"],
      "question": "지금 그 스크립트 돌려봐도 되나요?",
      "expect": { "disposition": "escalate", "inbox_created": true,
                  "draft_has": ["summary", "situation", "draft_answer"] }
    },
    {
      "id": "s3a", "act": 3, "title": "갈리는 답 병기",
      "asker": "person:han", "targets": ["person:kim", "person:choi"],
      "question": "우리 SDK는 왜 세션 바인딩을 안 쓰나요?",
      "expect": { "divergent": true, "answer_count": 2,
                  "answers_not_reordered": true,
                  "note_contains": ["둘 다 사실일 수 있습니다"] }
    },
    {
      "id": "s3b", "act": 3, "title": "검증 실패 → 폴백",
      "asker": "person:han", "targets": ["person:kim"],
      "question": "그 3천 TPS 테스트, 실제 수치가 어떻게 나왔나요?",
      "expect": { "tier": "secret", "disposition": "blocked",
                  "used_external_agent": false,
                  "audit_record": false,
                  "answer_contains": ["제공할 수 없습니다"] }
    }
  ]
}
```

**`expect`가 그대로 예제 테스트의 어서션이 된다** (PBT-10 보완 전략).
`audit_record: false`가 시나리오 3의 결정적 장면을 검증한다 — 레코드가 **없어야** 한다.

**`answer_must_not_contain`이 재수화 검증이다.** 최종 답변에 `REQ_A`가 남아 있으면 치환이 실패한 것이다.

---

## 5. `data/fixtures/` — 목업 응답

```
data/fixtures/
  exaone/
    classify_<sha1(text)[:12]>.json
    extract_<sha1(text+schema_id)[:12]>.json
    pseudonymize_<sha1>.json
    select_paths_<sha1>.json
    focus_summary_<sha1>.json
  agent/
    <schema_id>_<sha1(payload)[:12]>.json
  api/                                   <- U4 UI 선행 개발용
    GET_api_agents.json
    GET_api_health.json
    POST_api_ask_prepare_ready.json
    POST_api_ask_prepare_blocked.json
    POST_api_ask_send_auto.json
    POST_api_ask_send_divergent.json
    POST_api_ask_send_escalate.json
    GET_api_inbox.json
    GET_api_audit.json
    GET_api_audit_zero.json
```

**녹화**: `MESH_RECORD_FIXTURES=1`로 live 실행 시 자동 저장.
**재생**: 키가 없으면 **명시적 실패**. 조용히 기본값을 반환하지 않는다 — 리허설에서 누락을 발견하게 만드는 장치.

`api/` 픽스처는 Day 1에 U3 계약과 함께 만든다. C가 U3 완성을 기다리지 않게.

---

## 6. `LeakReport` — 전수 검사 결과

```python
class LeakHit(BaseModel):
    record_id: str
    document_path: str
    ngram: str
    kind: Literal["ngram", "banned_literal", "banned_pattern"]

class LeakReport(BaseModel):
    payloads_scanned: int
    documents_scanned: int
    ngram_size: int
    hits: tuple[LeakHit, ...]
    banned_hits: tuple[LeakHit, ...]
    elapsed_seconds: float

    @property
    def clean(self) -> bool:
        return not self.hits and not self.banned_hits
```

**로컬 검증(BR-V-05)과 다르다.** 로컬은 *이 호출에 동원된 원문*만 대조하지만, 전수 검사는 **전 문서 × 전 페이로드**를 대조한다.

이유: 등급 판정이 실패해 다른 문서의 원문이 섞였을 가능성을 잡는다. 그런 일이 생기면 로컬 검증은 통과하지만(그 문서를 originals에 넣지 않았으므로) 전수 검사가 잡는다.

**샘플 데이터라서 가능한 검증이다.** 문서 40~60건 × 페이로드 수십 건이라 전량 대조가 몇 초에 끝난다. 실제 데이터라면 불가능하다.

---

## 7. `ClassificationReport`

```python
class Misclassification(BaseModel):
    path: str
    expected: Tier
    actual: Tier
    is_trap: bool
    rule_tier: Tier
    exaone_tier: Tier | None
    reasons: tuple[str, ...]

class ClassificationReport(BaseModel):
    total: int
    correct: int
    secret_total: int
    secret_recalled: int
    trap_total: int
    trap_recalled: int
    misclassified: tuple[Misclassification, ...]

    @property
    def accuracy(self) -> float: return self.correct / self.total
    @property
    def secret_recall(self) -> float: return self.secret_recalled / self.secret_total
    @property
    def passes_gate(self) -> bool:                      # Day 2 게이트
        return self.secret_recall == 1.0 and self.accuracy >= 0.90
```

**`trap_recalled`를 별도로 추적한다.** 함정 문서를 놓치면 전체 재현율이 100%가 아니게 되지만, 어느 문서를 놓쳤는지가 중요하다.

`Misclassification`에 `rule_tier`와 `exaone_tier`를 둘 다 담아 **어느 판정기가 놓쳤는지** 즉시 알 수 있게 한다.

---

## 8. 테스트 가능한 속성 (PBT-01)

U6는 대부분 데이터와 테스트 하네스라 자체 PBT 대상이 적다.

| # | 속성 | 범주 | 대상 |
|---|---|---|---|
| PB-D1 | `vocab.json` 로드 → 직렬화 → 로드 = 항등 | 왕복 | `Vocabulary` |
| PB-D2 | `sweep_for_leaks`는 문서·페이로드 **순서에 무관** | 교환 | `sweep_for_leaks` |
| PB-D3 | `sweep_for_leaks`는 페이로드에 원문 5-gram을 심으면 **반드시 탐지** | 불변식 | `sweep_for_leaks` |

**PB-D3가 검사기 자체를 검사한다.** "유출 0건"이라는 결과를 믿으려면 검사기가 실제로 유출을 탐지하는지 확인해야 한다. 임의 원문에서 5-gram을 뽑아 페이로드에 심고, 탐지되는지 검사한다.

> 검사기가 아무것도 못 잡는 버그가 있으면 "유출 0건"은 무의미하다. 이 속성이 그걸 막는다.

**PBT 미적용 (N/A)**
- 코퍼스 문서 자체 — 데이터. 테스트 대상이 아니다
- 평가 하네스 — 예제 기반. 정답 라벨과 비교하는 것이 본질
- PBT-06 상태 기반 — 전부 stateless. **N/A**
- PBT-05 오라클 — `labels.json`이 오라클 역할을 하지만 이건 예제 기반 정답 비교이고 PBT가 아니다. **N/A**

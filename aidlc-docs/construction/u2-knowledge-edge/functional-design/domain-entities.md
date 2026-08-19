# U2 — Domain Entities

`src/mesh/store.py` + `config/agents.yaml` + `data/sessions/*.json`.
`Chunk`, `Tier`, `Freshness`는 U1 `schemas.py`에서 온다 (Day 1 동결 계약).

---

## 1. Session

```python
class RunInfo(BaseModel):
    cmd: str
    started_at: datetime
    status: Literal["running", "done", "failed"]
    eta: datetime | None = None
    gpu: str | None = None
    log: str | None = None                  # ${MESH_DATA_ROOT} 상대 경로

class EditInfo(BaseModel):
    path: str                                # ${MESH_DATA_ROOT} 상대 경로
    at: datetime

class DatasetInfo(BaseModel):
    path: str
    rows: int | None = None
    derived_from: str | None = None
    tier: Tier                               # ⚠️ 파생 데이터도 등급을 갖는다

class Session(BaseModel):
    entity_id: str                           # "person:kim"
    updated_at: datetime
    focus: str                               # ⚠️ 원문. 고객사명 포함 가능
    summary: str                             # ⚠️ 원문
    open_paths: tuple[str, ...]              # ${MESH_DATA_ROOT} 상대 경로
    recent_edits: tuple[EditInfo, ...] = ()
    recent_runs: tuple[RunInfo, ...] = ()
    datasets: tuple[DatasetInfo, ...] = ()
    verified_qa: tuple[VerifiedQA, ...] = () # 로드 시 병합됨
```

**`focus`와 `summary`가 원문 취급인 것이 중요하다.** "고객사 H 인증 요구사항 검토"에는 고객사명이 있다. 이 값이 에이전트 목록 화면(인증 없이 보이는 화면)에 그대로 뜨면 **게이트키퍼를 우회한 유출**이다 (FR-31).

**`DatasetInfo.tier`**: 시나리오 2에서 `preproc_v3/`가 `derived_from: "customer-H session logs"`이고 `tier: secret`이다. 파생 데이터도 원본의 등급을 물려받는다.

---

## 2. VerifiedQA

```python
class VerifiedQA(BaseModel):
    qa_id: str
    question: str
    answer: str
    tier: Tier                               # ⚠️ 보존 필수
    verified_by: str                          # "person:park"
    verified_at: datetime
    confidence: float = 0.95
    citations: tuple[str, ...] = ()
```

**`tier` 보존이 설계 결정이다** (Round 2 Q14).
승인된 답변은 사람이 검토했지만 여전히 사내/기밀 내용을 담을 수 있다. 이후 이 항목이 Agent 호출에 동원될 때 **다른 지식과 똑같이 게이트키퍼를 통과**시킨다.

"사람이 승인했으니 그대로 내보내도 된다"가 되면, 설계 §3.8이 금지한 논리("구조 추출을 거쳤으니 무엇이든 보내도 된다")와 같은 종류의 구멍이 생긴다.

**영속 위치**: `data/verified/{entity_id}.json` — 세션과 **별도 파일**이다. 세션은 데몬이 덮어쓰는 휘발성 상태이므로 승인된 QA를 세션 안에 넣으면 사라진다.

```json
{ "entity_id": "person:park",
  "items": [ { "qa_id":"qa_001", "question":"preprocess_v3 라벨 불균형 처리 방식",
               "answer":"...", "tier":"internal",
               "verified_by":"person:park", "verified_at":"2026-08-19T14:36:00+09:00",
               "confidence":0.95, "citations":["preprocess_v3.py"] } ] }
```

---

## 3. AgentCard — 목록에 나가는 것

```python
class Disclose(BaseModel):
    expertise: Literal[True] = True           # 항상 공개. 변경 불가
    activity_status: bool = False
    question_count_today: bool = False
    current_focus: bool = False

class AgentConfig(BaseModel):
    entity_id: str
    display_name: str                          # "김철수 책임"
    expertise: str                              # 본인 작성: "인증 · SSO · SDK 보안"
    persona_prompt: str
    knowledge_scope: tuple[str, ...]            # 세션 + 허용 경로 glob
    escalation_inbox: str
    daily_limit: int = 50
    disclose: Disclose = Disclose()

class AgentCard(BaseModel):
    """목록 API 응답. 원문이 없다."""
    entity_id: str
    display_name: str
    expertise: str
    activity_status: Literal["active","away","offline"] | None = None
    away_minutes: int | None = None
    question_count_today: int | None = None
    current_focus_summary: str | None = None    # ⚠️ 식별자 제거 요약. 세션 focus 원문이 아니다
    session_as_of: datetime | None = None
    freshness: Freshness | None = None
```

**`Disclose.expertise`가 `Literal[True]`인 것이 타입 수준 결정이다.** 담당 영역은 본인이 작성한 자기소개이므로 항상 공개해도 안전하고, 이걸 끄면 지목이 불가능해진다. 타입으로 `False`를 막았다.

`current_focus_summary`는 `Session.focus`와 **다른 필드다.** 변환은 게이트키퍼를 통과한다:
```
"고객사 H 인증 요구사항 검토 + SDK v3.2 토큰 정책"   (Session.focus, 원문)
  -> Gatekeeper 로 식별자 제거 요약
"인증 관련 작업 중"                                   (current_focus_summary)
```

---

## 4. `config/agents.yaml`

```yaml
agents:
  - entity_id: person:kim
    display_name: 김철수 책임
    expertise: 인증 · SSO · SDK 보안
    persona_prompt: |
      당신은 김철수 책임의 Agent입니다. 1인칭으로 김책임인 척하지 마십시오.
      당신이 받은 것은 실제 문서가 아니라 구조 요약 또는 가명화된 텍스트입니다.
      답변에서 대상은 반드시 참조 기호(REQ_A, COMP_B, <SYS_1>)로 지칭하십시오.
      근거가 부족하면 추측하지 말고 confidence 를 낮게 보고하십시오.
      김책임의 전문 영역: 인증 아키텍처, SSO 통합, SDK 보안.
    knowledge_scope:
      - corpus/kim/**
      - corpus/customer-H/**        # 협의 담당이므로 접근 범위에 포함
    escalation_inbox: person:kim
    daily_limit: 50
    disclose:
      activity_status: true
      question_count_today: true
      current_focus: true

  - entity_id: person:park
    display_name: 박선영 선임
    expertise: 데이터 파이프라인 · 모델 학습
    persona_prompt: | ...
    knowledge_scope: [corpus/park/**]
    escalation_inbox: person:park
    disclose: { activity_status: true, question_count_today: true, current_focus: true }

  - entity_id: person:choi
    display_name: 최민수 선임
    expertise: SDK 인증 모듈 · 배포 파이프라인
    persona_prompt: | ...
    knowledge_scope: [corpus/choi/**]
    escalation_inbox: person:choi
    disclose: { activity_status: true, question_count_today: true, current_focus: true }
```

**에이전트 추가가 이 파일에 항목 하나 더하는 것으로 끝나야 한다** (FR-23). 코드를 건드리지 않는다.
`knowledge_scope`가 그 사람의 지식 범위를 정하고, `KnowledgeStore.read()`가 이 glob 밖의 경로를 거부한다.

---

## 5. 세션 JSON 3개 (데모 고정)

### `data/sessions/person_kim.json` — 활동 중
```json
{ "entity_id": "person:kim",
  "updated_at": "2026-08-19T14:31:20+09:00",
  "focus": "고객사 H 인증 요구사항 검토 + SDK v3.2 토큰 정책",
  "summary": "고객사 H의 인증 요구사항과 자사 SDK 토큰 갱신 정책의 정합성을 검토 중",
  "open_paths": [
    "corpus/customer-H/req-spec-2026H.md",
    "corpus/kim/docs/auth-design.md"
  ],
  "recent_edits": [{ "path": "corpus/kim/docs/auth-design.md", "at": "2026-08-19T11:02:00+09:00" }],
  "recent_runs": [], "datasets": [] }
```

### `data/sessions/person_park.json` — 학습 실행 중
```json
{ "entity_id": "person:park",
  "updated_at": "2026-08-19T14:33:05+09:00",
  "focus": "atlas-ml 전처리 v3 재학습",
  "summary": "고객 로그 파생 데이터셋 v3로 재학습 중. 라벨 불균형 처리 방식을 조정함",
  "open_paths": [
    "corpus/park/scripts/preprocess_v3.py",
    "corpus/park/configs/v3.yaml"
  ],
  "recent_edits": [{ "path": "corpus/park/scripts/preprocess_v3.py", "at": "2026-08-19T13:47:00+09:00" }],
  "recent_runs": [{
    "cmd": "python train.py --config configs/v3.yaml",
    "started_at": "2026-08-19T14:02:00+09:00", "status": "running",
    "eta": "2026-08-19T17:10:00+09:00", "gpu": "cuda:0",
    "log": "corpus/park/runs/2026-08-19/train.log" }],
  "datasets": [{ "path": "corpus/park/data/preproc_v3/", "rows": 420135,
                 "derived_from": "customer-H session logs", "tier": "secret" }] }
```

### `data/sessions/person_choi.json` — 자리 비움 2시간
```json
{ "entity_id": "person:choi",
  "updated_at": "2026-08-19T12:30:00+09:00",
  "focus": "SDK v3.2 배포 준비",
  "summary": "SDK v3.2 릴리스 파이프라인 점검 중",
  "open_paths": [
    "corpus/choi/docs/auth-review.md",
    "corpus/choi/docs/release-checklist.md"
  ],
  "recent_edits": [], "recent_runs": [], "datasets": [] }
```

**데모 시각 기준**: 시연 시각을 `2026-08-19T14:35` 부근으로 가정하면 kim/park는 `LIVE`(4분·2분 경과), choi는 `STALE`(125분 경과)이 된다. 시나리오 3의 "자리 비움 (2시간)"이 자연스럽게 나온다.

**주의**: 실제 시연 날짜가 다르면 신선도가 전부 `EXPIRED`가 된다. `MESH_DEMO_NOW` 환경변수로 기준 시각을 고정할 수 있게 한다 (데모 재현성).

---

## 6. 경로 표현

```python
def expand(rel: str, root: Path) -> Path:
    """${MESH_DATA_ROOT} 치환 + 경로 탈출 거부."""
    s = rel.replace("${MESH_DATA_ROOT}/", "").replace("${MESH_DATA_ROOT}", "")
    p = (root / s).resolve()
    if not p.is_relative_to(root.resolve()):
        raise PathEscapeError(rel)
    return p
```

**저장된 경로는 항상 `MESH_DATA_ROOT` 상대 경로다** (NFR-PO-01, FR-22).
`~/work/...` 같은 절대 경로를 저장하지 않는다. 다른 컴퓨터에서 그대로 동작해야 한다.

시나리오 문서의 `~/work/customer-H/req-spec-2026H.md`는 → `corpus/customer-H/req-spec-2026H.md`로 매핑한다. UI에는 `display_title`("고객사 H 요구사항명세서")만 보이므로 사용자 경험은 동일하다.

---

## 7. 테스트 가능한 속성 (PBT-01)

| # | 속성 | 범주 | 대상 |
|---|---|---|---|
| PB-S1 | `expand()`는 `root` 밖 경로에 대해 항상 `PathEscapeError` | 불변식 | `expand` |
| PB-S2 | `Session` JSON 직렬화 → 역직렬화 = 항등 | 왕복 | `Session` |
| PB-S3 | `freshness(session, now)`는 `now`가 커질수록 단조 악화 (LIVE→STALE→EXPIRED) | 불변식 | `freshness` |
| PB-S4 | `list_agents()` 결과에 `Session.focus`/`summary` 문자열이 등장하지 않는다 | 불변식 | `list_agents` |
| PB-S5 | `read(paths)`가 반환한 모든 `Chunk.internal_path`가 `knowledge_scope` glob에 매치 | 불변식 | `read` |

**PB-S4가 FR-31의 검증이다.** 임의의 세션(고객사명 포함)을 생성해 `list_agents()`를 호출하고, 결과 JSON에 원문 문자열이 없음을 확인한다.

**PBT 미적용**: 세션 로드·파일 읽기의 I/O 자체는 예제 테스트. `select_paths()`는 EXAONE 호출이라 PBT 대상이 아니다.

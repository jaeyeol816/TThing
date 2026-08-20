# TODO — 알고리즘 / 백엔드 로직

Gatekeeper 판정, Agent orchestration, 보안 처리 로직에 관한 작업.

---

## ✅ 구현 완료

### 등급 판정 (Classifier)

6개 규칙을 순서대로 적용, 첫 매치에서 반환:

| # | 조건 | 결과 |
|---|---|---|
| 1 | 경로가 SECRET glob 매치 | **SECRET** |
| 2 | 본문에 금칙어 리터럴 | **SECRET** |
| 3 | 본문이 금칙어 정규식 매치 | **SECRET** |
| 4 | 헤더에 등급 표기 | 표기된 등급 |
| 5 | 경로가 사내 glob 매치 | **INTERNAL** |
| 6 | 해당 없음 | **INTERNAL** (기본값) |

- 기계적 검사(1~3)를 작성자 자기 신고(4)보다 앞에 둠 — 헤더 조작으로 우회 불가
- `OPEN`은 헤더 + 경로 두 신호 동시 충족 시에만 인정
- EXAONE 보조 판정 후 `max(규칙, EXAONE)` 채택
- EXAONE 실패·타임아웃·범위 밖 값 → **SECRET으로 간주** (fail closed)

### 표현 변환 (등급별 분기)

```
SECRET   → extractor.extract()      슬롯 채우기, 원문 0개
INTERNAL → pseudonymizer.apply()    식별자 치환, 기술 용어 보존
OPEN     → 변환 없음                원문 그대로
```

**슬롯 채우기의 루프 방향이 보안 속성**:
```python
for slot in schema.slots:       # 스키마를 순회 (O)
    if slot.name in raw:
        result[slot.name] = coerce(raw[slot.name], slot)
```
모델 출력을 순회하지 않으므로 "검사를 잊어서 유출"이 구조적으로 불가능.

### 검증 6단계

| 단계 | 검사 |
|---|---|
| schema | 페이로드 키가 스키마 허용 집합 내인가 |
| vocab | 값이 어휘 사전 허용값인가 |
| range | int 슬롯이 min/max 범위 내인가 |
| banned | 금칙어가 섞였는가 |
| ngram | 원문 n-gram과 겹치는가 (5-gram, 사내는 3-gram) |
| size | 크기 상한 초과인가 |

- 첫 실패에서 멈추지 않고 전부 수집 (진단 완전성)
- 실패 시 `ValidationBlocked` → `answer_in_zone` 폴백

### passthrough 경로

구조 추출 실패 시 SECRET이 아니면 원문/가명화본을 직접 전달:

```
plan_calls() → choose_schema() 실패
  → 등급이 SECRET → 차단 (원문 유출 방지)
  → 등급이 INTERNAL/OPEN → passthrough (가명화 적용)
```

### 지식 갱신 (Knowledge Miss → Search → Save)

```
쿼리 → 세션에 근거 없음
  → EXAONE으로 공개 정보 기반 답변 생성
  → Gatekeeper 등급 판정 (SECRET이면 저장 거부)
  → agents/{id}/data/kb/{slug}.md 저장
  → session.json open_paths 등록
  → 다음 쿼리부터 후보가 됨
```

### broadcast (ask_other_agents 백엔드)

```
BroadcastService.ask()
  → 모든 Agent에 asyncio.gather로 동시 질의
  → 각 Agent: EXAONE으로 "내 전문 영역인가?" 판단
  → 관련 있으면 prepare/send 흐름 실행
  → 관련 없으면 skipped
  → 응답 취합 → tool_result 텍스트 생성
```

### Bedrock tool-use 인프라

- `toolConfig` 파라미터 전달
- `stopReason == "tool_use"` 감지
- `toolUse` 블록에서 도구명·입력 추출
- `tool_handler` 콜백 실행 후 `toolResult` 메시지로 재요청
- 최대 3회 왕복

### 감사 로그

- 경계 통과 **직전** 기록 (실패해도 "나갔다"는 사실 남김)
- 금지 필드 재귀 검사 — `text`, `mapping`, 자격증명 등이 있으면 거부
- 신뢰 구역 내 처리는 `local_queries`에만 (질문 해시만 저장)

---

## 🔄 구현 중 / 불완전

### ~~A1. ask_other_agents를 Claude가 직접 호출하도록 전환~~ ✅ 완료

**구현된 흐름**:
```
hub_ask() → asker 지식 로드 → passthrough 페이로드 생성
          → Claude에게 toolConfig(ask_other_agents) + 자유 텍스트 출력 계약 전달
          → Claude가 스스로 판단:
             a. 자체 지식으로 답 가능 → 바로 답변
             b. 다른 Agent 필요 → ask_other_agents 호출 → broadcast
          → 실패 시 EXAONE 폴백
```

### ~~A2. broadcast 질의 자체의 등급 판정~~ ✅ 완료

tool_handler 내부에서 Claude가 생성한 질문을 `gatekeeper.classify()` 후 SECRET이면 차단.

---

## ❌ 구현 필요

### A3. build_agent — Agent 자동 생성

폴더 스캔 후 `agents.yaml`에 항목 추가.

작업:
- [ ] `agents/{name}/` 디렉터리 스캔
- [ ] `security_protocol/`, `data/` 존재 확인
- [ ] `data/` 내용으로 expertise 추론 (EXAONE 사용)
  - 파일명·디렉터리 구조에서 도메인 유추
  - 또는 사용자가 GUI에서 직접 입력
- [ ] `entity_id` 생성 규칙 — 폴더명 → `person:{name}`
- [ ] `agents.yaml` 항목 추가 (knowledge_scope 자동 생성)
- [ ] `gatekeeper/session.json` 초기 생성
- [ ] `DataBundle` 리로드 (서버 재시작 없이)

### A4. Agent 직접 선택 질의 백엔드

**현재**: `hub_ask(question, asker)` — 항상 broadcast
**목표**: `hub_ask(question, asker, targets=[...])` — targets 있으면 직접 질의

작업:
- [ ] `HubAskRequest`에 `targets: list[str] | None` 추가
- [ ] `hub_ask`에서 targets 있으면 broadcast 생략, 지정 Agent만 질의
- [ ] targets 없으면 기존 broadcast 동작

### A5. knowledge.md를 시스템 프롬프트에 주입

작업:
- [ ] `Config.agent_knowledge_path(entity_id)` 추가
- [ ] `agents/{id}/agent/knowledge.md` 읽기
- [ ] `build_system_prompt()`에 knowledge 섹션 추가
- [ ] knowledge.md도 Gatekeeper 등급 판정 대상에 포함
  - 이 파일이 경계를 넘으므로 기밀이 있으면 안 됨

### A6. 세션 상태 자동 갱신 (데몬)

**현재**: `session.json`을 사람이 수동 편집
**목표**: 파일 편집·실행 상태를 자동 반영

작업:
- [ ] 파일 시스템 watcher — `data/` 변경 감지
- [ ] `recent_edits` 자동 갱신
- [ ] 실행 중 프로세스 감지 (선택)
- [ ] `updated_at` 갱신 → 신선도 판정에 반영

**우선순위 낮음** — 설계상 의도적으로 없앤 것 (BR-S-08)

### A7. 외부 시스템 연동

- [ ] Jira — 이슈/코멘트를 Chunk로 변환
- [ ] Collab — 문서/스페이스
- [ ] OneDrive — 파일
- [ ] 각 소스별 등급 판정 규칙 정의

**우선순위 낮음** — 데모에서는 로컬 파일로 충분

---

## 알려진 이슈

| 이슈 | 영향 | 대응 |
|---|---|---|
| `answer_in_zone` 응답에 `[사내 · 사내망 밖으로 나간 것 없음]` 접두사 노출 | UX | 백엔드에서 분리 또는 프론트에서 제거 |
| `person:kim`의 customer-H 문서가 SECRET이라 자기 질문도 구조 추출 실패 | 답변 품질 | passthrough로 우회 중, 어휘 사전 확장 검토 |
| broadcast 시 각 Agent가 독립적으로 prepare/send → LLM 호출 3배 | 비용·지연 | 관련성 판단으로 이미 필터링됨, 추가 최적화 여지 |
| tool-use 중간 턴의 감사 로그가 없음 | 추적성 | tool_handler 내부에서 별도 기록 필요 |

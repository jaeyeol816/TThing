# TODO — 보안 이슈를 고려한 질의 응답 Agent Orchestration 서비스

기획서(`보안 이슈를 고려한 질의 응답 Agent orchestration 서비스.md`) 대비 구현 현황과 남은 작업.

---

## 요구사항 분석

### 핵심 명제

> **대리 Agent를 세우려면 Agent가 업무 내용을 알아야 하지만, 기밀을 사외 LLM에 넘길 수 없다.**

이 모순을 EXAONE(사내) + Claude(사외) 역할 분리로 해결한다.

| 역할 | 담당 | 근거 |
|---|---|---|
| 원문 열람 · 등급 판정 · 비식별 처리 | EXAONE (사내) | 기밀 입력 가능 |
| 추론 · 답변 생성 | Claude (사외) | 성능 우위 |
| 경계 통과 여부 결정 | Gatekeeper (룰 + EXAONE) | fail closed |

### 5개 구성 요소 (기획서 §3)

1. **개인 Agent** — Jira/Collab/OneDrive/로컬 자료를 md·db로 관리하는 페르소나 Agent
2. **GateKeeper** — EXAONE 백본 + 룰베이스로 보안 판정 → 마스킹 / 차단 결정
3. **사외 LLM** — 개인 Agent의 백본 (Claude)
4. **ask_other_agent 도구/스킬** — 사외 LLM이 다른 Agent에게 질의하는 스킬
5. **보안 프로토콜** — 전사 / 팀 / 개인 3계층, GUI에서 수정 가능

### 데모 워크플로 (기획서 §4)

```
사용자 → 자기 Agent와 대화
  ├── 스스로 해결 가능 → 바로 답변
  └── 불가능 → 다른 Agent들에 broadcast
        → 각 Agent가 자기 data/ 접근
        → Gatekeeper가 security_policy 준수 확인 + 비식별 처리
        → 응답 생성 → 사용자 Agent로 전송
  → 사용자 Agent가 답변 정리 → GUI 표시
```

---

## ✅ 구현 완료

### 폴더 구조 (§4.1)

```
agents/
├── shared/                    공유 자산
│   ├── vocab.json             어휘 사전 (경계 통과 가능 값의 전체 집합)
│   ├── banned.json            금칙어 (리터럴 + 정규식)
│   ├── pseudonyms.json        가명화 대상
│   ├── labels.json            등급 정답 라벨
│   ├── mesh.db                감사 로그 (SQLite)
│   ├── fixtures/              LLM 응답 캐시 (오프라인 데모)
│   ├── security_protocol/     전사 · 팀 프로토콜
│   └── data/public/           공개 문서
│
└── person_kim/
    ├── data/                  업무 자료 (md, py, yaml, log)
    │   ├── customer-H/        고객사 자료 (SECRET)
    │   ├── docs/
    │   ├── notes/
    │   ├── uploads/           사용자 업로드
    │   └── kb/                자동 생성 지식
    ├── gatekeeper/
    │   ├── session.json       현재 작업 상태
    │   └── verified.json      승인된 Q&A
    └── security_protocol/
        └── protocol.yaml      개인 프로토콜
```

### GateKeeper (§3)

- 6단계 등급 판정 규칙 (경로 glob → 금칙어 리터럴 → 정규식 → 헤더 표기 → 사내 경로 → 기본값)
- EXAONE 보조 판정 후 `max(규칙, EXAONE)` 채택
- EXAONE 실패 시 SECRET으로 간주 (fail closed)
- 구조 추출 — SECRET 문서를 슬롯 채우기로 원문 0개 페이로드 생성
- 가명화 — INTERNAL 문서의 식별자만 치환, 기술 용어 보존
- 검증 6단계 — 스키마 / 어휘 / 범위 / 금칙어 / 원문대조 / 크기
- passthrough 경로 — 구조 추출 실패 시 SECRET 아니면 직접 전달
- 감사 로그 — 경계 통과 직전 기록 (실패해도 흔적 남김)

### 보안 프로토콜 (§3)

- 전사 (`company`) / 팀 (`team`) / 개인 (`personal`) 3계층
- 설정 항목: 키워드, 정규식 패턴, 디렉토리 glob, 파일 확장자, EXAONE 컨텍스트 힌트
- REST API 5개 — 목록 / 조회 / 생성·수정 / 삭제 / 머지 결과 미리보기
- GUI 모달에서 CRUD — 저장 즉시 분류 규칙 반영 (서버 재시작 불필요)

### 개인 Agent — 데이터 (§3)

- `data/` 하위 md / py / yaml / log / json 읽기
- 세션 상태 — 현재 작업(focus), 열린 파일(open_paths), 최근 편집, 실행 로그
- knowledge_scope glob으로 Agent 간 지식 격리
- 승인된 Q&A 축적 (`verified.json`)
- 지식 갱신 — 근거 없을 때 EXAONE으로 답변 생성 후 `data/kb/`에 md 저장 + 세션 등록

### ask_other_agents (§3) — 백엔드

- `BroadcastService` — 모든 Agent에 동시 질의
- EXAONE 관련성 판단 — 자기 전문 영역 아니면 skip
- 관련 있는 Agent만 prepare/send 흐름 실행
- 허브 API — `POST /api/hub/ask`
- Bedrock tool-use 인프라 — `toolConfig` 전달, `tool_use` 블록 처리, 최대 3회 왕복

### GUI (§5)

- 채팅 인터페이스 (사용자/Agent 말풍선, 로딩 인디케이터)
- 우측 조직도 패널 — Agent 목록, 활동 상태(활동중/자리비움/오프라인)
- 응답 후 Agent별 상태 표시 — answered / skipped / error
- 말풍선 테두리 색상 — 초록(통과) / 노랑(SECRET) / 빨강(차단)
- 말풍선 하단 collapsible — Gatekeeper 등급, 검증 6단계, 표현 방식

---

## 🔄 구현 중 / 불완전

### 1. ask_other_agents가 Claude의 스킬로 동작하지 않음

**현재**: Python 서버(`hub_ask`)가 broadcast 여부를 판단
**목표**: Claude가 `ask_other_agents` 도구를 스스로 선택해서 호출

- [ ] `hub_ask`를 Claude tool-use 모드로 전환
- [ ] 시스템 프롬프트에 도구 사용 지침 추가
- [ ] 출력 계약(JSON) 과 tool-use 흐름 충돌 해결

**근거**: 기획서 §3 — "사외 llm의 백본에 붙는 도구/스킬"

### 2. 조직도 실시간 소통 표시

**현재**: 응답 완료 후에만 상태 표시
**목표**: 소통 중일 때 테두리 노란색으로 실시간 강조

- [ ] WebSocket 또는 SSE 엔드포인트 추가
- [ ] broadcast 시작 시 대상 Agent에 "소통 중" 이벤트 전송
- [ ] 프론트에서 실시간 테두리 색상 변경

**근거**: 기획서 §5.2 — "agent끼리 소통을 하는 경우, 해당 대상자의 요소의 테두리를 노란색으로 강조"

### 3. 말풍선 collapsible 상세 정보 부족

**현재**: 내 Agent의 Gatekeeper 등급/검증만 표시
**목표**: 다른 Agent와 소통한 내용 + 각 Agent의 Gatekeeper 처리 과정 포함

- [ ] `HubAskResponse`에 각 Agent의 Gatekeeper 상세 포함
- [ ] broadcast 질의 내용 / 응답 내용 표시
- [ ] Agent별 등급 판정 근거 + 비식별 처리 내역 표시

**근거**: 기획서 §5.4 — "PoC, MVP 프로젝트이기 때문"

---

## ❌ 구현 필요

### 4. build_agent 도구

폴더가 존재하면 Agent를 새로 생성하고 조직도에 추가.

- [ ] `agents/{name}/` 스캔 — `security_protocol/`, `data/` 존재 확인
- [ ] `agents.yaml`에 항목 자동 추가 (entity_id, display_name, expertise 추론)
- [ ] `gatekeeper/session.json` 초기 생성
- [ ] API 엔드포인트 — `POST /api/agents/build`
- [ ] GUI 버튼 — 조직도 하단 "Agent 추가"
- [ ] 생성 후 조직도 자동 갱신

**근거**: 기획서 §5.3

### 5. 사용자가 Agent를 직접 선택해 질의

**현재**: 항상 broadcast
**목표**: 조직도에서 클릭 / Ctrl+클릭으로 특정 Agent에 직접 전송

- [ ] 조직도 카드 클릭 이벤트 복원 (선택 상태 관리)
- [ ] Ctrl+클릭 다중 선택
- [ ] 선택된 대상이 있으면 broadcast 대신 직접 질의
- [ ] 선택 없으면 기존 broadcast 동작 유지
- [ ] 선택 상태 시각적 구분 (테두리 / 배경)

**근거**: 기획서 §5.2

### 6. agent/ 폴더 — skill, knowledge.md

**현재**: `data/`, `gatekeeper/`, `security_protocol/` 3개만
**목표**: `agent/` 폴더 추가 — Agent의 스킬 정의와 지식 요약

- [ ] `agents/{name}/agent/` 디렉터리 추가
- [ ] `knowledge.md` — Agent가 아는 것의 요약 (Claude 시스템 프롬프트에 주입)
- [ ] `skills/` — Agent별 추가 도구 정의
- [ ] Config에 경로 헬퍼 추가
- [ ] 시스템 프롬프트 조립 시 `knowledge.md` 포함

**근거**: 기획서 §4.1 — "Agent 폴더에는 agent에 필요한 skill, knowledge.md 등이 존재한다"

### 7. 보안 프로토콜 파일명 정합

**현재**: `security_protocol/protocol.yaml` 하나
**목표**: `lg_policy.md`, `team_policy.md`, `personal_policy.md` 3개

- [ ] 파일명 규칙 변경 또는 기획서와 절충안 결정
- [ ] 전사 정책을 각 Agent 폴더에도 복사할지 결정 (현재는 shared에만)

**근거**: 기획서 §4.1

### 8. 외부 시스템 연동

- [ ] Jira 연동 — 이슈 / 코멘트를 지식 소스로
- [ ] Collab 연동 — 문서 / 스페이스
- [ ] OneDrive 연동 — 파일
- [ ] 로컬 PC 자료 스캔 — 지정 경로 자동 인덱싱

**근거**: 기획서 §3 — "Jira, collab, onedrive, 로컬 pc의 자료 등"

**우선순위 낮음** — 데모에서는 로컬 파일로 충분

### 9. Tauri 데스크톱 앱

- [ ] `app/` 디렉터리의 Tauri 셸 완성
- [ ] 백엔드 자동 기동 확인
- [ ] 빌드 스크립트 검증 (`make app`)

**근거**: 기획서 §3 — "Tauri 기반의 웹 애플리케이션"

**우선순위 낮음** — 브라우저로도 데모 가능

---

## 우선순위 제안

### P0 — 데모 필수

1. **ask_other_agents를 Claude 스킬로** (§3 핵심 명제)
2. **조직도 실시간 소통 표시** (§5.2 시각적 임팩트)
3. **말풍선 상세 정보 확장** (§5.4 PoC 설명력)

### P1 — 완성도

4. **build_agent 도구** (§5.3 확장성 증명)
5. **Agent 직접 선택 질의** (§5.2 사용성)
6. **agent/ 폴더 + knowledge.md** (§4.1 구조 정합)

### P2 — 선택

7. 보안 프로토콜 파일명 정합
8. 외부 시스템 연동
9. Tauri 앱

---

## 알려진 이슈

- `hub_ask`에서 `answer_in_zone` 응답에 `[사내 · 사내망 밖으로 나간 것 없음]` 접두사가 붙어 사용자에게 노출됨 → 프론트에서 제거하거나 백엔드에서 분리 필요
- `person:kim`의 `data/customer-H/` 문서가 SECRET이라 자기 질문도 구조 추출 실패 → passthrough 경로로 우회되지만 답변 품질 저하
- 세션 파일의 `open_paths`가 하드코딩 — 실제 작업 상태를 반영하는 데몬 없음 (설계상 의도, BR-S-08)
- Windows에서 preflight 실행 시 `PYTHONUTF8=1` 필요 (run.ps1 / run.sh에 반영됨)

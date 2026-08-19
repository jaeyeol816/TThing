# 대리 에이전트 메시 (Delegate Agent Mesh)

> 지식을 가진 사람 앞에 대리 에이전트를 세우고, 질문을 사람이 아니라 에이전트에게 보낸다.
> 기밀 자료는 신뢰 구역 안에서만 읽히고, 외부 AI에는 **데이터가 아니라 문제의 구조만** 나간다.

해커톤 MVP. 설계 문서는 `requirements/`, 구현 설계는 `aidlc-docs/`.

---

## 빠른 시작

```bash
make setup                  # uv sync + .env 생성 + 디렉터리
# .env 에 FRIENDLI_TOKEN 기입
make preflight              # 환경 검증 — 사람이 읽을 진단을 출력한다
make test                   # 단위 + 속성 테스트
make run                    # http://127.0.0.1:8080
```

### 네트워크 없이 (오프라인 데모)

```bash
EXAONE_MODE=mock AGENT_TRANSPORT=mock make run
```

녹화된 픽스처로 3막 전체가 돈다. **화면에 목업 모드가 표시된다** — 심사자를 속이지 않는다.
목업 모드에서도 검증 6단계·화이트리스트 조립·감사 로그는 **실제 코드가 돈다.**

### 필요한 것

| | |
|---|---|
| `uv` | Python 3.12 고정 + 패키지 관리 |
| Node (`npx`) | CDK 배포할 때만 |
| git | |
| ~~`aws` CLI~~ | **필요 없다** (boto3로 대체) |

---

## 구조

```
src/mesh/            애플리케이션
  config.py          환경변수 · 경로 가드 · 로깅
  schemas.py         타입 계약  ⚠️ Day 1 동결
  gatekeeper.py      Agent 를 감싸는 막 — 경계를 넘는 유일한 통로
  classifier.py      등급 판정  max(규칙, EXAONE)
  extractor.py       구조 추출  슬롯 채우기 + 코드가 조립
  validator.py       검증 6단계  순수 함수
  pseudonymizer.py   가명화 (사내 등급)
  rehydrator.py      재수화  기호 → 실제 이름
  audit.py           감사 로그 (SQLite 원본)
  store.py           세션 + 파일 직접 읽기
  agent.py           Claude 대리인
  orchestrator.py    전달 · 신뢰도 분기 · 병기
  inbox.py           에스컬레이션 3버튼
  main.py            FastAPI
  llm/               exaone.py (신뢰 구역) · broker.py (경계 밖)
  web/               탭 3개 (빌드 없음)

data/                MESH_DATA_ROOT
  vocab.json         어휘 사전  ⚠️ Day 1 동결. 나갈 수 있는 값의 전체 집합
  labels.json        등급 정답 라벨 → 분류 정확도 측정
  banned.json        금칙어
  questions.json     데모 질문 + 기대 결과
  corpus/            샘플 문서 (전부 우리가 만든 것)
  sessions/          사람별 작업 상태
  verified/          승인된 Q&A (런타임 생성)
  fixtures/          목업 응답

config/agents.yaml   에이전트 정의 — 추가는 항목 하나 더하는 것으로 끝난다
infra/               AWS CDK (브로커 Lambda + 감사 미러)
tests/               unit / property / eval
aidlc-docs/          설계 문서 (마크다운만. 코드 없음)
```

---

## 핵심 개념 3개

### 1. 경계를 넘는 통로는 2개뿐이다

```
Gatekeeper.ask_agent()    검증 통과 + 사용자 승인이 전제조건
AuditLog.mirror()         위 페이로드의 사본
```

다른 어떤 모듈도 경계 밖 클라이언트를 import하지 않는다.
`tests/unit/test_import_boundary.py`가 `ast`로 파싱해 강제한다 — 리뷰 매너에 의존하지 않는다.

### 2. "무엇을 지울까"가 아니라 "무엇만 보낼까"

페이로드는 **모델이 만들지 않고 코드가 조립한다.**

```python
for slot in schema.slots:          # 스키마를 순회한다
    if slot.name in raw:           # 모델 출력을 순회하지 않는다
        result[slot.name] = coerce(raw[slot.name], slot)
```

두 코드는 결과가 같아 보이지만 다르다. 반대 방향은 "검사를 잊으면 유출"이고, 이 방향은 **잊을 검사가 없다.**

실측 근거: 모델에게 JSON 전체를 만들게 하면 첫 시도에서 어휘 사전 밖 필드 3개가 나왔다.
슬롯 채우기로 바꾸면 3회 반복 모두 in-vocab이었다. → `aidlc-docs/construction/preflight-findings.md`

### 3. 애매하면 항상 더 높은 등급으로

등급을 낮게 잡은 실수는 유출이고, 높게 잡은 실수는 불편이다. 비대칭이 명확하다.
모든 실패 경로가 `Tier.SECRET` + 신뢰 구역 내 처리로 귀결된다 (fail closed).

---

## 신뢰 경계

```
┌─ 신뢰 구역 (노트북 + 사내망) ─────────────┐
│  브라우저 → FastAPI → Store (원문)        │
│                    ↕ EXAONE               │  ← 원문을 보는 유일한 모델
│  Gatekeeper: 판정 · 조립 · 검증 · 재수화   │
└───────────────────┬───────────────────────┘
                    │ 검증 통과 페이로드만
┌───────────────────▼───────────────────────┐
│  신뢰 구역 밖 (AWS)                        │
│  API GW → Lambda → Bedrock (Claude)       │
│                  → DynamoDB (감사, PITR)   │
└───────────────────────────────────────────┘
```

**경계의 위치는 `TRUSTED_ZONE_LLM_BASE_URL` 하나로 정해진다.**
실배포 전환은 이 값을 사내 서빙 엔드포인트로 바꾸는 것이다 (OpenAI 호환이면 코드 변경 0).
감사 로그가 매 질의마다 이 값을 기록하므로 **원문이 어디로 갔는지가 로그로 증명된다.**

---

## 보안 주의사항

| | |
|---|---|
| `.kiro/.env`, `.kiro/opencode.jsonc` | 자격증명이 평문으로 있다. **`.gitignore`에 포함됨** |
| 해커톤 종료 후 | **Friendli API 키를 폐기·재발급할 것** |
| `MESH_BIND_HOST` | `127.0.0.1` 고정. 재수화된 실제 이름이 지나가는 표면이다 |
| 사용자 인증 | **없다.** 드롭다운 전환은 데모용 |
| 실배포 전제 | **원본 시스템의 접근 권한 승계가 최우선 요건.** 없으면 권한 우회 도구가 된다 |

검증: `git ls-files | grep -E '\.env|opencode'` 결과가 비어 있어야 한다.

---

## 품질 게이트

| 게이트 | 시점 | 기준 |
|---|---|---|
| SG1 | 첫 커밋 전 | `.gitignore`가 자격증명 커버 |
| G1 | Day 1 종료 | `schemas.py` + `vocab.json` 동결 |
| **G2** | **Day 2 종료** | **기밀 재현율 100%**, 정확도 ≥90% — 미달 시 Day 3 진입 금지 |
| G3 | Day 3 종료 | 시나리오 1 종단 통과, 인용 0개 차단 |
| **G4** | Day 5 | **유출 0건** (자동 5-gram 전수 + 육안) |
| G5 | Day 5 | 목업 모드로 3막 전체 통과 |

```bash
make eval-classify    # G2
make eval             # G4
```

---

## 분담

| | 영역 | 유닛 |
|---|---|---|
| **A** | Gatekeeper 전체 + 클라우드 브로커 | U1, U5 |
| **B** | Store + Agent + Orchestrator | U2, U3 |
| **C** | 코퍼스 + 화면 + 데모·평가 | U4, U6 |

**파일 단위 소유권으로 git 충돌을 원천 차단한다.** → `aidlc-docs/construction/shared-infrastructure.md` §11

`schemas.py`와 `vocab.json`은 Day 1 종료 시 동결되고, 이후 변경은 3인 합의로만.

---

## 설계 문서

| 문서 | 내용 |
|---|---|
| `aidlc-docs/construction/preflight-findings.md` | **실측값. 가장 먼저 읽을 것** |
| `aidlc-docs/construction/shared-infrastructure.md` | 부트스트랩 · 파일 소유권 · import 경계 |
| `aidlc-docs/construction/plans/u*-code-generation-plan.md` | 유닛별 실행 계획 (체크박스) |
| `aidlc-docs/construction/u1-gatekeeper-core/functional-design/business-rules.md` | 보안 규칙 전부 |
| `aidlc-docs/inception/requirements/requirements.md` | FR 55개 + NFR 34개 |
| `aidlc-docs/inception/user-stories/stories.md` | 스토리 31개 (수용 기준이 데모 대본) |
| `aidlc-docs/aidlc-state.md` | 진행 상황 · 게이트 |

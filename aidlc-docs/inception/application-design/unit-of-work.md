# Unit of Work

**분해 기준**: 소유권 경계 (설계 §7.3의 3인 분담) + 의존 순서 + 보안 로직 격리 (SECURITY-11).
**배포 모델**: 모놀리스 (로컬 단일 프로세스) + 서버리스 사이드카 1개. 유닛은 **독립 배포 단위가 아니라 작업 단위**다.

---

## 1. 유닛 목록

| ID | 유닛 | 한 줄 | 소유 | 일정 | 스토리 |
|---|---|---|:---:|---|---|
| **U1** | `gatekeeper-core` | 보안 코어. 판정 · 추출 · 검증 · 가명화 · 감사 | A | Day 1~2 | 11 |
| **U2** | `knowledge-edge` | 세션 + 파일 직접 읽기 + 승인 QA 영속 | B | Day 1, 3 | 6 |
| **U3** | `agent-mesh` | Claude 대리인 + Orchestrator + 인박스 | B | Day 3 | 9 |
| **U4** | `console-web` | 화면 3개 + 미리보기 모달 + 원문 검색 | C | Day 4 | 8 |
| **U5** | `cloud-broker` | AWS CDK. 브로커 Lambda + 감사 미러 + 레지스트리 | A | Day 2~3 | 4 |
| **U6** | `demo-corpus-eval` | 샘플 코퍼스 + 어휘 사전 + 평가 하네스 + 목업 픽스처 | C | Day 1~2, 4 | 6 |

---

## 2. U1 — `gatekeeper-core`

| | |
|---|---|
| **책임** | 신뢰 경계를 넘는 모든 것을 통제한다 |
| **소유** | A (비중이 가장 크다) |
| **경계** | 원문을 읽지만 밖으로 내보내지 않는다. 밖으로 나가는 유일한 통로를 소유한다 |

**포함 파일**
```
src/mesh/config.py          환경변수 + agents.yaml 로더, MESH_DATA_ROOT 해석
src/mesh/schemas.py         task 스키마 · 슬롯 정의 · vocab 로더 · pydantic 모델
src/mesh/gatekeeper.py      조율 + 단일 통로 (로직 없음)
src/mesh/classifier.py      max(규칙, EXAONE)
src/mesh/extractor.py       슬롯 채우기 + 화이트리스트 조립
src/mesh/validator.py       6단계 · 순수 함수 · Lambda 공유
src/mesh/pseudonymizer.py   식별자 치환
src/mesh/rehydrator.py      역치환 · 순수 함수
src/mesh/audit.py           SQLite 원본 + 원문 검색 + 미러
src/mesh/llm/exaone.py      Friendli · enable_thinking=False · reasoning* 삭제
src/mesh/llm/broker.py      broker/direct/mock 전환
tests/unit/test_*.py
tests/property/test_*.py
tests/generators.py         Hypothesis 도메인 생성기
scripts/preflight.py        환경 검증
.gitignore                  ⚠️ 첫 커밋 전 필수
Makefile, pyproject.toml, .env.example
```

**인터페이스 (다른 유닛이 의존하는 것)**
- `Gatekeeper` 클래스 (7개 메서드)
- `schemas.py`의 pydantic 모델 — **Day 1 종료 시 동결. 3인의 계약**
- `validator.py` — U5가 Lambda에 번들해 재사용

**완료 기준**
- [ ] 기밀 재현율 100%, 등급 정확도 ≥ 90% (Day 2 게이트)
- [ ] 검증 6단계 각각의 실패 케이스 테스트 통과
- [ ] PBT 5개 불변식 통과
- [ ] import 경계 테스트 통과
- [ ] `make preflight` 동작

---

## 3. U2 — `knowledge-edge`

| | |
|---|---|
| **책임** | 세션을 유지하고 지목된 경로의 파일만 읽는다 |
| **소유** | B |
| **경계** | 원문을 U1에만 넘긴다. 밖으로 나가는 클라이언트를 import하지 않는다 |

**포함 파일**
```
src/mesh/store.py                세션 로드 · 경로 선택 · 파일 읽기 · 신선도 · verified 병합
config/agents.yaml               에이전트 3개 설정 (페르소나 · 지식 범위 · disclose)
data/sessions/person_kim.json    김책임 (활동 중)
data/sessions/person_park.json   박선임 (학습 실행 중)
data/sessions/person_choi.json   최민수 (자리 비움 2시간)
data/verified/.gitkeep
tests/unit/test_store.py
```

**완료 기준**
- [ ] 세션 3개 로드 + `verified_qa` 병합 (등급 보존)
- [ ] `${MESH_DATA_ROOT}` 치환 + 경로 탈출 거부
- [ ] 신선도 3단 판정 (시각 조작 테스트)
- [ ] 데몬 없이 세션 JSON만으로 3막 통과 (FR-21)
- [ ] `list_agents()`가 `disclose` 설정을 반영, 고객사명 부재

---

## 4. U3 — `agent-mesh`

| | |
|---|---|
| **책임** | 대리인이 답하고, 못 하면 다듬어 넘긴다 |
| **소유** | B |
| **경계** | Bedrock을 직접 부르지 않는다. `Gatekeeper.ask_agent()` 경유 |

**포함 파일**
```
src/mesh/agent.py          페르소나 프롬프트 · 답변 · 신뢰도 · 인용 · 에스컬레이션 초안
src/mesh/orchestrator.py   전달 · 병렬 · 신뢰도 분기 · divergent 병기 · 30초 상한
src/mesh/inbox.py          3버튼 · 환류
src/mesh/main.py           FastAPI 8개 엔드포인트 + 정적 서빙 + 보안 헤더 + 전역 예외 핸들러
tests/unit/test_orchestrator.py
tests/unit/test_agent.py
```

**완료 기준**
- [ ] 인용 0개 차단 (신뢰도 무관)
- [ ] 신뢰도 3구간 분기 경계값 테스트
- [ ] 2명 병렬 호출 + `divergent` 병기 (상충 자동 판정 없음)
- [ ] 에이전트 추가가 `agents.yaml` 한 항목으로 끝남
- [ ] `prepare`/`send` 2단계 — `send`가 승인 없이 실패
- [ ] 30초 상한 동작

---

## 5. U4 — `console-web`

| | |
|---|---|
| **책임** | 결정적 장면 3개를 화면으로 만든다 |
| **소유** | C |

**포함 파일**
```
src/mesh/web/index.html    탭 3개
src/mesh/web/app.js        fetch + 상태 + 모달 (프레임워크 없음)
src/mesh/web/style.css
```

**완료 기준**
- [ ] 미리보기 모달에 JSON **전문** + 검증 `6/6` + "포함되지 않은 것" 목록
- [ ] 감사 로그 원문 검색 → 0건 표시
- [ ] 인용에 `internal_path` 부재
- [ ] 지목 목록에 `disclose` 반영, 최대 2명
- [ ] `divergent` 병기 화면
- [ ] 보안 헤더 4개 (NFR-S-04), 외부 CDN 미사용
- [ ] 모든 상호작용 요소에 `data-testid`
- [ ] 목업 모드임이 화면에 표시됨

---

## 6. U5 — `cloud-broker`

| | |
|---|---|
| **책임** | 경계 밖에서 Claude를 부르고, 독립 검증하고, 지울 수 없는 감사를 남긴다 |
| **소유** | A |
| **배포** | AWS CDK (Python), `us-east-1` |

**포함 파일**
```
infra/app.py                          CDK 앱 엔트리, 리전 고정
infra/cdk.json
infra/requirements.txt                aws-cdk-lib 고정 버전
infra/stacks/broker_stack.py          Lambda + API Gateway REST + API Key + Usage Plan
infra/stacks/hub_stack.py             DynamoDB 3개 (감사 · 레지스트리 · 인박스)
infra/stacks/observability_stack.py   CloudWatch 알람 · 대시보드 · 로그 보존 90일
infra/lambda/agent_broker/handler.py  재검증 → Bedrock → 감사
infra/lambda/agent_broker/requirements.txt
```

**완료 기준**
- [ ] `cdk bootstrap` + `cdk deploy --all` 성공
- [ ] API Key 없이 호출 시 403
- [ ] 어휘 사전 밖 페이로드로 호출 시 400 + `ValidationFailure` 메트릭 증가
- [ ] Lambda 역할에 와일드카드 리소스 없음, `bedrock:InvokeModel`이 특정 추론 프로파일 ARN 한정
- [ ] 감사 테이블에 삭제 방지 + PITR, Lambda에 `DeleteItem` 없음
- [ ] API Gateway 액세스 + 실행 로깅
- [ ] `cdk destroy`로 완전 정리

---

## 7. U6 — `demo-corpus-eval`

| | |
|---|---|
| **책임** | 검증 가능성을 만든다. 정답 라벨이 있어서 전수 검사가 가능하다 |
| **소유** | C |

**포함 파일**
```
data/vocab.json          어휘 사전 ⚠️ Day 1 동결. 성능 수치 필드 의도적 제외
data/labels.json         문서별 등급 정답
data/banned.json         고객사명 · 제품코드 · 계약번호 패턴
data/questions.json      데모 질문 + 기대 답변
data/corpus/**           40~60건
data/fixtures/**         목업 모드용 녹화 응답
tests/eval/test_classification.py   Day 2 게이트
tests/eval/test_leak_sweep.py       Day 5 게이트
tests/eval/test_scenarios.py        3막 예제 테스트
tests/eval/compare_exaone_solo.py   외부 추론 실익 비교
```

**코퍼스 구성** (설계 §5.1)

| 주체 | 문서 | 등급 | 역할 |
|---|---|---|---|
| 김책임 (인증/보안) | 설계 6, 회의록 4, 개인 메모 4 | 사내·공개 | 시나리오 1·3 응답자 |
| 박선임 (데이터/ML) | 스크립트 4, 설정 3, 실험 로그 3, 메모 3 | 사내 | 시나리오 2 응답자 |
| 최민수 (SDK 배포) | 설계 4, 메모 3 | 사내 | 시나리오 3 응답자 |
| 고객사 요구사항 | 명세서 1, 협의 기록 2 | **기밀** | 시나리오 1 핵심 |
| 성능 벤치마크 | 고객 환경 실측 1 | **기밀** | 시나리오 3 폴백 유발 |
| **함정 문서** | 일반 설계 문서인데 고객사 단가 혼입 1 | **기밀** | 분류기 검증 |

**반드시 섞을 것** (설계 §5.2 — 코퍼스가 공식 문서만이면 위키 검색과 구분되지 않는다)
- 개인 메모: `notes/2025-11-auth.md` — "토큰 수명 왜 24시간으로 뒀는지" 반쪽짜리 기록
- 스크립트·설정: `preprocess_v3.py`, `configs/v3.yaml`
- 실험 로그: `runs/2026-08-19/train.log`
- **엇갈리는 기록**: 김책임 메모(성능) vs 최민수 리뷰(레거시 SSO 호환) — 같은 사건, 다른 서술

**완료 기준**
- [ ] 문서 40~60건, 종류 분포 확인
- [ ] `labels.json` 전 문서 라벨링
- [ ] 함정 문서가 `secret`으로 분류됨
- [ ] `vocab.json`에 성능 수치 필드가 **없음**
- [ ] `make eval` 실행 → 5개 지표 리포트
- [ ] 목업 픽스처로 3막이 네트워크 없이 통과

---

## 8. 코드 조직 전략 (Greenfield)

**패턴**: 단일 유닛 구조 (`src/`, `tests/`, `config/`, `data/`) + 인프라 별 디렉터리 (`infra/`).
유닛이 독립 배포 단위가 아니므로 `{unit-name}/src/` 구조를 쓰지 않는다. 대신 **파일 소유권**으로 유닛을 구분한다.

```
prompthon/                          <- 워크스페이스 루트 (애플리케이션 코드)
├── .gitignore                      U1 ⚠️ git init 전 필수
├── .env.example                    U1
├── Makefile                        U1  setup/preflight/run/test/eval/deploy/demo
├── pyproject.toml                  U1  python>=3.12, 고정 버전
├── uv.lock                         U1  ⚠️ 커밋 (SECURITY-10)
├── README.md                       U1  다른 컴퓨터 온보딩 절차
│
├── src/mesh/
│   ├── __init__.py
│   ├── config.py                   U1
│   ├── schemas.py                  U1  ⚠️ Day 1 동결
│   ├── gatekeeper.py               U1
│   ├── classifier.py               U1
│   ├── extractor.py                U1
│   ├── validator.py                U1  (U5가 번들 재사용)
│   ├── pseudonymizer.py            U1
│   ├── rehydrator.py               U1
│   ├── audit.py                    U1
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── exaone.py               U1
│   │   └── broker.py               U1
│   ├── store.py                    U2
│   ├── agent.py                    U3
│   ├── orchestrator.py             U3
│   ├── inbox.py                    U3
│   ├── main.py                     U3
│   └── web/
│       ├── index.html              U4
│       ├── app.js                  U4
│       └── style.css               U4
│
├── config/
│   └── agents.yaml                 U2
│
├── data/                           <- MESH_DATA_ROOT 기본값
│   ├── vocab.json                  U6 ⚠️ Day 1 동결
│   ├── labels.json                 U6
│   ├── banned.json                 U6
│   ├── questions.json              U6
│   ├── corpus/                     U6
│   │   ├── kim/{docs,notes,minutes}/
│   │   ├── park/{scripts,configs,runs,notes}/
│   │   ├── choi/{docs,notes}/
│   │   └── customer-H/             (경로 규칙으로 secret 판정)
│   ├── sessions/                   U2
│   ├── verified/                   U2 (런타임 생성)
│   └── fixtures/                   U6 (목업 응답)
│
├── scripts/
│   ├── bootstrap.sh                U1
│   └── preflight.py                U1
│
├── tests/
│   ├── generators.py               U1  Hypothesis 도메인 생성기
│   ├── unit/                       U1,U2,U3
│   ├── property/                   U1
│   └── eval/                       U6
│
└── infra/                          U5  AWS CDK (Python)
    ├── app.py
    ├── cdk.json
    ├── requirements.txt
    ├── stacks/
    │   ├── broker_stack.py
    │   ├── hub_stack.py
    │   └── observability_stack.py
    └── lambda/agent_broker/
        ├── handler.py
        └── requirements.txt
```

**`aidlc-docs/`에는 애플리케이션 코드를 두지 않는다.** 마크다운 문서만.

---

## 9. Day 1 동결 계약

세 사람이 병렬로 가려면 Day 1 종료 시점에 다음이 커밋돼 있어야 한다. **스텁이어도 된다. 시그니처가 중요하다.**

| 산출물 | 왜 계약인가 |
|---|---|
| `src/mesh/schemas.py` | B와 C가 이 타입으로 코딩한다 |
| `data/vocab.json` | A의 추출기와 C의 코퍼스가 이걸 공유한다 |
| `src/mesh/gatekeeper.py` (스텁) | B의 U3가 Day 3에 이걸 호출한다 |
| `config/agents.yaml` (스키마) | B의 U2와 C의 U4가 이걸 읽는다 |
| API 계약 (`services.md` 8개 엔드포인트) | C의 U4가 이걸 fetch한다 |

**이후 변경은 3인 합의로만** (NFR-M-02).

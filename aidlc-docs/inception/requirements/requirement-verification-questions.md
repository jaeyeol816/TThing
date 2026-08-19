# 요구사항 확인 질문

**Round 1 상태**: ✅ 답변 완료 (2026-08-19)
**답변 출처**: 사용자 채팅 입력 + `requirements/hackathon-mvp-design.md` + `requirements/scenarios.md`
**Round 2 상태**: ⏳ 확인 필요 — `scenarios.md` §5의 미결 항목 6건에 대해 AI가 제안한 결정.
설계·구현은 이 제안대로 진행했으므로, 다르게 가고 싶은 항목만 알려주면 된다.

---

# Round 1 — 프로젝트 기본 정보 (답변 완료)

## Question 1
이번에 개발하고자 하는 프로젝트의 종류는 무엇인가요?

A) 웹 애플리케이션 (프론트엔드 + 백엔드)

B) REST API / Backend 서비스

C) 모바일 애플리케이션

D) CLI 도구 / 유틸리티

E) 데이터 파이프라인 / ETL

F) Other (please describe after [Answer]: tag below)

[Answer]: F — **하이브리드 2계층 시스템.** (1) 개발자 노트북에서 도는 로컬 에이전트: FastAPI 백엔드 + 정적 웹 UI(탭 3개). 신뢰 구역 안의 지식·게이트키퍼를 담당. (2) AWS 서버리스 브로커: AWS CDK로 배포하는 Lambda + API Gateway + DynamoDB. 신뢰 구역 밖에서 Claude(Bedrock) 호출·감사 로그 보관·에이전트 레지스트리를 담당. 두 계층 사이에 신뢰 경계가 있고, 그 경계를 통제하는 것이 이 프로젝트의 본질이다.

## Question 2
프로젝트의 주요 목적 또는 비즈니스 도메인은 무엇인가요?

A) 전자상거래 / 쇼핑

B) 소셜 네트워크 / 커뮤니티

C) 업무 관리 / 프로젝트 관리

D) 데이터 분석 / 대시보드

E) AI/ML 서비스 / 챗봇

F) Other (please describe after [Answer]: tag below)

[Answer]: F — **사내 지식 대리 에이전트 + 보안 게이트키퍼.** 두 문제를 하나의 구조로 푼다. (P1) 아는 사람에게 묻되 그 사람의 작업을 끊지 않는다 — 사람 앞에 대리 에이전트를 세운다. (P2) 기밀 자료를 외부 AI에 안전하게 활용한다 — 원문 대신 어휘 사전으로 통제된 구조 표현만 경계 밖으로 보낸다. E(챗봇)에 가깝지만 핵심 가치는 지식 라우팅이 아니라 **정보 흐름 통제**에 있다.

## Question 3
선호하는 프로그래밍 언어 / 프레임워크가 있나요?

A) TypeScript + React (프론트엔드) + Node.js (백엔드)

B) Python + FastAPI / Django

C) Java + Spring Boot

D) Go

E) 특별히 정해진 것 없음 (AI가 추천해주길 원함)

F) Other (please describe after [Answer]: tag below)

[Answer]: B — **Python 3.12 + FastAPI** (설계 문서 §6에서 지정). 프론트엔드는 빌드 파이프라인 없는 **단일 HTML + 바닐라 JS**(설계 문서 §6, 5일 일정에 빌드 도구를 넣지 않는다). 인프라는 **AWS CDK (Python)** — 앱과 언어를 통일해 다른 컴퓨터에서의 온보딩 비용을 줄인다. CDK CLI는 전역 설치 없이 `npx aws-cdk@2`로 실행. 패키지·파이썬 버전 관리는 `uv`(로컬에 이미 설치 확인됨). 현재 시스템 Python이 3.9.12라 pydantic v2 사용을 위해 `uv`로 3.12를 고정한다.

## Question 4
배포 환경은 어디를 고려하고 있나요?

A) AWS (EC2, Lambda, ECS 등)

B) 컨테이너 (Docker / Kubernetes)

C) Serverless (AWS Lambda, API Gateway)

D) 로컬 환경에서만 실행 (배포 불필요)

E) Other (please describe after [Answer]: tag below)

[Answer]: E — **C + D 하이브리드 (의도적).** 신뢰 구역 안(지식 저장소·게이트키퍼·재수화·웹 UI)은 **로컬 노트북**에서만 돈다. 신뢰 구역 밖(Claude 호출·감사 로그 원본 보관·에이전트 레지스트리)은 **AWS 서버리스**(Lambda + API Gateway + DynamoDB), **AWS CDK로 배포**. 이 분리는 편의가 아니라 보안 모델 자체다 — 클라우드는 Claude와 같은 편(경계 밖)에 있고, 클라우드로 나가는 모든 것은 이미 게이트키퍼를 통과한 것이다. 리전은 계정 정책상 **us-east-1 고정**.

## Question 5
예상되는 사용자 규모는 어느 정도인가요?

A) 개인 프로젝트 / 학습 목적

B) 소규모 팀 (10명 미만)

C) 중규모 (100~1000명)

D) 대규모 (1000명 이상)

E) Other (please describe after [Answer]: tag below)

[Answer]: B — **해커톤 데모 규모.** 개발 2~3명 / 5일. 에이전트 3개(김책임·박선임·최민수), 샘플 문서 40~60건, 동시 사용자 1~5명. 성능 목표는 처리량이 아니라 **단일 질의 응답 30초 이내**(설계 문서 §9). 확장성은 이번 범위 밖이며, 실제 도입 경로는 설계 문서 §10.2에 별도로 있다.

## Question 6: Security Extensions
이 프로젝트에 보안 관련 확장 규칙을 적용할까요?

A) Yes — 모든 보안 규칙을 blocking 제약으로 적용 (프로덕션급 애플리케이션에 권장)

B) No — 보안 규칙 건너뛰기 (PoC, 프로토타입, 실험적 프로젝트에 적합)

X) Other (please describe after [Answer]: tag below)

[Answer]: A — **Yes.** 해커톤 프로토타입이라 통상 B를 고르지만 여기서는 A가 맞다. 두 가지 이유. (1) 이 프로젝트가 심사받는 대상 자체가 보안 통제다. 보안 규칙을 건너뛴 보안 제품은 데모에서 무너진다. (2) 설계에 **인터넷 노출 엔드포인트가 생긴다** — 유료 모델(Bedrock)을 호출하는 API. 인증·레이트 리밋 없이 두면 실제 비용·남용 위험이 있다. 단, 규칙 적용 범위는 이번 스코프에 실제로 존재하는 것에 한한다(사용자 로그인이 없으므로 SECURITY-12의 비밀번호 정책 부분은 N/A). 스테이지별 준수 요약에 N/A 근거를 남긴다.

## Question 7: Resiliency Extensions
이 프로젝트에 복원력(Resiliency) 기본 규칙을 적용할까요?

A) Yes — 복원력 베이스라인을 설계 시점 모범 사례로 적용 (비즈니스 핵심 워크로드에 권장)

B) No — 복원력 베이스라인 건너뛰기 (PoC, 프로토타입, 실험적 프로젝트에 적합)

X) Other (please describe after [Answer]: tag below)

[Answer]: B — **No.** 5일 해커톤 프로토타입이고 워크샵 계정(회수 가능)에 배포한다. 가용성·RTO/RPO 목표가 없다. 다만 **데모가 죽지 않는 것**은 별개로 중요하므로, 복원력 베이스라인 전체를 적용하는 대신 **설계 문서 §6.2의 폴백 요구사항을 기능 요구사항(FR-13)으로 승격**해 다룬다: EXAONE 타임아웃·JSON 파싱 실패·Bedrock 오류·CDK 미배포 각각에 대한 폴백과 목업 모드. 이게 이 프로젝트에 실제로 필요한 복원력의 전부다.

## Question 8: Property-Based Testing Extension
이 프로젝트에 속성 기반 테스트(Property-Based Testing) 규칙을 적용할까요?

A) Yes — 모든 PBT 규칙을 blocking 제약으로 적용 (비즈니스 로직, 데이터 변환, 직렬화, 상태 관련 컴포넌트에 권장)

B) Partial — 순수 함수와 직렬화 왕복 테스트에만 PBT 규칙 적용

C) No — PBT 규칙 건너뛰기 (간단한 CRUD, UI 전용, 얇은 통합 레이어에 적합)

X) Other (please describe after [Answer]: tag below)

[Answer]: B — **Partial** (PBT-02, 03, 07, 08, 09 강제). Gatekeeper의 검증기·어휘 사전 조립·가명화/재수화가 **정확히 PBT가 잘하는 종류의 코드**다: 순수 함수이고, 강한 불변식이 있고, 실패가 곧 유출이다. 특히 두 속성은 예제 기반 테스트로는 절대 증명할 수 없다 — (1) 가명화 → 재수화 왕복이 항등이다, (2) 임의의 원문·임의의 페이로드 조합에 대해 원문 5-gram이 페이로드에 없다. 반면 UI·FastAPI 라우팅·CDK 스택에는 PBT를 넣지 않는다(A가 과하다고 본 이유). 상태 기반 PBT(PBT-06)는 세션 저장소에 적용 여지가 있으나 5일 일정에서 제외하고 N/A 근거를 남긴다.

---

# Round 2 — 미결 설계 결정 6건 (확인 필요)

`scenarios.md` §5가 "구현 전에 팀이 정하는 편이 좋다"고 남겨둔 항목들이다.
구현을 바로 시작할 수 있게 **AI가 각각 결정을 내려 설계에 반영했다.**
다르게 가고 싶은 것만 답변을 바꿔 주면 된다. 그대로 좋으면 Round 2는 건너뛰어도 된다.

## Question 9: 등급 상향 vs 질문 분해의 기준
시나리오 1은 "동원 지식 중 최고 등급이 호출 전체에 걸린다"로, 시나리오 2는 "등급이 갈리면 질문을 분해한다"로 처리했다. 둘 다 필요한데 언제 무엇을 할지의 기준이 필요하다.

A) 분해했을 때 각 조각이 **독립적으로 의미 있는 답**을 낼 수 있으면 분해, 아니면 상향 (scenarios.md 제안)

B) 항상 상향 — 단순하고 가장 안전하지만 답변 품질이 무뎌진다

C) 항상 분해 — 답변은 날카롭지만 조각을 대조해 원문이 복원될 위험이 있다

X) Other

[Answer]: A — **AI 결정.** 판정 규칙을 코드로 구체화했다: 하위 질문이 (1) 자기 `answer_format`을 갖고, (2) `needs[]`가 다른 하위 질문과 **겹치지 않고**, (3) 그 조각만으로 사용자에게 보여줄 값이 있으면 → 분해. 하나라도 어긋나면 → `max(tier)` 상향. 그리고 **한 번의 Agent 호출에는 한 등급만 담는다**(scenarios.md §2 ② 제약)는 것을 코드 불변식으로 강제한다: `AgentCall.tier`는 단일 값이고, 등급이 섞인 페이로드는 조립 단계에서 만들어지지 않는다. 상세: `aidlc-docs/construction/u1-gatekeeper-core/functional-design/business-rules.md` BR-G-07.

## Question 10: 인용에서 경로를 어디까지 보여줄까
최민수는 고객사 요구사항명세서를 볼 권한이 없는데 화면에는 문서명이 뜬다. MVP에 권한 관리가 없다.

A) 문서명 + 섹션까지만 표시. 절대 경로·본문은 표시하지 않음 (scenarios.md 제안)

B) 전체 경로까지 표시 — 디버깅은 쉽지만 경로 자체가 정보를 준다 (`customer-H/`)

C) 등급이 기밀이면 인용 자체를 숨김 — 답변의 신뢰성이 떨어진다

X) Other

[Answer]: A — **AI 결정.** `Citation`에 `display_title`(사람이 읽을 이름)과 `internal_path`(로컬 전용)를 분리한다. API 응답과 웹 UI에는 `display_title` + `section` + `tier` + `as_of`만 나가고 `internal_path`는 로컬 로그에만 남는다. 경로 자체가 정보라는 게 B를 버린 이유다 — `corpus/customer-H/`는 고객사명을 그대로 노출한다. 실배포에서는 원본 시스템 권한 승계가 전제이며 이 MVP의 한계로 명시한다.

## Question 11: 충돌을 어디까지 자동 판정할까
시나리오 3에서 두 답이 엇갈렸지만 둘 다 맞을 수 있다(성능 문제와 호환 문제가 함께 있었을 수도).

A) 자동 판정하지 않는다. 두 답을 **항상 병기**하고 시점·문서 성격만 표시. "엇갈립니다" 대신 "둘 다 사실일 수 있습니다" (scenarios.md 제안)

B) LLM에게 상충 여부를 판정하게 하고 `conflict: true/false`를 붙인다 — 오탐이 잦다

C) 신뢰도가 높은 쪽만 보여준다 — 나머지 하나가 영원히 묻힌다

X) Other

[Answer]: A — **AI 결정.** 응답 필드명을 `conflict`에서 **`divergent`**로 바꿨다. `conflict: true`는 "상충한다"는 단정이지만, `divergent: true`는 "서로 다른 답이 나왔다"는 관찰이다. 판정은 하지 않는다. 2명 이상 지목 시 항상 병기하고 각 답에 `as_of`(근거 문서 시점)와 `formality`(official / informal)를 표시한다. C를 버린 이유는 설계 문서의 원칙 그대로다 — "하나를 조용히 고르면 나머지 하나는 영원히 묻힌다."

## Question 12: 세션 갱신이 멈췄을 때
데몬이 죽었거나 사람이 자리를 비우면 세션이 오래된 상태다. 시나리오 2의 답변이 틀린 실시간 정보를 줄 수 있다.

A) `updated_at`이 N분 이상 지났으면 답변에 세션 기준 시각을 명시하고 신뢰도를 낮춘다 (scenarios.md 제안)

B) 오래된 세션은 아예 사용하지 않는다 — 시나리오 3(부재 중 최민수)이 성립하지 않는다

C) 그냥 쓴다 — 틀린 실시간 정보를 자신 있게 말하는 최악의 경우가 생긴다

X) Other

[Answer]: A — **AI 결정.** 3단 구간으로 구체화했다. `SESSION_STALE_MINUTES=15` 기본값 기준: (1) 15분 이내 → `freshness: live`, 신뢰도 보정 없음, UI에 `🔴 실시간`. (2) 15분~24시간 → `freshness: stale`, **신뢰도 × 0.8**, 답변에 세션 기준 시각 명시, UI에 `⚪ N시간 전 기준`. (3) 24시간 초과 → `freshness: expired`, **`session_facts`를 실시간 주장에서 제외**하고 파일 근거만 사용. B를 버린 이유는 시나리오 3의 핵심(자리에 없는 사람도 답한다)이 깨지기 때문이다 — 파일은 언제든 읽히므로 세션만 신뢰도를 깎으면 된다.

## Question 13: 에이전트 목록에 무엇까지 표시할까
지목을 사람이 하므로 목록의 정보량이 곧 선택의 품질이다.

A) 담당 영역은 필수 공개, 활동 상태·오늘 질문 수·현재 작업 요약은 **본인 opt-in**. 데모에서는 셋 다 켜 둔다 (scenarios.md 제안)

B) 전부 항상 공개 — 구현은 간단하지만 실제 도입 시 감시 도구가 된다

C) 담당 영역만 공개 — 시나리오 2의 "지금 학습 실행 중" 확신이 사라진다

X) Other

[Answer]: A — **AI 결정.** `config/agents.yaml`의 에이전트별 `disclose:` 블록으로 구현한다: `expertise`(항상 true, 변경 불가), `activity_status`, `question_count_today`, `current_focus` 각각 boolean. 데모용 3개 에이전트는 셋 다 `true`. 그리고 `current_focus`는 세션 `focus` 원문을 그대로 쓰지 않고 **식별자를 제거한 요약**을 쓴다("고객사 H 인증 요구사항 검토" → "인증 관련 작업 중"). 목록은 인증 없이 보이는 화면이므로 여기서 고객사명이 새면 게이트키퍼를 우회한 유출이 된다. 이 변환도 게이트키퍼를 통과시킨다.

## Question 14: 승인된 답변을 어디에 저장하나
세션은 데몬이 계속 덮어쓰는 휘발성 상태다. 승인된 Q&A는 별도의 영속 저장이 필요하다.

A) `data/verified/{entity_id}.json`. 세션 로드 시 함께 읽어 붙인다 (scenarios.md 제안)

B) SQLite 테이블에 저장 — 감사 로그와 같은 저장소를 쓴다

C) 세션 JSON 안에 넣는다 — 데몬이 덮어쓰면 사라진다

X) Other

[Answer]: A — **AI 결정.** 단, 저장할 때 **원문 등급을 함께 보존**한다. 승인된 답변은 사람이 검토했지만 여전히 사내/기밀 내용을 담을 수 있다. `verified_qa` 항목마다 `tier`를 붙이고, 이후 이 항목이 Agent 호출에 동원될 때 **다른 지식과 똑같이 게이트키퍼를 통과**시킨다. "사람이 승인했으니 그대로 내보내도 된다"가 되면 §3.8의 금지 규칙("구조 추출을 거쳤으니 무엇이든 보내도 된다")과 같은 종류의 구멍이 생긴다. 스키마: `aidlc-docs/construction/u2-knowledge-edge/functional-design/domain-entities.md` §VerifiedQA.

---

# Round 3 — 신뢰 경계에 대한 확인 (권고)

## Question 15: EXAONE 엔드포인트가 사외 SaaS인 점을 데모에서 어떻게 다룰까
`.kiro/opencode.jsonc`의 EXAONE은 `api.friendli.ai` — 공개 SaaS다. 설계 문서의 "사내망 서버"가 아니다. 즉 **"원문이 사내망을 벗어나지 않는다"는 주장이 이번 구현에서는 문자 그대로 성립하지 않는다.**

A) 데모 시작 시 **먼저 밝힌다.** "경계의 위치는 설정값 하나이고, 우리가 만든 것은 경계를 지키는 구조다"로 프레이밍하고, 실배포 전환이 환경변수 1개임을 보여준다

B) 언급하지 않는다 — 심사자가 지적하면 주장 전체의 신뢰가 무너진다

C) 사내망 EXAONE 엔드포인트를 확보해 실제로 사내에서 돌린다 (가능하면 최선)

X) Other

[Answer]: A — **AI 권고 (사용자 확인 요청).** C가 가능하다면 C가 최선이니 확보 가능한지 확인해 달라. 확보 못 하면 A로 간다. 코드는 어느 쪽이든 동일하게 동작하도록 `TRUSTED_ZONE_LLM_BASE_URL` 하나로 추상화했고, 감사 로그에 이 값을 매 질의마다 기록해서 **"원문이 어디로 갔는지"가 로그로 증명**되게 했다. B는 선택하지 않았다 — 발견되면 프로젝트의 유일한 주장이 무너지고, 먼저 밝히면 오히려 설계의 이식성을 보여주는 장면이 된다.

## Question 16: 다른 컴퓨터로 옮길 가능성에 어디까지 대비할까
사용자가 "다른 컴퓨터를 사용할 수도 있다"고 했다.

A) 전체 이식성 보장 — 절대 경로 금지, `uv`로 파이썬 버전 고정, `.env.example` + `make setup` 원커맨드, `make preflight`로 환경 검증, 샘플 코퍼스·세션을 저장소에 포함

B) 현재 컴퓨터에 맞춰 만들고 필요할 때 옮긴다

X) Other

[Answer]: A — **AI 결정.** 구체적 조치: (1) 코퍼스 경로를 `~/work/...` 하드코딩 대신 `MESH_DATA_ROOT` 기준 상대 경로로 두고, 세션 JSON의 `open_paths`도 `${MESH_DATA_ROOT}/...` 치환 문법을 지원. (2) `uv`로 Python 3.12 고정 + `uv.lock` 커밋(SECURITY-10 의존성 고정과 동일 조치). (3) CDK CLI를 전역 설치하지 않고 `npx aws-cdk@2`. (4) 자격증명은 저장소에 없고 `.env.example`만 커밋. (5) `make preflight`가 EXAONE 왕복·Bedrock 모델 접근·리전·CDK 부트스트랩 여부를 한 번에 확인. (6) 목업 모드(`EXAONE_MODE=mock`, `AGENT_MODE=mock`)로 네트워크 없이도 데모가 돈다 — 다른 컴퓨터에서 사내망을 못 붙일 최악의 경우 대비.

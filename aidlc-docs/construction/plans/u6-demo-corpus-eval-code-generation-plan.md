# U6 `demo-corpus-eval` — Code Generation Plan

**소유**: C · **일정**: Day 1~2 (데이터), Day 4 (픽스처·시나리오) · **스토리**: 9개 주담당
**설계 근거**: `aidlc-docs/construction/u6-demo-corpus-eval/`
**코드 위치**: `data/`, `tests/eval/`

> **이 유닛이 임계 경로의 시작이다.** `vocab.json`이 없으면 U1의 추출기를 만들 수 없다.

---

## 유닛 컨텍스트

| | |
|---|---|
| **구현 스토리** | S-02, S-05, S-08, S-12, S-14, S-19, S-20, S-21, S-25, S-30 |
| **제공** | `vocab.json`(U1, U5) · `corpus/**`(U2) · `fixtures/api/`(U4) · 평가 하네스 |
| **의존** | U1 `schemas.py` 타입 (Step 5 이후) |

---

# Day 1 — 계약과 최소 데이터

## Step 1 · `data/vocab.json` ⚠️ 동결 대상 🔴

A(U1)와 함께 작업한다. **완벽을 추구하지 말고 시나리오 3개가 돌 최소 슬롯으로 시작한다.**

- [x] 1.1 슬롯 9개 정의 (`u6/domain-entities.md` §1)
- [x] 1.2 **`kind`는 `enum`/`int`/`bool` 3개만. 자유 문자열 슬롯 금지** 🔴
- [x] 1.3 `tasks` 3개, `domains` 3개, `question_templates` 3개
- [x] 1.4 `task_schemas` 3개 (`constraint_conflict_check`, `technique_lookup`, `rationale_lookup`)
- [x] 1.5 **`_intentionally_absent` 작성** 🔴 — 성능 수치·금액·계약번호·인명·고객사명
- [x] 1.6 **성능 수치 슬롯(`p99_latency_ms`, `throughput_tps`)을 넣지 않는다** 🔴 (FR-54)
- [x] 1.7 A와 스키마 정합 확인 → **동결**

**1.6이 시나리오 3의 폴백을 만든다.** 슬롯을 추가하고 싶은 유혹이 생기면 `_intentionally_absent`를 읽는다.

## Step 2 · `data/banned.json`

- [x] 2.1 `literals` — 고객사명·시스템명·프로젝트명 (`H社`, `HanaTel`, `Nova 게이트웨이`, `atlas-ml`, ...)
- [x] 2.2 `patterns` — `REQ-\d{4}`, `CTR-\d{6}`, `SKU-...`
- [x] 2.3 **금액 패턴 여러 개** 🔴 — `\d+\s*억\s*원?`, `\d{1,3}(,\d{3})+\s*원`, `USD\s*[\d,]+`, `\$\s?[\d,]{4,}`
- [x] 2.4 한국어 금액 표기 변형 허용 (공백 포함)
- [x] 2.5 `_notes` — 이 목록이 검증 4단계와 등급 판정 규칙 3·4번에 둘 다 쓰인다

**2.3이 함정 문서 탐지의 유일한 수단이다** (BR-C-04, BR-D-03).

## Step 3 · 시나리오 필수 문서 8개 (BR-EV-09) 🔴

**규모보다 먼저.** 시간이 부족해도 시나리오는 돌아야 한다.

- [x] 3.1 `corpus/customer-H/req-spec-2026H.md` (**기밀**) — REQ-4412, EAP-AKA, 세션 8시간
- [x] 3.2 `corpus/kim/docs/auth-design.md` (사내) — SDK v3.2 토큰 24시간, 무음 갱신, 바인딩 없음
- [x] 3.3 `corpus/park/scripts/preprocess_v3.py` (사내) — `atlas_ml` + `RandomOverSampler(0.5)` + `balanced_subsample`
- [x] 3.4 `corpus/park/configs/v3.yaml` (사내)
- [x] 3.5 `corpus/park/runs/2026-08-19/train.log` (사내) — 실행 중 로그
- [x] 3.6 `corpus/kim/notes/2025-11-auth.md` (**비공식**) — 성능 이유. 벤치마크 문서 언급
- [x] 3.7 `corpus/choi/docs/auth-review.md` (**공식**) — 레거시 SSO 호환 이유
- [x] 3.8 `corpus/customer-H/benchmark-prod-2025-11.md` (**기밀**) — p99 840ms, 3120 TPS
- [x] 3.9 프런트매터 5개 필드 전부 (BR-D-07)
- [x] 3.10 **3.6과 3.7을 함께 작성 — 둘 다 사실이게** 🔴 (BR-D-02)
- [x] 3.11 3.3에 가명화 대상(`atlas_ml`)과 기술 용어를 **한 줄에 섞는다** (BR-P-01 검증용)

## Step 4 · `data/fixtures/api/` — U4 선행 개발용

U3의 API 계약(Step 0)이 확정되면 즉시 작성한다.

- [x] 4.1 `GET_api_agents.json` — 에이전트 3개
- [x] 4.2 `GET_api_health.json` — `trust_boundary_simulated: true`
- [x] 4.3 `POST_api_ask_prepare_ready.json` — 시나리오 1 (기밀, 검증 6/6)
- [x] 4.4 `POST_api_ask_prepare_blocked.json` — 시나리오 3 후속 (검증 실패 + 폴백)
- [x] 4.5 `POST_api_ask_send_auto.json` — 시나리오 1 자동 응답
- [x] 4.6 `POST_api_ask_send_divergent.json` — 시나리오 3 병기
- [x] 4.7 `POST_api_ask_send_escalate.json` — 시나리오 2 q2
- [x] 4.8 `GET_api_inbox.json` — 시나리오 2 인박스 (초안 3요소)
- [x] 4.9 `GET_api_audit.json`, `GET_api_audit_zero.json`
- [x] 4.10 **C 자신이 U4에서 소비한다** — 형태가 실제 응답과 다르면 Day 4에 UI를 다시 만든다

---

# Day 2 — 코퍼스 완성과 Day 2 게이트

## Step 5 · 코퍼스 40~60건

- [ ] 5.1 김책임: 설계 6 / 회의록 4 / 개인 메모 4 (사내·공개)
- [ ] 5.2 박선임: 스크립트 4 / 설정 3 / 실험 로그 3 / 메모 3 (사내)
- [ ] 5.3 최민수: 설계 4 / 메모 3 (사내)
- [ ] 5.4 고객사: 명세서 1 / 협의 기록 2 (**기밀**)
- [ ] 5.5 공개: 오픈소스·공개 스펙 요약 2~3 (`corpus/public/`)
- [ ] 5.6 **개인 메모·스크립트·설정·실험 로그를 반드시 섞는다** 🔴 (BR-D-01)
- [ ] 5.7 공식 문서 비율 < 60% 확인
- [ ] 5.8 실제 회사·제품·인명 미사용 (BR-D-08)
- [ ] 5.9 절대 경로 미사용 (BR-D-06)
- [ ] 5.10 `banned.json`과 문서 내 고객사명 표기 **동기화** 🔴

**5.10이 놓치기 쉽다.** 문서에 `하나텔`이라고 썼는데 사전에 `HanaTel`만 있으면 규칙 3번이 놓친다.

## Step 6 · 함정 문서 🔴

- [x] 6.1 `corpus/kim/docs/sdk-pricing-tiers.md` 작성
- [x] 6.2 경로가 `customer-*/`가 **아니다**
- [x] 6.3 프런트매터에 `보안등급` **없음**
- [x] 6.4 겉보기는 평범한 SDK 티어 설계 문서
- [x] 6.5 본문 중간에 고객사별 단가 (`H社 라이선스 12억원`)
- [x] 6.6 `labels.json`에 `tier: secret` + `trap: true`

## Step 7 · `data/labels.json`

- [x] 7.1 전 문서 등급 라벨링
- [x] 7.2 `reason` 필드 — 왜 그 등급인지
- [x] 7.3 `note` 필드 — 시나리오 연관
- [x] 7.4 `trap: true` — 함정 문서
- [x] 7.5 경로 규칙(`ClassificationRules`)과 정합 확인 (BR-D-05)

## Step 8 · `tests/eval/test_classification.py` — Day 2 게이트 🔴

- [ ] 8.1 `labels.json` 로드 → 문서별 `classify()` 호출
- [ ] 8.2 기록: `expected`, `actual`, `rule_tier`, `exaone_tier`, `reasons`, `is_trap`
- [ ] 8.3 집계: `accuracy`, `secret_recall`, `trap_recall`
- [ ] 8.4 **오분류를 상향(불편)/하향(유출)으로 분류** 🔴
- [ ] 8.5 **리포트를 assert 전에 출력** (실패 시에도 보이게)
- [ ] 8.6 `assert secret_recall == 1.0` 🔴
- [ ] 8.7 `assert accuracy >= 0.90`
- [ ] 8.8 `assert trap_recall == 1.0`
- [ ] 8.9 `make eval-classify` 연결
- [ ] 8.10 **A와 함께 게이트 G2 통과 확인** — 미달이면 U1 Step 9~10 반복

---

# Day 4 — 픽스처와 시나리오 테스트

## Step 9 · `data/questions.json`

- [ ] 9.1 5개 시나리오 (`u6/domain-entities.md` §4)
- [ ] 9.2 `expect` 필드 — 그대로 어서션이 된다
- [ ] 9.3 `leak_probes` — `REQ-4412`, `EAP-AKA`, `H社`, `12억`
- [ ] 9.4 s1: `verbatim_sentence_count: 0`, `audit_record: true`, `answer_must_not_contain: ["REQ_A"]`
- [ ] 9.5 s3b: **`audit_record: false`** 🔴, `used_external_agent: false`
- [ ] 9.6 s3a: `divergent: true`, `answers_not_reordered: true`

## Step 10 · 목업 픽스처 녹화 🔴

- [ ] 10.1 `EXAONE_MODE=live AGENT_TRANSPORT=direct MESH_RECORD_FIXTURES=1 make demo`
- [ ] 10.2 3막 전체(5개 시나리오)를 UI로 한 번 수행
- [ ] 10.3 `data/fixtures/exaone/`, `data/fixtures/agent/` 생성 확인
- [ ] 10.4 커밋
- [ ] 10.5 **`EXAONE_MODE=mock AGENT_MODE=mock`으로 재실행해 동일 결과 확인** 🔴
- [ ] 10.6 재생 실패 시 키 불일치 조사 (`FixtureMissing`이 명시적으로 나야 한다)

**10.5가 필수다.** 녹화만 하고 재생을 확인하지 않으면 데모 당일 실패한다.

## Step 11 · `tests/eval/test_scenarios.py`

- [ ] 11.1 `questions.json` 파라미터화
- [ ] 11.2 **목업 모드로 실행** (네트워크 없이 CI에서)
- [ ] 11.3 `expect` 키별 어서션 함수
- [ ] 11.4 `audit_record: false` 검증 — 실행 전후 레코드 수 비교 🔴
- [ ] 11.5 `answer_must_not_contain` — 재수화 실패 탐지
- [ ] 11.6 `answers_not_reordered` — 요청 순서 유지

## Step 12 · `tests/eval/test_leak_sweep.py` 🔴

- [ ] 12.1 5개 시나리오를 먼저 실행해 감사 로그 채우기
- [ ] 12.2 `audit.sweep_for_leaks(corpus, records)` — 전 문서 × 전 페이로드
- [ ] 12.3 `assert report.payloads_scanned > 0` — **빈 로그 검사는 무의미** 🔴
- [ ] 12.4 `assert report.clean`
- [ ] 12.5 실패 시 `record_id` · `document_path` · `ngram` 출력
- [ ] 12.6 **PB-D3: 심은 유출을 반드시 탐지** — Hypothesis로 5-gram 주입 🔴

**12.6이 검사기 자체를 검사한다.** 검사기가 아무것도 못 잡는 버그가 있으면 "유출 0건"은 무의미하다.

## Step 13 · `tests/eval/compare_exaone_solo.py`

- [ ] 13.1 각 시나리오를 두 경로로 실행 (EXAONE 단독 / 구조추출+Agent)
- [ ] 13.2 두 답을 나란히 마크다운 출력
- [ ] 13.3 **자동 채점기를 만들지 않는다** — 사람이 읽는다 (BR-EV-05)
- [ ] 13.4 데모 1막 결정적 장면 ③에 쓸 출력 확보

## Step 14 · PBT와 리포트

- [ ] 14.1 PB-D1: `vocab.json` 로드 왕복 항등
- [ ] 14.2 PB-D2: `sweep_for_leaks` 순서 무관
- [ ] 14.3 **PB-D3: 심은 유출 반드시 탐지** 🔴
- [ ] 14.4 `make eval` — 6단계 흐름 (`u6/business-logic-model.md` §6)
- [ ] 14.5 리포트 생성 → `aidlc-docs/construction/build-and-test/eval-report.md`
- [ ] 14.6 처분 카운터 조회 (자동 응답률 ≥50%, 인용 준수 100%)
- [ ] 14.7 코퍼스 구성 집계 (`source_kind` 분포, 공식 비율 <60%)
- [ ] 14.8 목표 미달 시 non-zero 종료

---

# Day 5 — 최종 검증

## Step 15 · 게이트 G4

- [ ] 15.1 `make eval` 전체 (live 모드)
- [ ] 15.2 **유출 0건 확인** 🔴
- [ ] 15.3 분류 정확도 최종 측정
- [ ] 15.4 **육안 전수 확인** — `make eval-dump-payloads` (BR-EV-04) 🔴
- [ ] 15.5 체크리스트: 고객사명·제품명·버전·요구사항번호·원문·담당자·일정·금액
- [ ] 15.6 목업 모드로 3막 전체 재확인 (게이트 G5)
- [ ] 15.7 리포트 커밋

---

## 스토리 추적

| Story | 단계 | 완료 |
|---|---|:---:|
| S-02 기밀 최고 등급 판정 | 6, 7, 8 | [ ] |
| S-30 Day 2 게이트 🔴 | 8 | [ ] |
| S-21 어휘 밖은 못 나간다 🔴 | 1.6, 3.8 | [ ] |
| S-05 유출 0건 증명 🔴 | 12, 15 | [ ] |
| S-20 갈리는 답 병기 | 3.6, 3.10 | [ ] |
| S-12 문서에 없는 지식 | 3.3~3.5, 5.6 | [ ] |
| S-14 지금 무슨 일이 | 3.5 | [ ] |
| S-19 부재 중 응답 | 5.3 | [ ] |
| S-25 네트워크 없이 3막 🔴 | 10 | [ ] |
| S-08 외부 추론 실익 | 13 | [ ] |

---

## 완료 기준

- [ ] 문서 40~60건, 공식 문서 비율 < 60%
- [ ] 함정 문서가 `secret`으로 분류됨 🔴
- [ ] 엇갈리는 기록 1쌍이 `divergent: true`를 만든다
- [ ] `vocab.json`에 성능 수치 슬롯 부재 → 시나리오 3 후속 폴백 🔴
- [ ] `make eval-classify` Day 2 통과 (기밀 재현율 100%) 🔴
- [ ] `make eval` 유출 0건 🔴
- [ ] PB-D3 (검사기가 심은 유출 탐지) 통과 🔴
- [ ] 목업 모드로 3막 전체 재생 🔴
- [ ] 육안 전수 확인 완료
- [ ] EXAONE 단독 대비 품질 우위 예시 확보
- [ ] `banned.json`과 코퍼스 고객사명 표기 동기화

## 보안 준수 요약

| 규칙 | 상태 | 근거 |
|---|---|---|
| SECURITY-01 | N/A | 데이터 파일. 저장소를 만들지 않는다 |
| SECURITY-02 | N/A | 네트워크 중개 없음 |
| SECURITY-03 | 준수 | 평가 리포트에 원문 미기록 (경로·등급만) |
| SECURITY-04 | N/A | HTML 미서빙 |
| SECURITY-05 | N/A | 입력 인터페이스 없음 |
| SECURITY-06 | N/A | IAM 없음 |
| SECURITY-07 | N/A | 네트워크 없음 |
| SECURITY-08 | N/A | 엔드포인트 없음 |
| SECURITY-09 | 준수 | 실제 사내 문서·고객 자료 미사용. **전부 가상 샘플** |
| SECURITY-10 | 준수 | 새 의존성 없음 |
| SECURITY-11 | 준수 | 함정 문서가 남용 시나리오 검증에 기여 |
| SECURITY-12 | 준수 | 코퍼스에 실제 자격증명·토큰 미포함 (샘플 문서에 가짜 키도 넣지 않는다) |
| SECURITY-13 | 준수 | `json.loads`만 |
| SECURITY-14 | N/A | 알람 없음 |
| SECURITY-15 | 준수 | 평가 하네스에 명시적 예외 처리 |

**PII 정책**: 인명은 `김철수`·`박선영`·`최민수`·`정연구원`·`한지원`으로 고정. **실제 팀원 이름을 쓰지 않는다.**

| PBT 규칙 | 상태 |
|---|---|
| PBT-01 | 준수 (`u6/domain-entities.md` §8) |
| PBT-02 | 준수 (PB-D1) |
| PBT-03 | 준수 (**PB-D3** — 검사기 자체 검증) |
| PBT-04 | N/A (멱등 연산 없음) |
| PBT-05 | **N/A** — `labels.json`이 정답이지만 고정 데이터 비교이고 오라클 PBT가 아니다 |
| PBT-06 | **N/A** — 전부 stateless |
| PBT-07 | 준수 (U1 `korean_technical_text()` 재사용) |
| PBT-08 | 준수 (U1 프로파일) |
| PBT-09 | 준수 (U1 의존성) |
| PBT-10 | **준수** — 이 유닛이 예제 기반 테스트의 본거지다 (`test_scenarios.py`) |

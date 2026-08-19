# U2 `knowledge-edge` — Code Generation Plan

**소유**: B · **일정**: Day 1 (세션), Day 3 (읽기) · **스토리**: 7개 주담당
**설계 근거**: `aidlc-docs/construction/u2-knowledge-edge/`
**코드 위치**: `src/mesh/store.py`, `config/agents.yaml`, `data/sessions/`

---

## 유닛 컨텍스트

| | |
|---|---|
| **구현 스토리** | S-09, S-10, S-12, S-14, S-17, S-19, S-23 |
| **의존** | U1 `schemas.py`(타입 계약) · U6 `corpus/**`(읽을 파일) |
| **제공** | `KnowledgeStore` — U3가 소비 |
| **경계** | 원문을 읽어 U1에만 넘긴다. **경계 밖 클라이언트를 import하지 않는다** |

---

# Day 1 — 세션 로더

## Step 1 · `config/agents.yaml`

- [x] 1.1 에이전트 3개 정의 (`u2/domain-entities.md` §4)
- [x] 1.2 `persona_prompt` — 필수 문구 5개 포함 (BR-AG-02)
- [x] 1.3 `knowledge_scope` glob — 김책임에 `corpus/customer-H/**` 포함 (협의 담당)
- [x] 1.4 `disclose` 블록 — 데모용 3개 모두 `true`
- [x] 1.5 **`agents.yaml`에 고객사명·기밀을 넣지 않는다** (프롬프트도 검증 대상)
- [x] 1.6 U1의 `config.py` 로더로 파싱 확인

## Step 2 · `Session` 관련 엔티티

U1 `schemas.py`에 두지 않고 `store.py`에 둔다 (U2 소유).

- [x] 2.1 `RunInfo`, `EditInfo`, `DatasetInfo` (`tier` 필드 포함)
- [x] 2.2 `Session` — `focus`/`summary`를 **원문 취급**
- [x] 2.3 `VerifiedQA` — **`tier` 보존 필수** (BR-S-05)
- [x] 2.4 `Disclose`(`expertise: Literal[True]`), `AgentConfig`, `AgentCard`

## Step 3 · 세션 JSON 3개

- [x] 3.1 `data/sessions/person_kim.json` — 활동 중, `open_paths` 2개
- [x] 3.2 `data/sessions/person_park.json` — 학습 실행 중, `recent_runs` + `datasets(tier=secret)`
- [x] 3.3 `data/sessions/person_choi.json` — 2시간 전 (자리 비움)
- [x] 3.4 **경로가 전부 `MESH_DATA_ROOT` 상대** (`~/work/...` 금지) (BR-D-06)
- [x] 3.5 시각을 데모 기준(`2026-08-19T14:35`)에 맞춤

## Step 4 · `store.py` 세션 로드

- [x] 4.1 `load_session()` — JSON + `verified_qa` 병합
- [x] 4.2 `mtime` 비교 재로드 (데몬 없이 수동 갱신 지원, BR-S-08)
- [x] 4.3 `${MESH_DATA_ROOT}` 치환 (U1 `safe_resolve()` 사용)
- [x] 4.4 `freshness()` — 3단 판정. `MESH_DEMO_NOW` 지원 (BR-S-04)
- [x] 4.5 `append_verified()` — 추가 전용, `tier` 보존
- [x] 4.6 `tests/unit/test_store_session.py` — 3개 세션 로드, 신선도 시각 조작

---

# Day 3 — 파일 읽기와 목록

## Step 5 · `read()` — 파일 읽기

- [ ] 5.1 `expand()` — `safe_resolve()` + **경로 탈출 거부**
- [ ] 5.2 `knowledge_scope` glob 매치 → `ScopeViolationError` (BR-S-03)
- [ ] 5.3 파일 종류 판정 (BR-S-09 매핑 테이블)
- [ ] 5.4 프런트매터 파싱 → `display_title`, `as_of`, `formality`
- [ ] 5.5 스크립트·설정(`.py`/`.yaml`)은 주석 헤더 파싱
- [ ] 5.6 **`run_log`는 마지막 200줄**, 그 외는 앞부분. 256KB 상한 (BR-S-10)
- [ ] 5.7 `Chunk` 생성 — **`tier`는 채우지 않는다** (U1의 일)
- [ ] 5.8 `tests/unit/test_store_read.py` — 경로 탈출, scope 위반, 종류별 파싱

## Step 6 · `select_paths()` — 경로 선택

- [ ] 6.1 프롬프트 구성 — 경로 + `display_title` + 세션 `focus`/`summary`
- [ ] 6.2 **파일 본문을 넣지 않는다** 🔴 (BR-S-02)
- [ ] 6.3 출력은 **인덱스 배열** (`{"selected":[0,1]}`) — 경로 문자열 생성 금지
- [ ] 6.4 실패/파싱 오류 → `open_paths` 전체 반환
- [ ] 6.5 `tests/unit/test_select_paths.py` — 본문 미포함 확인, 실패 폴백

**6.2 검증**: 프롬프트 문자열에 `Chunk.text`가 등장하지 않음을 테스트로 확인.

## Step 7 · `list_agents()` — 지목 목록

- [ ] 7.1 `AgentCard` 구성 — `expertise`는 항상, 나머지는 `disclose` 반영
- [ ] 7.2 `activity_status` 매핑 (LIVE→active / STALE→away / EXPIRED→offline)
- [ ] 7.3 `question_count_today` — `audit` + `local_queries`에서 집계
- [ ] 7.4 **`current_focus_summary` — Gatekeeper로 식별자 제거 요약** 🔴 (BR-S-06)
- [ ] 7.5 **요약 실패 시 `None`. 원문 폴백 없음** (fail closed)
- [ ] 7.6 요약 캐시 (키: `entity_id` + `updated_at`, TTL 5분)
- [ ] 7.7 `disclose: false`인 필드는 `None`
- [ ] 7.8 앱 시작 시 워밍업 (첫 화면에서 3초 대기 방지)
- [ ] 7.9 `tests/unit/test_list_agents.py`

## Step 8 · PBT

- [ ] 8.1 `tests/generators.py`에 `sessions()`, `verified_qas()` 추가
- [ ] 8.2 PB-S1: `expand()`가 root 밖 경로에 항상 `PathEscapeError`
- [ ] 8.3 PB-S2: `Session` 직렬화 왕복 항등
- [ ] 8.4 PB-S3: `freshness()` 단조 악화
- [ ] 8.5 **PB-S4: `list_agents()` 결과에 `Session.focus`/`summary` 문자열 부재** 🔴
- [ ] 8.6 PB-S5: `read()` 결과의 모든 `internal_path`가 `knowledge_scope`에 매치

**8.5가 FR-31의 검증이다.** 고객사명·금액·인명을 포함한 임의 세션을 생성해 목록 응답 JSON에 원문이 없음을 확인한다.

## Step 9 · 경계 확인

- [ ] 9.1 `grep -c "boto3\|BrokerClient\|bedrock" src/mesh/store.py` == 0
- [ ] 9.2 `rglob("**/*")` 같은 전역 스캔이 없음 확인 (BR-S-01)
- [ ] 9.3 import 경계 테스트 통과
- [ ] 9.4 커밋

---

## 스토리 추적

| Story | 단계 | 완료 |
|---|---|:---:|
| S-09 직접 지목 | 1, 7 | [ ] |
| S-10 목록이 기밀을 안 새게 🔴 | 7.4, 7.5, 8.5 | [ ] |
| S-12 문서에 없는 지식 | 5, 6 | [ ] |
| S-14 지금 무슨 일이 | 3.2, 4.4 | [ ] |
| S-17 승인 답변 환류 | 2.3, 4.5 | [ ] |
| S-19 부재 중 응답 | 3.3, 4.4 | [ ] |
| S-23 인용이 권한 우회 안 함 | 5.4 (`display_title`/`internal_path` 분리) | [ ] |

---

## 완료 기준

- [ ] 세션 3개 로드 + `verified_qa` 병합 (`tier` 보존)
- [ ] `${MESH_DATA_ROOT}` 치환 + 경로 탈출 거부 🔴
- [ ] `knowledge_scope` 위반 거부 (에이전트 간 지식 격리)
- [ ] 신선도 3단 판정 (시각 조작 테스트)
- [ ] **데몬 없이 세션 JSON만으로 3막 통과** (FR-21)
- [ ] `list_agents()` 응답에 고객사명·경로 부재 🔴
- [ ] `select_paths()` 프롬프트에 파일 본문 부재 🔴
- [ ] `run_log` 마지막 200줄 읽기
- [ ] PB-S1~PB-S5 통과
- [ ] `store.py`에 경계 밖 import 부재
- [ ] 전역 파일 스캔 부재

## 보안 준수 요약

| 규칙 | 상태 | 단계 |
|---|---|---|
| SECURITY-01 | N/A (U2는 저장소를 만들지 않는다. 파일 읽기만) | — |
| SECURITY-02 | N/A | — |
| SECURITY-03 | 준수 (U1 로거 사용) | — |
| SECURITY-04 | N/A | — |
| SECURITY-05 | **준수** — 경로 탈출 거부, scope glob | 5.1, 5.2 |
| SECURITY-06 | N/A | — |
| SECURITY-07 | N/A | — |
| SECURITY-08 | 준수 — `knowledge_scope`가 에이전트 간 격리 | 5.2 |
| SECURITY-09 | 준수 | — |
| SECURITY-10 | 준수 (U1 `pyproject.toml` 공유. 새 의존성 없음) | — |
| SECURITY-11 | 준수 — U2는 보안 로직을 갖지 않는다 (전부 U1) | 9.1 |
| SECURITY-12 | N/A | — |
| SECURITY-13 | 준수 — `json.loads`/`yaml.safe_load`만 | 4.1 |
| SECURITY-14 | N/A | — |
| SECURITY-15 | 준수 — 파일 I/O에 명시적 예외 처리 | 5 |

| PBT 규칙 | 상태 |
|---|---|
| PBT-01 | 준수 (`u2/domain-entities.md` §7) |
| PBT-02 | 준수 (PB-S2) |
| PBT-03 | 준수 (PB-S1, S3, S4, S5) |
| PBT-04 | N/A (멱등 연산 없음. `append_verified`는 추가) |
| PBT-05 | N/A |
| PBT-06 | **N/A** — `append_verified`가 유일한 쓰기이고 추가 전용이라 상태 기계가 자명하다 |
| PBT-07 | 준수 (U1 생성기 + `sessions()`) |
| PBT-08 | 준수 (U1 프로파일 공유) |
| PBT-09 | 준수 (U1 의존성) |
| PBT-10 | 준수 (U6 시나리오 예제 테스트) |

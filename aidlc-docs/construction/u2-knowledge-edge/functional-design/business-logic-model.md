# U2 — Business Logic Model

---

## 1. 이 유닛이 하는 일

> 사람의 작업 상태를 유지하고, 질문에 필요한 파일만 골라 읽는다.

핵심 발상은 **인덱스 대신 세션**이다.

```
[데몬 또는 수동 갱신] --> [개인 세션] --> [필요할 때 파일 직접 읽기]
   작업 상태 모니터링       메모리/JSON      경로를 이미 알고 있으므로
                                            인덱스 없이 바로 읽는다
```

**인덱스는 항상 뒤처진다. 세션은 실시간이다.** 그래서 "박선임이 지금 그 스크립트를 돌리고 있다" 같은 답이 가능해진다. 어떤 벡터 DB도 이건 담지 못한다.

---

## 2. 주 흐름 — 세션 로드

```
load_session(entity_id):
  1. data/sessions/{entity_id}.json  (mtime 확인 후 필요시 재로드)
  2. data/verified/{entity_id}.json  (없으면 빈 목록)
  3. verified_qa 병합                       <- tier 보존 (BR-S-05)
  4. Session 반환

freshness(session, now):
  경과 = now - session.updated_at
  < 15분     -> LIVE
  < 24시간   -> STALE     (신뢰도 x0.8, 기준 시각 명시)
  그 외      -> EXPIRED   (실시간 주장에서 제외, 파일은 계속 읽는다)
```

`now`는 `MESH_DEMO_NOW`가 설정돼 있으면 그 값을 쓴다 (데모 재현성).

---

## 3. 주 흐름 — 지식 꺼내기

```
select_paths(session, question):
  1. 후보 = session.open_paths
  2. 프롬프트에 넣는 것: 경로, display_title, 세션 focus/summary
     프롬프트에 넣지 않는 것: *** 파일 본문 ***          (BR-S-02)
  3. EXAONE -> {"selected": [0, 1]}     인덱스 배열
  4. 실패/파싱 오류 -> open_paths 전체 반환 (더 많이 읽고 게이트키퍼가 막게)

read(paths):
  for rel in paths:
    1. expand(rel, MESH_DATA_ROOT)     -> PathEscapeError 가능
    2. knowledge_scope glob 매치       -> ScopeViolationError 가능
    3. 파일 종류 판정 (BR-S-09)
    4. run_log 는 마지막 200줄, 그 외는 앞부분 (256KB 상한, BR-S-10)
    5. 헤더에서 as_of 추출, 없으면 mtime
    6. Chunk 생성 (text=원문, tier=미정 — 게이트키퍼가 채운다)
```

**`read()`가 `tier`를 정하지 않는 것이 중요하다.** 등급 판정은 U1의 일이다. U2는 원문을 읽어 넘기기만 한다. 이 분리가 U2를 게이트키퍼로부터 독립시킨다.

---

## 4. 주 흐름 — 에이전트 목록

```
list_agents():
  for cfg in agents.yaml:
    session = load_session(cfg.entity_id)
    fresh   = freshness(session, now)
    card = AgentCard(
      entity_id, display_name,
      expertise = cfg.expertise,                    # 항상 공개
      activity_status       = map(fresh) if cfg.disclose.activity_status else None,
      away_minutes          = 경과분      if fresh != LIVE and disclose else None,
      question_count_today  = count()     if cfg.disclose.question_count_today else None,
      current_focus_summary = summarize(session.focus) if cfg.disclose.current_focus else None,
      session_as_of         = session.updated_at if cfg.disclose.current_focus else None,
      freshness             = fresh if disclose else None,
    )

summarize(focus):                                   # BR-S-06
  1. 캐시 조회 (키: entity_id + session.updated_at, TTL 5분)
  2. Gatekeeper 로 식별자 제거 요약
  3. 실패 -> None  (원문 폴백 없음. fail closed)
```

**`summarize()` 실패 시 `None`을 반환하는 것이 설계다.** "요약에 실패했으니 원문을 그냥 보여준다"는 폴백이 있으면, 그게 유출 경로가 된다.

---

## 5. 시나리오별 동작

### 시나리오 1 — 경로 지목

| 단계 | 동작 |
|---|---|
| 세션 로드 | `person:kim`, `updated_at 14:31`, 경과 4분 → `LIVE` |
| 후보 | `corpus/customer-H/req-spec-2026H.md`, `corpus/kim/docs/auth-design.md` |
| 선택 근거 | 세션 `focus`에 "고객사 H 인증 요구사항"과 "SDK v3.2 토큰 정책"이 둘 다 있다 |
| 선택 결과 | `[0, 1]` — 둘 다 |
| 읽기 | 2개 파일. `knowledge_scope`에 `corpus/customer-H/**` 포함 (김책임이 협의 담당) |
| U1에 넘김 | `Chunk` 2개 (원문 포함, `tier` 미정) |

### 시나리오 2 — 실시간 상태가 답이 된다

| 단계 | 동작 |
|---|---|
| 세션 로드 | `person:park`, 경과 2분 → `LIVE` |
| 목록 표시 | `🟢 활동 중 · 지금 학습 실행 중` → 정연구원이 **선택에 확신** |
| q1 (기법) 후보 | `preprocess_v3.py`, `configs/v3.yaml` → 둘 다 선택 |
| q2 (허락) 후보 | 파일 아님. `session.recent_runs`에서 사실 추출 |
| `session_facts` | `run_status=running`, `started_at=14:02`, `eta=17:10`, `gpu_occupied=cuda:0`, `script_last_edited=13:47` |
| 신선도 표시 | `🔴 세션 · 2026-08-19 14:33 기준 (실시간)` |

**`session_facts`는 파일이 아니라 세션에서 온다.** 이게 이 유닛의 진짜 가치다 — 어떤 문서에도 없는 지식.

`datasets[0].tier == "secret"`이지만 **q1은 데이터를 볼 필요가 없다.** 답은 코드에 있다. 그래서 q1은 `internal`로 처리된다 (`scenarios.md` §2 ②).

### 시나리오 3 — 부재 중 응답

| 단계 | 동작 |
|---|---|
| 세션 로드 | `person:choi`, `updated_at 12:30`, 경과 125분 → `STALE` |
| 목록 표시 | `⚪ 자리 비움 (2시간) · 오늘 0건` |
| 후보 | `corpus/choi/docs/auth-review.md` 등 — **파일은 그대로 읽힌다** |
| 신뢰도 | Agent 신뢰도 0.78 × 0.8 = **0.62** → `UNVERIFIED` 배지 |
| 답변 | 나온다. 세션 기준 시각 명시 |

**여기서 신뢰도 감쇠가 실제로 결과를 바꾼다.** 0.78이면 자동 응답(≥0.75)이지만 `STALE` 보정으로 0.62가 되어 `미검증` 배지가 붙는다. `scenarios.md` §5 ④의 제안이 동작하는 지점이다.

> 시나리오 3 화면의 최민수 신뢰도 0.78은 원래 자동 응답이었다. `STALE` 보정을 넣으면 0.62로 내려가 배지가 붙는다. 이게 더 정직하다 — 2시간 전 상태로 답한 것이니까. 데모 대본을 이에 맞춰 조정한다.

---

## 6. 이 유닛이 하지 않는 일

| 안 하는 것 | 어디서 하나 |
|---|---|
| 등급 판정 | U1 `classifier.py` |
| 원문 변환 (추출·가명화) | U1 `extractor.py`, `pseudonymizer.py` |
| Agent 호출 | U1 `gatekeeper.ask_agent()` |
| 벡터 검색·임베딩·전역 스캔 | **아무도 안 한다** (설계에서 제거) |
| 파일시스템 감시 데몬 | 구현하지 않는다 (`mtime` 재로드로 대체) |
| 인박스 관리 | U3 `inbox.py` |

`store.py`는 경계 밖 클라이언트를 import하지 않는다 (import 경계 테스트로 강제).

---

## 7. 지연 예산

| 동작 | 목표 | 근거 |
|---|---|---|
| `load_session` | < 10ms | JSON 파일 2개 |
| `freshness` | < 1ms | 순수 계산 |
| `select_paths` | < 1.5s | EXAONE ×1 (실측 0.9s) |
| `read` (파일 3개) | < 50ms | 로컬 I/O, 256KB 상한 |
| `list_agents` (캐시 히트) | < 20ms | 요약 캐시 |
| `list_agents` (캐시 미스) | < 3s | EXAONE ×3 (에이전트 3개) 병렬 |

**`list_agents` 캐시 미스가 가장 비싸다.** 앱 시작 시 워밍업으로 미리 채운다. 목록은 질문 화면 진입 시 필요하므로 사용자가 기다리면 안 된다.

---

## 8. 테스트 가능한 속성 (PBT-01)

`domain-entities.md` §7에 PB-S1~PB-S5로 정리돼 있다.

**PB-S4가 가장 중요하다** — `list_agents()` 결과에 세션 원문이 없다는 불변식.
`Session`을 임의 생성(고객사명·금액·인명 포함)하고 `list_agents()` 결과 JSON을 검사한다.

**PBT 미적용 (N/A 근거)**
- `select_paths()` — EXAONE 호출. 출력이 인덱스 배열인지만 예제 테스트
- `read()`의 I/O 자체 — 예제 테스트. 단 경로 탈출 거부는 PB-S1으로 PBT
- 상태 기반 PBT (PBT-06) — `append_verified`가 유일한 쓰기 연산이고 추가 전용이라 상태 기계가 자명하다. **N/A**

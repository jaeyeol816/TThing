# U2 — Business Rules

규칙 ID: `BR-S-*`

---

## BR-S-01 · 검색이 아니라 지목이다 (FR-17)

질문이 오면 임베딩으로 청크를 뒤지지 않는다. **세션이 이미 좁혀 놓은 후보 경로 중에서 고르고 그 파일만 읽는다.**

```
open_paths (세션이 제공한 후보)  ->  EXAONE 이 질문과 관련된 것 선택  ->  그 파일만 읽기
```

**세션에 없는 것은 못 찾는다.** 이건 한계이자, 동시에 "지금 이 사람의 관심사"라는 강력한 사전 필터다.

**금지**: 전역 파일 검색, 벡터 유사도, `corpus/**` 전체 스캔. 코드에 `rglob("**/*")`가 등장하면 안 된다.

---

## BR-S-02 · 경로 선택 프롬프트에 파일 본문을 넣지 않는다 (NFR-P-04)

```
SYSTEM: Choose which candidate paths are relevant to the question.
        Output {"selected": [<index>...]} only.
USER:   QUESTION: <질문>
        CANDIDATES:
          0. corpus/customer-H/req-spec-2026H.md   (title: 고객사 요구사항명세서)
          1. corpus/kim/docs/auth-design.md        (title: SDK 인증 설계 문서)
        SESSION FOCUS: 고객사 H 인증 요구사항 검토 + SDK v3.2 토큰 정책
```

**본문을 넣으면 안 되는 이유 2가지**
1. 지연·토큰 비용 (설계 §6.2)
2. **경로 선택은 등급 판정 *전*에 일어난다.** 아직 어떤 파일이 기밀인지 모르는 시점에 본문을 EXAONE에 보내는 것은 순서가 뒤바뀐 것이다. 이번 구현의 EXAONE은 사외 SaaS이므로 더욱 그렇다

전달하는 것: 경로, `display_title`, 세션 `focus`/`summary`.
출력은 **인덱스 배열**이다. 경로 문자열을 생성하게 하면 존재하지 않는 경로를 만들어낼 수 있다.

선택 실패·파싱 오류 → `open_paths` 전체를 반환 (fail closed 방향: 더 많이 읽고 게이트키퍼가 막게 한다).

---

## BR-S-03 · 읽기 범위 제한 (NFR-S-05)

`read(paths)`는 2중 검사를 한다.

1. `expand()` — `MESH_DATA_ROOT` 하위인지 (`is_relative_to`)
2. `knowledge_scope` glob 매치 — 그 에이전트의 지식 범위인지

둘 중 하나라도 실패하면 `PathEscapeError` / `ScopeViolationError`.

**왜 2중인가**: `open_paths`는 세션 JSON에서 오고 세션 JSON은 사람이 편집한다. `../../../etc/passwd`가 들어갈 수 있다. 그리고 `knowledge_scope` 검사는 김책임 Agent가 박선임 파일을 읽는 것을 막는다 — 에이전트 간 지식 격리다.

---

## BR-S-04 · 신선도 3단 (FR-19, Round 2 Q12)

`SESSION_STALE_MINUTES=15` 기준.

| 경과 | `Freshness` | 신뢰도 | 답변 표시 | `session_facts` |
|---|---|---|---|---|
| < 15분 | `LIVE` | 보정 없음 | `🔴 실시간` | 사용 |
| 15분 ~ 24시간 | `STALE` | **× 0.8** | `⚪ N시간 전 기준` + 기준 시각 명시 | 사용 (시각 병기) |
| ≥ 24시간 | `EXPIRED` | × 0.8 | `⚪ 세션 오래됨` | **실시간 주장에서 제외** |

`EXPIRED`에서도 **파일은 그대로 읽는다.** 파일은 언제든 유효하고, 세션만 신뢰도를 깎는다.
이게 시나리오 3(자리에 없는 최민수도 답한다)이 성립하는 이유다 (FR-18).

`EXPIRED`에서 제외되는 것은 `recent_runs`·`recent_edits` 같은 **"지금 무슨 일이 벌어지는지"** 주장이다. 24시간 전 세션으로 "지금 학습 실행 중"이라고 말하면 틀린 실시간 정보가 된다.

**데모 재현성**: `MESH_DEMO_NOW` 환경변수로 기준 시각을 고정할 수 있게 한다. 시연 날짜가 바뀌어도 세션 3개의 신선도가 유지된다.

---

## BR-S-05 · 승인된 QA 병합과 등급 보존 (FR-20, Round 2 Q14)

```
load_session(entity_id):
  1. data/sessions/{entity_id}.json 로드
  2. data/verified/{entity_id}.json 로드 (없으면 빈 목록)
  3. Session.verified_qa 에 병합
```

**병합된 QA도 다른 지식과 똑같이 게이트키퍼를 통과한다.** `VerifiedQA.tier`가 그 기준이다.

```
verified_qa 항목 -> Chunk(text=answer, tier=qa.tier, ...) -> 게이트키퍼
```

**금지**: "사람이 승인했으니 그대로 Agent에 보낸다." 승인은 *답변의 정확성*을 검증한 것이고, *등급*을 낮춘 것이 아니다.

`append_verified()`는 추가 전용이다. 기존 항목을 수정·삭제하지 않는다 (같은 질문에 대한 새 승인은 새 항목으로 추가하고 `verified_at`이 최신인 것을 쓴다).

---

## BR-S-06 · 목록 표시는 게이트키퍼를 통과한다 (FR-31, Round 2 Q13)

`list_agents()`가 만드는 `current_focus_summary`는 `Session.focus` 원문이 **아니다.**

```
Session.focus = "고객사 H 인증 요구사항 검토 + SDK v3.2 토큰 정책"
   |
   | Gatekeeper 로 식별자 제거 요약 (기술 영역 상위어만 남긴다)
   v
current_focus_summary = "인증 관련 작업 중"
```

**이 화면은 인증 없이 보인다.** 여기서 고객사명이 새면 게이트키퍼를 우회한 유출이다.

**변환 실패 시**: `current_focus_summary = None`. 즉 **표시하지 않는다.** 원문을 대신 표시하는 폴백은 없다 (fail closed).

**캐싱**: 목록 조회마다 EXAONE을 부르면 비싸다. `Session.updated_at`을 키로 요약을 캐시한다 (메모리, TTL 5분).

`disclose` 설정을 존중한다.

| `disclose` 항목 | `false`일 때 |
|---|---|
| `activity_status` | `activity_status`, `away_minutes` 모두 `None` |
| `question_count_today` | `None` |
| `current_focus` | `current_focus_summary`, `session_as_of` 모두 `None` |
| `expertise` | 끌 수 없다 (`Literal[True]`) |

---

## BR-S-07 · 활동 상태 판정

```
LIVE     -> "active"
STALE    -> "away" + away_minutes = 경과 분
EXPIRED  -> "offline"
```

세션 `updated_at`에서 파생한다. 별도 하트비트를 만들지 않는다 (데몬 없이 동작해야 하므로, FR-21).

---

## BR-S-08 · 데몬 없이 동작 (FR-21)

세션 갱신 방식 3가지를 지원하고, 데모에서는 가장 단순한 것을 쓴다.

| 방식 | 구현 | 데모 |
|---|---|---|
| 파일시스템 감시 데몬 | 구현하지 않는다 | ✕ |
| **앱 시작 시 1회 로드 + 파일 변경 감지** | `mtime` 비교로 재로드 | **○** |
| 시연 중 수동 갱신 | JSON 편집 → 다음 요청에 반영 | ○ |

`load_session()`이 매 호출 시 `mtime`을 확인해 변경됐으면 재로드한다. 시연 중 JSON을 편집하면 즉시 반영된다 — 데몬의 효과를 흉내 내는 가장 저렴한 방법이다.

**세션의 존재와 그것이 답에 미치는 영향만 보이면 데모 목적은 달성된다** (`scenarios.md` §0.4).

---

## BR-S-09 · 파일 종류별 `Chunk` 메타데이터

`Chunk.formality`와 `source_kind`가 시나리오 3의 병기 화면에 쓰인다 (FR-33).

| 경로 패턴 | `source_kind` | `formality` |
|---|---|---|
| `**/docs/**` | `design_doc` | `official` |
| `**/minutes/**` | `minutes` | `official` |
| `**/notes/**` | `note` | **`informal`** |
| `**/scripts/**` | `script` | `official` |
| `**/configs/**` | `config` | `official` |
| `**/runs/**` | `run_log` | `official` |
| `customer-*/**` | `spec` | `official` |
| `**/benchmark/**` | `benchmark` | `official` |

`as_of`는 문서 헤더의 날짜 표기를 우선하고, 없으면 파일 `mtime`을 쓴다.

**왜 필요한가**: 시나리오 3에서 김책임 근거는 개인 메모(2025-11, 비공식), 최민수 근거는 설계 리뷰(2025-12, 공식)다. 화면에 이 차이를 표시하는 것이 "둘 다 사실일 수 있습니다"라는 서술을 뒷받침한다.

---

## BR-S-10 · 파일 크기 상한

파일 하나당 **256KB** 상한. 초과하면 앞부분만 읽고 `truncated: true`를 표시한다.

이유: 실험 로그(`train.log`)가 수십 MB가 될 수 있다. 전부 읽으면 EXAONE 토큰 한도를 넘고 지연이 폭발한다.

`run_log` 종류는 **마지막 200줄**을 읽는다 (로그는 뒤가 중요하다). 다른 종류는 앞부분.

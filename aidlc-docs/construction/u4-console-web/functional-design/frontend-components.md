# U4 — Frontend Components

**빌드 파이프라인 없음.** 단일 HTML + 바닐라 JS + CSS. FastAPI가 정적 서빙.
프레임워크·번들러·npm 의존성이 없다 (설계 §6).

**CSP 제약**: `default-src 'self'`이고 `unsafe-inline`을 쓰지 않으므로 **인라인 `<script>`·`<style>`·`onclick` 속성을 쓸 수 없다.** 모든 JS는 `app.js`, 모든 CSS는 `style.css`, 이벤트는 `addEventListener`로 붙인다.

---

## 1. 컴포넌트 계층

```
App                                     app.js  루트. 탭 라우팅 + 전역 상태
├── HeaderBar                           모드 배지 · 신뢰 경계 고지 · 사용자 전환
├── TabNav                              질문 / 인박스 / 감사 로그
├── AskTab
│   ├── AgentPicker                     지목 목록 (최대 2)
│   │   └── AgentCardView  x3
│   ├── QuestionInput                   4000자 제한 + 카운터
│   ├── PreviewModal        ⭐ 결정적 장면 ①
│   │   ├── PayloadViewer               JSON 전문 (생략 없음)
│   │   ├── ValidationBadges            6단계 각각
│   │   └── ExclusionList               "포함되지 않은 것"
│   └── AnswerPanel
│       ├── AnswerCard      x1~2        divergent 시 병기
│       │   ├── TierBadge · ConfidenceBadge · AgentLabel
│       │   └── CitationList            display_title + section + tier + as_of
│       ├── DivergenceNote              "둘 다 사실일 수 있습니다"
│       └── FallbackNotice              "[사내망 밖으로 나간 것 없음]"
├── InboxTab
│   └── InboxItemView       xN
│       ├── DraftEditor                 수정 후 승인용
│       └── ResolveButtons              [승인] [수정 후 승인] [내가 아님]
└── AuditTab                ⭐ 결정적 장면 ②
    ├── SearchBox                       원문 문구 검색
    ├── ZeroHitBanner                   "0건 — 경계를 넘은 적이 없습니다"
    └── AuditRow            xN
```

---

## 2. 상태 (전역 객체 1개)

```javascript
const state = {
  tab: 'ask',
  currentUser: 'person:choi',      // 드롭다운 전환 (인증 없음)
  agents: [],                      // GET /api/agents
  selectedTargets: [],             // 최대 2
  question: '',
  prepareResult: null,             // PrepareResult
  askResult: null,                 // AskResult
  inbox: [],
  auditRows: [],
  auditQuery: '',
  health: null,                    // GET /api/health
  busy: false,
};
```

프레임워크가 없으므로 `render()`를 명시적으로 호출하는 방식이다.
상태 변경 → `render()` → 해당 탭만 다시 그린다. 전체 재렌더가 아니라 탭 단위로 나눈다.

---

## 3. HeaderBar — 정직성 표시

```
┌──────────────────────────────────────────────────────────────────────┐
│ 대리 에이전트 메시            [LIVE]  [broker]  사용자: 최민수 선임 v │
│ 신뢰 구역 LLM: api.friendli.ai   경계 시뮬레이션 (i)                 │
└──────────────────────────────────────────────────────────────────────┘
```

| 요소 | 출처 | 표시 규칙 |
|---|---|---|
| 모드 배지 | `health.exaone_mode` | `LIVE` 초록 / `MOCK` **주황 + "목업 모드"** |
| 전송 배지 | `health.agent_transport` | `broker` / `direct` / `mock` |
| 신뢰 구역 LLM | `health.trusted_zone_llm_base_url` | 호스트만 표시 |
| 경계 고지 | `health.trust_boundary_simulated` | `true`면 **"경계 시뮬레이션"** + 클릭 시 설명 팝오버 |

**목업 모드를 숨기지 않는다.** 심사자를 속이지 않는 것이 이 프로젝트의 신뢰성이다 (FR-48).
**경계 시뮬레이션 고지도 상시 표시한다.** 지적당하기 전에 먼저 말한다 (Round 2 Q15).

팝오버 문구:
> 이번 구현의 EXAONE은 사외 SaaS 엔드포인트입니다. 아키텍처가 보장하는 것은 "원문이 이 엔드포인트 하나에만 전달된다"는 것이고, 그 엔드포인트를 사내망으로 옮기는 것은 환경변수 하나입니다.

---

## 4. AgentPicker

```
누구에게 물어보시겠어요?                          [ 2명까지 선택 가능 ]

 ● 김철수 책임의 Agent          인증 · SSO · SDK 보안
   🟢 활동 중 · 오늘 질문 3건    지금 인증 관련 작업 중

 ○ 박선영 선임의 Agent          데이터 파이프라인 · 모델 학습
   🟢 활동 중 · 오늘 질문 1건    지금 학습 실행 중

 ○ 최민수 선임의 Agent          SDK 인증 모듈 · 배포 파이프라인
   ⚪ 자리 비움 (2시간)         오늘 질문 0건
```

| 필드 | 출처 | `null`일 때 |
|---|---|---|
| `expertise` | `AgentCard.expertise` | 항상 있다 (`Literal[True]`) |
| 활동 상태 아이콘 | `activity_status` | **행 자체를 생략** |
| `away_minutes` | `away_minutes` | 생략 |
| 오늘 질문 수 | `question_count_today` | 생략 |
| 현재 작업 | `current_focus_summary` | 생략 |

**`null`이면 "정보 없음"이 아니라 아예 표시하지 않는다.** `disclose`가 꺼진 것을 사용자가 알 필요가 없고, "숨김"이라고 표시하면 그 자체가 정보가 된다.

3번째 선택 시도 → 버튼 비활성 + 툴팁 "최대 2명".
`daily_limit` 초과 에이전트 → 회색 처리 + "오늘 상한 도달".

`data-testid`: `agent-card-{entity_id}`, `agent-select-{entity_id}`

---

## 5. PreviewModal — 결정적 장면 ① ⭐

```
┌─ 이 내용이 김책임 Agent(Claude)에게 전송됩니다 ────────────────────┐
│  [기밀]  원문 문장 0개 · 1.1KB · 검증 6/6 통과                     │
│                                                                    │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ {                                                          │   │
│  │   "task": "constraint_conflict_check",                     │   │
│  │   "domain": "authentication",                              │   │
│  │   "entities": [                                            │   │
│  │     { "ref": "REQ_A", "role": "external_requirement",      │   │
│  │       "facts": { "auth_mechanism_class": "challenge_...    │   │
│  │   ...                                                      │   │
│  │ }                                                          │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  검증  ✓스키마  ✓어휘  ✓범위  ✓금칙어  ✓원문대조  ✓크기         │
│                                                                    │
│  포함되지 않은 것                                                  │
│    고객사명 · 제품명 · 버전 · 요구사항 번호 · 원문 문장            │
│    담당자 · 일정 · 금액                                            │
│                                                                    │
│                                    [ 전송 ]      [ 취소 ]          │
└────────────────────────────────────────────────────────────────────┘
```

**요구사항**

| # | 요구 | 근거 |
|---|---|---|
| 1 | JSON **전문**. `...` 생략·접기·"더 보기" 금지 | 심사자가 전량을 읽어야 한다 |
| 2 | 스크롤 없이 읽히는 크기 (2KB 제한이 이걸 보장) | 3초 검토가 성립하는 조건 |
| 3 | 검증 6단계를 개별 배지로 표시 | 어느 단계를 통과했는지가 증거 |
| 4 | `verbatim_sentence_count`를 **측정값으로** 표시 | "원문 0개"가 주장이 아니라 계산 결과 |
| 5 | `excluded_categories`를 명시 | 없는 것을 보여주는 것이 설득력 |
| 6 | `[취소]` 시 감사 레코드 없음 (서버가 보장) | FR-09 |
| 7 | 2명 지목이면 모달을 **순차로** 2개 | 각각 승인. 하나만 승인도 가능 |
| 8 | `disposition == "blocked"`면 모달 대신 폴백 답변 표시 | 시나리오 3 |

JSON 하이라이팅은 CSS 클래스로 처리한다 (외부 라이브러리 없음, CSP 준수).

`data-testid`: `preview-modal`, `preview-payload`, `preview-send`, `preview-cancel`, `preview-check-{stage}`

---

## 6. AnswerPanel

### 단일 답변
```
김철수 책임의 Agent  ·  [기밀 · 사내 처리]  ·  신뢰도 0.83  ·  Agent 응답

충돌합니다. 고객사 H REQ-4412는 인증을 세션에 바인딩하고 자격증명 재사용을
금지하며 세션 상한이 8시간입니다. SDK v3.2는 토큰 수명이 24시간이고 ...

완화안
 1. SDK 토큰 수명을 세션 상한(8시간) 이하로 낮춘다
 2. 세션 종료 이벤트에 토큰 무효화를 연동한다

근거  📄 고객사 H 요구사항명세서 §3.2  [기밀]  2026-08
      📄 SDK 인증 설계 문서 §5        [사내]  2026-08
                                                  [ 직접 물어보기 ]
```

### 병기 (`divergent: true`)
```
두 답변이 서로 다릅니다. 판단에 참고하세요.

김철수 책임의 Agent  ·  [사내]  ·  신뢰도 0.71
  성능 문제 — 3천 TPS에서 세션 조회 지연이 임계 초과
  📄 개인 메모  ·  2025-11  ·  비공식

최민수 선임의 Agent  ·  [사내]  ·  신뢰도 0.62  [미검증]
  호환 문제 — 레거시 SSO가 세션 식별자를 전파하지 않음
  📄 설계 리뷰 문서  ·  2025-12  ·  공식
  ⚪ 세션 2시간 전 기준

둘 다 사실일 수 있습니다. 시점이 한 달 차이이고 문서 성격이 다릅니다.
                          [ 두 분께 확인 요청 ]   [ 직접 물어보기 ]
```

**문구 규칙**: "엇갈립니다"라는 단정을 쓰지 않는다. "서로 다릅니다"(관찰) + "둘 다 사실일 수 있습니다"(판단 보류).

### 배지

| 배지 | 조건 | 스타일 |
|---|---|---|
| `[기밀]` / `[사내]` / `[공개]` | `answer.tier` | 빨강 / 주황 / 초록 |
| `[사내 처리]` | `used_external_agent == false` | 파랑 |
| `[미검증]` | `disposition == UNVERIFIED` | 주황 |
| `신뢰도 N.NN` | `confidence` | ≥0.75 초록 / ≥0.45 주황 / <0.45 회색 |
| `🔴 실시간` / `⚪ N시간 전 기준` | `freshness` + `session_as_of` | |

### 폴백 표시 (시나리오 3)
```
[기밀 · 사내망 밖으로 나간 것 없음]

정확한 수치는 이 화면에서 제공할 수 없습니다.
확인된 것은 "임계를 넘었다"는 정성적 기록뿐입니다.
수치 열람은 고객 환경 벤치마크 권한이 필요하며, 김책임에게 확인 요청을 보냈습니다.

📄 개인 메모 · 2025-11
```

### 인용 렌더 (FR-43) 🔴
`display_title` + `section` + `tier` + `as_of` + `formality`만 표시.
**`internal_path`는 응답에 없으므로 표시할 방법이 없다.** 서버가 보장한다.

`data-testid`: `answer-card-{entity_id}`, `divergence-note`, `citation-{ref}`, `fallback-notice`

---

## 7. InboxTab

```
┌─ 정연구원 · 방금 ──────────────────────────────────────────────┐
│ 요청  preprocess_v3 스크립트를 지금 실행해도 되는지            │
│                                                                │
│ 상황  · 현재 train.py 실행 중 (14:02~, 약 3h 남음, cuda:0)     │
│       · 스크립트는 오늘 13:47에 수정됨                          │
│       · 기법 질문(라벨 불균형)은 Agent가 이미 답변함            │
│                                                                │
│ 초안  ┌──────────────────────────────────────────────────┐    │
│       │ 지금은 GPU를 점유 중이라 17:10 이후에 실행해      │    │
│       │ 주세요. 급하면 configs/v3.yaml을 복사해서 다른    │    │
│       │ GPU로 돌리셔도 됩니다.                            │    │
│       └──────────────────────────────────────────────────┘    │
│                                                                │
│         [ 승인 ]   [ 수정 후 승인 ]   [ 내가 아님 ]            │
└────────────────────────────────────────────────────────────────┘
```

| 버튼 | 동작 |
|---|---|
`[승인]` | 즉시 `POST resolve {action:"approve"}` |
`[수정 후 승인]` | 초안을 편집 가능 `<textarea>`로 전환 → `{action:"approve_with_edit", edited_text}` |
`[내가 아님]` | 대상 선택 드롭다운 → `{action:"not_me", redirect_to}` |

- `owner`는 `state.currentUser` 기준으로 필터링
- 같은 `thread_id` 항목은 그룹으로 묶어 표시 (2명 지목, BR-I-04)
- `already_answered`를 "Agent가 이미 답변함"으로 표시 — **담당자가 자기가 답할 조각만 보게 한다**
- `tier` 배지 표시

`data-testid`: `inbox-item-{item_id}`, `inbox-approve-{id}`, `inbox-edit-{id}`, `inbox-notme-{id}`

---

## 8. AuditTab — 결정적 장면 ② ⭐

```
감사 로그 — 신뢰 구역 밖으로 나간 것 전량

원문 검색  [ REQ-4412                      ]  [ 검색 ]

╔════════════════════════════════════════════════════════════════╗
║  0건 — 이 문구는 경계를 넘은 적이 없습니다                     ║
╚════════════════════════════════════════════════════════════════╝

────────────────────────────────────────────────────────────────
2026-08-19 14:33:41 │ 최민수 │ us.anthropic.claude-sonnet-4-5
  transport=broker │ trusted_zone_llm=api.friendli.ai
  tier=secret │ structured │ 1.1KB │ sha256=9f2a8c… │ 검증 6/6 │ 승인=최민수
  [ 페이로드 보기 ▾ ]
────────────────────────────────────────────────────────────────
```

| 요구 | 근거 |
|---|---|
| `q`가 있고 결과 0건이면 **`ZeroHitBanner`를 크게** 표시 | 1막 결정적 장면 |
| 각 행에 `trusted_zone_llm_base_url`과 `transport` 표시 | "원문이 어디로 갔는지"가 로그로 증명 |
| `[페이로드 보기]`로 전문 펼침 | 심사자가 직접 확인 |
| 검색은 `payload` 전문 대상 (대소문자 무시) | |
| 데모용 빠른 검색 버튼: `REQ-4412` `EAP-AKA` `H社` `12억` | 시연 흐름을 매끄럽게 |
| **신뢰 구역 내 처리(`local_queries`)는 표시하지 않는다** | "레코드가 없다"가 증거가 되려면 섞이면 안 된다 |

`data-testid`: `audit-search`, `audit-zero-hit`, `audit-row-{record_id}`, `audit-payload-{record_id}`

---

## 9. 사용자 상호작용 흐름

```
1. 앱 로드
   GET /api/health   -> HeaderBar (모드 · 경계 고지)
   GET /api/agents   -> AgentPicker

2. 에이전트 1~2명 선택 + 질문 입력 -> [물어보기]
   POST /api/ask/prepare
     disposition="ready"   -> PreviewModal
     disposition="blocked" -> 폴백 답변 즉시 표시 (한 왕복에 끝)

3. 모달에서 JSON 전문 읽고 [전송]
   POST /api/ask/send  { envelope_ids, approved_by: currentUser }
   -> AnswerPanel

4. 에스컬레이션되면 안내 표시. 인박스 탭에 배지 카운트

5. 사용자 전환 (드롭다운) -> 인박스 탭
   GET /api/inbox?owner=... -> InboxItemView
   [승인] -> POST resolve -> 목록 갱신

6. 감사 로그 탭
   GET /api/audit -> 목록
   검색 -> GET /api/audit?q=... -> ZeroHitBanner
```

---

## 10. 폼 검증 (클라이언트)

| 필드 | 규칙 | 서버 검증 |
|---|---|---|
| 질문 | 1~4000자. 카운터 표시. 초과 시 전송 버튼 비활성 | pydantic (`max_length=4000`) |
| 대상 | 1~2개. 3번째 클릭 차단 | pydantic (`max_length=2`) |
| 수정 답변 | 1~4000자 | pydantic |
| 재지목 대상 | 목록에서 선택 (자유 입력 없음) | pydantic 정규식 |
| 검색어 | 200자 | pydantic |

**클라이언트 검증은 편의일 뿐이고 서버가 진짜 검증이다** (NFR-S-05). 두 곳 모두에 둔다.

---

## 11. 접근성

| 항목 | 구현 |
|---|---|
| 시맨틱 HTML | `<nav>` `<main>` `<section>` `<button>` `<dialog>` |
| 모달 | `<dialog>` 요소 + `aria-labelledby` + 포커스 트랩 + Esc 닫기 |
| 배지 | 색상 단독 금지. 텍스트 라벨 병기 (`[기밀]`, `[미검증]`) |
| 탭 | `role="tablist"` + `aria-selected` + 화살표 키 이동 |
| 로딩 | `aria-live="polite"` 영역에 상태 알림 |
| 검색 결과 | `aria-live="assertive"`로 "0건" 알림 |
| 포커스 | `:focus-visible` 명시 스타일 |
| 대비 | 배지 색상 대비 4.5:1 이상 |

**색상 단독 금지가 특히 중요하다.** 등급 배지가 색으로만 구분되면 색각 이상 사용자가 기밀과 공개를 구별할 수 없다. 보안 UI에서 이건 기능 결함이다.

전체 WCAG 준수 검증에는 보조 기술 수동 테스트와 전문가 검토가 필요하다. 이 목록은 코드 수준에서 확보할 수 있는 범위다.

---

## 12. `data-testid` 규칙

`{component}-{element-role}` 또는 `{component}-{role}-{id}`.
자동 생성 ID를 쓰지 않고 안정적인 값(`entity_id`, `item_id`, `record_id`)을 쓴다.

```
agent-card-person_kim         agent-select-person_kim
question-input                question-submit
preview-modal                 preview-payload
preview-send                  preview-cancel
preview-check-ngram
answer-card-person_kim        divergence-note
citation-REQ_A                fallback-notice
inbox-item-itm_001            inbox-approve-itm_001
audit-search                  audit-zero-hit
audit-row-rec_001             health-mode-badge
trust-boundary-notice
```

`entity_id`의 `:`을 `_`로 치환한다 (CSS 셀렉터 충돌 방지).

---

## 13. 파일 크기 목표

| 파일 | 목표 |
|---|---|
| `index.html` | < 200줄 (구조만. 콘텐츠는 JS가 채운다) |
| `app.js` | < 700줄 |
| `style.css` | < 400줄 |

프레임워크가 없으므로 `app.js`가 가장 크다. 함수를 컴포넌트 단위로 나누고 (`renderAskTab`, `renderPreviewModal`, ...) 파일 상단에 목차 주석을 둔다.

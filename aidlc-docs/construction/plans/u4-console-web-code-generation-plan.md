# U4 `console-web` — Code Generation Plan

**소유**: C · **일정**: Day 4 (Day 1부터 목업으로 선행 가능) · **스토리**: 10개 주담당
**설계 근거**: `aidlc-docs/construction/u4-console-web/` (특히 `frontend-components.md`)
**코드 위치**: `src/mesh/web/{index.html, app.js, style.css}`

---

## 유닛 컨텍스트

| | |
|---|---|
| **구현 스토리** | S-01, S-05, S-09, S-16, S-18, S-20, S-23 주담당 + S-11/S-14/S-19/S-21/S-25 협력 |
| **의존 (계약만)** | U3 HTTP API 8개. **`?mock`으로 U3 없이 선행 개발 가능** |
| **제약** | 프레임워크·번들러·npm 의존성 없음. **CSP `default-src 'self'`, `unsafe-inline` 금지** |

**결정적 장면 3개가 이 유닛의 성공 기준이다.** 시간이 부족하면 배경 UI를 깎고 이 셋을 지킨다.

---

## Step 1 · 골격과 목업 (Day 1~2에 선행 가능)

- [ ] 1.1 `index.html` — `<nav>`/`<main>`/`<section>` 시맨틱 구조. **인라인 script/style 없음** 🔴
- [ ] 1.2 `style.css` — 시스템 폰트(`system-ui`), 유니코드 아이콘. **외부 CDN 0건** 🔴
- [ ] 1.3 `app.js` — 목차 주석 13개 섹션
- [ ] 1.4 `state` 전역 객체 (`u4/domain-entities.md` §2)
- [ ] 1.5 `api()` 클라이언트 + 오류 매핑 (410/422/429/500/네트워크)
- [ ] 1.6 `?mock` 파라미터 → `data/fixtures/api/` 재생
- [ ] 1.7 `render()` 루트 + 탭 분기
- [ ] 1.8 표시 매핑 테이블 6개 (tier / freshness / disposition / activity / stage / mode)

## Step 2 · HeaderBar — 정직성 표시 🔴

- [ ] 2.1 `GET /api/health` 로드
- [ ] 2.2 `exaone_mode == "mock"` → **주황 `MOCK · 목업 모드` 배지** (BR-U-09)
- [ ] 2.3 `agent_transport` 배지 (`broker`/`direct`/`mock`)
- [ ] 2.4 `trusted_zone_llm_base_url` 호스트 표시
- [ ] 2.5 **`trust_boundary_simulated` → `경계 시뮬레이션` 배지 + 설명 팝오버** 🔴
- [ ] 2.6 사용자 전환 드롭다운 + **"(데모용 전환 · 인증 없음)"** 표시 (BR-U-15)
- [ ] 2.7 `data-testid`: `health-mode-badge`, `trust-boundary-notice`

**2.2와 2.5를 숨기면 심사자를 속이는 것이다.** 데모의 신뢰성이 프로젝트의 유일한 자산이다.

## Step 3 · AgentPicker

- [ ] 3.1 `GET /api/agents` → 카드 3개
- [ ] 3.2 `expertise` 항상 표시
- [ ] 3.3 `activity_status` / `away_minutes` / `question_count_today` / `current_focus_summary`
- [ ] 3.4 **`null`인 필드는 아예 렌더하지 않는다** ("비공개" 표시 금지) 🔴 (BR-U-08)
- [ ] 3.5 최대 2명 — 3번째 클릭 차단 + 툴팁
- [ ] 3.6 `daily_limit` 초과 → 회색 + 클릭 불가
- [ ] 3.7 `data-testid`: `agent-card-{id}`, `agent-select-{id}`

## Step 4 · QuestionInput

- [ ] 4.1 `<textarea>` + 4000자 카운터
- [ ] 4.2 초과 시 전송 버튼 비활성
- [ ] 4.3 대상 0명이면 비활성
- [ ] 4.4 `state.busy` 중 비활성 (BR-U-14)
- [ ] 4.5 데모 질문 프리셋 드롭다운 (`data/questions.json`)
- [ ] 4.6 `aria-live="polite"` 상태 알림

## Step 5 · PreviewModal — 결정적 장면 ① 🔴

- [ ] 5.1 `<dialog>` + `aria-labelledby` + 포커스 트랩 + Esc 닫기
- [ ] 5.2 헤더: 등급 배지 + **`verbatim_sentence_count` 측정값** + 크기 + `validation_summary`
- [ ] 5.3 **`PayloadViewer` — 전문. `...` 생략·`<details>` 접기·"더 보기" 전부 금지** 🔴
- [ ] 5.4 JSON 하이라이팅 — `document.createElement` + 클래스. **문자열 조립 금지** (CSP+XSS)
- [ ] 5.5 `ValidationBadges` — **6단계 개별 표시** (`6/6` 요약만 보여주지 않는다)
- [ ] 5.6 `ExclusionList` — `excluded_categories` 명시 (BR-U-02)
- [ ] 5.7 `[전송]` → `approvedEnvelopeIds` 누적 → 다음 모달 또는 `send()`
- [ ] 5.8 `[취소]` → `modalQueue.shift()`. `send` 호출 안 함 (BR-U-03)
- [ ] 5.9 **2명이면 모달 순차 2개** (BR-U-04)
- [ ] 5.10 `disposition == "blocked"` → 모달 대신 폴백 답변 즉시 표시
- [ ] 5.11 `data-testid`: `preview-modal`, `preview-payload`, `preview-send`, `preview-cancel`, `preview-check-{stage}`

## Step 6 · AnswerPanel — 결정적 장면 ③ 🔴

- [ ] 6.1 `AnswerCard` — `agent_label` 항상 표시 (BR-U-06)
- [ ] 6.2 배지: tier / 사내처리 / 미검증 / 신뢰도 — **색상 + 텍스트 라벨 병기** 🔴 (BR-U-13)
- [ ] 6.3 답변 텍스트를 `textContent`로 (BR-U-12)
- [ ] 6.4 `CitationList` — `display_title` + `section` + `tier` + `as_of` + `formality`
- [ ] 6.5 **`internal_path`를 참조하지 않는다** (응답에 없다) 🔴 (BR-U-05)
- [ ] 6.6 `divergent` 시 **요청 순서 그대로 병기. 신뢰도 정렬 금지** 🔴 (BR-U-07)
- [ ] 6.7 `divergence_note`를 서버 문구 그대로 표시
- [ ] 6.8 헤더 문구 "두 답변이 서로 다릅니다. 판단에 참고하세요." (단정 금지)
- [ ] 6.9 `[두 분께 확인 요청]` 버튼
- [ ] 6.10 `freshness` 표시 (`🔴 실시간` / `⚪ N시간 전 기준`)
- [ ] 6.11 `used_external_agent == false` → **`[사내망 밖으로 나간 것 없음]`** 배지
- [ ] 6.12 `data-testid`: `answer-card-{id}`, `divergence-note`, `citation-{ref}`, `fallback-notice`

## Step 7 · InboxTab

- [ ] 7.1 `GET /api/inbox?owner={currentUser}`
- [ ] 7.2 요약 / 상황(근거) / 초안 3단 표시
- [ ] 7.3 `already_answered` → "Agent가 이미 답변함"
- [ ] 7.4 같은 `thread_id` 그룹 묶음
- [ ] 7.5 `[승인]` → `POST resolve {action:"approve"}`
- [ ] 7.6 `[수정 후 승인]` → `<textarea>` 전환 → `{action:"approve_with_edit", edited_text}`
- [ ] 7.7 `[내가 아님]` → 대상 드롭다운(자유 입력 없음) → `{action:"not_me", redirect_to}`
- [ ] 7.8 `tier` 배지
- [ ] 7.9 질문자 화면에 "○○이 △△를 지목했습니다 `[다시 묻기]`" (BR-I-03)
- [ ] 7.10 `data-testid`: `inbox-item-{id}`, `inbox-approve-{id}`, `inbox-edit-{id}`, `inbox-notme-{id}`

## Step 8 · AuditTab — 결정적 장면 ② 🔴

- [ ] 8.1 `GET /api/audit` → 행 목록
- [ ] 8.2 각 행: 시각 · 행위자 · 모델 · **`transport`** · **`trusted_zone_llm_base_url`** · tier · 크기 · sha256 · 검증 · 승인자
- [ ] 8.3 `[페이로드 보기]` 토글 → 전문
- [ ] 8.4 `SearchBox` — 200자 제한
- [ ] 8.5 **`ZeroHitBanner`** — 검색어 있고 0건일 때 **크게** 표시 🔴 (BR-U-10)
- [ ] 8.6 `aria-live="assertive"`로 "0건" 알림
- [ ] 8.7 데모 빠른 검색 버튼: `REQ-4412` `EAP-AKA` `H社` `12억`
- [ ] 8.8 `data-testid`: `audit-search`, `audit-zero-hit`, `audit-row-{id}`, `audit-payload-{id}`

## Step 9 · 접근성

- [ ] 9.1 `role="tablist"` + `aria-selected` + 화살표 키 이동
- [ ] 9.2 `<dialog>` 포커스 트랩 + Esc
- [ ] 9.3 모든 배지에 텍스트 라벨 (색상 단독 금지) 🔴
- [ ] 9.4 `:focus-visible` 명시 스타일
- [ ] 9.5 배지 색상 대비 4.5:1 이상
- [ ] 9.6 `aria-live` 영역 2개 (polite: 로딩 / assertive: 검색 결과)

## Step 10 · lint 검사 스크립트 🔴

`scripts/lint-web.sh` — `make lint`에서 실행.

- [ ] 10.1 `! grep -q "internal_path" app.js` (BR-U-05)
- [ ] 10.2 `! grep -q "innerHTML" app.js` (BR-U-12)
- [ ] 10.3 `! grep -qE "onclick=|<script>|<style>" index.html` (CSP)
- [ ] 10.4 `! grep -qE "https?://" index.html app.js style.css` (외부 CDN, SRI N/A 근거)
- [ ] 10.5 `! grep -qE "<details|slice\(0, ?[0-9]+\).*payload" app.js` (BR-U-01)
- [ ] 10.6 `make lint`에 연결

## Step 11 · 3막 통과 확인

- [ ] 11.1 목업 모드(`?mock`)로 3막 전체 화면 재생
- [ ] 11.2 실제 API로 3막 전체 재생
- [ ] 11.3 취소 후 감사 로그에 레코드 없음 확인 (BR-U-03)
- [ ] 11.4 `disclose: false`로 바꿔 필드가 생략되는지 확인 (BR-U-08)
- [ ] 11.5 검색 0건 배너 확인
- [ ] 11.6 미리보기 모달 전문 확인 (생략 없음)
- [ ] 11.7 병기 화면 확인 (정렬 안 됨)
- [ ] 11.8 브라우저 콘솔에 CSP 위반 0건
- [ ] 11.9 파일 크기: `index.html` < 200줄, `app.js` < 700줄, `style.css` < 400줄
- [ ] 11.10 커밋

---

## 스토리 추적

| Story | 단계 | 완료 |
|---|---|:---:|
| S-01 눈으로 확인하고 승인 🔴 | 5 | [ ] |
| S-05 유출 0건 증명 🔴 | 8 | [ ] |
| S-09 직접 지목 | 3 | [ ] |
| S-16 초안 인박스 | 7 | [ ] |
| S-18 사람이 되돌린다 | 7.7, 7.9 | [ ] |
| S-20 갈리는 답 병기 🔴 | 6.6~6.8 | [ ] |
| S-23 인용이 권한 우회 안 함 🔴 | 6.4, 6.5, 10.1 | [ ] |
| S-11 방해받지 않는다 (협력) | 6 | [ ] |
| S-14 지금 무슨 일이 (협력) | 6.10 | [ ] |
| S-19 부재 중 응답 (협력) | 3.3 | [ ] |
| S-21 어휘 밖은 못 나간다 (협력) | 5.10, 6.11 | [ ] |
| S-25 네트워크 없이 3막 (협력) | 1.6, 2.2 | [ ] |

---

## 완료 기준

- [ ] 미리보기 모달에 JSON **전문** + 검증 6단계 개별 + "포함되지 않은 것" 🔴
- [ ] 감사 로그 원문 검색 → **0건 크게 표시** 🔴
- [ ] 인용에 `internal_path` 부재 (grep 검사) 🔴
- [ ] `divergent` 병기 — 정렬 안 됨, 단정 문구 없음 🔴
- [ ] 지목 목록에 `disclose` 반영, `null`은 생략 🔴
- [ ] `MOCK` + `경계 시뮬레이션` 배지 표시 🔴
- [ ] 보안 헤더 위반 0건 (브라우저 콘솔 CSP 확인)
- [ ] 외부 CDN 0건 (grep 검사)
- [ ] `innerHTML` 0건 (grep 검사)
- [ ] 모든 배지에 텍스트 라벨
- [ ] 모든 상호작용 요소에 `data-testid`
- [ ] `?mock`으로 3막 전체 재생
- [ ] 파일 크기 목표 충족

## 보안 준수 요약

| 규칙 | 상태 | 단계 |
|---|---|---|
| SECURITY-01 | N/A (클라이언트에 저장소 없음. `localStorage` 미사용) | — |
| SECURITY-02 | N/A | — |
| SECURITY-03 | N/A (클라이언트 로깅 없음. `console.log` 최소화) | — |
| SECURITY-04 | **준수** — U3 미들웨어가 헤더 설정, U4가 CSP를 우회하지 않는다. **HSTS는 localhost HTTP이므로 N/A** | 1.1, 1.2, 10.3 |
| SECURITY-05 | 준수 — 클라이언트 폼 검증 (서버가 진짜 검증) | 4.1, 8.4 |
| SECURITY-06 | N/A | — |
| SECURITY-07 | N/A | — |
| SECURITY-08 | **부분 N/A** — 사용자 인증 범위 밖. `internal_path` 미표시로 대체. **한계를 화면에 표시** | 2.6, 6.5 |
| SECURITY-09 | 준수 — 오류 메시지에 내부 정보 없음 (`correlation_id`만) | 1.5 |
| SECURITY-10 | **준수** — npm 의존성 0개. 외부 CDN 0개 | 10.4 |
| SECURITY-11 | 준수 — 보안 로직 없음 (전부 서버) | — |
| SECURITY-12 | N/A (인증 없음) | — |
| SECURITY-13 | **준수** — 외부 CDN 미사용 → **SRI N/A**. `innerHTML` 금지로 XSS 방어 | 10.2, 10.4 |
| SECURITY-14 | N/A | — |
| SECURITY-15 | 준수 — `api()` 오류 처리, 픽스처 누락 시 명시적 실패 | 1.5, 1.6 |

| PBT 규칙 | 상태 |
|---|---|
| PBT-01~10 | **전부 N/A** — 순수 함수는 표시 매핑 테이블뿐이고 예제 테스트가 전수 커버한다. 나머지는 DOM 조작과 fetch로 PBT의 이득이 없다. 대신 `u4/domain-entities.md` §6의 예제 검증 10개 + Step 10 grep 검사로 대체 |

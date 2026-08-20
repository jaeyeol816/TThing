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

- [x] 1.1 `index.html` — `<nav>`/`<main>`/`<section>` 시맨틱 구조. **인라인 script/style 없음** 🔴
- [x] 1.2 `style.css` — 시스템 폰트(`system-ui`), 유니코드 아이콘. **외부 CDN 0건** 🔴
- [x] 1.3 `app.js` — 목차 주석 13개 섹션
- [x] 1.4 `state` 전역 객체 (`u4/domain-entities.md` §2)
- [x] 1.5 `api()` 클라이언트 + 오류 매핑 (410/422/429/500/네트워크)
- [ ] 1.6 `?mock` 파라미터 → `data/fixtures/api/` 재생 — **불필요해져 생략**
      백엔드가 완전한 목업 모드를 갖는다 (`EXAONE_MODE=mock AGENT_TRANSPORT=mock`).
      이 항목은 화면이 백엔드보다 먼저 만들어질 경우를 대비한 것이었는데,
      실제로는 Day 3 에 백엔드가 먼저 끝났다. 화면에 두 번째 목업 경로를 두면
      **"화면에서 본 것"과 "서버가 한 것"이 갈릴 수 있다** — 그게 더 위험하다
- [x] 1.7 `render()` 루트 + 탭 분기
- [x] 1.8 표시 매핑 테이블 6개 (tier / freshness / disposition / activity / stage / mode)

## Step 2 · HeaderBar — 정직성 표시 🔴

- [x] 2.1 `GET /api/health` 로드
- [x] 2.2 `exaone_mode == "mock"` → **주황 `MOCK · 목업 모드` 배지** (BR-U-09)
- [x] 2.3 `agent_transport` 배지 (`broker`/`direct`/`mock`)
- [x] 2.4 `trusted_zone_llm_base_url` 호스트 표시
- [x] 2.5 **`trust_boundary_simulated` → `경계 시뮬레이션` 배지 + 설명 팝오버** 🔴
- [x] 2.6 사용자 전환 드롭다운 + **"(데모용 전환 · 인증 없음)"** 표시 (BR-U-15)
- [x] 2.7 `data-testid`: `health-mode-badge`, `trust-boundary-notice`

**2.2와 2.5를 숨기면 심사자를 속이는 것이다.** 데모의 신뢰성이 프로젝트의 유일한 자산이다.

## Step 3 · AgentPicker

- [x] 3.1 `GET /api/agents` → 카드 3개
- [x] 3.2 `expertise` 항상 표시
- [x] 3.3 `activity_status` / `away_minutes` / `question_count_today` / `current_focus_summary`
- [x] 3.4 **`null`인 필드는 아예 렌더하지 않는다** ("비공개" 표시 금지) 🔴 (BR-U-08)
- [x] 3.5 최대 2명 — 3번째 클릭 차단 + 툴팁
- [x] 3.6 `daily_limit` 초과 → 회색 + 클릭 불가
- [x] 3.7 `data-testid`: `agent-card-{id}`, `agent-select-{id}`

## Step 4 · QuestionInput

- [x] 4.1 `<textarea>` + 4000자 카운터
- [x] 4.2 초과 시 전송 버튼 비활성
- [x] 4.3 대상 0명이면 비활성
- [x] 4.4 `state.busy` 중 비활성 (BR-U-14)
- [x] 4.5 데모 질문 프리셋 드롭다운 (`data/questions.json`)
- [x] 4.6 `aria-live="polite"` 상태 알림

## Step 5 · PreviewModal — 결정적 장면 ① 🔴

- [x] 5.1 `<dialog>` + `aria-labelledby` + 포커스 트랩 + Esc 닫기
- [x] 5.2 헤더: 등급 배지 + **`verbatim_sentence_count` 측정값** + 크기 + `validation_summary`
- [x] 5.3 **`PayloadViewer` — 전문. `...` 생략·`<details>` 접기·"더 보기" 전부 금지** 🔴
- [x] 5.4 JSON 하이라이팅 — `document.createElement` + 클래스. **문자열 조립 금지** (CSP+XSS)
- [x] 5.5 `ValidationBadges` — **6단계 개별 표시** (`6/6` 요약만 보여주지 않는다)
- [x] 5.6 `ExclusionList` — `excluded_categories` 명시 (BR-U-02)
- [x] 5.7 `[전송]` → `approvedEnvelopeIds` 누적 → 다음 모달 또는 `send()`
- [x] 5.8 `[취소]` → `modalQueue.shift()`. `send` 호출 안 함 (BR-U-03)
- [x] 5.9 **2명이면 모달 순차 2개** (BR-U-04)
- [x] 5.10 `disposition == "blocked"` → 모달 대신 폴백 답변 즉시 표시
- [x] 5.11 `data-testid`: `preview-modal`, `preview-payload`, `preview-send`, `preview-cancel`, `preview-check-{stage}`

## Step 6 · AnswerPanel — 결정적 장면 ③ 🔴

- [x] 6.1 `AnswerCard` — `agent_label` 항상 표시 (BR-U-06)
- [x] 6.2 배지: tier / 사내처리 / 미검증 / 신뢰도 — **색상 + 텍스트 라벨 병기** 🔴 (BR-U-13)
- [x] 6.3 답변 텍스트를 `textContent`로 (BR-U-12)
- [x] 6.4 `CitationList` — `display_title` + `section` + `tier` + `as_of` + `formality`
- [x] 6.5 **`internal_path`를 참조하지 않는다** (응답에 없다) 🔴 (BR-U-05)
- [x] 6.6 `divergent` 시 **요청 순서 그대로 병기. 신뢰도 정렬 금지** 🔴 (BR-U-07)
- [x] 6.7 `divergence_note`를 서버 문구 그대로 표시
- [x] 6.8 헤더 문구 "두 답변이 서로 다릅니다. 판단에 참고하세요." (단정 금지)
- [x] 6.9 `[두 분께 확인 요청]` 버튼
- [x] 6.10 `freshness` 표시 (`🔴 실시간` / `⚪ N시간 전 기준`)
- [x] 6.11 `used_external_agent == false` → **`[사내망 밖으로 나간 것 없음]`** 배지
- [x] 6.12 `data-testid`: `answer-card-{id}`, `divergence-note`, `citation-{ref}`, `fallback-notice`

## Step 7 · InboxTab

- [x] 7.1 `GET /api/inbox?owner={currentUser}`
- [x] 7.2 요약 / 상황(근거) / 초안 3단 표시
- [x] 7.3 `already_answered` → "Agent가 이미 답변함"
- [ ] 7.4 같은 `thread_id` 그룹 묶음 — **미구현**
      데모 시나리오에서 한 사람의 인박스에 같은 스레드가 2건 이상 쌓이지 않는다.
      묶기 UI 를 만들어도 시연에서 보이지 않는다. `thread_id` 는 서버가 이미
      발급하므로 나중에 화면만 붙이면 된다
- [x] 7.5 `[승인]` → `POST resolve {action:"approve"}`
- [x] 7.6 `[수정 후 승인]` → `<textarea>` 전환 → `{action:"approve_with_edit", edited_text}`
- [x] 7.7 `[내가 아님]` → 대상 드롭다운(자유 입력 없음) → `{action:"not_me", redirect_to}`
- [x] 7.8 `tier` 배지
- [x] 7.9 `not_me` 로 넘겨진 것을 질문자에게 알린다 + `[다시 묻기]` (BR-I-03)
      **자동 재질의는 넣지 않았다.** 넣으면 넘기기만 반복되는 고리가 생긴다 —
      화면이 그 사실을 문구로 밝힌다
- [x] 7.10 `data-testid`: `inbox-item-{id}`, `inbox-approve-{id}`, `inbox-edit-{id}`, `inbox-notme-{id}`

## Step 8 · AuditTab — 결정적 장면 ② 🔴

- [x] 8.1 `GET /api/audit` → 행 목록
- [x] 8.2 각 행: 시각 · 행위자 · 모델 · **`transport`** · **`trusted_zone_llm_base_url`** · tier · 크기 · sha256 · 검증 · 승인자
- [x] 8.3 `[페이로드 보기]` 토글 → 전문
- [x] 8.4 `SearchBox` — 200자 제한
- [x] 8.5 **`ZeroHitBanner`** — 검색어 있고 0건일 때 **크게** 표시 🔴 (BR-U-10)
- [x] 8.6 `aria-live="assertive"`로 "0건" 알림
- [x] 8.7 데모 빠른 검색 버튼: `REQ-4412` `EAP-AKA` `H社` `12억`
- [x] 8.8 `data-testid`: `audit-search`, `audit-zero-hit`, `audit-row-{id}`, `audit-payload-{id}`

## Step 9 · 접근성

- [x] 9.1 `role="tablist"` + `aria-selected` + 화살표 키 이동
- [x] 9.2 `<dialog>` 포커스 트랩 + Esc
- [x] 9.3 모든 배지에 텍스트 라벨 (색상 단독 금지) 🔴
- [x] 9.4 `:focus-visible` 명시 스타일
- [x] 9.5 배지 색상 대비 4.5:1 이상
- [x] 9.6 `aria-live` 영역 2개 (polite: 로딩 / assertive: 검색 결과)

## Step 10 · lint 검사 스크립트 🔴

> ⚠️ **bash grep 검사기(10.1~10.5)는 기각했다.** 대신 `scripts/lint_web.py` 를 만들었다.
>
> 계획대로 `grep -q "internal_path" app.js` 를 쓰자마자 **오탐 3건**이 났다 —
> 규칙을 설명하는 *주석*("internal_path 를 참조하지 않는다")까지 위반으로 잡았고,
> `<details>` 검사는 페이로드와 무관한 붙여넣기 폼을 잡았다.
> Day 2·Day 3 에서 같은 문제를 두 번 겪었다.
>
> 문자열 검사가 주석까지 잡으면 사람이 **주석을 지우거나 검사를 약화시킨다.**
> 둘 다 나쁘다. 그래서 검사기를 Python 으로 다시 썼다.
>
> | 계획 (bash) | 실제 (`lint_web.py`) |
> |---|---|
> | `grep -q` | 주석을 먼저 제거한 뒤 검사 (문자열 리터럴 안의 `//` 는 보존) |
> | 예외 불가 | 줄 단위 허용 마커 `lint-web: allow <규칙>` · **상한 2건** |
> | 파일 전체 | 영역 한정 (`<details>` 는 미리보기 모달 안에서만 금지) |
> | 검사기 미검증 | `tests/unit/test_lint_web.py` 42개 — 심은 위반 12종을 잡는지 확인 |
>
> 검사 항목은 계획보다 늘었다: `outerHTML` · `insertAdjacentHTML` · `document.write` ·
> `eval` · `new Function` · 인라인 `style` 속성 · CSS 의 외부 URL ·
> `.payload` 영역의 `max-height`/`overflow:hidden` · 접근성 3항목.
>
> `payload_sha256.slice(0, 12)` 를 절단으로 오인하지 않게 `payload(?!_sha256)` 로 좁혔다 —
> 해시 앞 12자만 보여주는 것은 정당하다.

**대체 산출물**: `scripts/lint_web.py` (`make lint` 에서 실행). 아래 5항목은 그것으로 대체됐다.

- [ ] 10.1 `! grep -q "internal_path" app.js` (BR-U-05)
- [ ] 10.2 `! grep -q "innerHTML" app.js` (BR-U-12)
- [ ] 10.3 `! grep -qE "onclick=|<script>|<style>" index.html` (CSP)
- [ ] 10.4 `! grep -qE "https?://" index.html app.js style.css` (외부 CDN, SRI N/A 근거)
- [ ] 10.5 `! grep -qE "<details|slice\(0, ?[0-9]+\).*payload" app.js` (BR-U-01)
- [x] 10.6 `make lint`에 연결

## Step 11 · 3막 통과 확인

- [x] 11.1 목업 모드로 전 시나리오 화면 재생 — **서버 목업**으로 (1.6 참조)
- [x] 11.2 실제 API로 3막 전체 재생
- [x] 11.3 취소 후 감사 로그에 레코드 없음 확인 (BR-U-03)
- [x] 11.4 `null` 필드가 생략되는지 확인 (BR-U-08)
      `disclose` 는 서버가 해석해 `null` 로 내려 준다. 화면은 `null` 을 렌더하지
      않는 것으로 대응한다 — "비공개" 라고 쓰면 그 표시 자체가 정보다
- [x] 11.5 검색 0건 배너 확인
- [x] 11.6 미리보기 모달 전문 확인 (생략 없음)
- [x] 11.7 병기 화면 확인 (정렬 안 됨)
- [x] 11.8 브라우저 콘솔에 CSP 위반 0건
- [ ] 11.9 파일 크기 — **초과. 의도적으로 받아들였다**
      | 파일 | 목표 | 실제 |
      |---|---:|---:|
      | `index.html` | < 200 | **193** ✓ |
      | `app.js` | < 700 | 1,069 |
      | `style.css` | < 400 | 452 |

      목표는 탭 3개 기준이었다. Day 4 에 **문서 업로드 탭**이 추가됐다 —
      드래그&드롭 · 붙여넣기 · 등급 근거 표시 · 목록 · 삭제. 여기에 §8 하나가
      약 120줄이고 CSS 도 그만큼 늘었다.
      줄 수를 맞추려면 주석을 지우거나 기능을 빼야 한다. 둘 다 이 프로젝트의
      우선순위가 아니다 (주석이 왜 그렇게 했는지를 담고 있다)
- [x] 11.10 커밋

---

## Step 12 · 문서 업로드 (Day 4 신설 — 원래 계획에 없었다)

계획을 세울 때는 저장소에 심어둔 샘플 코퍼스로 시연할 생각이었다.
그런데 **사용자가 자기 문서를 올리는 지점이 이 도구의 실사용 형태**다.
새 입구가 생겼으므로 화면도 그것을 다뤄야 한다.

### 백엔드 (U2·U3 증분)

- [x] 12.1 `api_models` — `UploadRequest` · `UploadResult` · `DocumentView` · `TierEvidence` · `DocumentList`
- [x] 12.2 `store.save_upload()` — 3중 경로 검사 (성분 제거 · `safe_resolve` · `in_scope`)
- [x] 12.3 `store.save_upload()` — 같은 이름이면 **덮어쓰지 않고** `-1` 접미사
- [x] 12.4 `store.list_uploads()` / `delete_upload()` — `uploads/` 아래만 삭제 가능
- [x] 12.5 `store.attach_path()` / `detach_path()` — **`updated_at` 미변경** 🔴 (BR-S-04)
- [x] 12.6 `documents.py` (L6) — 업로드 + 즉시 등급 판정 + 근거
- [x] 12.7 라우트 5개 — `POST/GET/DELETE /api/documents` · `GET /api/users` · `GET /api/questions`
- [x] 12.8 `RequestValidationError` 핸들러 — **오류 응답이 요청 본문을 되비추지 않는다** 🔴

### 화면

- [x] 12.9 드래그&드롭 영역 + `<input type="file">` + 키보드 접근 (`role="button"` + `tabindex`)
- [x] 12.10 `FileReader` 로 텍스트 읽기 — 확장자·크기를 클라이언트에서 먼저 걸러 빨리 알린다
- [x] 12.11 붙여넣기 폼 (`<details>`) — 파일 없이도 데모 가능
- [x] 12.12 **등급 + 근거 표시** 🔴 — "왜 기밀인가"에 즉시 답한다
- [x] 12.13 문서 목록 — `질의 후보` / `원래 있던 것`(seeded) 구분
- [x] 12.14 삭제 버튼 — 업로드한 것만. 시드 코퍼스는 지울 수 없다
- [x] 12.15 `internal_path` 표시 (소유자 화면만) + `lint-web: allow BR-U-05` 마커
- [x] 12.16 `data-testid`: `dropzone`, `file-input`, `paste-name`, `paste-body`, `paste-submit`, `upload-result`, `doc-list`

### 테스트

- [x] 12.17 `tests/unit/test_documents.py` (40개) — 경로 탈출 7종 · 확장자 · scope · 중복 · 삭제 제한 · 등급 근거 · 신선도 보존
- [x] 12.18 `tests/unit/test_api.py` 증분 (25개) — HTTP 계약 · 422 가 본문을 되비추지 않음
- [x] 12.19 `tests/eval/test_scenarios.py` 시나리오 4 (5개) — **업로드 → 타인 질문** 종단
- [x] 12.20 `scripts/e2e_upload_ask.py` — 라이브 실측 (live 모드 필요, `/api/health` 로 확인)

## Step 13 · 데스크톱 셸 (Day 4 신설)

- [x] 13.1 `app/package.json` + `@tauri-apps/cli` 고정 버전
- [x] 13.2 `app/src-tauri/` — `Cargo.toml` · `build.rs` · `tauri.conf.json` · `capabilities/`
- [x] 13.3 아이콘 생성 — 순수 Python(`zlib`+`struct`)으로 PNG, `iconutil` 로 `.icns`
- [x] 13.4 **백엔드 URL 을 직접 연다** 🔴 — 정적 번들 기각 (origin 이 달라지면 CSP 가 적용되지 않는다)
- [x] 13.5 백엔드 자동 기동 + TCP 준비 확인 + 120초 타임아웃
- [x] 13.6 "이미 실행 중" 이면 그 서버에 붙는다
- [x] 13.7 백엔드 로그를 `[backend:out]` 으로 흘려보낸다 — 조용한 실패 금지
- [x] 13.8 종료 시 자식 프로세스 kill
- [x] 13.9 `Makefile` — `app` · `app-setup` · `app-build`
- [x] 13.10 실측 — `cargo build --release` 성공 · `npm run tauri dev` 창 열림 · API 왕복 확인

## Step 14 · 게이트 G4 (Day 5)

- [x] 14.1 `scripts/dump_payloads.py` — 페이로드 1건 = 섹션 1개, 전문 절단 없음
- [x] 14.2 `--generate` — 시연 시나리오 + **업로드**를 돌려 감사 DB 를 채운다
- [x] 14.3 `--fresh` — 제출용 한 판 (기본값은 "DB 에 있는 것 전부")
- [x] 14.4 사람이 찾아야 하는 것 7항목 체크리스트
- [x] 14.5 `KNOWN_RESIDUALS` — 남기기로 결정한 것 3건 + 근거
- [x] 14.6 `make eval-dump-payloads` 연결 + 오프라인 재생 확인
- [x] 14.7 **육안 확인 수행** — 결함 2건 발견·수정 (entity_id 유출 · 거짓 보증)
- [ ] 14.8 체크박스 서명 (10건) — **확인자 몫**

---

## 스토리 추적

| Story | 단계 | 완료 |
|---|---|:---:|
| S-01 눈으로 확인하고 승인 🔴 | 5 | [x] |
| S-05 유출 0건 증명 🔴 | 8 | [x] |
| S-09 직접 지목 | 3 | [x] |
| S-16 초안 인박스 | 7 | [x] |
| S-18 사람이 되돌린다 | 7.7, 7.9 | [x] |
| S-20 갈리는 답 병기 🔴 | 6.6~6.8 | [x] |
| S-23 인용이 권한 우회 안 함 🔴 | 6.4, 6.5, `lint_web.py` | [x] |
| S-11 방해받지 않는다 (협력) | 6 | [x] |
| S-14 지금 무슨 일이 (협력) | 6.10 | [x] |
| S-19 부재 중 응답 (협력) | 3.3 | [x] |
| S-21 어휘 밖은 못 나간다 (협력) | 5.10, 6.11 | [x] |
| S-25 네트워크 없이 3막 (협력) | 2.2 + 서버 목업 | [x] |

---

## 완료 기준

- [x] 미리보기 모달에 JSON **전문** + 검증 6단계 개별 + "포함되지 않은 것" 🔴
      ⚠️ "포함되지 않은 것" 은 **표현별로 다르다** — 가명화는 원문 문장을 유지하는
      것이 정의이므로 그것을 약속하지 않는다 (G4 육안 확인이 찾은 결함 28)
- [x] 감사 로그 원문 검색 → **0건 크게 표시** 🔴
- [x] 인용에 `internal_path` 부재 (`lint_web.py` 검사) 🔴
      소유자 문서 관리 화면만 허용 마커로 예외 — 자기 문서 경로를 보는 것은
      권한 우회가 아니다. FR-43 이 막는 것은 *다른 사람* 지식 인용 시 경로 유출
- [x] `divergent` 병기 — 정렬 안 됨, 단정 문구 없음 🔴
- [x] 지목 목록에 `disclose` 반영, `null`은 생략 🔴
- [x] `MOCK` + `경계 시뮬레이션` 배지 표시 🔴
- [x] 보안 헤더 위반 0건 — 브라우저 실측 + Tauri 창 실측 (같은 origin 이므로 같은 CSP)
- [x] 외부 CDN 0건 (`lint_web.py` — HTML·JS·CSS 3개 파일 전부)
- [x] `innerHTML` 0건 — `outerHTML` · `insertAdjacentHTML` · `document.write` · `eval` · `new Function` 도 함께
- [x] 모든 배지에 텍스트 라벨 (색상 단독 금지)
- [x] 모든 상호작용 요소에 `data-testid` (HTML 27 · JS 19)
- [x] 목업 모드로 전 시나리오 재생 — **서버 목업**으로 (1.6 참조)
- [ ] 파일 크기 목표 충족 — `index.html` 만 충족. 11.9 참조 (탭 4개가 됐다)

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

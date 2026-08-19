# U4 — Business Logic Model

---

## 1. 이 유닛이 하는 일

> 결정적 장면 3개를 화면으로 만든다.

이 유닛의 성공 기준은 "예쁘다"가 아니라 **"심사자가 3초 안에 확인할 수 있다"** 다.

| # | 결정적 장면 | 컴포넌트 | 성립 조건 |
|---|---|---|---|
| ① | 나갈 JSON 전문을 읽고 승인 | `PreviewModal` | 생략 없이 스크롤 없이 읽힌다 |
| ② | 원문 문구 검색 → 0건 | `AuditTab` | 0건이 크게 표시된다 |
| ③ | 갈리는 답을 병기 | `AnswerPanel` | 하나가 강조되지 않는다 |

나머지 UI는 이 셋을 뒷받침하는 배경이다. 시간이 부족하면 배경을 깎고 이 셋을 지킨다.

---

## 2. 렌더 모델

프레임워크가 없으므로 명시적 렌더다.

```javascript
function render() {
  renderHeader();
  renderTabNav();
  switch (state.tab) {
    case 'ask':   renderAskTab();   break;
    case 'inbox': renderInboxTab(); break;
    case 'audit': renderAuditTab(); break;
  }
  renderModal();     // modalQueue 가 비어 있지 않으면
  renderToast();
}
```

**규칙**: 상태를 바꾼 함수가 `render()`를 호출한다. DOM을 직접 수정하지 않는다.
탭 단위로 나눠 전체 재렌더 비용을 줄인다 (데이터가 수십 건 규모라 성능은 문제되지 않는다).

---

## 3. 주 흐름 — 질문하기

```
[앱 로드]
  GET /api/health   -> state.health -> renderHeader()
                       모드 배지 · 경계 시뮬레이션 고지 (BR-U-09)
  GET /api/agents   -> state.agents -> AgentPicker

[에이전트 선택]
  클릭 -> toggle(selectedTargets)
  3번째 클릭 -> 무시 + 툴팁 "최대 2명"          (BR-O-02)
  daily_limit 초과 -> 회색 + 클릭 불가

[질문 입력]
  4000자 카운터. 초과 시 전송 버튼 비활성        (§10 폼 검증)

[물어보기 클릭]
  state.busy = true                              (BR-U-14)
  POST /api/ask/prepare { question, asker: currentUser, targets }
    -> state.prepareResult
    -> state.modalQueue = calls.filter(c => c.disposition === 'ready')
    -> blocked 인 call 은 즉시 AnswerPanel 에 폴백 표시  (한 왕복에 끝)
  renderModal()

[모달: JSON 전문 읽고 전송]
  PayloadViewer      전문. 생략 금지                (BR-U-01)
  ValidationBadges   6단계 개별 표시
  ExclusionList      "포함되지 않은 것"             (BR-U-02)

  [전송] -> approvedEnvelopeIds.push(envelope_id)
            modalQueue.shift()
            큐가 남았으면 다음 모달                (BR-U-04)
            비었으면 send()
  [취소] -> modalQueue.shift()                     (BR-U-03)
            큐가 비고 승인된 것이 없으면 종료

[send]
  POST /api/ask/send { request_id, envelope_ids: approvedEnvelopeIds, approved_by }
    -> state.askResult -> AnswerPanel
    -> escalations 있으면 인박스 탭에 배지 카운트
  state.busy = false
```

**`blocked`를 `prepare` 응답에서 바로 처리하는 것이 핵심이다.**
검증 실패는 "차단됐다"만이 아니라 "차단됐고 대신 이 답이 있다"여야 한다. 서버가 `fallback`을 함께 주므로 한 왕복에 끝난다 (시나리오 3 후속 질문).

---

## 4. 주 흐름 — 인박스

```
[사용자 전환]
  드롭다운 -> state.currentUser 변경 -> render()

[인박스 탭]
  GET /api/inbox?owner={currentUser} -> state.inbox
  같은 thread_id 는 그룹으로 묶어 표시               (BR-I-04)
  already_answered 를 "Agent가 이미 답변함"으로 표시

[승인]
  POST /api/inbox/{id}/resolve { action: 'approve' }
  -> 목록 갱신 + 토스트 "승인했습니다"

[수정 후 승인]
  초안을 <textarea> 로 전환 (state.editingItemId)
  POST resolve { action: 'approve_with_edit', edited_text }

[내가 아님]
  대상 선택 드롭다운 (agents 목록에서, 자유 입력 없음)
  POST resolve { action: 'not_me', redirect_to }
  -> 질문자 화면에 "○○이 △△를 지목했습니다 [다시 묻기]"   (BR-I-03)
```

**`[다시 묻기]`는 시스템이 자동으로 묻지 않는다.** 질문자가 눌러야 새 질의가 시작된다.

---

## 5. 주 흐름 — 감사 로그

```
[감사 로그 탭]
  GET /api/audit -> state.auditRows
  각 행: 시각 · 행위자 · 모델 · transport · trusted_zone_llm · tier ·
         크기 · sha256 · 검증 · 승인자
  [페이로드 보기] -> expandedPayloads 토글 -> 전문 표시

[원문 검색]
  빠른 검색 버튼: REQ-4412 / EAP-AKA / H社 / 12억
  또는 직접 입력
  GET /api/audit?q={query} -> state.auditRows

  결과 0건이면:
    ZeroHitBanner 크게 표시                        (BR-U-10)
    aria-live="assertive" 로 알림
```

**`local_queries`(신뢰 구역 내 처리)는 이 탭에 나타나지 않는다** (BR-U-11).
"레코드가 없다"가 증거가 되려면 섞이면 안 된다.

---

## 6. 결정적 장면 ① — PreviewModal 렌더 로직

```javascript
function renderPreviewModal(call) {
  const p = call.preview;

  // 1. 헤더: 등급 + 원문 문장 수(측정값) + 크기 + 검증 요약
  header(`[${tierLabel(p.tier)}]  원문 문장 ${p.verbatim_sentence_count}개 · ` +
         `${fmtBytes(p.size_bytes)} · 검증 ${p.validation_summary} 통과`);

  // 2. 페이로드 전문 — textContent 로만. innerHTML 금지 (BR-U-12)
  //    JSON 하이라이팅은 DOM 노드를 만들어 붙인다 (문자열 조립 금지)
  payloadViewer(p.payload_pretty);          // 생략·접기 없음 (BR-U-01)

  // 3. 검증 6단계 개별 배지
  for (const c of p.checks) badge(stageLabel(c.stage), c.passed);

  // 4. 포함되지 않은 것 (BR-U-02)
  exclusionList(p.excluded_categories);

  // 5. 버튼
  button('전송', () => approve(call));
  button('취소', () => skip(call));
}
```

**`verbatim_sentence_count`를 그대로 표시하는 것이 중요하다.**
하드코딩된 "원문 0개"가 아니라 서버가 계산한 값이다. 만약 0이 아니면 화면에 그렇게 뜬다 — 그게 정직하고, 그런 일이 생기면 즉시 발견된다.

**JSON 하이라이팅**: `JSON.parse` → 재귀적으로 `document.createElement('span')` + 클래스 부여. 문자열로 HTML을 조립하면 XSS가 되고 CSP도 걸린다.

---

## 7. 결정적 장면 ③ — 병기 렌더 로직

```javascript
function renderAnswerPanel(merged) {
  if (merged.divergent) {
    heading('두 답변이 서로 다릅니다. 판단에 참고하세요.');   // BR-U-07
  }

  // 요청 순서 그대로. 신뢰도로 정렬하지 않는다 (BR-O-07, BR-U-07)
  for (const a of merged.answers) {
    answerCard({
      label: a.agent_label,                    // "김철수 책임의 Agent"
      badges: [
        tierBadge(a.tier),
        a.used_external_agent ? null : fallbackBadge(),
        dispositionBadge(merged.disposition),
        confidenceBadge(a.confidence),
      ],
      text: a.text,                            // textContent
      citations: a.citations.map(renderCitation),
      freshness: a.freshness ? freshnessNote(a) : null,
    });
  }

  if (merged.divergence_note) {
    note(merged.divergence_note);              // 서버 고정 템플릿 그대로
  }
}
```

**정렬하지 않는 것이 규칙이다.** 신뢰도 순으로 정렬하면 사용자가 위쪽 답을 정답으로 읽는다. 요청 순서 그대로 두어 판단을 사람에게 남긴다.

**`divergence_note`를 UI가 만들지 않는다.** 서버가 준 문구를 그대로 표시한다. UI가 문구를 만들면 "엇갈립니다" 같은 단정이 슬며시 들어온다.

---

## 8. 인용 렌더 (FR-43)

```javascript
function renderCitation(c) {
  // c 에는 internal_path 가 없다. 서버가 주지 않는다.
  return `📄 ${c.display_title}` +
         (c.section ? ` ${c.section}` : '') +
         `  [${tierLabel(c.tier)}]` +
         (c.as_of ? `  ${c.as_of}` : '') +
         (c.formality === 'informal' ? '  비공식' : '');
}
```

문자열을 반환하지만 `textContent`로 삽입한다 (BR-U-12).

`formality === 'informal'`을 표시하는 것이 시나리오 3의 병기를 뒷받침한다 — 개인 메모와 설계 리뷰의 무게가 다르다는 것을 사용자가 판단할 재료.

---

## 9. 오류 처리

| 상황 | UI |
|---|---|
| `410 Gone` (envelope 만료) | 토스트 "미리보기가 만료되었습니다. 다시 질문해 주세요." **질문 텍스트는 유지** |
| `422` | 필드별 오류 메시지 |
| `429` (daily_limit) | "이 에이전트의 일일 상한에 도달했습니다" |
| `500` | "일시적 오류입니다 (참조: {correlation_id})" |
| 네트워크 실패 | "서버에 연결할 수 없습니다. `make run`이 실행 중인지 확인하세요." |
| 픽스처 누락 (목업) | **명시적 오류.** 조용히 빈 화면을 보여주지 않는다 |

**질문 텍스트 유지가 중요하다.** 4000자를 타이핑했는데 만료로 날아가면 데모가 무너진다.

---

## 10. 데모 보조 기능

시연 흐름이 타이핑 실수로 끊기는 것을 막는 장치들.

| 기능 | 구현 |
|---|---|
| 데모 질문 프리셋 | `data/questions.json`을 읽어 드롭다운으로 제공 |
| 감사 로그 빠른 검색 | `REQ-4412` `EAP-AKA` `H社` `12억` 버튼 |
| 사용자 빠른 전환 | 드롭다운 (최민수 / 정연구원 / 한지원 / 김책임 / 박선임) |
| `?mock` 파라미터 | 브라우저에서 즉시 목업 전환 (재시작 불필요) |
| `?tab=audit` | 탭 딥링크. 시연 중 즉시 이동 |

**데모 보조가 제품 기능처럼 보이지 않게** 별도 영역(`데모 도구` 접이식)에 둔다.

---

## 11. 파일 구조

```
src/mesh/web/
  index.html    구조만. < 200줄
  app.js        < 700줄. 상단에 목차 주석
  style.css     < 400줄
```

`app.js` 목차
```
// 1. 상태
// 2. API 클라이언트
// 3. 표시 매핑 (tier/freshness/disposition/mode)
// 4. 렌더 루트
// 5. HeaderBar
// 6. AskTab — AgentPicker / QuestionInput
// 7. PreviewModal        <- 결정적 장면 ①
// 8. AnswerPanel         <- 결정적 장면 ③
// 9. InboxTab
// 10. AuditTab           <- 결정적 장면 ②
// 11. Toast / 접근성 헬퍼
// 12. 데모 보조
// 13. 부트스트랩
```

---

## 12. 검증 (`make lint`에 포함)

```bash
# BR-U-05: 인용에 경로 없음
! grep -q "internal_path" src/mesh/web/app.js
# BR-U-12: XSS·CSP
! grep -q "innerHTML" src/mesh/web/app.js
! grep -qE "onclick=|<script>|<style>" src/mesh/web/index.html
# BR-U-12: 외부 CDN 없음 (SRI N/A 근거)
! grep -qE "https?://" src/mesh/web/index.html src/mesh/web/app.js src/mesh/web/style.css
# BR-U-01: 페이로드 생략 없음
! grep -qE "<details|slice\(0, ?[0-9]+\).*payload" src/mesh/web/app.js
```

리뷰 매너가 아니라 CI가 잡는다.

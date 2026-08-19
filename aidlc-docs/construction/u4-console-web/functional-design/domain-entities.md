# U4 — Domain Entities

프런트엔드는 자체 도메인 모델을 갖지 않는다. **U3의 API 응답 타입을 그대로 소비한다.**
정의는 U1 `schemas.py`와 U3 `domain-entities.md` 참조.

---

## 1. 소비하는 서버 타입

| 타입 | 출처 | 사용 컴포넌트 |
|---|---|---|
| `AgentCard` | U2 | `AgentPicker` |
| `PrepareResult` / `PreparedCall` | U3 | `PreviewModal` |
| `PreviewCard` | U1 | `PayloadViewer`, `ValidationBadges`, `ExclusionList` |
| `CheckResult` | U1 | `ValidationBadges` |
| `AskResult` / `MergedAnswer` | U3 | `AnswerPanel` |
| `RehydratedAnswer` | U1 | `AnswerCard` |
| `Citation` | U1 | `CitationList` |
| `InboxItem` / `EscalationDraft` | U3 | `InboxItemView` |
| `AuditRecord` | U1 | `AuditRow` |
| `HealthStatus` | U3 | `HeaderBar` |

---

## 2. 클라이언트 전용 상태

```javascript
const state = {
  tab: 'ask',                    // 'ask' | 'inbox' | 'audit'
  currentUser: 'person:choi',    // 드롭다운 전환 (인증 아님)
  busy: false,                   // 중복 전송 방지

  agents: [],                    // AgentCard[]
  selectedTargets: [],           // string[] 최대 2
  question: '',

  prepareResult: null,           // PrepareResult
  modalQueue: [],                // PreparedCall[] — 순차 승인 대기열
  approvedEnvelopeIds: [],       // 승인된 것만 send 에 넘긴다
  askResult: null,               // AskResult

  inbox: [],                     // InboxItem[]
  editingItemId: null,           // 수정 후 승인 중인 항목
  editDraft: '',

  auditRows: [],                 // AuditRecord[]
  auditQuery: '',
  expandedPayloads: new Set(),   // record_id

  health: null,                  // HealthStatus
  toast: null,                   // { level, message }
};
```

**`modalQueue`가 BR-U-04(순차 승인)의 구현이다.** 2명 지목 시 `PreparedCall` 2개가 큐에 들어가고 하나씩 모달로 뜬다. 승인된 것만 `approvedEnvelopeIds`에 쌓인다.

**`state`에 없는 것**: 페이로드 원문 사본, 매핑 테이블, `internal_path`. 서버가 주지 않으므로 클라이언트에 존재할 수 없다.

---

## 3. 표시 매핑 테이블

### 등급 → 배지

| `tier` | 라벨 | CSS 클래스 | 색 |
|---|---|---|---|
| `secret` | `기밀` | `badge-tier-secret` | 빨강 |
| `internal` | `사내` | `badge-tier-internal` | 주황 |
| `open` | `공개` | `badge-tier-open` | 초록 |

### 신선도 → 표시

| `freshness` | 표시 | 조건 |
|---|---|---|
| `live` | `🔴 실시간` | |
| `stale` | `⚪ {N}시간 전 기준` | `session_as_of` 병기 |
| `expired` | `⚪ 세션 오래됨` | |
| `null` | (표시 없음) | `disclose.current_focus == false` |

### 처분 → 배지

| `disposition` | 배지 | 추가 표시 |
|---|---|---|
| `auto` | (없음) | |
| `unverified` | `[미검증]` 주황 | "담당자에게 확인 요청했습니다" |
| `escalate` | (답변 대신) | "담당자에게 전달했습니다" |
| `blocked` | `[사내망 밖으로 나간 것 없음]` 파랑 | 폴백 답변 |

### 활동 상태 → 아이콘

| `activity_status` | 표시 |
|---|---|
| `active` | `🟢 활동 중` |
| `away` | `⚪ 자리 비움 ({away_minutes}분)` — 60분 이상은 시간 단위 |
| `offline` | `⚪ 오프라인` |
| `null` | (행 생략) |

### 검증 단계 → 라벨

| `stage` | 라벨 |
|---|---|
| `schema` | `스키마` |
| `vocab` | `어휘` |
| `range` | `범위` |
| `banned` | `금칙어` |
| `ngram` | `원문대조` |
| `size` | `크기` |

통과 `✓` 초록 / 실패 `✗` 빨강. **6단계 전부를 개별 표시한다** (요약 `6/6`만 보여주지 않는다).

### 모드 → 헤더 배지

| 조건 | 배지 |
|---|---|
| `exaone_mode == "live"` | `LIVE` 초록 |
| `exaone_mode == "mock"` | `MOCK · 목업 모드` **주황** |
| `agent_transport` | `broker` / `direct` / `mock` 회색 |
| `trust_boundary_simulated == true` | `경계 시뮬레이션` **주황** + 팝오버 |

---

## 4. API 클라이언트

```javascript
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 410) throw new EnvelopeExpired();
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new ApiError(e.error ?? 'unknown', e.correlation_id, res.status);
  }
  return res.json();
}
```

| 상태 | 처리 |
|---|---|
| `410 Gone` | "미리보기가 만료되었습니다. 다시 질문해 주세요." + 질문은 유지 |
| `422` | 필드별 오류를 폼에 표시 |
| `429` | "일일 상한에 도달했습니다" |
| `500` | "일시적 오류입니다 (참조: {correlation_id})" — 스택 트레이스 없음 |
| 네트워크 실패 | "서버에 연결할 수 없습니다. `make run` 실행 중인지 확인하세요." |

**`correlation_id`를 사용자에게 보여준다.** 로그에서 찾을 수 있는 유일한 실마리이고, 내부 정보를 노출하지 않으면서 디버깅을 돕는다.

---

## 5. 목업 모드 (`MESH_UI_MOCK=1`)

U3 완성 전에 C가 UI를 선행 개발하기 위한 장치.

```javascript
const MOCK = new URLSearchParams(location.search).has('mock');

async function api(method, path, body) {
  if (MOCK) return loadFixture(method, path);
  ...
}
```

**픽스처 위치**: `data/fixtures/api/`

```
data/fixtures/api/
  GET_api_agents.json
  GET_api_health.json
  POST_api_ask_prepare_ready.json      시나리오 1 (기밀, 검증 통과)
  POST_api_ask_prepare_blocked.json    시나리오 3 후속 (검증 실패 + 폴백)
  POST_api_ask_send_auto.json          시나리오 1 자동 응답
  POST_api_ask_send_divergent.json     시나리오 3 병기
  POST_api_ask_send_escalate.json      시나리오 2 q2
  GET_api_inbox.json                   시나리오 2 인박스
  GET_api_audit.json
  GET_api_audit_zero.json              검색 0건
```

**픽스처는 U3가 Day 1 계약 동결 시 함께 만든다.** 실제 응답 형태와 다르면 Day 4에 UI를 다시 만들어야 한다.

`?mock` 쿼리 파라미터로 브라우저에서 즉시 전환할 수 있게 한다 (환경변수 재시작 불필요).

---

## 6. 테스트 가능한 속성

프런트엔드에 PBT를 적용하지 않는다. **N/A 근거**: 순수 함수는 표시 매핑(§3)뿐이고, 이건 테이블 조회라 예제 테스트가 전수 커버한다. 나머지는 DOM 조작과 fetch로 PBT의 이득이 없다 (PBT-01 "식별 가능한 속성 없음" 판정).

**대신 예제 기반 검증 목록** (`data-testid` 기반, 수동 또는 브라우저 자동화)

| # | 검증 | 규칙 |
|---|---|---|
| 1 | `grep -c "internal_path" app.js` == 0 | BR-U-05 |
| 2 | `grep -c "innerHTML" app.js` == 0 | BR-U-12 |
| 3 | `grep -cE "onclick=|<script>|<style>" index.html` == 0 | BR-U-12 |
| 4 | 외부 도메인 참조 0건 (`grep -cE "https?://" index.html app.js style.css` == 0) | BR-U-12, SRI N/A |
| 5 | 미리보기 모달에 `...`·`<details>` 부재 | BR-U-01 |
| 6 | `mock=1`에서 3막 전체가 화면상 재생 | FR-48 |
| 7 | 모든 배지에 텍스트 라벨 존재 | BR-U-13 |
| 8 | 취소 후 감사 로그에 레코드 없음 | BR-U-03 |
| 9 | `disclose: false` 필드가 "비공개"로 표시되지 않고 생략 | BR-U-08 |
| 10 | 검색 0건 시 `audit-zero-hit` 표시 | BR-U-10 |

1~4번은 `make lint`에 grep 검사로 넣는다. 리뷰 매너에 의존하지 않는다.

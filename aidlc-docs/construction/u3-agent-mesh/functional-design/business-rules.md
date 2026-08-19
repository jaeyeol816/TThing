# U3 — Business Rules

규칙 ID: `BR-O-*` Orchestrator · `BR-AG-*` Agent · `BR-I-*` Inbox · `BR-M-*` main/API

---

## BR-O — Orchestrator

### BR-O-01 · 지목은 사람이 한다 (FR-29)
전문성 매칭·임베딩·브로드캐스트·자동 재지목을 **구현하지 않는다.**

**코드에 없어야 하는 것**: 유사도 계산, 에이전트 점수화, `for agent in all_agents` 순회 후 선택.
`targets`는 요청에서 그대로 온다.

**사라진 것과 함께 사라지는 부작용**: 오라우팅 없음, 기밀 질문이 전사에 뿌려질 일 없음, 프로필 노후·콜드스타트 없음.

### BR-O-02 · 최대 2명 (FR-32)
`MAX_TARGETS=2`. 초과는 422. 중복 지목도 422.

그 이상은 답변이 길어지고 비용만 늘어난다 (설계 §4.4.4).

### BR-O-03 · `prepare`는 사람에게 알리지 않는다 (FR-11, S-11)
`prepare` 단계에서 담당자 인박스에 아무것도 쓰지 않는다.
에스컬레이션은 `send` 이후 신뢰도 분기 결과로만 발생한다.

**타입으로 강제**: `PrepareResult.agents_notified: Literal[False]`.

### BR-O-04 · 인용 0개면 무조건 에스컬레이션 (FR-35)
신뢰도 검사보다 **먼저** 검사한다.

```python
if not a.citations: return Disposition.ESCALATE   # 최우선
```

**근거 없는 생성은 사용자에게 도달하지 않는다.** 이 규칙이 자동 응답의 인용 준수율을 구조적으로 100%로 만든다.

### BR-O-05 · 신뢰도 3구간 (FR-34)

| 조건 | 처분 | 화면 |
|---|---|---|
| 인용 ≥ 1 & 신뢰도 ≥ 0.75 | `AUTO` | 답변만 |
| 인용 ≥ 1 & 0.45 ≤ 신뢰도 < 0.75 | `UNVERIFIED` | **`미검증` 배지** + 담당자에게 확인 요청 |
| 신뢰도 < 0.45 | `ESCALATE` | "담당자에게 전달했습니다" |
| 인용 0개 | `ESCALATE` | 동일 |

2명일 때는 **낮은 쪽 신뢰도**를 기준으로 한다 (`min`). 약한 답이 강한 답에 편승하지 않게.

신뢰도는 U2의 `STALE` 보정(×0.8)이 이미 적용된 값이다.

### BR-O-06 · 답을 버리지 않는다 (FR-33, Round 2 Q11)
2명 지목 시 병렬 호출하고 **양쪽을 모두 반환**한다.

- 상충 여부를 **자동 판정하지 않는다.** `divergent`는 "답 텍스트가 실질적으로 다르다"는 관찰일 뿐
- `divergent` 판정 방법: 두 답의 정규화 텍스트가 다르고, 각각의 근거 문서가 다르면 `True`. **LLM을 부르지 않는다**
- 각 답에 `as_of`(근거 시점)와 `formality`(공식/비공식)를 붙인다
- 문구는 고정 템플릿: `"둘 다 사실일 수 있습니다. 시점이 {gap} 차이이고 문서 성격이 다릅니다."`

**금지**: 신뢰도 높은 쪽만 보여주기, `conflict: true`로 단정하기.
> 하나를 조용히 고르면 나머지 하나는 영원히 묻힌다.

### BR-O-07 · 순서 무관 (PB-O4)
병렬 호출이므로 응답 도착 순서가 비결정적이다. `merge()`는 **`targets` 요청 순서**로 정렬해 반환한다. 화면이 매번 달라지면 데모가 흔들린다.

### BR-O-08 · 30초 상한 (FR-36)
`asyncio.wait_for(..., timeout=TOTAL_TIMEOUT_SECONDS)`로 전체를 감싼다.
타임아웃 시: 이미 도착한 답이 있으면 그것만 반환하고 나머지는 에스컬레이션. 하나도 없으면 `answer_in_zone()` 폴백.

### BR-O-09 · 에이전트 간 직접 호출 없음 (FR-36)
`AgentClient`가 다른 `AgentClient`를 호출하는 코드 경로가 없다. 모든 조율은 Orchestrator를 거친다.

**이유**: 에이전트끼리 자유롭게 대화하게 두면 순환과 토큰 폭발로 디버깅이 불가능해진다.

### BR-O-10 · 일일 상한
`AgentConfig.daily_limit` 초과 시 해당 에이전트 지목을 거부하고 이유를 표시한다.
비용 폭주 방어. 데모에서는 50회면 충분하다.

---

## BR-AG — Agent

### BR-AG-01 · Bedrock을 직접 부르지 않는다
`AgentClient`는 `Gatekeeper.ask_agent()`만 호출한다. `boto3`·`BrokerClient`를 import하지 않는다.
import 경계 테스트로 강제.

### BR-AG-02 · 시스템 프롬프트 필수 문구 (FR-25, FR-26)
페르소나 프롬프트에 다음이 **반드시** 들어간다. `build_system_prompt()`가 템플릿에 강제 삽입하고, 없으면 예외를 던진다.

```
1. "당신은 {display_name}의 Agent입니다. 1인칭으로 {name}인 척하지 마십시오."
2. "당신이 받은 것은 실제 문서가 아니라 구조 요약 또는 가명화된 텍스트입니다."
3. "답변에서 대상은 반드시 참조 기호(REQ_A, COMP_B, <SYS_1>)로 지칭하십시오."
4. "근거가 부족하면 추측하지 말고 confidence 를 낮게 보고하십시오."
5. "citations 에는 실제로 근거로 사용한 ref 만 넣으십시오. 비워도 됩니다."
```

**3번이 없으면 재수화가 성립하지 않는다.** Agent가 기호 대신 자기가 상상한 이름을 쓰면 치환할 대상이 없다.

**5번이 인용 0개 차단(BR-O-04)을 실효화한다.** "인용을 채워라"고 압박하면 모델이 가짜 인용을 만든다. "비워도 된다"고 해야 정직하게 빈다.

### BR-AG-03 · 등급별 프롬프트 차이

| 등급 | 추가 문구 |
|---|---|
| `SECRET` | "입력은 고정 스키마의 구조 요약입니다. 필드 이름과 열거값만으로 추론하십시오." |
| `INTERNAL` | "입력은 식별자가 placeholder로 치환된 텍스트입니다. placeholder를 실제 이름으로 추측하지 마십시오." |
| `OPEN` | 추가 없음 |

`INTERNAL`의 문구가 중요하다. Claude가 `<SYS_1>`을 "아마 Okta겠지"라고 추측해서 답변에 쓰면, 재수화 후 틀린 이름이 남는다.

### BR-AG-04 · 에스컬레이션 초안 (FR-27)
신뢰도 < 0.45 또는 인용 0개일 때 생성한다. 구성 3요소:

1. **요약** — 질문을 담당자가 3초에 파악할 형태로
2. **상황** — 지금까지 찾은 근거 (세션 사실 + 파일 인용)
3. **초안** — 담당자가 그대로 승인할 수 있는 답변 문장

저비용 모델(`DRAFT_MODEL_ID`, haiku-4-5, 0.92s)을 쓴다.

**초안 생성도 게이트키퍼를 통과한다.** 초안 프롬프트에 원문을 넣지 않는다 — 이미 변환된 페이로드와 부분 응답만 넣는다.

> 담당자에게 질문 원문만 던지면 알림이 하나 늘 뿐이다. 요약·근거·초안이 함께 가야 처리 비용이 몇 분에서 몇 초로 떨어진다.

### BR-AG-05 · 지식 공백 기록 (FR-37, P2)
답하지 못한 질문을 `local_queries`에 `reason="knowledge_gap"`으로 기록한다.
"이 사람에게 물었는데 답이 없었다"는 기록만으로도 문서화 우선순위의 재료가 된다.

---

## BR-I — Inbox

### BR-I-01 · 3버튼 (FR-38)

| 버튼 | 동작 |
|---|---|
`[승인]` | `status=approved`. 초안을 그대로 답변으로 확정 |
`[수정 후 승인]` | `status=approved_with_edit`. `edited_text`가 답변 |
`[내가 아님]` | `status=redirected`. `redirect_to` 필수 |

### BR-I-02 · 승인 시 환류 (FR-20)
`approved` / `approved_with_edit`에서 `VerifiedQA`를 생성해 `data/verified/{owner}.json`에 추가한다.
**`tier`를 원 질의의 등급으로 보존한다** (BR-S-05).

### BR-I-03 · 자동 재지목 금지 (FR-39)
`not_me`는 시스템이 자동으로 다시 묻지 않는다. 질문자 화면에 표시하고 **질문자가 다시 누르게** 한다.

```
"김책임이 박선임을 지목했습니다  [ 박선임에게 다시 묻기 ]"
```

현실에서 벌어지는 일과 같고, **사람이 지목했으므로 정확하다** — 알고리즘 추정보다 낫다.

### BR-I-04 · 2명 지목 시 같은 스레드 (FR-33)
`[두 분께 확인 요청]`은 두 인박스에 **같은 `thread_id`**로 들어간다. 한쪽이 해결하면 다른 쪽에 그 사실이 표시돼 중재를 유도한다.

### BR-I-05 · 인박스 항목의 등급
`InboxItem.tier`를 보존한다. 클라우드 미러에는 `tier == open`인 항목만 전문을 올리고, 나머지는 `item_id`와 상태만 올린다.

이유: `situation`(근거)과 `question_summary`는 신뢰 구역 안에서 만든 원문 기반 텍스트다. 시나리오 2 인박스의 `"13:47에 스크립트를 고쳤으니"`가 그 예다.

---

## BR-M — main.py / API

### BR-M-01 · localhost 바인딩 강제 (NFR-S-08)
`MESH_BIND_HOST`가 `127.0.0.1`/`localhost`가 아니면 시작 시 경고하고 명시적 확인을 요구한다.

이 서비스는 원문 파일을 읽고 **재수화된 답변(실제 이름 포함)** 을 반환한다. 인증이 없는 MVP에서 네트워크에 노출하면 권한 우회 도구가 된다.

### BR-M-02 · `prepare`/`send` 2단계 (FR-09)
`send`는 `envelope_id` + `approved_by`가 없으면 동작하지 않는다.
`envelope_id`는 `prepare`만 발급하고, 캐시는 `take()`로 **일회용**이다 (중복 전송·중복 과금 방지).

만료된 `envelope_id` → **410 Gone** (404가 아니다. 있었다가 없어진 것이므로).

### BR-M-03 · 보안 헤더 (NFR-S-04)
모든 응답에 미들웨어로 설정:

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
```

`unsafe-inline`을 쓰지 않는다 → U4의 JS·CSS를 인라인이 아니라 별도 파일로 둔다.
HSTS는 localhost HTTP이므로 **N/A**로 기록한다 (문서에 근거 명시).

### BR-M-04 · CORS
`Access-Control-Allow-Origin`을 설정하지 않는다 (동일 출처만). 와일드카드 금지.

### BR-M-05 · 전역 예외 핸들러 (NFR-S-09, NFR-S-15)
```python
@app.exception_handler(Exception)
async def _all(request, exc):
    log.exception("unhandled", extra={"correlation_id": cid.get()})
    return JSONResponse(500, {"error": "internal_error", "correlation_id": cid.get()})
```

**응답에 스택 트레이스·내부 경로·프레임워크 버전을 담지 않는다.** `correlation_id`만 주고 상세는 로그에서 찾는다.

### BR-M-06 · 입력 검증 (NFR-S-05)
모든 요청 바디를 pydantic 모델로 받는다. 수동 `dict` 파싱 금지.
`GET /api/audit?q=`의 `q`는 200자 제한 + SQL 파라미터화.

### BR-M-07 · `correlation_id` 전파
요청 시작 시 생성해 `contextvars`로 전파하고 모든 로그에 포함한다. 응답 헤더 `X-Correlation-Id`로도 반환한다.

### BR-M-08 · 정적 파일 명시 매핑
`StaticFiles(html=True)`를 디렉터리 전체에 붙이지 않고 3개 파일을 명시 매핑한다.
디렉터리 리스팅을 원천 차단한다 (NFR-S-09).

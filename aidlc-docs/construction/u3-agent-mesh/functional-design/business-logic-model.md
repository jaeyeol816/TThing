# U3 — Business Logic Model

---

## 1. 이 유닛이 하는 일

> 지목된 에이전트에게 질문을 넘기고, 답을 신뢰도로 분기한다. 모델을 부르는 것은 Agent뿐이다.

Orchestrator는 **모델을 부르지 않는다.** 앱 코드일 뿐이다. 시스템이 푸는 문제는 "누구인지 찾아주는 것"이 아니라 **"그 사람을 깨우지 않는 것"** 이므로, 라우팅 지능이 필요 없다.

---

## 2. 주 흐름 — `prepare`

```
POST /api/ask/prepare  { question, asker, targets[] }

1. 입력 검증 (pydantic)  targets 1~2개, 중복 없음, question <= 4000자
2. daily_limit 확인       초과 시 해당 대상 거부
3. 대상별 병렬 (asyncio.gather):
     a. Store.load_session(entity_id)
     b. Store.freshness()                          -> live/stale/expired
     c. Gatekeeper.classify(question)              -> 질문 등급     [관문 ①]
     d. Store.select_paths(session, question)      -> 후보 중 선택
     e. Store.read(paths)                          -> Chunk[] (원문)
     f. Gatekeeper.classify(chunk) 각각              -> 파일 등급
     g. Gatekeeper.plan_calls()                    -> 분해 or 상향
     h. call 별로:
          Gatekeeper.to_payload(call)              -> Envelope     [관문 ②]
          Gatekeeper.validate(env, originals)      -> 6단계
          통과 -> PreparedCall(disposition="ready", preview=...)
          실패 -> Gatekeeper.answer_in_zone()
                  PreparedCall(disposition="blocked", fallback=...)
4. PrepareResult 반환    agents_notified = False   <- 사람에게 알림 없음

*** Agent 를 호출하지 않는다. 감사 레코드도 없다 ***
```

---

## 3. 주 흐름 — `send`

```
POST /api/ask/send  { request_id, envelope_ids[], approved_by }

1. envelope 캐시에서 take() (일회용). 없으면 410 Gone
2. 병렬로 AgentClient.ask() -> Gatekeeper.ask_agent()
     전제조건: validation.passed, approved_by, tier 단일값
     AuditLog.record() 후 경계 통과
3. Gatekeeper.rehydrate() 각각                     [관문 ③]
   finally: mapping 폐기
4. branch(answers) -> AUTO / UNVERIFIED / ESCALATE
5. ESCALATE 또는 UNVERIFIED 면:
     AgentClient.draft_escalation()  (haiku-4-5)
     Inbox.add(item, thread_id=request_id)
6. merge(answers) -> divergent 판정 (LLM 미사용)
7. 카운터 갱신 (AUTO 인 경우 interrupts_avoided += 1)
8. AuditLog.mirror() 비동기 fire-and-forget
9. AskResult 반환

전체를 asyncio.wait_for(timeout=30) 로 감싼다
```

---

## 4. 신뢰도 분기 로직

```python
def branch(answers: list[RehydratedAnswer]) -> Disposition:
    # 1. 인용 검사가 최우선 (BR-O-04)
    if any(not a.citations for a in answers):
        return Disposition.ESCALATE

    # 2. 2명이면 낮은 쪽 기준 (BR-O-05)
    conf = min(a.confidence for a in answers)

    if conf >= CONFIDENCE_AUTO:      return Disposition.AUTO        # 0.75
    if conf >= CONFIDENCE_ESCALATE:  return Disposition.UNVERIFIED  # 0.45
    return Disposition.ESCALATE
```

`confidence`는 U2의 `STALE` 보정이 이미 적용된 값이다. 즉:

```
Agent 원본 신뢰도 0.78  (최민수, 2시간 전 세션)
  x 0.8 (STALE 보정)
= 0.62  ->  UNVERIFIED  ->  "미검증" 배지
```

**보정이 실제로 결과를 바꾼다.** 0.78이면 자동 응답이었을 것이 배지가 붙는다. 2시간 전 상태로 답한 것이니 더 정직하다.

---

## 5. 병기 로직 (`merge`)

```python
def merge(answers) -> MergedAnswer:
    ordered = sort_by_request_order(answers)        # BR-O-07 순서 고정

    divergent = False
    if len(ordered) == 2:
        a, b = ordered
        text_differs   = normalize(a.text) != normalize(b.text)
        source_differs = citation_titles(a) != citation_titles(b)
        divergent = text_differs and source_differs   # LLM 호출 없음

    note = None
    if divergent:
        gap = time_gap(a, b)                          # "한 달"
        note = f"둘 다 사실일 수 있습니다. 시점이 {gap} 차이이고 문서 성격이 다릅니다."

    return MergedAnswer(answers=ordered, divergent=divergent,
                        divergence_note=note, disposition=branch(ordered))
```

**`divergent` 판정에 LLM을 쓰지 않는다.** 두 조건의 논리곱이다:
1. 답 텍스트가 실질적으로 다르다
2. 근거 문서가 다르다

2번 조건이 오탐을 줄인다. 같은 문서를 근거로 표현만 다르게 말한 것은 상충이 아니다.

**답을 하나도 버리지 않는다** (PB-O3). `merge()`에 답변을 제거하는 코드 경로가 없다.

---

## 6. 시스템 프롬프트 조립

```python
def build_system_prompt(persona: Persona, tier: Tier) -> str:
    parts = [
        persona.persona_prompt,                       # agents.yaml
        MANDATORY_NO_FIRST_PERSON,                    # "1인칭 금지"
        MANDATORY_NOT_REAL_DOCUMENT,                  # "구조 요약입니다"
        MANDATORY_USE_REFS,                           # "참조 기호로 지칭"
        MANDATORY_LOW_CONFIDENCE_OK,                  # "추측 금지"
        MANDATORY_CITATIONS_MAY_BE_EMPTY,             # "비워도 됩니다"
        TIER_SPECIFIC[tier],                          # 등급별 추가 문구
        answer_format_instruction(schema),
    ]
    prompt = "\n\n".join(parts)
    assert_all_mandatory_present(prompt)              # 누락 시 예외
    return prompt
```

`assert_all_mandatory_present()`가 5개 필수 문구의 존재를 확인한다.
누군가 `agents.yaml`을 편집하다 페르소나 프롬프트로 덮어써도 필수 문구는 남는다.

**`MANDATORY_CITATIONS_MAY_BE_EMPTY`가 중요하다.** "인용을 반드시 채워라"고 압박하면 모델이 가짜 인용을 만들고, 그러면 BR-O-04(인용 0개 차단)가 무력화된다. 정직하게 빌 수 있게 해야 차단이 작동한다.

---

## 7. 에스컬레이션 초안 생성

```
draft_escalation(envelope, partial_response):
  입력에 넣는 것:
    - 변환된 페이로드 (원문 아님)
    - Agent 의 부분 응답 (confidence 낮은 것)
    - 세션 사실 (session_facts)
    - 인용 목록 (display_title 만)
  입력에 넣지 않는 것:
    - *** 원문 (Chunk.text) ***
  모델: DRAFT_MODEL_ID (haiku-4-5, 0.92s)
  출력: { summary, situation[], draft_answer, already_answered[] }
```

**초안 생성도 경계를 넘는 호출이다.** 그러므로 `Gatekeeper.ask_agent()`를 경유하고 감사 로그에 기록된다. 원문을 넣지 않는 것이 핵심.

`already_answered`는 시나리오 2의 인박스 화면에 쓰인다:
> "기법 질문(라벨 불균형)은 Agent가 이미 답변함"

담당자가 **자기가 답해야 하는 조각만** 보게 만드는 장치다.

---

## 8. 시나리오별 흐름

### 시나리오 1 — 자동 응답 (1명)

```
prepare: 상향 -> secret -> 구조 추출 -> 검증 6/6 -> PreviewCard
send:    ask_agent -> confidence 0.83, citations 2
         branch: 인용 2개, 0.83 >= 0.75 -> AUTO
         merge:  1개 답변, divergent=False
         카운터: interrupts_avoided += 1, minutes_saved += 20
화면:    답변 + 근거 2개 + [기밀] 배지 + 신뢰도 0.83
김책임:  *** 알림 없음 ***
```

### 시나리오 2 — 분해 + 부분 에스컬레이션

```
prepare: plan_calls -> q1(internal, 파일) / q2(internal, 세션)
         envelope 2개, 각각 검증 통과
send:    ask_agent x2 병렬
         q1: confidence 0.86, citations 1 -> AUTO
         q2: confidence 0.38, citations 1 -> ESCALATE
         branch(전체): min(0.86, 0.38) = 0.38 -> ESCALATE
                       하지만 q1 은 이미 답이 있다
         => 하위 질문별로 처분을 분리한다 (아래 참조)
         draft_escalation(q2) -> Inbox
화면:    q1 답변 (자동) + q2 "확인 요청했습니다" + 세션 사실 참고
박선임:  인박스에 초안. [수정 후 승인] 25초
환류:    VerifiedQA(tier=internal) -> data/verified/person_park.json
```

**하위 질문별 처분 분리**: `branch()`를 전체가 아니라 `PreparedCall` 단위로 적용한다.
`AskResult.merged.answers`에 q1 답변이 들어가고, q2는 `escalations`에 들어간다.
`MergedAnswer.disposition`은 "사용자가 지금 답을 받았는가"를 나타내므로 q1이 있으면 `AUTO`다.

이건 `branch()` 시그니처를 `branch(answers_for_one_call)`로 두고 호출을 call 단위로 하면 자연스럽게 해결된다.

### 시나리오 3 — 2명 병기 + 폴백

```
prepare: 2명 -> 각각 internal -> 가명화 -> 검증 통과
send:    ask_agent x2 병렬
         kim:  confidence 0.71, LIVE   -> 0.71
         choi: confidence 0.78, STALE  -> 0.78 x 0.8 = 0.62
         branch(kim):  0.71 -> UNVERIFIED
         branch(choi): 0.62 -> UNVERIFIED
         merge: 텍스트 다름 + 근거 문서 다름 -> divergent=True
                note = "둘 다 사실일 수 있습니다. 시점이 한 달 차이이고..."
화면:    양쪽 병기. 각각 미검증 배지 + as_of + formality
         김책임: 개인 메모 2025-11 비공식
         최민수: 설계 리뷰 2025-12 공식
         [두 분께 확인 요청] -> 같은 thread_id 로 두 인박스

후속 질문 (성능 수치):
prepare: secret -> 추출 시도 -> 필수 슬롯 미충족 -> ExtractionFailed
         answer_in_zone() -> PreparedCall(disposition="blocked", fallback=...)
         *** send 를 호출할 필요가 없다. 감사 레코드 없음 ***
화면:    "[기밀 · 사내망 밖으로 나간 것 없음]" + 폴백 답변
감사탭:  이 질의 레코드가 없다
```

**후속 질문에서 `send`가 아예 호출되지 않는 것이 깔끔하다.** `prepare`가 `blocked` + `fallback`을 함께 반환하므로 UI가 바로 답을 표시한다. "차단됐는데 답은 나온다"가 한 왕복에 끝난다.

---

## 9. 지연 예산

| 단계 | 목표 | 근거 |
|---|---|---|
| `prepare` (1명) | < 6s | U1 §8 예산 |
| `prepare` (2명 병렬) | < 7s | 병렬이므로 +1s |
| **사람 확인** | — | 예산 밖 |
| `send` (1명) | < 4s | Bedrock 2.2s + 재수화 |
| `send` (2명 병렬) | < 5s | |
| 에스컬레이션 초안 | < 2s | haiku-4-5 0.92s |
| **전체 상한** | **30s** | `asyncio.wait_for` |

여유가 충분하다. 30초는 재시도와 초안 생성까지 흡수한다.

---

## 10. 테스트 가능한 속성 (PBT-01)

`domain-entities.md` §8에 PB-O1~PB-O6.

**PB-O1(인용 0개 → 항상 ESCALATE)과 PB-O3(답을 버리지 않는다)이 가장 중요하다.** 둘 다 순수 함수에 대한 불변식이라 Hypothesis로 완전히 검사할 수 있다.

**PBT 미적용 (N/A 근거)**
- `agent.py` — Bedrock 호출 래퍼. 필수 문구 존재 확인은 예제 테스트
- `inbox.py` — 상태 4개·전이 3개의 자명한 상태 기계. 예제 테스트로 완전 커버. 상태 기반 PBT(PBT-06) **N/A**
- `main.py` — FastAPI 라우팅. `TestClient` 예제 테스트

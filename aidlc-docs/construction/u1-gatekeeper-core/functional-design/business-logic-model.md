# U1 — Business Logic Model

기술 중립적 로직 서술. 타입은 `domain-entities.md`, 규칙은 `business-rules.md` 참조.

---

## 1. 이 유닛이 하는 일

> Agent가 무엇을 볼 수 있는지 정한다. 그게 전부다.

Agent에게 정보가 도달하는 경로는 셋뿐이다 — 질문, 지식, 그리고 응답을 되돌리는 길.
그래서 관문도 셋이다.

| 관문 | 통제 대상 | `SECRET`일 때 |
|---|---|---|
| ① 질문 변환 | 사용자 질문 문장 | 구조화해서 넘긴다 |
| ② 지식 변환 | 읽은 파일 내용 | 구조 페이로드로 바꾼다 (원문 0개) |
| ③ 재수화 | Agent 응답 | 기호를 실제 이름으로 되돌린다 |

①을 빠뜨리면 안 된다. 지식을 아무리 잘 막아도 **질문 문장 자체가 기밀을 담고 있으면** 그대로 새어 나간다.

---

## 2. 주 흐름 — `prepare`

```
입력: question, entity_id, chunks[] (원문 포함)

1. 질문 등급 판정                                          [관문 ①]
   q_tier = classify(question, source_path=None)

2. 지식 등급 판정
   for c in chunks: c.tier = classify(c.text, c.internal_path)

3. 호출 계획
   plan_calls(question, entity_id, chunks) -> AgentCall[]
   3a. 하위 질문 분해 시도 (BR-G-07 세 조건)
   3b. 분해 불가 -> 단일 호출, tier = max(q_tier, *chunk_tiers)
   3c. 분해 가능 -> 하위 질문별 호출, 각각 자기 tier
   불변식: 각 AgentCall.tier 는 단일값

4. 표현 변환                                                [관문 ②]
   for call in calls:
       OPEN     -> verbatim(chunks)
       INTERNAL -> pseudonymize(chunks)   -> (text, mapping)
       SECRET   -> extract(chunks, schema) -> (payload, mapping)

5. 검증 6단계
   validate(envelope, originals=[c.text for c in call chunks])

6. 분기
   통과   -> PreviewCard 반환. Agent 호출 없음
   실패   -> answer_in_zone() 폴백. 감사 레코드 없음

출력: PreviewCard 또는 폴백 답변
부작용: envelope + mapping 을 메모리 캐시에 TTL 5분 저장
```

---

## 3. 주 흐름 — `send`

```
입력: envelope_id, approved_by

1. 캐시 조회. 없으면 410 Gone (TTL 만료)
2. 전제조건 (BR-G-02): validation.passed, approved_by, tier 단일값
3. 감사 기록                                    <- 호출 직전
4. ask_agent()                                  *** 경계 통과 ***
5. 응답 검사: revalidated 여야 함 (BR-G-01)
6. rehydrate(response, mapping)                 [관문 ③]
7. finally: mapping 폐기 + 캐시 제거            (BR-G-06)

출력: RehydratedAnswer
```

`try/finally`가 아니라 다른 구조로 쓰면 안 된다. 재수화 실패 시에도 매핑은 폐기돼야 한다.

---

## 4. 등급 판정 로직

```
classify(text, source_path):

  rule = rule_tier(text, source_path, rules)      # 순수 함수, BR-C-03
    1. 경로 glob (customer-*/**)          -> SECRET
    2. 헤더 등급 표기                      -> 표기된 등급
    3. 금칙어 리터럴 (고객사명)            -> SECRET
    4. 금칙어 정규식 (계약번호·금액·코드)  -> SECRET   <- 함정 문서를 잡는 규칙
    5. internal 경로 glob                  -> INTERNAL
    6. 기본값                              -> INTERNAL   <- OPEN 이 아니다

  if rule == SECRET:
      return TierDecision(tier=SECRET, exaone_skipped=True)   # BR-C-02

  try:
      ex = exaone_tier(text)               # enum 출력만, enable_thinking=False
  except (Timeout, ParseError, ValueError):
      return TierDecision(tier=SECRET, exaone_failed=True)    # BR-G-01

  return TierDecision(tier=max(rule, ex))                     # BR-C-01
```

**두 가지 설계 판단**

1. **기본값이 `INTERNAL`이다.** 판정 못 한 문서가 `OPEN`으로 흘러가면 원문이 그대로 나간다. `OPEN`은 명시적 표기가 있는 문서만 받는다.
2. **규칙이 하한선을 만든다.** EXAONE 판정만 쓰면 프롬프트 인젝션 한 번에 무너진다. 규칙은 모델이 무엇을 하든 동작한다.

---

## 5. 구조 추출 로직 (슬롯 채우기)

```
extract(chunks, schema, vocab, exaone):

  1. 슬롯 목록을 배치로 나눈다 (배치당 최대 12개, BR-E-06)
  2. 배치별로:
       프롬프트 = 슬롯 정의(이름 + 허용값 목록) + 원문
                  + "Never quote the document"        <- BR-E-01
       응답 = exaone.complete_json(...)                <- enable_thinking=False
                                                          reasoning* 삭제
  3. raw = 배치 응답 병합

  4. assemble(raw, schema):                            <- 순수 함수, BR-G-03
       result = {}
       for slot in schema.slots:                       <- 스키마를 순회. raw 를 순회하지 않는다
           if slot.name not in raw: continue
           v = raw[slot.name]
           if v == "__unknown__": continue
           v = coerce(v, slot)                         <- BR-E-02
           if v is DROP: continue
           result[slot.name] = v
       return result
       # raw 의 미등록 키는 읽히지도 않는다

  5. 필수 슬롯 미충족 -> ExtractionFailed              <- BR-E-03, 시나리오 3 폴백

  6. ref 라벨 생성 + mapping 구성                      <- BR-E-04
  7. entities 구조로 포장
```

### 4번 루프의 방향이 이 프로젝트의 핵심이다

```
❌ for key in raw:                    "모델이 준 것을 검사해서 걸러낸다"
       if key in schema: result[key] = raw[key]

✅ for slot in schema.slots:          "스키마가 요구하는 것만 찾아 쓴다"
       if slot.name in raw: result[slot.name] = ...
```

두 코드는 결과가 같아 보이지만 다르다. 위쪽은 **검사를 잊으면 유출**이고, 아래쪽은 **잊을 검사가 없다.**
설계 문서 §3.1의 "무엇을 지울까가 아니라 무엇만 보낼까"가 이 루프의 방향으로 표현된다.

---

## 6. 검증 로직

```
validate(payload, schema, vocab, banned, originals):

  checks = [
    check_schema(payload, schema),                    # 정의된 키만
    check_vocab(payload, vocab),                      # 어휘 사전 안
    check_ranges(payload, schema),                    # 숫자 범위
    check_banned(payload, banned),                    # 금칙어 0건
    check_no_source_ngram(payload, originals, n=5),   # 원문 조각 0건  <- 가장 강력
    check_size(payload, 2048),                        # 자유 텍스트 혼입 신호
  ]
  # 첫 실패에서 멈추지 않는다 (BR-V-00) — 진단이 완전해야 한다
  return ValidationResult(checks=tuple(checks))
```

`INTERNAL` 등급은 5단계 규칙이 다르다 (BR-P-03): 원문 5-gram 전체가 아니라 **치환 대상 토큰을 포함한 5-gram만** 검사한다. 사내 등급의 정의가 "원문을 안 보낸다"가 아니라 "식별자를 안 보낸다"이기 때문이다.

전부 순수 함수다 → PBT 대상 (PB-3~PB-5).

---

## 7. 폴백 로직 — `answer_in_zone`

```
answer_in_zone(question, chunks):
  1. 신뢰 구역 안의 EXAONE 에게 원문을 그대로 주고 답을 받는다
  2. 답변에 "[등급 · 사내망 밖으로 나간 것 없음]" 표시
  3. used_external_agent = False
  4. 감사 레코드를 남기지 않는다                      <- BR-A-03
  5. local_queries 테이블에만 기록 (감사 로그 탭에 표시 안 함)
```

**답변 품질은 떨어지지만 유출은 없다.** 어떤 경우에도 "잘 모르겠으니 일단 Agent에게 보낸다"가 되지 않는다.

시나리오 3의 결정적 장면이 여기다. 감사 로그에 레코드가 **없는 것**이 증거가 된다.

---

## 8. 지연 예산 (NFR-P-01: 30초)

| 단계 | 호출 | 실측 기준 | 예산 |
|---|---|---|---|
| 질문 등급 판정 | EXAONE ×1 (규칙이 secret이면 0회) | 0.8s | 1s |
| 경로 선택 | EXAONE ×1 (U2) | 0.9s | 1s |
| 파일 읽기 | 로컬 I/O | — | 0.1s |
| 파일 등급 판정 | 규칙만 (경로 매치로 대부분 해소) | — | 0.5s |
| 구조 추출 | EXAONE ×1~2 (슬롯 배치) | 1.0s/배치 | 3s |
| 검증 6단계 | 순수 코드 | — | 0.1s |
| **사람 확인** | — | 사용자 | (예산 밖) |
| Agent 호출 | Bedrock ×1~2 병렬 | 2.2s | 6s |
| 재수화 | 순수 코드 | — | 0.1s |
| **합계 (사람 확인 제외)** | | | **≈ 12s** |

여유 18초. 재시도 2회(추출 실패)와 2명 병렬 호출을 흡수한다.

**타임아웃 설정**: EXAONE 10s, 브로커/Bedrock 25s, 전체 30s (Orchestrator).

---

## 9. 시나리오별 경로

| | 시나리오 1 | 시나리오 2 | 시나리오 3 후속 |
|---|---|---|---|
| 질문 등급 | `internal` | `internal` | `internal` |
| 파일 등급 | `secret` + `internal` | `internal` | `secret` |
| 분해/상향 | **상향** (조건 2 위반) | **분해** (q1/q2 독립) | 상향 |
| 표현 | 구조 페이로드 | 가명화 | 구조 추출 시도 |
| 검증 | 6/6 통과 | 6/6 통과 | **필수 슬롯 미충족** |
| Agent 호출 | ○ | ○ ×2 | **✕** |
| 감사 레코드 | ○ | ○ ×2 | **✕** |
| 결과 | 자동 응답 (0.83) | q1 자동 / q2 에스컬레이션 | 신뢰 구역 내 답변 |

---

## 10. 테스트 가능한 속성 (PBT-01)

`domain-entities.md` §9에 PB-1 ~ PB-10으로 정리돼 있다.

**PBT 미적용 컴포넌트와 근거**
- `gatekeeper.py` — 조율만 하고 로직이 없다. 예제 기반 테스트로 충분
- `audit.py` — I/O 중심. 저장·검색 라운드트립은 예제 테스트
- `llm/exaone.py` — 외부 호출. `reasoning*` 삭제만 예제 테스트로 확인

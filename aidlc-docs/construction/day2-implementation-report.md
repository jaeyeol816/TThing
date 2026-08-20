# Day 2 구현 보고서 — 보안 코어

**일자**: 2026-08-19 · **범위**: U1 Step 9~18 (소유 A)
**결과**: 테스트 712개 통과 · 게이트 G2 통과 (정확도 100% · 기밀 재현율 100%) · LLM 호출 5회

---

## 1. Day 2 가 하는 일

Day 1 이 계약이었다면 Day 2 는 **막 그 자체**다. 경계를 넘는 것을 실제로 통제하는
코드가 여기서 나온다.

```
Day 1  누가 무엇을 주고받는지 정했다 (타입 · 시그니처 · 어휘 사전)
Day 2  실제로 막는다              (판정 · 변환 · 검증 · 감사)
Day 3  사람과 연결한다            (Store · Agent · Orchestrator)   <- B
```

새로 만든 모듈 6개와 채운 관문 8개가 전부다.

| 모듈 | 레이어 | 역할 | 순수? |
|---|:---:|---|:---:|
| `validator.py` | L1 | 검증 6단계 | **순수** |
| `rehydrator.py` | L1 | 기호 → 실제 이름 | **순수** |
| `classifier.py` | L3 | 등급 판정 (규칙 + EXAONE) | 규칙만 순수 |
| `extractor.py` | L3 | 슬롯 채우기 + 화이트리스트 조립 | `assemble` 순수 |
| `pseudonymizer.py` | L3 | 식별자 치환 | **순수** |
| `audit.py` | L4 | SQLite 감사 + 전수 유출 검사 | I/O |

`gatekeeper.py` 의 8개 관문(`classify` / `plan_calls` / `to_payload` / `validate` /
`preview` / `ask_agent` / `rehydrate` / `answer_in_zone`)이 채워졌다.
`test_no_method_is_still_a_stub` 이 ast 로 스텁이 남아 있지 않은지 검사한다.

---

## 2. 세 관문이 실제로 어떻게 막는가

### 2.1 관문 ① 질문 변환 — 질문도 판정 대상이다

지식을 아무리 잘 막아도 **질문 문장 자체가 기밀을 담고 있으면** 그대로 새어 나간다.

```python
await gk.classify("REQ-4412 요구가 우리 SDK 와 충돌하나요?", source_path=None)
# -> Tier.SECRET  (금칙어 정규식 REQ-\d{4})
```

질문에는 경로가 없으므로 경로 규칙(1·5)을 건너뛰고 금칙어 검사와 기본값
`INTERNAL` 이 적용된다. **기본값이 `OPEN` 이 아닌 것이 핵심이다.**

### 2.2 관문 ② 지식 변환 — 등급마다 표현이 다르다

| 등급 | 표현 | 원문 | 담당 | LLM |
|---|---|:---:|---|:---:|
| `SECRET` | 구조 페이로드 (어휘 사전 제한) | **0개** | `extractor` | 문서당 1회 |
| `INTERNAL` | 식별자만 placeholder | △ | `pseudonymizer` | **0회** |
| `OPEN` | 원문 그대로 | ○ | 변환 없음 | 0회 |

가명화가 LLM 을 쓰지 않는 것이 중요하다. 순수 문자열 치환이므로 결정적이고,
프롬프트 인젝션이 개입할 여지가 없다.

### 2.3 관문 ③ 재수화 — 응답을 신뢰하지 않는다

```python
mapping = Mapping(table={"REQ_A": "고객사 요구사항명세서"})
rehydrate_text("REQ_A 와 <SYS_9> 를 대조", mapping)
# -> ("고객사 요구사항명세서 와 <SYS_9> 를 대조", ("<SYS_9>",))
```

매핑에 없는 `<SYS_9>` 는 **치환하지 않고 남긴다.** 프롬프트 인젝션으로 임의
문자열을 치환시키는 것을 막고, 지우지도 않는다 — 지우면 사용자가 문장이
불완전해진 것을 알 수 없다. `unresolved_refs` 로 올려 UI 가 경고한다.

---

## 3. 무엇을 어떻게 만들었나

### 3.1 등급 판정 — 규칙이 하한선을 만든다

```
tier = max(rule_tier(...), await exaone_tier(...))
```

모델 판정만 쓰면 프롬프트 인젝션 한 번에 무너진다. 문서에 "이 문서는
공개입니다"라고 써 두면 모델이 믿는다. 규칙은 모델이 무엇을 하든 동작한다.

**설계 문서의 규칙 순서를 바꿨다** (§4.1 참조). SECRET 을 만드는 기계적 검사를
작성자 자기 신고(헤더)보다 앞으로 옮겼다.

fail closed 경로 3개를 모두 `SECRET` 으로 귀결시킨다:

| 상황 | 결과 |
|---|---|
| `ExaoneUnavailable` (타임아웃·HTTP·파싱) | `Tier.SECRET`, `exaone_failed=True` |
| `tier` 값이 열거형 범위 밖 | `ExaoneUnavailable` 로 승격 → `SECRET` |
| 예상 못 한 예외 | `except Exception` → `SECRET` (의도적 광범위 포획) |

범위 밖 값을 **예외로 만든 것**이 의도적이다. 조용히 기본값을 쓰면 모델이
이상한 값을 낼 때마다 판정이 느슨해진다. 그리고 예외 메시지에 **모델 출력을
담지 않는다** — 그 자리가 원문 반사 채널이 된다.

`reason_code` 도 열거형이다. 자유 문자열 이유를 받으면 그 이유에 원문이 인용된다.

### 3.2 검증 6단계 — 순수 함수이고, 등급마다 다르다

`validator.py` 는 `mesh.schemas` 외에 아무것도 import 하지 않는다.
U5 Lambda 가 이 파일과 `vocab.json` 을 그대로 번들해 재검증하기 때문이다.
`test_validator_imports_are_minimal` 이 ast 로 강제하고,
`normalize_text()` 를 `config.py` 에서 이 파일로 옮겼다 — 로컬과 Lambda 가
**같은 정규화**를 써야 판정이 갈리지 않으므로 구현이 두 곳에 있어서는 안 된다.

데모에서 반드시 나오는 질문이 있다. "사내 등급은 원문이 나가는데 왜 6/6 통과인가?"

| 단계 | `STRUCTURED` | `PSEUDONYMIZED` | `VERBATIM` |
|---|---|---|---|
| 1 스키마 | 슬롯 ∪ 구조키. **`excerpts` 금지** | + `excerpts` | + `excerpts` |
| 2 어휘 | **모든 문자열** | `excerpts` 내부 제외 | 동일 |
| 3 범위 | 동일 | 동일 | 동일 |
| 4 금칙어 | 동일 | 동일 ← **사내의 하한선** | 동일 |
| 5 원문 | 원문 5-gram 0건 | **식별자 포함 5-gram 0건** | 검사 불가(정의) |
| 6 크기 | 2KB | 2KB × 8 | 2KB × 8 |

등급의 정의가 다르므로 검사도 다르다. 사내 등급에서 검사하는 것은
"원문이 나갔는가"가 아니라 **"식별자가 나갔는가"** 다 (BR-P-03).

두 가지를 정직하게 처리했다:
- `VERBATIM` 은 5단계를 적용할 수 없다. **통과시키되 그 사실을
  `CheckResult.detail` 에 남긴다** — 조용히 넘기면 미리보기가 거짓이 된다
- **첫 실패에서 멈추지 않는다** (BR-V-00). 조기 반환을 넣으면 "5/6 실패"만
  보이고 나머지 상태를 알 수 없다

### 3.3 구조 추출 — 루프의 방향이 보안 속성이다

```python
❌ for key in raw:                  # "모델이 준 것을 검사해서 걸러낸다"
       if key in schema.slot_names:
           result[key] = raw[key]

✅ for slot in schema.slots:        # "스키마가 요구하는 것만 찾아 쓴다"
       if slot.name in raw:
           result[slot.name] = coerce(raw[slot.name], slot)
```

위쪽은 **검사를 잊으면 유출**이고 아래쪽은 **잊을 검사가 없다.**
미등록 키는 검증 실패가 아니라 **조립 단계에서 버려진다(drop)** — 검증 실패는
전송 차단(데모 중단)이고 drop 은 정상 진행이다.

미등록 키를 두 지점에서 버린다:
1. 배치 응답 병합 시 (`k in names` 필터) — 그 값이 어디로도 전파되지 않는다
2. `assemble()` 의 스키마 순회 — 임의의 `raw` 에 대한 불변식

`coerce()` 의 실측 기반 결정 셋:

| 결정 | 이유 |
|---|---|
| `enum` 유사 매칭 **금지** | `"challenge-response"` 를 고쳐주기 시작하면 어디까지 고칠지 경계가 없다 |
| `int` 는 숫자+단위 형태만 | `"2026-07-15"` → `2026` 은 범위(0..8760) 안이라 조용히 통과한다 |
| `bool` 슬롯에 `True` 가 와도 `int` 슬롯은 DROP | 파이썬에서 `True == 1` 이지만 의미가 다르다 |
| 범위 검사를 **하지 않는다** | 검증 3단계의 일이다. 여기서 자르면 환각값이 정상값으로 위장된다 |

### 3.4 가명화 — 기술 용어를 지키는 것이 품질이다

```
원문   atlas_ml 파이프라인은 RandomOverSampler(sampling_strategy=0.5) 로 오버샘플링
결과   <PROJ_1> 파이프라인은 RandomOverSampler(sampling_strategy=0.5) 로 오버샘플링
```

`<TERM_1>` 이 오버샘플링인지 Claude 가 알 수 없다. `technical_terms` 는 명시적
허용 목록(45개)이고, 목록에 없는 고유명사는 치환하는 쪽으로 기울인다.

placeholder 번호를 **리터럴 길이 순으로 먼저 배정하고 그다음에 치환한다.**
문서 순서에 따라 번호가 흔들리면 한 질의 안에서 일관성이 깨지고 Claude 가
관계를 추론하지 못한다 (BR-P-02).

검증 5단계에 넘기는 `identifiers` 는 **치환 대상 전체**다 (실제 치환분이 아니다).
치환된 것만 넘기면 **가명화가 놓친 표기 변형**을 검사할 방법이 사라진다.

### 3.5 감사 — 기록하지 않는 것을 검사로 만든다

```python
@staticmethod
def reject_forbidden(payload) -> None:
    # 페이로드에 금지 키가 있으면 GatekeeperError
```

"원문을 기록하지 않는다"를 주석이 아니라 실행되는 검사로 만들었다.
`record()` 가 거부하면 `ask_agent()` 가 예외를 받고 **전송도 일어나지 않는다** —
fail closed 방향이다.

`local_queries` 에 **질문 원문을 넣지 않는다.** `question_sha256` 만 남기고
이유도 열거형(`LOCAL_REASON_CODES`)이다. 자유 문자열 이유를 받으면 그 이유에
질문 원문이 섞여 들어간다. `answer_in_zone(reason=...)` 이 열거값을 받는 이유가 이것이다.

`audit` 테이블에 대한 `DELETE`/`UPDATE` 문이 앱 코드 전체에 없다 —
`test_no_delete_or_update_anywhere_in_src` 가 정규식으로 확인한다.

미러링(`mirror()`)이 이 프로젝트의 **유일한 fail-open** 경로다. 근거 셋:
미러링 실패로 질의가 죽으면 데모가 멈춘다 · 로컬 SQLite 가 원본이라 증거가
사라지지 않는다 · 실패 건수를 `/api/health` 에 노출해 조용히 넘기지 않는다.

### 3.6 조율 — 매핑 폐기를 구조로 강제한다

`ask_agent` + `rehydrate` 를 호출자가 직접 조합하면 재수화가 실패했을 때
매핑이 메모리에 남는 경로가 생긴다. 그 실수를 구조적으로 막는
`send_and_rehydrate()` 를 추가했다.

```python
entry = self.cache.take(envelope_id)       # 조회 + 제거. 일회용
try:
    resp = await self.ask_agent(entry.envelope, persona, approved_by)
    return self.rehydrate(resp, entry.mapping, persona=persona, chunks=chunks)
finally:
    entry.mapping.table.clear()            # 재수화 실패 시에도
    self.cache.discard(envelope_id)
```

필수 문구 5개(BR-AG-02)를 `build_system_prompt()` 가 **강제 삽입**하고
`assert_all_mandatory_present()` 가 존재를 확인한다. `assert` 를 쓰지 않는다 —
`python -O` 에서 제거되면 검사가 사라진다. `agents.yaml` 을 편집해 페르소나
프롬프트로 덮어써도 필수 문구는 남는다.

이 함수를 `gatekeeper.py`(L4)에 둔 이유: `ask_agent()` 가 프롬프트를 필요로 하는데
L4 는 L5(`agent.py`)를 import 할 수 없다. 구현이 한 곳에 있어야 "필수 문구가
빠진 경로"가 생기지 않는다.

---

## 4. 설계 문서의 결함 3건 (Day 2 에 발견)

셋 다 "테스트는 통과하는데 실제로는 뚫려 있는" 종류다.
상세는 `preflight-findings.md` §9.

### 4.1 `BR-C-03` 의 규칙 순서에 조용한 하향 경로 🔴

원안은 **헤더(작성자 자기 신고)를 금칙어 검사(기계적)보다 먼저** 평가하고,
"앞에서 걸리면 뒤를 보지 않는다"였다. 그러면

```markdown
---
보안등급: 사내          <- 여기서 확정
---
티어 3 계약 규모는 12억원이다.   <- 금액 검사가 실행되지 않는다
```

함정 문서가 잡힌 것은 **헤더가 없었기 때문**이다. 작성자가 한 줄만 추가하면
FR-52 의 유일한 탐지 수단이 무력화된다.

**조치**: SECRET 을 만드는 기계적 검사를 헤더보다 앞으로. 조기 반환이 여전히
안전한 이유는 그것들이 **천장값**을 내기 때문이다. 라벨 코퍼스 11건의 판정
결과는 재배치 전후 동일하고, 잠재적 하향 경로만 사라졌다.

**함께**: `OPEN` 은 헤더 + 경로 **두 신호**를 요구한다. 원문이 그대로 나가는
유일한 등급이므로 하향 결정에 단일 신호를 쓰지 않는다.

### 4.2 `json.dumps` 가 5-gram 대조를 우회시킨다 🔴

```
페이로드    "세션    최대\n유지시간은 여덟 시간으로 제한한다"    <- 실제 개행
dumps 후    "세션 최대\\n유지시간은 …"                          <- 두 글자 \n
정규화      토큰이 "최대\n유지시간은" 이 되어 5-gram 이 어긋난다   -> 통과 ✗
```

BR-V-05 가 "공백만 바꿔 우회하는 것을 막는다"고 명시했는데 **개행으로는
우회됐다.** 테스트를 먼저 쓴 덕에 잡혔다.

**조치**: `payload_text()` 가 구조 부분(`json.dumps`)과 **이스케이프 없는 원시
문자열 값**을 함께 이어 붙인다. 같은 결함이 `audit.sweep_for_leaks()` 에도 있었다.

### 4.3 평탄한 `facts` 가 상충하는 사실을 합쳐 버린다 🔴

실측 첫 페이로드는 **검증 6/6 통과 + 원문 0개**였는데 답이 틀린다.

| 근거 | `session_binding` |
|---|---|
| 고객사 요구사항명세서 | `required` |
| 자사 인증 설계 문서 | `none` |

평탄한 `{슬롯: 값}` 으로 조립하니 하나가 다른 하나를 덮어써
`session_binding: none` 만 남았다. Agent 는 "충돌 없음"이라고 답한다.
`constraint_conflict_check` 는 **두 근거를 대조하는** task 인데 대조 대상이 사라졌다.

유출이 아니라 **정확성 실패**이고, 유출보다 발견하기 어렵다 —
검증 6단계가 전부 통과하기 때문이다.

**조치**: ① 문서마다 따로 슬롯을 채운다 ② `facts` 를 `{ref: {슬롯: 값}}` 으로 분리.

```json
"facts": {
  "REQ_A":  {"session_binding": "required", "max_session_hours": 8,
             "credential_reuse_allowed": false, "auth_mechanism_class": "challenge_response"},
  "COMP_A": {"session_binding": "none", "credential_lifetime_hours": 24,
             "renewal_mode": "background_silent"}
}
```

파생 수정: `slot_entries()` 가 **경로별로** 반환한다 (이름별 dict 로 뭉치면 두
근거 중 하나만 범위 검사된다) · `check_schema` 가 `facts` 하위 ref 라벨을 허용 ·
필수 슬롯은 **근거 전체에서** 채워지면 충족.

### 4.4 (부수) Day 1 계약에 사내·공개 페이로드 형태가 없었다

`STRUCTURAL_KEYS` 에 텍스트 키가 없어서 가명화·원문 페이로드를 **만들 방법이
없었다.** Day 1 의 계약이 기밀 경로만 완전했다.

`excerpts` 키(`{ref: text}`)를 도입하고 **표현별로 허용 키를 다르게** 했다.
`STRUCTURED` 에서만 `excerpts` 를 금지하는 것이 기밀 등급의 "원문 0개"를
구조적으로 보장한다 — 텍스트를 담을 키가 화이트리스트에 없다.

---

## 5. 테스트 — 712개

| 파일 | 개수 | 무엇을 보증하나 |
|---|---:|---|
| `test_validator.py` | 65 | 6단계 각각의 실패 · **순수성(ast)** · 등급별 차이 |
| `test_classifier.py` | 51 | 규칙 6단계 · 헤더가 금칙어를 우회 못함 · fail closed |
| `test_extractor.py` | 71 | `coerce` 실측 형태 · **drop** · ref 라벨 · 시나리오 1 |
| `test_pseudonymizer.py` | 37 | 기술 용어 보존 · placeholder 일관성 · 왕복 |
| `test_audit.py` | 38 | 추가 전용 · 금지 필드 거부 · 원문 검색 0건 |
| `test_gatekeeper.py` | 44 | 세 시나리오 · 전제조건 · **매핑 폐기** |
| `test_invariants.py` | 31 | PB-1 ~ PB-10 |
| Day 1 파일 | 375 | 계약 동결 · import 경계 · 로그 |

### 5.1 PB-5 가 가장 중요하다

```python
@given(schema=task_schemas(), source=source_texts(), data=st.data())
@settings(max_examples=300)
def test_pb5_no_source_ngram_reaches_the_payload(schema, source, data):
    raw = data.draw(adversarial_raw(schema, source=source))
    payload = build_payload(schema, (), {"REQ_A": assemble(raw, schema)})
    assert validator.check_no_source_ngram(payload, (source,), ...).passed
```

예제 기반 테스트로는 절대 증명할 수 없다 — 우리가 생각해낸 원문에 대해서만
확인하게 되기 때문이다.

### 5.2 `adversarial_raw()` 가 PBT 를 실효화한다

원시 타입 생성기(`st.dictionaries(st.text(), st.text())`)만으로는 화이트리스트
조립을 시험할 수 없다. 임의 문자열 키는 슬롯 이름과 거의 겹치지 않으므로
`assemble()` 이 빈 dict 를 반환하고 **테스트가 아무 일도 하지 않는데 통과한다.**

그래서 실측에서 관찰된 실패 방식을 그대로 생성한다:
미등록 키(`max_session_duration`) · 하이픈 변형(`challenge-response`) ·
자유 문자열(`"8 hours"`) · **원문 조각** · 중첩 구조 · 타입 불일치 · `__unknown__`.

그리고 그 생성기 자체를 검사하는 테스트를 뒀다
(`test_adversarial_generator_actually_produces_adversarial_input`) — 표본 200개에서
미등록 키·원문 조각·타입 불일치가 **실제로 나오는지** 그리고 **살아남는 값이
있는지**를 확인한다. 모든 것을 버리는 조립기는 PB-3/4/5 를 자동으로 통과한다.

### 5.3 검사기 자체 검사

이 프로젝트의 테스트 중 상당수가 "무언가 없음"을 주장한다. 그런 검사는
**아무것도 못 잡을 때 조용히 통과**하므로 심은 위반을 잡는지 함께 확인한다.

| 검사기 | 자체 검사 |
|---|---|
| 5-gram 대조 | `test_pb5_detector_catches_a_planted_quote` |
| 전수 유출 검사 | `test_sweep_detects_a_planted_leak` |
| import 경계 (Day 1) | 심은 `import boto3` 를 잡는지 |
| 적대적 생성기 | 위 §5.2 |

---

## 6. 게이트 G2

```
$ make eval-classify

  판정     정답       결과       규칙    문서
  OK     secret   secret   1     corpus/customer-H/req-spec-2026H.md
  OK     secret   secret   1     corpus/customer-H/benchmark-prod-2025-11.md
  OK     secret   secret   2     corpus/kim/docs/sdk-pricing-tiers.md [함정]
  OK     internal internal 4     corpus/kim/docs/auth-design.md
  ... (11건)
  OK     open     open     4     corpus/public/oauth-rfc-summary.md

  정확도        11/11 = 100.0%   (목표 >= 90%)
  기밀 재현율   3/3 = 100.0%     (목표 100%)
  함정 탐지     100%   (1건)
  상향 오류     0건   불편 — 답변이 무뎌진다
  하향 오류     0건   *** 유출 *** — blocking
```

**리포트를 assert 보다 먼저 출력한다.** 실패했을 때 무엇이 왜 틀렸는지 보이지
않으면 게이트가 진단 도구가 되지 못한다.

### 6.1 왜 규칙만으로 측정하는가

프로덕션 판정은 `max(규칙, EXAONE)` 이고 `max` 는 **등급을 올릴 수만 있다.**
따라서 `기밀 재현율(프로덕션) >= 기밀 재현율(규칙)` 이 항상 성립한다.
규칙만으로 100% 면 EXAONE 을 더해도 100% 다.

이점 셋: 결정적이다(LLM 0회, CI 무료) · 하한선을 측정한다(모델 없이도 보장) ·
모델 가용성에 게이트가 흔들리지 않는다.

EXAONE 포함 실측이 필요하면 `MESH_EVAL_WITH_EXAONE=1` (문서 11건 × 1회).
그 테스트는 `max()` 가 규칙보다 **낮아지지 않음**을 항목별로 확인한다.

### 6.2 게이트가 확인하는 것

| 검사 | 성격 |
|---|---|
| 하향 오류 0건 | 🔴 blocking — 하향은 유출이다 |
| 기밀 재현율 100% | 🔴 blocking |
| 함정 문서 탐지 | 🔴 blocking (FR-52) |
| 정확도 ≥ 90% | 목표 |
| `OPEN` 오판정 0건 | `OPEN` 쪽 정밀도 (원문이 그대로 나간다) |
| 모든 코퍼스 문서에 라벨 | 라벨 없는 문서가 있으면 정확도가 좋게 보인다 |
| 세 등급 모두 존재 | 한 등급만 있으면 정확도가 무의미하다 |
| 재실행 결과 동일 | CI 재현성 |

---

## 7. 실측 (LLM 호출 5회)

EXAONE 5회 · Bedrock 0회. 사용량을 최소로 유지했다.

### 7.1 구조 추출 실측 결과 (최종)

```json
{
  "task": "constraint_conflict_check",
  "domain": "authentication",
  "question_template": "conflict_and_mitigation",
  "entities": [{"ref": "REQ_A", "role": "external_requirement"},
               {"ref": "COMP_A", "role": "our_component"}],
  "facts": {
    "REQ_A":  {"auth_mechanism_class": "challenge_response", "session_binding": "required",
               "credential_reuse_allowed": false, "max_session_hours": 8,
               "renewal_mode": "explicit"},
    "COMP_A": {"session_binding": "none", "credential_lifetime_hours": 24,
               "renewal_mode": "background_silent"}
  },
  "answer_format": {"conflict": "bool", "reason": "string", "mitigations": "string[]"}
}
```

```
검증: 6/6
  OK  schema   키 19개 전부 등록됨
  OK  vocab    문자열 값 전부 in-vocab (전체)
  OK  range    숫자·타입 전부 범위 안
  OK  banned   금칙어 0건
  OK  ngram    원문 5-gram 0건 (대조 549개)
  OK  size     562/2048 bytes

유출 검사: H社 · 하나텔 · REQ-4412 · CTR-204817 · EAP-AKA · 12억 · 김철수 · Nova · atlas  전부 부재
verbatim_sentence_count = 0
```

입력 문서 2건(고객사 요구사항명세서 · 자사 인증 설계 문서)에 고객사명·계약번호·
금액·담당자명이 모두 들어 있었고, 페이로드에는 하나도 남지 않았다.
그러면서 **충돌 판단에 필요한 사실은 보존됐다** — `REQ_A.session_binding=required`
vs `COMP_A.session_binding=none`.

### 7.2 지연

| 단계 | 조건 | 지연 |
|---|---|---|
| 슬롯 채우기 | 6슬롯, 문서 1건 | 0.43s |
| 구조 추출 전체 | 문서 2건 (호출 2회) | 0.86s |
| 검증 6단계 | 순수 코드, 5-gram 549개 | < 0.01s |
| 가명화 | 순수 치환 (LLM 0회) | < 0.01s |

구조 추출 예산 3초에 0.86초를 쓴다. 문서 4건이어도 1.8초로 예산 안이다.

---

## 8. 검증 상태

```bash
make test            # 712 passed  (unit 681 + property 31)
make lint            # ruff check + format + lint-web  통과
make audit           # No known vulnerabilities
make eval-classify   # 게이트 G2 통과
make preflight       # 실패 1 · 경고 2   <- 실패는 AWS 자격증명 만료 (§9)
```

| Gate | 기준 | 상태 |
|---|---|---|
| **G2** | 기밀 재현율 100%, 정확도 ≥90%, 함정 탐지 | ✅ **통과** |
| SG5 | 로그·감사에 원문·토큰·`reasoning*` 부재 | ✅ 감사 로그까지 확인 |
| SG10 | `validator.py` 순수성 (Lambda 번들 조건) | ✅ ast 검사 |
| SG11 | `audit` 테이블 추가 전용 | ✅ 소스 정규식 검사 |
| S-31 | PB-1 ~ PB-10 전부 통과 | ✅ |

---

## 9. 조치가 필요한 것 — AWS 임시 자격증명 만료

```
ExpiredToken: The security token included in the request is expired
```

`AWS_ACCESS_KEY_ID` 가 `ASIA…` STS 임시 자격증명이고 Day 1 실측 이후 만료됐다.
`preflight` 의 실패 1건이 이것이다.

**Day 2 에는 영향이 없다.** Day 2 의 대상은 판정·검증·추출·가명화·감사이고
Bedrock 은 대역으로 검증한다. EXAONE 은 정상이다.

**Day 3 (U3 Agent 실호출) 전에 갱신이 필요하다.**

```bash
# 새 자격증명을 .kiro/.env 에 기입한 뒤
set -a; . ./.env; . ./.kiro/.env; set +a
make preflight
```

갱신이 어렵다면 `AGENT_TRANSPORT=mock` 으로 3막 전체가 돌아간다 (FR-48).

---

## 10. Day 3 착수 지점 (소유 B)

Day 2 산출물이 B 의 입력이다.

| B 가 쓰는 것 | 상태 |
|---|---|
| `Gatekeeper` 8개 관문 | 구현 완료. 시그니처는 Day 1 그대로 |
| `Gatekeeper.send_and_rehydrate()` | 신설 — 매핑 폐기가 보장된 `send` 흐름 |
| `build_system_prompt()` | `gatekeeper.py` 에 있다 (`agent.py` 가 import) |
| `can_decompose()` + `SubQuestion` | 하위 질문 그래프를 B 가 만들어 넘긴다 |
| `AuditLog` | `record` / `record_local` / `search` / `add_inbox` / `list_inbox` |

B 가 채울 것: `store.read()` · `store.select_paths()` · `store.list_agents()` ·
`agent.py` · `orchestrator.py` · `inbox.py` · `main.py`.

`plan_calls()` 는 **질문 하나에 호출 하나**를 만든다. 분해가 결정되면
Orchestrator 가 `can_decompose()` 로 판정하고 하위 질문마다 `plan_calls()` 를
한 번씩 부른다 — 그러면 각 호출이 자기 근거의 등급만 갖게 되어 BR-G-08 이
자연히 성립한다.

참조: `aidlc-docs/construction/plans/u1-gatekeeper-core-code-generation-plan.md` Step 9~18

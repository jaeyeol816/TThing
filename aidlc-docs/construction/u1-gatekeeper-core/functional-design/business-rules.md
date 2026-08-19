# U1 — Business Rules

규칙 ID 체계: `BR-G-*` 게이트키퍼 · `BR-C-*` 분류 · `BR-E-*` 추출 · `BR-V-*` 검증 · `BR-P-*` 가명화 · `BR-A-*` 감사

**전역 원칙**: 애매하면 **항상 더 높은 등급으로.** 등급을 낮게 잡은 실수는 유출이고, 높게 잡은 실수는 불편일 뿐이다. 비대칭이 명확하다.

---

## BR-G — 게이트키퍼 조율

### BR-G-01 · 실패는 항상 닫는다 (fail closed)
어떤 실패든 결과는 `Tier.SECRET` + 신뢰 구역 내 처리다.

| 실패 | 결과 |
|---|---|
| EXAONE 타임아웃/오류 | `Tier.SECRET` |
| 등급 판정 예외 | `Tier.SECRET` |
| 구조 추출 실패 (2회 재시도 후) | Agent 호출 없이 `answer_in_zone()` |
| 검증 6단계 중 하나라도 실패 | 전송 차단 + `answer_in_zone()` |
| 브로커/Bedrock 오류 | `answer_in_zone()` + 사용자에게 품질 저하 고지 |
| 브로커 응답에 `revalidated != True` | 응답 거부 + `answer_in_zone()` |

**예외 1건**: `AuditLog.mirror()`만 fail-open이다. 클라우드 미러링 실패가 질의를 죽이면 안 되고, 로컬 SQLite가 원본이므로 증거가 사라지지 않는다.

**금지**: "잘 모르겠으니 일단 보낸다"에 해당하는 코드 경로가 존재하지 않아야 한다.

### BR-G-02 · `ask_agent`는 유일한 외부 통로
`ask_agent()` 진입 시 3개 전제조건을 검사하고, 하나라도 위반이면 `GatekeeperError`를 던진다.

```python
assert env.validation is not None and env.validation.passed
assert approved_by                      # 빈 문자열 불가
assert isinstance(env.tier, Tier)       # 단일값
```

`assert`가 아니라 명시적 `raise`로 구현한다 (`python -O`에서 assert가 제거되므로).

### BR-G-03 · 화이트리스트 조립 (설계 핵심)
페이로드는 **모델이 만들지 않고 코드가 조립한다.**

```
for slot in schema.slots:
    if slot.name in raw and raw[slot.name] != "__unknown__":
        result[slot.name] = coerce(raw[slot.name], slot)
    # raw 의 나머지 키는 아예 읽지 않는다
```

모델이 반환한 미등록 키는 **검증 실패가 아니라 조립 단계에서 버려진다.**
차이가 중요하다 — 검증 실패는 전송 차단(데모 중단)이고, drop은 정상 진행이다.

**실측 근거**: 모델에게 JSON 전체를 만들게 하면 어휘 사전을 벗어난다. 슬롯 채우기 + drop 조립으로 3회 반복 모두 in-vocab이 됐다 (`preflight-findings.md` §1).

### BR-G-04 · 등급별 표현 (FR-03, FR-05)

| 등급 | 표현 | 원문 문장 | 담당 |
|---|---|:---:|---|
| `OPEN` | 원문 그대로 | ○ | 변환 없음 |
| `INTERNAL` | 고유명사 → placeholder | △ (식별자 제거) | `Pseudonymizer` |
| `SECRET` | 구조 페이로드 (어휘 사전 제한) | **✕ 0개** | `Extractor` |

### BR-G-05 · 등급 상향 (FR-11)
```python
tier = max([question_tier, *[c.tier for c in chunks]])
```
질문이 `internal`이어도 동원된 파일에 `secret`이 하나 있으면 호출 전체가 `secret`이다.

### BR-G-06 · 매핑 폐기
`rehydrate()` 호출 후 `try/finally`로 매핑을 폐기하고 `envelope_id` 캐시 항목을 제거한다.
재수화 실패 시에도 폐기한다 (자원 정리, NFR-S-15).

### BR-G-07 · 분해 vs 상향 판정 (FR-12)
하위 질문 `q`를 **분해**하는 조건 — 3개를 **모두** 만족할 때만:

1. `q`가 자기 `answer_format`을 가진다 (독립적으로 답이 정의된다)
2. `q.needs[]`가 다른 하위 질문의 `needs[]`와 **교집합이 없다**
3. `q` 하나만으로 사용자에게 보여줄 값이 있다

하나라도 어긋나면 분해하지 않고 `max(tier)`로 상향한다.

**조건 2가 핵심이다.** 같은 파일을 두 하위 질문이 함께 쓰면, 한쪽은 가명화 원문·다른 쪽은 구조 요약으로 나가고 **두 표현을 대조해 원문이 복원**될 수 있다. `scenarios.md` §2 ②가 지적한 위험이다.

**시나리오 검증**
- 시나리오 1: 두 파일이 하나의 충돌 판단에 함께 필요 → 조건 2 위반 → **상향** (`secret`)
- 시나리오 2: q1(기법, `preprocess_v3.py`) / q2(허락, `session.recent_runs`) → 겹치지 않음, 각각 독립적 답 → **분해**

### BR-G-08 · 한 호출에 한 등급 (FR-12)
`AgentCall.tier`와 `PayloadEnvelope.tier`는 단일값이다. 등급이 섞인 페이로드는 **타입 수준에서 생성되지 않는다.**

### BR-G-09 · 매핑 비영속 (FR-13)
`Mapping`은 `dataclass`이고 `__getstate__`/`__reduce__`가 `TypeError`를 던진다.
`PayloadEnvelope`에 `mapping` 필드를 두지 않는다 — `model_dump()`가 실수로 매핑을 흘리지 않게.

### BR-G-10 · 브로커 응답을 신뢰하지 않는다
Agent 응답의 `ref`가 매핑 테이블에 **없으면 치환하지 않고 기호를 그대로 남긴다.**
프롬프트 인젝션으로 임의 문자열을 치환시키는 것을 막는다.
치환되지 않은 `ref`가 있으면 UI에 경고를 띄운다.

---

## BR-C — 등급 판정

### BR-C-01 · `max(규칙, EXAONE)` (FR-01)
```python
tier = max(rule_tier(text, path, rules), await exaone_tier(text, exaone))
```
둘 중 하나만 기밀이라고 해도 기밀로 처리한다.

**근거**: 안전성이 모델 판단에만 의존하면 프롬프트 인젝션과 환각에 노출된다. 규칙이 하한선을 만든다.

### BR-C-02 · 규칙이 이미 `SECRET`이면 EXAONE 생략
`max`의 결과가 바뀌지 않으므로 왕복을 절약한다 (NFR-P-02). `TierDecision.exaone_skipped = True`로 기록.

### BR-C-03 · 규칙 판정 우선순위 (앞에서 걸리면 뒤를 보지 않는다)

| # | 검사 | 결과 |
|---|---|---|
| 1 | 경로가 `secret_path_globs`에 매치 (`customer-*/**`, `**/benchmark/**`) | `SECRET` |
| 2 | 문서 헤더에 등급 표기 (`보안등급: 기밀`) | 표기된 등급 |
| 3 | 본문에 금칙어 리터럴 (고객사명 사전) | `SECRET` |
| 4 | 본문이 금칙어 정규식에 매치 (계약번호 `REQ-\d{4}`, 제품코드, 금액) | `SECRET` |
| 5 | 경로가 `internal_path_globs`에 매치 | `INTERNAL` |
| 6 | 그 외 | `INTERNAL` |

**기본값이 `INTERNAL`인 것이 의도적이다.** `OPEN`은 명시적으로 표기된 문서만 받는다. 판정 못 한 문서가 공개로 흘러가면 안 된다.

### BR-C-04 · 함정 문서를 잡는 것은 규칙 4번이다 (FR-52)
"겉보기엔 일반 설계 문서인데 고객사 단가가 섞인" 문서는 경로가 `customer-*/`가 아니고 헤더 표기도 없다. **본문의 금액 패턴**이 유일한 단서다. 그래서 `banned.json`의 정규식에 금액 표현(`\d+억`, `\d{1,3}(,\d{3})*원`, `USD\s*\d+`)을 반드시 넣는다.

### BR-C-05 · EXAONE 판정 프롬프트 제약
```
enable_thinking = False
response_format = json_object
출력: {"tier": "open"|"internal"|"secret", "reason_code": <enum>}
```
`reason_code`도 열거형이다. 자유 문자열 이유를 받으면 **그 이유에 원문이 인용될 수 있다.**
실패·파싱 오류·범위 밖 값 → `Tier.SECRET` (BR-G-01).

---

## BR-E — 구조 추출

### BR-E-01 · 슬롯 채우기 프롬프트 형식
실측에서 결정적으로 동작한 형식 (3회 반복 동일):

```
SYSTEM: You are a slot filler. For each slot output exactly one value copied
character-for-character from that slot's allowed list, or "__unknown__".
Never invent values. Never invent slot names. Never quote the document.
Output a flat JSON object whose keys are exactly the slot names given.

USER: SLOTS:
  auth_mechanism_class: ["password","challenge_response",...]
  session_binding: ["required","optional","none"]
  max_session_hours: integer 0..8760
DOCUMENT:
  <원문>
```

**"Never quote the document"가 반드시 들어간다.** 이것이 없으면 모델이 근거를 설명하려고 원문을 인용한다.

### BR-E-02 · 타입 강제 (실측된 모델 습성)
| 슬롯 `kind` | 모델이 주는 것 | 강제 결과 |
|---|---|---|
| `bool` | `"false"`, `"False"`, `"no"`, `0` | `False` |
| `bool` | `"true"`, `"True"`, `"yes"`, `1` | `True` |
| `int` | `"8"`, `"8 hours"`, `8.0` | `8` (숫자만 추출, 실패 시 drop) |
| `enum` | 정확히 일치하지 않는 값 | **drop** (유사 매칭 금지) |

**`enum`에 유사 매칭을 하지 않는 것이 중요하다.** `"challenge-response"`를 `"challenge_response"`로 고쳐주기 시작하면 화이트리스트가 뭉개진다. 정확히 일치하지 않으면 버린다.

### BR-E-03 · `__unknown__` 처리
- 선택 슬롯이 `__unknown__` → 페이로드에서 생략
- **필수 슬롯이 `__unknown__` → `ExtractionFailed`** → `answer_in_zone()` 폴백
- 모든 슬롯이 `__unknown__` → `ExtractionFailed`

시나리오 3의 폴백이 정확히 이 경로다. 성능 수치 슬롯이 어휘 사전에 없으므로 필수 슬롯이 채워지지 않는다.

### BR-E-04 · `ref` 라벨 자동 생성
- 실제 이름과 **무관한** 자동 생성 번호: `REQ_A`, `REQ_B`, `COMP_A`, `COMP_B`, ...
- 접두사는 `TaskSchema.entity_roles`에서 유도 (`external_requirement` → `REQ`, `our_component` → `COMP`)
- 문서 순서대로 배정. 문서명·경로의 어떤 부분도 라벨에 반영하지 않는다

### BR-E-05 · 재시도 (FR-46)
JSON 파싱 실패 시 **최대 2회 재시도**. 재시도 프롬프트에 "output valid JSON only"를 덧붙인다.
3회 모두 실패 → `ExtractionFailed`.

### BR-E-06 · 슬롯 배치 크기
한 번의 EXAONE 호출에 최대 12개 슬롯. 초과하면 나눠 호출한다.
실측: 5개 슬롯 + 원문 235 토큰 → 0.96초. 12개까지는 지연 예산 안에 들어온다.

---

## BR-V — 검증 6단계 (순수 함수)

### BR-V-00 · 전체 규칙
6단계를 순서대로 실행하되 **첫 실패에서 멈추지 않고 전부 수집**한다.
이유: 사람이 볼 진단이 완전해야 하고, `PreviewCard`에 `6/6`을 표시해야 한다.

### BR-V-01 · 스키마
페이로드의 모든 키가 `TaskSchema.slot_names` ∪ 구조 키(`task`, `domain`, `entities`, `ref`, `role`, `facts`, `question_template`, `answer_format`)에 속하는가.

### BR-V-02 · 어휘
모든 문자열 값이 해당 슬롯의 `allowed`에 있는가. `task`/`domain`/`question_template`도 `vocab.json`의 목록에 있는가.

### BR-V-03 · 범위
모든 정수가 슬롯의 `[min, max]` 안에 있는가.

### BR-V-04 · 금칙어
페이로드를 문자열로 평탄화한 뒤 `banned.literals`(부분 문자열, 대소문자 무시)와 `banned.patterns`(정규식)를 검사한다.

### BR-V-05 · 원문 5-gram 대조 (가장 강력한 검사)
```python
def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()

def ngrams(s: str, n: int = 5) -> set[str]:
    toks = normalize(s).split()
    return {" ".join(toks[i:i+n]) for i in range(len(toks) - n + 1)}

# 원문의 어떤 5-gram도 페이로드 문자열에 등장하면 실패
```

- 정규화: 공백 축약 + 소문자화. 공백만 바꿔 우회하는 것을 막는다
- 한국어는 형태소 분리 없이 공백 토큰 기준으로 한다. 5-gram이면 오탐이 거의 없다
- 대상 원문: **이 호출에 동원된 모든 `Chunk.text`** (전체 코퍼스가 아니다 — 그건 U6의 전수 검사)

> 원문 문장이 한 조각이라도 페이로드에 있으면 기계적으로 잡힌다. 모델이 무엇을 하든.

### BR-V-06 · 크기
`len(json.dumps(payload).encode()) <= 2048`.
초과는 자유 텍스트가 섞였다는 신호로 간주한다.

### BR-V-07 · 브로커 재검증 (NFR-S-11)
U5 Lambda가 **같은 `validator.py`와 같은 `vocab.json`을 번들해** 1~4·6단계를 재실행한다.
5단계(원문 대조)는 원문이 클라우드에 없으므로 **재실행할 수 없다** — 이건 한계이며 문서화한다. 로컬에서만 수행된다.

Lambda의 재검증 실패 시: HTTP 400 + CloudWatch 메트릭 `ValidationFailure` 증가 + 알람.
응답에 **어떤 필드·값이 문제였는지 담지 않는다** — 브로커 응답이 원문 추측 채널이 되지 않게.

---

## BR-P — 가명화 (INTERNAL 등급)

### BR-P-01 · 치환 대상과 비대상 (FR-05)

| 치환 | 예 |
|---|---|
| ✅ 사내 프로젝트명 | `atlas_ml` → `<PROJ_1>` |
| ✅ 시스템·제품 고유명사 | `Nova 게이트웨이` → `<SYS_1>` |
| ✅ 경로 세그먼트 | `~/work/atlas-ml/` → `<PATH_1>` |
| ✅ 인명 | `김철수` → `<PERSON_1>` |
| ❌ 기술 용어 | `RandomOverSampler`, `balanced_subsample`, `SSO`, `claim mapping`, `EAP-AKA` |
| ❌ 표준·프로토콜명 | `OAuth`, `SAML`, `TLS` |
| ❌ 숫자 파라미터 | `sampling_strategy=0.5`, `random_state=42` |

**기술 용어를 치환하면 답변 품질이 무너진다.** `<TERM_1>`이 오버샘플링인지 뭔지 Claude가 알 수 없다.

`technical_terms()`는 명시적 허용 목록(frozenset)이다. 목록에 없는 대문자 고유명사는 **치환하는 쪽으로 기울인다** (애매하면 더 안전한 쪽).

### BR-P-02 · placeholder 일관성 (FR-06)
같은 대상은 한 질의 안에서 같은 번호. 카테고리별 카운터를 하나 유지한다.
일관성이 깨지면 Claude가 관계를 추론하지 못한다.

### BR-P-03 · `INTERNAL`은 5-gram 검사를 어떻게 통과하는가
가명화된 텍스트는 **원문 문장을 대부분 유지**하므로 BR-V-05가 그대로 적용되면 반드시 실패한다.

**규칙**: `representation == "pseudonymized"`인 경우 5-gram 검사를 다음으로 바꾼다.

```
원문의 5-gram 중 "치환 대상 토큰을 포함한 것"만 검사한다.
=> 식별자가 원문 형태로 남아 있으면 실패, 기술 내용은 통과
```

즉 `INTERNAL`에서 검사하는 것은 "원문이 나갔는가"가 아니라 **"식별자가 나갔는가"** 다. 등급 정의가 다르므로 검사도 다르다. 이 차이를 코드 주석과 `PreviewCard`에 명시한다 — 데모에서 "사내 등급은 원문이 나가는데 왜 통과했나"라는 질문이 반드시 나온다.

### BR-P-04 · 재수화는 긴 키부터
`<SYS_1>`과 `<SYS_11>`이 함께 있을 때 짧은 키를 먼저 치환하면 `<SYS_11>`이 `실제이름1`로 망가진다.
키를 길이 내림차순으로 정렬해 치환한다.

---

## BR-A — 감사

### BR-A-01 · 기록 시점
`ask_agent()`가 브로커/Bedrock을 호출하기 **직전**에 기록한다. 호출 성공 후가 아니다.
호출이 실패해도 "나갔다"는 사실은 남아야 한다.

### BR-A-02 · 기록하지 않는 것 (NFR-S-03)
원문(`Chunk.text`), 매핑 테이블, API 키, AWS 자격증명, EXAONE `reasoning`/`reasoning_content`, HTTP 요청 헤더.

**`reasoning*` 제외가 특히 중요하다.** 실측에서 EXAONE이 이 필드에 사고 과정을 담는 것을 확인했고, 사고 과정은 원문을 인용할 수 있다 (`preflight-findings.md` §1 발견 1).

### BR-A-03 · 레코드가 없어야 하는 경우
`answer_in_zone()` 폴백은 감사 레코드를 남기지 않는다. 경계를 넘은 것이 없으므로.
**"감사 로그에 없다"가 증거가 된다** — 시나리오 3의 결정적 장면.

혼동을 막기 위해 신뢰 구역 내 처리는 별도 테이블(`local_queries`)에 기록한다. 감사 로그 탭에는 표시하지 않는다.

### BR-A-04 · 원문 검색 (FR-42)
`payload` JSON 전문에 대한 부분 문자열 검색 (대소문자 무시).
결과 0건일 때 화면에 "**0건 — 이 문구는 경계를 넘은 적이 없습니다**"를 명시적으로 표시한다.

### BR-A-05 · 미러링은 fail-open
DynamoDB 미러 실패는 무시하고 로컬 기록만 유지한다. 실패를 로그에 남기고 `/api/health`에 미러 지연 건수를 노출한다.

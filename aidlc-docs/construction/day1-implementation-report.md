# Day 1 구현 보고서

**일자**: 2026-08-19 · **커밋**: `c91c1b8` (부트스트랩) → `e2b54d0` (계약 동결)
**결과**: 테스트 382개 통과 · lint 통과 · 의존성 취약점 0건 · LLM 호출 6회

---

## 1. Day 1 의 목표는 "동작"이 아니라 "계약"이었다

5일 해커톤에서 3명이 병렬로 작업한다. Day 1 에 기능을 만들면 Day 2 부터 세 사람이
서로를 기다린다. 그래서 Day 1 의 산출물은 **동작하는 기능이 아니라 세 사람의 계약**이다.

```
Day 1 종료 시점의 상태

  A(게이트키퍼)  ──┐
  B(Store/Agent) ──┼── 같은 타입, 같은 시그니처, 같은 어휘 사전을 보고 있다
  C(코퍼스/화면) ──┘

  B 는 Gatekeeper 구현이 비어 있어도 Day 3 에 코딩할 수 있다 (시그니처가 있다)
  C 는 API 가 안 떠 있어도 Day 4 에 화면을 만들 수 있다 (픽스처가 있다)
```

이 판단의 근거는 설계 문서 §7.3 이다.

> **Day 1 종료 시 `schemas.py`와 `vocab.json`을 동결한다.** 이게 세 사람의 계약이다.
> A 의 비중이 가장 크다. Day 2 종료 시 Gatekeeper 인터페이스가 스텁이라도 나와야
> B 가 Day 3 에 막히지 않는다.

계획보다 하루 앞당겨 **Day 1 에 스텁을 냈다.** Day 2 에 A 가 게이트키퍼 구현에
집중할 수 있게 하려는 것이다.

---

## 2. 무엇을 만들었나

### 2.1 동결된 계약 6종

| 산출물 | 소비자 | 없으면 |
|---|---|---|
| `src/mesh/schemas.py` | 전원 | 타입 없이 코딩 불가 |
| `data/vocab.json` v1.0.0 | A, C | 추출기·재검증 불가 |
| `src/mesh/gatekeeper.py` 시그니처 | B | Day 3 에 막힌다 |
| `src/mesh/api_models.py` | C | 화면 선행 개발 불가 |
| `config/agents.yaml` | B, C | 에이전트 정의 불가 |
| `data/fixtures/api/*.json` 11개 | C | 화면 선행 개발 불가 |

**변경은 3인 합의로만** (NFR-M-02). `tests/unit/test_gatekeeper_contract.py` 의
시그니처 동결 테스트가 무단 변경을 실패시킨다.

### 2.2 실제로 동작하는 코드

계약만 낸 게 아니라, **다른 사람을 막지 않는 범위에서** 실제 구현을 했다.

| 모듈 | 구현 범위 | 왜 Day 1 에 했나 |
|---|---|---|
| `config.py` | 전체 | 전원이 첫 줄부터 쓴다 |
| `exceptions.py` | 전체 | fail-closed 정책을 예외 계층으로 표현해야 나머지가 그 규칙을 따른다 |
| `llm/exaone.py` | 전체 | A·B 가 둘 다 쓴다. `reasoning*` 삭제는 보안 요건이라 미룰 수 없다 |
| `llm/broker.py` | 전체 | 3모드 전환이 데모 안전망이다 |
| `llm/fixtures.py` | 전체 | C 의 목업 모드 전제 |
| `gatekeeper.py` | `check_preconditions` + `EnvelopeCache` | §3.2 참조 |
| `store.py` | 세션 로드 · 신선도 · 승인 QA | B 의 Day 3 작업량을 줄인다 |
| `api_models.py` | 전체 | C 의 Day 4 전제 |
| `scripts/preflight.py` | 전체 | 다른 컴퓨터로 옮길 때 가장 먼저 실행 |

### 2.3 데이터

| 파일 | 내용 |
|---|---|
| `data/vocab.json` | 슬롯 8개 · task 3개 · `_intentionally_absent` 목록 |
| `data/banned.json` v1.1.0 | 차단 목록 — 고객사명 8 · 정규식 10 |
| `data/pseudonyms.json` | 치환 목록 — 대상 18 · 기술 용어 45 |
| `data/labels.json` | 등급 정답 11건 (함정 1건 포함) |
| `data/corpus/**` | 문서 11건 (기밀 3 · 사내 7 · 공개 1) |
| `data/sessions/*.json` | 세션 3개 |
| `data/fixtures/api/*.json` | API 목업 11개 |

---

## 3. 어떻게 만들었나 — 설계 원칙의 코드 표현

이 프로젝트의 원칙은 "조심하자"가 아니다. **실수해도 유출 경로가 없게** 만드는 것이다.
5일 동안 3명이 작업하면 반드시 누군가 실수한다.

Day 1 에 그 원칙을 코드로 바꾼 방식이 아래 6가지다.

### 3.1 불변식을 타입으로 표현한다

문서에 쓴 규칙은 지켜지지 않는다. 타입 시스템에 넣으면 지켜진다.

| 불변식 | 타입 표현 | 위반 시 |
|---|---|---|
| 한 호출에 한 등급 (BR-G-08) | `AgentCall.tier: Tier` (`list[Tier]` 아님) | 생성 시점 `ValidationError` |
| 페이로드에 원문 없음 | `PayloadEnvelope` 에 `text` 필드 부재 | 담을 방법이 없다 |
| 매핑 비영속 (BR-G-09) | `Mapping.__getstate__` → `TypeError` | 직렬화 시도가 실패 |
| 인용에 경로 없음 (FR-43) | `Citation` 에 `internal_path` 부재 | 표시할 방법이 없다 |
| 등급 상향이 `max()` (FR-11) | `Tier.__lt__` 등 4개 명시 | §5.1 참조 |
| 자유 문자열 슬롯 금지 | `SlotDef.kind: Literal["enum","int","bool"]` | `"str"` 을 넣을 수 없다 |
| 담당 영역은 끌 수 없다 | `Disclose.expertise: Literal[True]` | `False` 를 넣을 수 없다 |
| prepare 는 사람을 깨우지 않는다 | `PrepareResult.agents_notified: Literal[False]` | `True` 를 넣을 수 없다 |
| blocked 면 답이 함께 온다 | `PreparedCall` 의 `model_validator` | `fallback` 없으면 생성 실패 |

**`SlotDef.kind` 에 `"str"` 이 없는 것이 가장 강한 결정이다.** 자유 문자열 슬롯을
만들 수 없으므로 원문이 새어나갈 채널 자체가 존재하지 않는다. 새 task 에
자유 문자열이 필요해 보이면, 그건 그 task 가 이 방식에 맞지 않는다는 신호다.

### 3.2 사람 확인을 API 구조로 강제한다

`Gatekeeper.check_preconditions()` 를 **Day 1 에 구현한 것이 의도적이다.**
스텁만 냈으면 Day 2 에 다른 코드가 먼저 붙어 전제조건 없이 경계를 넘을 수 있다.

```python
@staticmethod
def check_preconditions(env: PayloadEnvelope, approved_by: str) -> None:
    if env.validation is None:      raise GatekeeperError("검증되지 않은 페이로드…")
    if not env.validation.passed:   raise GatekeeperError("검증 실패 페이로드…")
    if not approved_by.strip():     raise GatekeeperError("사용자 승인 없이…")
```

`assert` 를 쓰지 않는다 — `python -O` 에서 제거된다. 이걸 테스트가 확인한다
(`test_preconditions_are_not_assert_based`: 소스에 `assert` 문이 없어야 한다).

`ask_agent()` 가 구현이 비어 있어도 이 검사를 **먼저** 호출한다. 그래서
`NotImplementedError` 가 아니라 `GatekeeperError` 가 난다 — 테스트가 그 순서를 검증한다.

### 3.3 규칙을 실행 가능하게 만든다 (ast 정적 검사)

문서에 "다른 파일에서 Claude 클라이언트를 import 하지 않는다"고 쓰는 것과
CI 가 실패하는 것은 다르다. Day 1 에 정적 검사 2종을 만들었다.

**`test_import_boundary.py`** — 3개 경계 + 레이어 순서

```
규칙 1  경계 밖 클라이언트(mesh.llm.broker / boto3 / botocore)
        -> gatekeeper · audit · broker 만 허용
규칙 2  Chunk (원문)  -> 변환·판정·검증·읽기 모듈 9개만
규칙 3  Mapping       -> rehydrator · gatekeeper 등 5개만
레이어  L0~L7 단방향 의존. 새 모듈은 레이어 선언 강제
```

`TYPE_CHECKING` 블록은 런타임에 실행되지 않으므로 허용한다. `gatekeeper.py` 가
그 패턴으로 `BrokerClient` 를 타입 힌트로만 참조한다 (순환 import 회피).

**`test_log_extra_static.py`** — `extra=` 에 `logging` 예약어 사용 금지 (§5.2)

두 검사기 모두 **자기 자신을 검사한다.** 심은 위반을 잡는지 확인하는 테스트가 있다 —
아무것도 못 잡는 검사기는 무의미하다.

### 3.4 실패는 항상 닫는다 (fail closed)

`exceptions.py` 의 각 예외 docstring 에 **"귀결: 어느 안전한 상태로 가는가"** 를 적었다.
예외를 추가할 때 그 답이 없으면 fail-closed 설계가 깨진다.

| 예외 | 귀결 |
|---|---|
| `ExaoneUnavailable` | 등급 판정 맥락에서는 `Tier.SECRET` 간주 |
| `ExtractionFailed` | Agent 호출 없이 `answer_in_zone()` 폴백, **감사 레코드 없음** |
| `ValidationBlocked` | 전송 차단 + 폴백 |
| `BrokerError` | 폴백 + 사용자에게 품질 저하 고지 |
| `GatekeeperError` | **전파 → 500.** 코드 버그이므로 조용히 폴백하지 않는다 |
| `FixtureMissing` | **명시적 실패.** 조용히 기본값을 반환하면 리허설에서 누락을 못 잡는다 |
| `ConfigError` | 앱 시작 실패 |

예외 1건만 fail-open 이다: `AuditLog.mirror()`. 클라우드 미러링 실패가 질의를
죽이면 안 되고, 로컬 SQLite 가 원본이므로 증거가 사라지지 않는다.

### 3.5 개발자 실수를 필터로 막는다

`log.info("chunk: %s", chunk)` 를 쓰면 원문이 로그에 남는다. 규율로 막는 대신
`RedactingFilter` 가 금지 키 30개를 재귀적으로 치환한다.

```
원문       text · chunk_text · focus · summary · originals …
매핑       mapping · table
thinking   reasoning · reasoning_content     <- 실측된 유출 채널
자격증명   friendli_token · broker_api_key · aws_secret_access_key …
```

`reasoning*` 이 목록에 있는 이유가 실측 근거다. EXAONE 이 `enable_thinking:true`
일 때 사고 과정에 **원문을 그대로 인용**한다 (§4.1).

### 3.6 목업이 거짓말하지 않게 만든다

목업 모드가 검증을 우회하면 데모가 거짓이 된다. 그래서 목업은 **LLM 응답만** 재생한다.

| 컴포넌트 | 목업 모드 |
|---|---|
| `ExaoneClient` / `BrokerClient` | 픽스처 재생 |
| 조립 · 검증 · 감사 · 재수화 | **실제 코드** |
| UI 표시 | **"목업 모드" 배지** |

픽스처 키가 없으면 `FixtureMissing` 을 던진다. 조용히 기본값을 반환하면
리허설에서 누락을 발견할 수 없다.

---

## 4. 왜 이렇게 했나 — 실측이 설계를 바꾼 지점

Day 1 에 LLM 을 **6회** 호출했다 (EXAONE 3 · Bedrock 3). 전부 검증 목적이고,
그 결과가 설계를 세 곳 바꿨다.

### 4.1 `enable_thinking:false` 는 보안 요건이다 (성능은 부수 효과)

실측한 실제 EXAONE 응답:

```json
{"choices":[{"message":{
  "content": "{\"session_binding\": \"required\"}",
  "reasoning": "문서에 'H社 5G 코어망 요구사항 REQ-4412: 인증은 세션에 바인딩된
                EAP-AKA 방식이어야 하며' 라고 나와 있으므로 …",
  "reasoning_content": "원문 재인용: 세션 최대 유지시간은 8시간이다."
}}]}
```

**`reasoning` 에 원문이 그대로 있다.** 이건 유출 채널이다.

조치 3중:
1. `chat_template_kwargs={"enable_thinking": False}` 고정
2. `_extract_content()` 가 `reasoning*` 를 **파싱보다 먼저** 삭제
   (파싱 중 예외가 나면 예외 메시지에 원문이 실린다)
3. `RedactingFilter` 의 금지 키에 포함

부수 효과로 지연이 개선됐다. 사고 과정 토큰을 만들지 않기 때문이다.

| 설정 | 지연 |
|---|---|
| `enable_thinking: true` | 0.78 ~ 0.96s |
| **`false` (채택)** | **0.27 ~ 0.42s** |

### 4.2 모델이 JSON 전체를 만들면 어휘 사전을 벗어난다

설계 문서 §3.4 는 "EXAONE 에 스키마와 어휘 사전을 주고 JSON 전체를 만들게 한다"였다.
실제로 해보니 **첫 시도에서 어휘 사전 밖의 필드를 3개** 만들었다.

```json
"facts": {
  "auth_mechanism_class": "challenge_response",   // OK
  "session_binding": "required",                  // OK
  "max_session_duration": "8 hours",              // ✗ 미등록 필드 + 자유 문자열
  "credential_reuse": "prohibited"                // ✗ 미등록 필드 + 자유 문자열
}
```

슬롯별로 허용값 목록을 명시하는 방식으로 바꾸니 **3회 반복 모두 완전히 in-vocab** 이었고,
함정으로 심은 `계약금액 12억원`·`담당 김철수` 는 **슬롯이 없으므로 나올 자리가 없었다.**

→ 설계 변경: 페이로드를 **모델이 만들지 않고 코드가 조립한다.**
`extractor.assemble()` 이 `schema.slots` 를 순회하고, 모델이 반환한 미등록 키는
검증 실패가 아니라 **조립 단계에서 버려진다(drop)**.

루프의 방향이 보안 속성을 결정한다:

```python
❌ for key in raw:                 # "모델이 준 것을 검사해서 걸러낸다"
       if key in schema: ...       #  검사를 잊으면 유출

✅ for slot in schema.slots:       # "스키마가 요구하는 것만 찾아 쓴다"
       if slot.name in raw: ...    #  잊을 검사가 없다
```

Day 1 에는 `vocab.json` 과 `SlotDef` 타입만 확정했고, `extractor.py` 구현은 Day 2 다.

### 4.3 임시 자격증명과 모델 접근 권한

| 확인 | 결과 | 조치 |
|---|---|---|
| `claude-sonnet-5` | 이 계정에서 `AccessDeniedException` | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` 로 확정 |
| 추론 프로파일 | 모든 Claude 가 `us.` 접두사 필수 | `.env.example` 에 반영 |
| 자격증명 | 임시 STS (`ASIA…` + 세션 토큰) | 사용자 확인: 해커톤 기간 내 만료 없음. `broker` 모드는 여전히 유효한 대안 |
| 리전 | us-east-1 외 대부분 Deny | 전 스택 us-east-1 |
| CDK | 미부트스트랩 | Day 0 작업. `direct` 모드로 데모가 돌므로 필수는 아니다 |

### 4.4 지연 예산 수정 — 출력 토큰이 지배한다

설계의 2.17초는 **4토큰 응답** 기준이었다.

| 측정 | 입력 | 출력 | 지연 |
|---|---|---|---|
| ping | 12 tok | 4 tok | 2.30s |
| **시나리오 1 실제 답변** | 460 tok | **513 tok** | **9.23s** |

→ `u3-agent-mesh/nfr-requirements` 의 `send` 목표를 **4초 → 12초**로 수정.
합계 약 18초로 30초 상한 대비 여유 12초. 여유가 줄었으므로 재시도를 남발할 수 없고,
"타임아웃은 재시도하지 않는다"는 정책이 여기서도 옳다.

`preflight` 가 이 경고를 매번 출력한다 — 4토큰 응답으로 지연을 판단하지 않게.

---

## 5. 테스트가 잡은 결함 4건

테스트를 구현과 함께 쓴 덕에 나왔다. 넷 다 **조용히 실패하는** 종류다.

### 5.1 `Tier` 비교가 알파벳 순이었다 (조용한 유출)

```python
@total_ordering
class Tier(StrEnum):
    def __lt__(self, other): return self.rank < other.rank
```

`functools.total_ordering` 은 `StrEnum` 에 `__gt__` 를 주입하지 못한다. 실측:

```
__lt__ in T.__dict__  : True
__gt__ in T.__dict__  : False
T.__gt__ is str.__gt__: True      <- str 의 알파벳 비교가 남았다
```

`max()` 는 `__gt__` 를 쓴다. 그래서:

```
max(Tier.INTERNAL, Tier.OPEN)  ->  Tier.OPEN     ⚠️
```

FR-11(등급 상향)이 `max(tiers)` 한 줄로 표현되므로 **사내 문서가 공개로 판정되어
원문이 그대로 나간다.** 예외도 로그도 없다.

조치: 4개 비교 메서드를 명시적으로 정의. `test_tier_order.py` 가 3개 등급의
**모든 순열**에 대해 `max()` 를 검사한다. 보안 결정적 비교를 데코레이터의
미묘함에 의존하지 않는다.

### 5.2 로그 한 줄이 요청을 죽인다

`logging` 은 `extra` 의 키가 `LogRecord` 속성과 겹치면 `KeyError` 를 던진다.
`name`, `module`, `args`, `msg`, `levelname` 이 전부 예약어다.

`exaone.py` 의 재시도 경고에서 터졌다. **실패 경로에서만 실행되는 로그**라
정상 개발 중에는 발견되지 않는다.

조치: `config.log_extra()` 헬퍼(예약어에 `x_` 접두사) + `test_log_extra_static.py`
의 ast 정적 검사. 리뷰 매너가 아니라 CI 가 잡는다.

### 5.3 차단 목록과 가명화 목록을 섞으면 가명화 경로가 죽는다 🔴

`banned.json` v1.0.0 에 사내 프로젝트명(`atlas-ml`)과 시스템명(`Nova 게이트웨이`)을
넣었다. **그 파일 자체 주석이 "가명화 대상 목록과 다르다"고 명시한 것을 위반했다.**

코퍼스 정합성 검사가 잡았다:

| 문서 | 정답 | 차단 히트 | 규칙 예측 |
|---|---|---|---|
| `kim/docs/auth-design.md` | internal | 1 (`Nova 게이트웨이`) | **secret** ✗ |
| `choi/docs/auth-review.md` | internal | 1 | **secret** ✗ |
| `choi/docs/release-checklist.md` | internal | 1 | **secret** ✗ |
| `park/scripts/preprocess_v3.py` | internal | 2 (`atlas-ml`, `atlas_ml`) | **secret** ✗ |
| `park/runs/.../train.log` | internal | 1 | **secret** ✗ |

정확도 **6/11 = 55%**. 상향 오류라 유출은 없지만 **시나리오 2 의 가명화 경로가
아예 실행되지 않는다** — 모든 사내 문서가 기밀로 처리되어 답변이 무뎌진다.

두 목록의 성격이 정반대다:

| | 성격 | 대상 | 결과 |
|---|---|---|---|
| `banned.json` | **차단** | 고객사명 · 계약/요구사항 번호 · 금액 | SECRET 상향 + 전송 차단 |
| `pseudonyms.json` | **치환** | 사내 프로젝트명 · 시스템명 · 인명 | 치환하고 경계를 넘게 허용 |

조치 4단:
1. `data/pseudonyms.json` 신설 (`targets` 4카테고리 + `technical_terms` 45개)
2. `schemas.PseudonymTargets` — `all_literals()` 가 **긴 리터럴부터** 반환
   (`atlas-ml` vs `atlas-ml-core` 부분 치환 사고 방지)
3. **`DataBundle._check_lists_are_disjoint()`** — 로드 시점에 겹침을 `ConfigError` 로
   거부. 재발 방지를 코드로 강제
4. 테스트 2종 추가

**분리 후 실측 (규칙 기반만, LLM 호출 0회)**

```
문서 11건 · 정확도 11/11 = 100%   (목표 >= 90%)
기밀 재현율 3/3 = 100%            (목표 100%)
함정 문서 1/1 탐지                경로=internal, 헤더=없음, 금칙어 5건으로만 잡힘
internal 문서 차단 히트 0건        가명화 경로 정상 작동
```

### 5.4 의존성 취약점 8건

`make audit` 이 설계 시점에 고른 버전에서 찾았다.

| 패키지 | 초기 | 취약점 | 원인 |
|---|---|---|---|
| `starlette` | 0.41.3 | **7건** | `fastapi==0.115.6` 의 전이 의존 |
| `pytest` | 8.3.4 | 1건 | 직접 지정 |

**설계 문서에 버전을 적을 때 `pip-audit` 을 돌리지 않은 결과다.**

조치: 버전 상향 + `starlette` **직접 고정**(전이로 내려가지 않게). 재검사 0건.

부수 교훈: 버전을 추측으로 적으면 안 된다. `hypothesis==6.143.4` 는
**존재하지 않는 버전**이었고 `uv sync` 가 거부했다. 실제 해석 결과를 확인해 고정했다.

---

## 6. 테스트 전략

### 6.1 382개의 구성

| 파일 | 개수 | 무엇을 보증하나 |
|---|---:|---|
| `test_schemas.py` | 60 | 타입 계약. 자유 문자열 슬롯 금지, 목록 분리, 경로 필드 부재 |
| `test_tier_order.py` | 24 | **`max()` 정확성** (모든 순열) |
| `test_mapping_not_serializable.py` | 13 | 매핑 직렬화 차단 3종 + 값 은닉 |
| `test_config.py` | 66 | 경로 탈출 12케이스, 로그 리댁션, fail-fast 검증 |
| `test_exaone.py` | 22 | **`reasoning*` 삭제**, 재시도 정책, 목업 왕복 |
| `test_broker.py` | 24 | 3모드, `revalidated` 없으면 거부, vocab drift 경고 |
| `test_gatekeeper_contract.py` | 44 | 시그니처 동결, 전제조건, `EnvelopeCache` |
| `test_import_boundary.py` | 12 | **3개 경계 + 레이어 순서** |
| `test_log_extra_static.py` | 12 | 예약어 충돌 |
| `test_store_session.py` | 47 | 신선도 3단, 승인 QA 등급 보존, 지식 격리 |
| `test_api_contract.py` | 58 | API 계약 + **픽스처 역파싱** + 유출 방어 |

### 6.2 테스트 종류별 목적

**계약 동결 테스트** — 변경을 실패시킨다

```python
EXPECTED_SIGNATURES = {
    "rehydrate": (["self", "resp", "mapping"], ["persona", "chunks"]),
    ...
}
```
이 테스트가 실패하면 U3 의 코드가 깨진다는 뜻이다.

**정적 검사 테스트** — 규칙을 실행 가능하게 만든다

`ast` 로 소스를 파싱해 import 경계·예약어 충돌을 잡는다. 각 검사기는
**자기 자신을 검사하는 테스트**를 함께 갖는다.

**불변식 테스트** — 순열·경계값을 전수 검사

```python
@pytest.mark.parametrize("order", list(permutations(Tier)))
def test_max_over_all_orderings_is_secret(order):
    assert max(order) is Tier.SECRET
```

**왕복 테스트** — 녹화한 것이 재생되는지

목업 픽스처를 녹화하고 재생해 같은 결과가 나오는지 확인한다.
이걸 안 하면 데모 당일 키 불일치로 실패한다.

**픽스처 정합성 테스트** — 손으로 쓴 JSON 을 쓰지 않는다

`data/fixtures/api/*.json` 을 **실제 pydantic 모델로 생성**하고,
테스트가 그 JSON 을 모델로 역파싱해 왕복 항등을 확인한다.
C 가 Day 4 에 화면을 다시 만들지 않게 하는 장치다.

### 6.3 테스트가 잡은 "개념 혼동" 하나

`test_api_contract.py` 를 처음 쓸 때 "응답에 `REQ-4412` 가 있으면 유출"로 검사했다.
2건이 실패했고, 그게 **테스트가 틀린 경우**였다.

"유출"의 의미가 위치에 따라 다르다:

| 위치 | 원문·식별자 | 근거 |
|---|---|---|
| 페이로드 (경계를 넘는 것) | **하나도 없어야 한다** | FR-03 |
| 재수화된 답변 (신뢰 구역에 남는 것) | **있어야 한다** | FR-13 — 재수화의 목적 자체 |
| 감사 검색어 | 있는 게 정상 | 사용자가 원문 문구를 입력한다 |
| 어디에도 | 경로 · 매핑 · `reasoning` 은 금지 | FR-43, BR-G-09, FR-14 |

검사를 `NEVER_ANYWHERE` 와 `NEVER_IN_PAYLOAD` 로 나누고, 페이로드만 추출하는
`_payloads_in()` 을 만들었다. 그 추출기도 자기 검사 테스트를 갖는다.

이 혼동을 코드에 남기면 "재수화가 안 되는 게 정상"이라는 잘못된 전제가 굳는다.

---

## 7. 범위 — 무엇을 안 했나

Day 1 에 **의도적으로 하지 않은 것**들이다. 각각 언제 할지가 정해져 있다.

### 7.1 Day 2 (A) 로 미룬 것 — `NotImplementedError`

| 대상 | 이유 |
|---|---|
| `Gatekeeper.classify` | `classifier.py` 위임. Day 2 |
| `Gatekeeper.plan_calls` | 분해/상향 판정 |
| `Gatekeeper.to_payload` | `extractor` / `pseudonymizer` 위임 |
| `Gatekeeper.validate` | `validator.py` 위임 |
| `Gatekeeper.preview` | |
| `Gatekeeper.ask_agent` (본문) | 전제조건 검사는 구현됨 |
| `Gatekeeper.rehydrate` | `rehydrator.py` 위임 |
| `Gatekeeper.answer_in_zone` | 폴백 |

`classifier.py` · `validator.py` · `extractor.py` · `pseudonymizer.py` ·
`rehydrator.py` · `audit.py` 는 **파일 자체가 없다.** Day 2 에 만든다.

### 7.2 Day 3 (B) 로 미룬 것

| 대상 | 이유 |
|---|---|
| `store.read()` | 파일 읽기 + 프런트매터 파싱 |
| `store.select_paths()` | EXAONE 경로 선택 |
| `store.list_agents()` | 목록 구성 + 요약 캐시 |
| `agent.py` · `orchestrator.py` · `inbox.py` · `main.py` | 파일 없음 |

### 7.3 Day 4 (C) 로 미룬 것

`src/mesh/web/` 이 비어 있다. `scripts/lint-web.sh` 가 "파일이 없으면 조용히 통과"
하도록 만들어 두었다.

### 7.4 Day 5 · U5 로 미룬 것

`infra/` 가 비어 있다. CDK 미부트스트랩. `direct` 모드로 데모가 돌기 때문에
필수 경로가 아니다 (FR-49).

### 7.5 프로젝트 범위 밖 (설계 §10.1)

- 실제 시스템 연동 (Confluence · Jira · Git)
- 로그인 · 권한 관리 — **실배포 시 원본 시스템 권한 승계가 최우선 요건**
- 벡터 DB · 임베딩
- 파일시스템 감시 데몬 — `mtime` 재로드로 대체
- 끝난 프로젝트를 대리하는 에이전트

---

## 8. 현재 검증 상태

```bash
make test           # 382 passed
make lint           # ruff check + format + lint-web
make audit          # No known vulnerabilities
make preflight      # 검사 27건 · 실패 0 · 경고 2
```

### 8.1 게이트 현황

| Gate | 기준 | 상태 |
|---|---|---|
| **SG1** | `.gitignore` 가 자격증명 3종 커버 | ✅ 커밋 `c91c1b8` 검증 |
| **G1** | `schemas.py` + `vocab.json` 동결 | ✅ 계약 6종 |
| **G2** | 기밀 재현율 100%, 정확도 ≥90% | ⏳ 규칙만으로 11/11 예비 통과. `make eval-classify` 는 Day 2 |
| SG6 | 의존성 취약점 0건 | ✅ 8건 → 0건 |
| SG7 | import 경계 3개 규칙 | ✅ |
| SG8 | `logging` 예약어 충돌 0건 | ✅ |
| SG9 | 차단 ∩ 가명화 = ∅ | ✅ 로드 시점 강제 |

### 8.2 예상된 경고 2건

| 경고 | 이유 | 조치 |
|---|---|---|
| 신뢰 경계 시뮬레이션 | `TRUSTED_ZONE_LLM_BASE_URL` 이 공개 SaaS | **사용자 확인: 전제로 수용.** 도구가 매번 고지하도록 남김 |
| CDK 미부트스트랩 | Day 0 작업 미완 | `direct` 모드로 데모가 돈다. U5 배포 시 `make bootstrap` |

### 8.3 코드 규모

| | 줄 | 파일 |
|---|---:|---:|
| `src/mesh` | 3,130 | 11 |
| `tests` | 3,440 | 12 |
| `data/corpus` | 665 | 11 |

테스트가 소스보다 많다. 이 프로젝트에서 **유출 부재를 증명하는 것이 기능만큼
중요**하기 때문이다.

---

## 9. 다른 컴포터로 옮길 때 (NFR-PO)

```bash
git clone <repo> && cd prompthon
make setup                  # uv sync + .env 생성 + 디렉터리
# .env 에 FRIENDLI_TOKEN 기입
make preflight              # 27개 항목 진단 — 사람이 읽을 조치 방법 포함
make test
make run                    # http://127.0.0.1:8080
```

네트워크가 없으면:
```bash
EXAONE_MODE=mock AGENT_TRANSPORT=mock make run
```

필요한 것: `uv`, git, (CDK 배포 시에만) Node. **`aws` CLI 는 필요 없다** —
현재 컴퓨터의 CLI 가 `bedrock` 서비스를 모르는 구버전이라 boto3 로 대체했다.

이식성 조치:
- 절대 경로 금지 (`MESH_DATA_ROOT` 상대 경로). `safe_resolve()` 가 절대 경로를 **거부**한다
- `uv` 로 Python 3.12 고정 + `uv.lock` 커밋
- `npx aws-cdk@2` (전역 설치 없음)
- 샘플 코퍼스·세션·픽스처를 저장소에 포함
- `MESH_DEMO_NOW` 로 세션 신선도 고정 (시연 날짜가 바뀌어도 재현)

---

## 10. Day 2 착수 지점

```
U1 Step 9~18 (소유 A)

  Step 9   classifier.py 규칙 기반        <- 여기부터
  Step 10  classifier.py EXAONE 보조
  Step 11  validator.py 6단계 (순수 함수)  <- EXAONE 없이 개발·테스트 가능
  Step 12  extractor.py 슬롯 채우기 + 조립  <- 프로젝트의 심장
  Step 13  pseudonymizer.py + rehydrator.py
  Step 14  audit.py SQLite + 원문 검색
  Step 15  gatekeeper.py 구현 채우기
  Step 16  PBT (PB-1~PB-10)
  Step 18  게이트 G2: make eval-classify
```

작업 순서가 계획대로다: **규칙 판정 → 검증기 → 추출기.**
규칙만으로 이미 정확도 100% 가 나왔으니 G2 는 무리 없어 보이고,
검증기는 순수 함수라 EXAONE 없이 병렬 개발이 된다.

참조: `aidlc-docs/construction/plans/u1-gatekeeper-core-code-generation-plan.md`

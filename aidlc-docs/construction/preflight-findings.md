# Preflight Findings — 착수 전 실측 결과

> 이 문서는 설계 이전에 **실제로 호출해서 확인한 사실**만 담는다.
> 측정 시각: 2026-08-19T10:12:00Z · 측정 위치: 개발자 노트북 (macOS / darwin)
> `hackathon-mvp-design.md` §7.2 Day 0 체크리스트에 해당하며, **이미 완료되었다.**

설계 문서의 추정과 실측이 다른 항목이 4개 있다. 각각 설계에 반영했다.

---

## 1. EXAONE (Friendli dedicated endpoint)

| 항목 | 실측값 |
|---|---|
| 엔드포인트 | `https://api.friendli.ai/dedicated/v1/chat/completions` |
| 프로토콜 | **OpenAI 호환** (`/chat/completions`) — 별도 어댑터 불필요 |
| 모델 ID | `depe675tjc2rcpo` (K-EXAONE 2.0 Dedicated) |
| 인증 | `Authorization: Bearer <FRIENDLI_TOKEN>` |
| 왕복 지연 | **0.78s** (13 in / 91 out tokens) · **0.96s** (235 in / 204 out) |
| `response_format: {"type":"json_object"}` | **지원됨** — JSON 파싱 실패율을 크게 낮춘다 |
| `chat_template_kwargs.enable_thinking` | 지원됨. `false`로 두면 `reasoning` 필드가 사라진다 |

### 발견 1 — thinking 출력이 원문을 담을 수 있다 (보안)

`enable_thinking: true`일 때 응답에 `reasoning` / `reasoning_content` 필드가 함께 온다.
이 필드는 모델의 사고 과정이므로 **원문 문장을 그대로 포함할 수 있다.**

> **설계 반영**: 등급 판정·구조 추출·가명화 등 **모든 Gatekeeper 경로의 EXAONE 호출은
> `enable_thinking: false`** 로 고정한다. 그리고 클라이언트 계층(`llm/exaone.py`)에서
> `reasoning*` 키를 **응답 파싱 전에 삭제**한다. 감사 로그에도 남기지 않는다.
> 지연 시간도 함께 줄어든다 (91 → 4 completion tokens 수준).

### 발견 2 — 전체 JSON 생성 방식은 어휘 사전을 벗어난다 (핵심)

`hackathon-mvp-design.md` §3.4는 "EXAONE에 스키마와 어휘 사전을 주고 JSON 전체를 만들게 한다"로
설계돼 있다. **실제로 해보니 첫 시도에서 어휘 사전 밖의 필드를 3개 만들어냈다.**

시나리오 1의 실제 원문을 넣고 §3.4의 프롬프트 방식을 그대로 재현한 결과:

```json
"facts": {
  "auth_mechanism_class": "challenge_response",   // OK
  "session_binding": "required",                  // OK
  "renewal_mode": "none",                         // OK
  "max_session_duration": "8 hours",              // ✗ 사전에 없는 필드 + 자유 문자열
  "credential_reuse": "prohibited"                // ✗ 사전에 없는 필드 + 자유 문자열
}
```

`ref` 값도 프롬프트에 준 `"REQ_A|COMP_B"` 문자열을 그대로 복사했다.

즉 §3.5의 검증기 ③은 **선택이 아니라 필수**이고, 그것만으로는 부족하다.
검증기가 잡아주긴 하지만 매번 전송이 차단되면 데모가 성립하지 않는다.

### 발견 3 — 슬롯 채우기(slot filling) 방식은 결정적으로 동작한다

같은 원문에 **필드별로 허용값 목록을 명시하고 `__unknown__` 탈출구를 주는** 방식으로 바꾸자
**3회 반복 모두 완전히 동일하고, 전부 어휘 사전 안**이었다.

프롬프트에 넣은 슬롯 정의:
```
auth_mechanism_class: ["password","challenge_response","certificate","biometric","token_bearer"]
session_binding: ["required","optional","none"]
credential_reuse_allowed: [true,false]
max_session_hours: integer 0..8760
renewal_mode: ["explicit","background_silent","none"]
```

원문에 함정으로 심어둔 `계약금액 12억원`, `담당 김철수`를 포함시켰고, 결과:
```json
{"auth_mechanism_class":"challenge_response","session_binding":"required",
 "credential_reuse_allowed":"false","max_session_hours":8,"renewal_mode":"__unknown__"}
```

- 금액·인명은 **슬롯이 없으므로 나올 자리가 아예 없었다** — 화이트리스트의 실효성 확인
- 원문에 없는 `renewal_mode`는 정확히 `__unknown__`으로 반환
- 유일한 흠: `credential_reuse_allowed`가 boolean 대신 문자열 `"false"` → 앱에서 타입 강제 필요

> **설계 반영 (설계 변경)**: 페이로드를 **모델이 만들지 않고 코드가 조립한다.**
> `extractor.py`가 task 스키마에 선언된 슬롯을 순회하며 EXAONE에게 값 하나씩 고르게 하고,
> **화이트리스트 키만 골라 페이로드를 새로 만든다.** 모델이 반환한 미등록 키는
> 검증 실패가 아니라 **조립 단계에서 그냥 버린다(drop)**.
> 이렇게 하면 어휘 사전 이탈이 "검증으로 잡는 오류"에서 "구조적으로 불가능한 것"으로 바뀐다.
> 검증기 6단계는 그대로 유지한다 (2중 안전망).

---

## 2. Claude (AWS Bedrock)

| 항목 | 실측값 |
|---|---|
| 리전 | `us-east-1` (계정 정책이 다른 리전을 Deny) |
| 계정 | `891401657794` · 역할 `WSParticipantRole/Participant` (워크샵 계정) |
| 자격증명 종류 | **임시 STS 자격증명** (`ASIA...` + `AWS_SESSION_TOKEN`) → **만료된다** |
| 호출 API | `bedrock-runtime.Converse` (권장) |

### 발견 4 — 설계 문서의 `claude-sonnet-5`는 이 계정에서 쓸 수 없다

`list_foundation_models`에는 `anthropic.claude-sonnet-5`가 보이지만, 실제 호출하면:

```
AccessDeniedException: anthropic.claude-sonnet-5 is not available for this account.
```

또한 모든 Claude 모델의 `inferenceTypesSupported`가 `["INFERENCE_PROFILE"]`이므로
**`anthropic.` 접두사 그대로는 호출할 수 없고 `us.` / `global.` 추론 프로파일 ID가 필요하다.**

실제로 호출에 성공한 모델 (측정된 왕복 지연 — **4 토큰 응답 기준**):

| 모델 ID | 지연 | 비고 |
|---|---|---|
| `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | 2.17s | **기본값으로 채택** |
| `us.anthropic.claude-sonnet-4-6` | 1.28s | 대안 |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | 0.92s | 초안 생성·저비용 경로 |
| `us.anthropic.claude-opus-4-5-20251101-v1:0` | 1.69s | 품질 비교용 |
| `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | 3.01s | us. 쪽이 빠름 |

호출 실패: `us.anthropic.claude-3-haiku-20240307-v1:0` (ResourceNotFoundException)

> **설계 반영**: `AGENT_MODEL_ID` 기본값을 `us.anthropic.claude-sonnet-4-5-20250929-v1:0`로 두고
> **환경변수로 교체 가능**하게 한다. 설계 문서의 `claude-sonnet-5`는 문서상 표기로만 남긴다.
> 에스컬레이션 초안 생성은 haiku-4-5로 돌려 비용과 지연을 줄인다.

### 발견 4-b — 실제 답변 지연은 9.2초다 (지연 예산 수정)

Day 1 구현 후 시나리오 1의 실제 페이로드로 재측정했다.

| 측정 | 입력 | 출력 | 지연 |
|---|---|---|---|
| 초기 (`"Reply with exactly: OK"`) | 12 tok | 4 tok | **2.17s** |
| **실제 (시나리오 1 구조 페이로드)** | 460 tok | **513 tok** | **9.23s** |

출력 토큰이 지연을 지배한다. 답변·이유·완화안 3개를 생성하면 500 토큰이 나온다.

> **설계 반영 (지연 예산 수정)**: `u3/nfr-requirements.md` §1의 `send` 목표를
> **< 4s 에서 < 12s 로** 고친다. 전체 예산은 여전히 안전하다.
>
> | 단계 | 수정 전 | 수정 후 | 근거 |
> |---|---|---|---|
> | `prepare` (1명) | < 6s | < 6s | EXAONE 0.42s x 3회 |
> | `send` (1명) | < 4s | **< 12s** | 실측 9.23s |
> | `send` (2명 병렬) | < 5s | **< 14s** | 병렬이므로 +2s |
> | **합계** | ~10s | **~18s** | 상한 30s 대비 여유 12s |
>
> `AGENT_TIMEOUT_SECONDS=25` 는 그대로 둔다 (9.2s의 2.7배 여유).
> 2명 병렬 + 에스컬레이션 초안(haiku)까지 더해도 30초 안에 들어온다.
> 다만 **여유가 12초로 줄었으므로** 재시도를 남발할 수 없다 —
> 타임아웃을 재시도하지 않는 정책(§1 발견 3의 재시도 규칙)이 여기서도 옳다.

### 발견 4-c — EXAONE 이 `enable_thinking=False` 로 더 빨라졌다

| 설정 | 지연 |
|---|---|
| `enable_thinking: true` (초기 측정) | 0.78 ~ 0.96s |
| **`enable_thinking: false` (채택)** | **0.42s** |

원문 유출 채널을 막은 것이 성능도 개선했다. 사고 과정 토큰을 생성하지 않기 때문이다.

### 발견 5 — 임시 자격증명은 만료된다 → 클라우드 브로커의 근거

노트북의 `.kiro/.env` 자격증명은 STS 임시 토큰이다. 시연 중 만료되면 Agent 호출이 전부 죽는다.
게다가 사용자가 **다른 컴퓨터를 쓸 수도 있다**고 했으므로, 노트북에 자격증명을 심는 방식은
이식성도 나쁘다.

> **설계 반영**: Bedrock 호출을 **Lambda(실행 역할 사용)로 옮긴다.** Lambda의 IAM 실행 역할은
> 만료되지 않고, 노트북에는 브로커 API 키 하나만 두면 된다. 컴퓨터를 바꿔도 API 키만 옮기면 된다.
> 동시에 `AGENT_TRANSPORT=direct`로 두면 노트북에서 Bedrock을 직접 부르는 로컬 전용 모드로도
> 돈다 (CDK가 아직 안 올라갔을 때의 폴백).

---

## 3. AWS 계정 제약 (CDK 배포 가능성)

| 확인 항목 | 결과 |
|---|---|
| CDK 부트스트랩 | **아직 안 됨** (`CDKToolkit` 스택 없음) → `cdk bootstrap` 필요 |
| 역할 정책 | `PowerUserAccess`, `workshop-iam-1`, `ws-default-policy`, `landing-console-0` |
| `iam:CreateRole` / `AttachRolePolicy` / `CreatePolicy` | **허용** (`workshop-iam-1`) → CDK 부트스트랩 가능 |
| `iam:PassRole` | lambda / apigateway / cloudformation / dynamodb / bedrock 등으로 허용 |
| `bedrock:Invoke*` | 허용 |
| 리전 | **us-east-1 외 대부분 Deny** → 모든 스택을 us-east-1에 배포 |
| 보호된 역할 | `WSParticipantRole`, `WSOpsRole`, `*OrganizationAccountAccessRole*` 변경 Deny |
| 접근 확인된 서비스 | lambda, dynamodb, apigateway, s3, iam, secretsmanager, cloudformation |

> **설계 반영**: `infra/` CDK 앱은 리전을 `us-east-1`로 고정한다.
> 부트스트랩은 Day 0 작업으로 못 박는다 (`npx aws-cdk@2 bootstrap aws://891401657794/us-east-1`).
> 워크샵 계정은 회수될 수 있으므로 CDK 스택은 **데모의 필수 경로가 아니어야 한다** —
> `AGENT_TRANSPORT=direct` 로컬 모드가 항상 동작해야 한다.

---

## 4. 로컬 개발 환경

| 항목 | 실측값 | 조치 |
|---|---|---|
| Python | 3.9.12 (anaconda) | **부족.** FastAPI/pydantic v2에 3.11+ 권장 → `uv`로 3.12 고정 |
| `uv` | 설치됨 (`/opt/homebrew/bin/uv`) | 패키지·파이썬 버전 관리에 사용 |
| Node | v26.7.0, npm 11.19.0 | CDK CLI를 `npx aws-cdk@2`로 실행 |
| `cdk` 전역 설치 | 없음 | 전역 설치하지 않고 `npx` 사용 (다른 컴퓨터 이식성) |
| `aws` CLI | 있으나 **`bedrock` 서비스를 모르는 구버전** | Bedrock 확인은 boto3로. CLI 버전 의존 금지 |
| git | **저장소 아님, `.gitignore` 없음** | 최우선 조치 (아래) |

### 발견 6 — 워크스페이스에 자격증명이 평문으로 있고 gitignore가 없다 (SECURITY-12)

- `.kiro/opencode.jsonc` → Friendli API 키 평문
- `.kiro/.env` → AWS 액세스 키·시크릿·세션 토큰 평문
- `git init`이 아직 안 됐고 `.gitignore`가 없다

> **설계 반영 (U1 코드 생성 계획 Step 1, blocking)**:
> `git init` 전에 `.gitignore`를 먼저 만든다 (`.kiro/.env`, `.kiro/opencode.jsonc`, `.env`, `data/sessions/local*`).
> Friendli 키는 `opencode.jsonc`에서 빼내 `FRIENDLI_TOKEN` 환경변수로 읽는다.
> 해커톤 종료 후 Friendli 키를 폐기·재발급한다.

---

## 5. 설계 문서 대비 변경 요약

| # | 설계 문서 서술 | 실측 후 변경 |
|---|---|---|
| 1 | `claude-sonnet-5` | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (계정 접근 불가) |
| 2 | EXAONE이 구조 JSON 전체를 생성 | **슬롯 채우기 + 코드 조립** (어휘 이탈 실측 확인) |
| 3 | Agent 호출을 노트북에서 직접 | **Lambda 브로커 경유** (STS 만료·이식성) + direct 폴백 유지 |
| 4 | EXAONE thinking 미언급 | `enable_thinking:false` 고정 + `reasoning*` 필드 삭제 |
| 5 | §4.7 청크 + numpy 코사인 유사도 | **사용하지 않음.** `scenarios.md` §0의 세션+파일 직접 읽기 채택 |
| 6 | "사내망 서버의 EXAONE" | 실제로는 Friendli SaaS 엔드포인트. **신뢰 경계는 시뮬레이션**이며 문서·데모에서 그렇게 밝힌다 (§신뢰 경계 참조) |

### 신뢰 경계에 대한 정직한 서술

이 프로젝트의 가치 주장은 "원문이 사내망을 벗어나지 않는다"에 있다.
그런데 이번 해커톤의 EXAONE은 `api.friendli.ai`, 즉 **공개 SaaS 엔드포인트**다.
따라서 지금 구현되는 것은 다음과 같다.

- **아키텍처상 보장되는 것**: 원문은 `TRUSTED_ZONE_LLM` 엔드포인트 **하나**에만 전달되고,
  그 밖의 어떤 호출에도 원문이 실리지 않는다. 이 불변식은 코드·검증기·감사 로그로 증명된다.
- **이번 해커톤에서 보장되지 않는 것**: 그 엔드포인트 자체가 사내망 안에 있다는 것.
  Friendli는 사외다.
- **실배포 시 필요한 변경**: `TRUSTED_ZONE_LLM_BASE_URL` 환경변수 하나를 사내 서빙
  엔드포인트로 바꾸면 된다. OpenAI 호환이면 코드 변경이 없다.

데모에서 이 점을 먼저 밝히는 것이 낫다. 심사자가 알아채고 지적하는 것보다,
"경계의 위치는 설정값 하나이고 경계를 지키는 구조가 우리가 만든 것"이라고
먼저 설명하는 쪽이 주장을 강하게 만든다.

---

## 6. 재실행 방법

이 문서의 모든 수치는 `scripts/preflight.py`로 재현할 수 있다 (U1에서 구현).
다른 컴퓨터로 옮겼을 때 **가장 먼저 실행할 스크립트**다.

```bash
make preflight     # EXAONE 왕복 · Bedrock 모델 접근 · 리전 · CDK 부트스트랩 여부 확인
```

---

## 7. Day 1 구현 중 발견 (추가 실측)

### 발견 7 — `Tier` 비교가 알파벳 순이었다 (조용한 유출)

`schemas.py` 작성 후 `tests/unit/test_tier_order.py` 가 즉시 잡았다.

```python
@total_ordering
class Tier(StrEnum):
    def __lt__(self, other): return self.rank < other.rank
```

`functools.total_ordering` 은 **`__gt__` 를 주입하지 못한다.** 실측 확인:

```
__lt__ in T.__dict__  : True
__gt__ in T.__dict__  : False
T.__gt__ is str.__gt__: True      <- str 의 알파벳 비교가 남았다
```

`max()` 는 `__gt__` 를 쓴다. 그래서:

```
max(Tier.INTERNAL, Tier.OPEN)  ->  Tier.OPEN     ⚠️ 틀렸다
```

FR-11(동원된 지식 중 최고 등급이 호출 전체에 걸린다)이 `max(tiers)` 한 줄로
표현되므로, **사내 문서가 공개로 판정되어 원문이 그대로 나간다.**
예외도 없고 로그도 없다 — 조용한 유출이다.

> **조치**: `__lt__` `__le__` `__gt__` `__ge__` 4개를 **명시적으로** 정의했다.
> 보안 결정적 비교를 데코레이터의 미묘함에 의존하지 않는다.
> `test_tier_order.py` 가 3개 등급의 **모든 순열**에 대해 `max()` 를 검사한다.

### 발견 8 — `extra={"name": ...}` 가 로그 한 줄로 요청을 죽인다

`logging` 은 `extra` 의 키가 `LogRecord` 속성과 겹치면 `KeyError` 를 던진다.
`name`, `module`, `args`, `msg`, `levelname` 이 전부 예약어다.

`exaone.py` 의 재시도 경고에서 터졌고 `test_exaone.py` 가 잡았다.
**실패 경로에서만 실행되는 로그**라 개발 중에는 발견되지 않는다.

> **조치**: `config.log_extra()` 헬퍼(예약어에 `x_` 접두사) +
> `tests/unit/test_log_extra_static.py` 의 ast 정적 검사.
> 리뷰 매너가 아니라 CI 가 잡는다.

### 발견 9 — 차단 목록과 가명화 목록을 섞으면 가명화 경로가 죽는다 🔴

`banned.json` v1.0.0 에 사내 프로젝트명(`atlas-ml`)과 시스템명(`Nova 게이트웨이`)을
넣었다. `banned.json` 자체의 주석이 "가명화 대상 목록과 다르다"고 명시했는데 위반했다.

코퍼스 정합성 검사가 잡았다:

| 문서 | 정답 | 차단 히트 | 규칙 예측 |
|---|---|---|---|
| `kim/docs/auth-design.md` | internal | 1 (`Nova 게이트웨이`) | **secret** ✗ |
| `choi/docs/auth-review.md` | internal | 1 | **secret** ✗ |
| `choi/docs/release-checklist.md` | internal | 1 | **secret** ✗ |
| `park/scripts/preprocess_v3.py` | internal | 2 (`atlas-ml`, `atlas_ml`) | **secret** ✗ |
| `park/runs/.../train.log` | internal | 1 | **secret** ✗ |

결과: **정확도 6/11 = 55%** (목표 90%). 상향 오류라 유출은 없지만
**시나리오 2의 가명화 경로가 아예 실행되지 않는다** — 모든 사내 문서가
기밀로 처리되어 구조 추출로 가고, 답변이 무뎌진다.

두 목록의 성격이 정반대다:

| | 성격 | 대상 | 결과 |
|---|---|---|---|
| `banned.json` | **차단** | 고객사명 · 계약/요구사항 번호 · 금액 | SECRET 상향 + 전송 차단 |
| `pseudonyms.json` | **치환** | 사내 프로젝트명 · 시스템명 · 인명 | 치환하고 경계를 넘게 허용 |

> **조치 (설계 변경)**:
> 1. `data/pseudonyms.json` 신설. `targets`(PROJ/SYS/PERSON/PATH 카테고리별) +
>    `technical_terms`(절대 치환 금지 허용 목록)
> 2. `schemas.PseudonymTargets` 추가. `all_literals()` 가 **긴 리터럴부터** 반환
>    (`atlas-ml` vs `atlas-ml-core` 부분 치환 사고 방지)
> 3. **`DataBundle._check_lists_are_disjoint()`** — 로드 시점에 두 목록의 겹침을
>    `ConfigError` 로 거부한다. 재발 방지를 코드로 강제
> 4. `test_pseudonym_and_banned_lists_are_disjoint` + `test_data_bundle_rejects_overlapping_lists`

**분리 후 실측 (규칙 기반만, LLM 호출 0회)**

```
문서 11건 · 정확도 11/11 = 100%  (목표 >=90%)
기밀 재현율 3/3 = 100%           (목표 100%)
함정 문서 1/1 탐지               경로=internal, 헤더=없음, 금칙어 5건으로만 잡힘
internal 문서 차단 히트 0건      가명화 경로 정상 작동 (치환 대상 1~5건)
```

> **Day 2 게이트 G2 는 규칙 기반만으로 달성 가능하다.** 계획(`u1` Step 9.8)의
> 예상이 맞았다. EXAONE 보조 판정은 정확도를 위해서가 아니라
> **규칙이 못 잡는 문맥적 기밀성**을 위해 추가한다.

### 발견 10 — 실제 답변 지연 재확인

§2 발견 4-b 참조. 시나리오 1 페이로드로 9.23s (460 in / 513 out).
지연 예산을 수정했다.

### 발견 11 — 초기 선정 의존성에 알려진 취약점 8건 (SECURITY-10)

`make audit`(pip-audit)이 설계 시점에 고른 버전에서 취약점을 찾았다.

| 패키지 | 초기 버전 | 취약점 | 수정 버전 |
|---|---|---|---|
| `starlette` | 0.41.3 (fastapi 0.115.6 이 끌어옴) | **7건** (PYSEC-2026-161, 248, 249, 1941, 1942, 2280, 2281) | ≥ 1.3.1 |
| `pytest` | 8.3.4 | 1건 (PYSEC-2026-1845) | ≥ 9.0.3 |

`starlette` 는 직접 의존이 아니라 `fastapi` 의 전이 의존이었다.
설계 문서에서 `fastapi==0.115.6` 을 고정한 것이 원인이다 —
**의존성 버전을 실제로 검사하지 않고 고른 결과다.**

> **조치**: `pyproject.toml` 갱신 + `starlette` 를 **직접 고정**해
> 전이 의존으로 내려가지 않게 했다.
>
> | 패키지 | 확정 버전 |
> |---|---|
> | `fastapi` | 0.141.1 |
> | `starlette` | 1.6.0 (직접 고정) |
> | `pydantic` | 2.13.4 |
> | `uvicorn[standard]` | 0.52.4 |
> | `boto3` | 1.43.74 |
> | `pyyaml` | 6.0.3 |
> | `pytest` | 9.1.1 |
> | `pytest-asyncio` | 1.4.0 |
> | `hypothesis` | 6.165.10 |
> | `pip-audit` | 2.10.1 |
> | `ruff` | 0.16.3 |
>
> 재검사: **취약점 0건.** 테스트 382개 전부 통과 (버전 상승 후에도).

**교훈**: 설계 문서에 버전을 적을 때는 반드시 `pip-audit` 을 함께 돌린다.
`make audit` 을 Day 1 부트스트랩에 넣은 것이 이걸 잡았다.
버전을 추측으로 적으면 안 된다 — `hypothesis==6.143.4` 는 **존재하지 않는 버전**이었고
`uv sync` 가 거부했다.

---

## 8. Day 1 종료 시점 상태

| 항목 | 값 |
|---|---|
| 테스트 | **382개 통과** (unit) |
| lint / format | 통과 |
| 의존성 취약점 | **0건** |
| `make preflight` | 실패 0 · 경고 2 (경계 시뮬레이션, CDK 미부트스트랩 — 둘 다 예상됨) |
| 규칙 기반 분류 정확도 | **11/11 = 100%** (Day 2 게이트 G2 예비 통과) |
| 기밀 재현율 | **3/3 = 100%** |
| LLM 호출 (Day 1 전체) | EXAONE 3회 · Bedrock 3회 |

### 실측 지연 (최종)

| 호출 | 조건 | 지연 |
|---|---|---|
| EXAONE | ping, 4 토큰 출력 | **0.27s** |
| EXAONE | 슬롯 채우기, 원문 235 토큰 | **0.42s** |
| Bedrock | ping, 4 토큰 출력 | **2.30s** |
| Bedrock | 시나리오 1 실제 답변, 513 토큰 출력 | **9.23s** |

> **출력 토큰이 지연을 지배한다.** 4 토큰 응답으로 지연을 판단하면 안 된다 —
> `preflight` 가 이 경고를 매번 출력한다.

---

## 9. Day 2 구현 중 발견 (설계 결함 3건 + 환경 1건)

Day 2 는 보안 코어(판정·검증·추출·가명화·감사)를 구현했다. 그 과정에서
**설계 문서의 규칙 자체에 있던 결함 3건**을 찾았다. 셋 다 "테스트는 통과하는데
실제로는 뚫려 있는" 종류다.

### 발견 12 — `BR-C-03` 의 규칙 순서에 조용한 하향 경로가 있다 🔴

**설계 원안** (`u1/functional-design/business-rules.md` BR-C-03):

```
① 경로 glob → ② 헤더 등급 표기 → ③ 금칙어 리터럴 → ④ 금칙어 정규식 → ⑤ internal glob → ⑥ 기본값
```

규칙은 "앞에서 걸리면 뒤를 보지 않는다"다. 그러면 ②에서 걸린 문서는
**③④를 아예 검사하지 않는다.**

```markdown
---
보안등급: 사내          <- ② 에서 확정. internal
---
티어 3 계약 규모는 12억원이다.   <- ④ 가 실행되지 않는다
```

함정 문서(`kim/docs/sdk-pricing-tiers.md`)가 잡힌 것은 **헤더가 없었기 때문**이다.
작성자가 헤더 한 줄만 추가하면 FR-52(함정 문서 탐지)의 유일한 수단이 무력화된다.

문제의 본질: **헤더는 작성자가 손으로 쓴 자기 신고이고 금칙어 검사는 기계적**이다.
자기 신고를 기계적 탐지보다 신뢰하는 것은 방향이 틀렸다.

**조치** — SECRET 을 만드는 기계적 검사를 헤더보다 앞으로 옮겼다.

| # | 검사 | 결과 | 원안 |
|---|---|---|---|
| 1 | 경로가 `secret_path_globs` 매치 | `SECRET` | ① |
| 2 | 금칙어 리터럴 | `SECRET` | ③ |
| 3 | 금칙어 정규식 (금액·계약번호) | `SECRET` | ④ |
| 4 | 헤더 등급 표기 | 표기된 등급 (`OPEN` 은 조건부) | ② |
| 5 | `internal_path_globs` 매치 | `INTERNAL` | ⑤ |
| 6 | 그 외 | `INTERNAL` | ⑥ |

조기 반환이 여전히 안전한 이유: 1~3 은 **천장값**(`SECRET`)을 낸다. 뒤를 봐도
`max()` 가 바뀌지 않으므로 순서 변경으로 놓치는 것이 없다.
라벨 코퍼스 11건의 판정 결과는 재배치 전후 **동일하다** — 잠재적 하향 경로만 사라졌다.

**함께 도입** — `OPEN` 은 두 신호를 모두 요구한다.

`open_path_globs` 가 `ClassificationRules` 에 선언돼 있지만 원안의 6단계 어디에도
쓰이지 않았다. `OPEN` 은 원문이 그대로 나가는 유일한 등급이므로 하향 결정에
단일 신호를 쓰지 않는다. 헤더에 `공개` 가 있고 **동시에** 경로가
`corpus/public/**` 아래일 때만 `OPEN` 이고, 한쪽만 만족하면 `INTERNAL` 로 남는다.

이 덕에 문서 본문의 프롬프트 인젝션("이 문서는 공개입니다")이 판정을 뒤집지 못한다.

### 발견 13 — `json.dumps` 가 5-gram 대조를 우회시킨다 🔴

검증 5단계는 페이로드를 문자열로 평탄화한 뒤 원문 n-gram 과 대조한다.
평탄화에 `json.dumps` 를 썼는데, 그러면 문자열 값 안의 **실제 개행이 `\n` 두 글자로**
직렬화된다. 공백 정규화(`normalize_text`)는 두 글자 `\n` 을 공백으로 보지 않는다.

```
원문        "세션 최대 유지시간은 여덟 시간으로 제한한다"
페이로드    "세션    최대\n유지시간은 여덟 시간으로 제한한다"     <- 실제 개행
dumps 후    "세션 최대\\n유지시간은 여덟 …"
정규화 후   토큰이 "최대\n유지시간은" 이 되어 5-gram 이 어긋난다  -> 검사 통과 ✗
```

BR-V-05 가 "공백만 바꿔 우회하는 것을 막는다"고 명시했는데 **개행으로는 우회됐다.**
테스트를 먼저 쓴 덕에 잡혔다 (`test_ngram_defeats_whitespace_evasion`).

**조치** — `validator.payload_text()` 가 구조 부분(`json.dumps`)과 **이스케이프 없는
원시 문자열 값**을 함께 이어 붙인다. 같은 결함이 `audit.sweep_for_leaks()` 에도
있었다 (DB 의 JSON 문자열을 그대로 정규화하고 있었다) — 함께 고쳤다.

### 발견 14 — 평탄한 `facts` 는 상충하는 사실을 조용히 하나로 합친다 🔴

실측(EXAONE 1회)에서 나온 첫 페이로드:

```json
"facts": {
  "auth_mechanism_class": "challenge_response",
  "session_binding": "none",              // ⚠️
  "credential_lifetime_hours": 24,
  "renewal_mode": "background_silent"
}
```

검증은 **6/6 통과**하고 원문도 0개였다. 그런데 답이 틀린다.

시나리오 1의 사실관계는 이렇다:

| 근거 | `session_binding` |
|---|---|
| 고객사 요구사항명세서 | `required` |
| 자사 인증 설계 문서 | `none` |

두 문서를 한 프롬프트에 넣고 평탄한 `{슬롯: 값}` 으로 조립하니 **하나가 다른 하나를
덮어썼다.** Agent 는 `session_binding: none` 만 보고 "충돌 없음"이라고 답한다.
`constraint_conflict_check` 는 **두 근거를 대조하는** task 인데 대조할 대상이 사라졌다.

이건 유출이 아니라 **정확성 실패**다. 그리고 유출보다 발견하기 어렵다 —
검증 6단계가 전부 통과하기 때문이다.

**조치 2단**

1. **문서마다 따로 슬롯을 채운다.** 한 프롬프트에 여러 문서를 넣으면 모델이
   상충하는 사실을 뭉개고, 어느 근거에서 나온 값인지도 사라진다.
   호출 수가 문서 수에 비례한다 (2문서 = 2회, 0.43s × 2).

2. **`facts` 를 근거별로 분리한다** — `{ref: {슬롯: 값}}`.

```json
"facts": {
  "REQ_A": {"auth_mechanism_class": "challenge_response", "session_binding": "required",
            "credential_reuse_allowed": false, "max_session_hours": 8,
            "renewal_mode": "explicit"},
  "COMP_A": {"session_binding": "none", "credential_lifetime_hours": 24,
             "renewal_mode": "background_silent"}
}
```

파생 수정 3건:
- `validator.slot_entries()` 가 **경로별로** 반환한다. 이름별 dict 로 뭉치면
  `REQ_A.max_session_hours` 와 `COMP_A.max_session_hours` 중 하나만 범위 검사된다
- `check_schema` 가 `facts` 하위의 ref 라벨 키를 형식 정규식으로 허용한다
- 필수 슬롯은 **근거 전체에서** 채워지면 충족이다 — 세션 최대시간은 고객사
  문서에만, 토큰 수명은 자사 문서에만 있다

**재실측 결과** (EXAONE 2회): 검증 6/6 · 562 bytes · 원문 5-gram 0건 ·
`verbatim_sentence_count = 0` · 충돌이 페이로드에 보존됨.

### 발견 15 — Day 1 설계에 사내·공개 등급의 페이로드 형태가 없었다

`STRUCTURAL_KEYS` 에 텍스트를 담을 키가 없어서 `INTERNAL`(가명화)·`OPEN`(원문)
페이로드를 **만들 방법이 없었다.** Day 1 의 계약이 기밀 경로만 완전했던 것이다.

**조치** — `excerpts` 키(`{ref: text}`)를 도입하고, **표현별로 허용 키를 다르게** 했다.

| 단계 | `STRUCTURED` (기밀) | `PSEUDONYMIZED` (사내) | `VERBATIM` (공개) |
|---|---|---|---|
| 1 스키마 | 슬롯 ∪ 구조키. **`excerpts` 금지** | + `excerpts` | + `excerpts` |
| 2 어휘 | **모든 문자열** | `excerpts` 내부 제외 | 동일 |
| 4 금칙어 | 동일 | 동일 (사내의 하한선) | 동일 |
| 5 원문 | 원문 5-gram 0건 | **식별자 포함 5-gram 0건** | 검사 불가(정의) |
| 6 크기 | 2KB | 2KB × 8 | 2KB × 8 |

`STRUCTURED` 에서만 `excerpts` 를 금지하는 것이 기밀 등급의 "원문 0개"를
**구조적으로** 보장한다. 텍스트를 담을 키가 화이트리스트에 없다.

두 가지를 함께 정직하게 처리했다:
- `VERBATIM` 은 원문 전송이 등급의 정의라서 5단계를 적용할 수 없다.
  통과시키되 그 사실을 `CheckResult.detail` 에 남긴다 — 조용히 넘기지 않는다.
- 2KB 상한의 근거는 "초과는 자유 텍스트가 섞였다는 신호"다. 가명화 본문에는
  그 논리가 성립하지 않아 같은 숫자를 쓰면 정상 동작이 차단된다.

### 발견 16 — 임시 자격증명이 만료됐다 (환경)

```
$ aws sts get-caller-identity
ExpiredToken: The security token included in the request is expired
```

`AWS_ACCESS_KEY_ID` 가 `ASIA…` 로 시작하는 STS 임시 자격증명이고, Day 1 실측
이후 만료됐다. 발견 5가 예측한 그대로다.

**Day 2 에는 영향이 없다.** Day 2 의 대상은 판정·검증·추출·가명화·감사이고
Bedrock 은 대역(fake broker)으로 검증한다. EXAONE(Friendli)은 정상이다 (0.39s).

**조치가 필요한 시점**: Day 3 (U3 Agent 실호출) 전에 자격증명을 갱신해야 한다.

```bash
# 새 자격증명을 .kiro/.env 에 기입한 뒤
set -a; . ./.env; . ./.kiro/.env; set +a
make preflight        # AWS 항목이 OK 로 바뀌는지 확인
```

갱신이 어렵다면 `AGENT_TRANSPORT=mock` 으로 3막 전체가 돌아간다 (FR-48).

---

## 10. Day 2 종료 시점 상태

| 항목 | 값 |
|---|---|
| 테스트 | **712개 통과** (unit 681 + property 31) |
| 게이트 G2 | **정확도 11/11 = 100% · 기밀 재현율 3/3 = 100% · 함정 1/1** |
| lint / format | 통과 |
| 의존성 취약점 | **0건** |
| LLM 호출 (Day 2) | EXAONE **5회** (추출 검증 4 + preflight 1) · Bedrock 0회 |

### 실측 지연 (Day 2 추가)

| 호출 | 조건 | 지연 |
|---|---|---|
| EXAONE | 슬롯 채우기 6슬롯, 문서 1건 | **0.43s** |
| 구조 추출 전체 | 문서 2건 (호출 2회) | **0.86s** |
| 검증 6단계 | 순수 코드, 5-gram 549개 대조 | < 0.01s |
| 가명화 | 순수 문자열 치환 (LLM 0회) | < 0.01s |

구조 추출 예산은 3초였고 문서 2건에 0.86초를 쓴다. 문서가 4건이어도 1.8초로
예산 안이다.

### 남은 한계 (문서화)

| 한계 | 왜 남겨두는가 |
|---|---|
| 5단계는 로컬에서만 수행된다 | 원문이 클라우드에 없다. `revalidate_without_originals()` 가 그 사실을 `detail` 에 남긴다 (BR-V-07) |
| 모델이 원문을 **의역**하면 n-gram 이 못 잡는다 | 어휘 사전(2단계)이 막는 영역이다. 두 겹이 함께 필요한 이유 |
| 한국어는 공백이 적어 5-gram 이 긴 구간을 덮는다 | 가명화 등급에 3-gram 을 추가 검사한다 (`NGRAM_SIZE_INTERNAL=3`) |
| task 스키마 선택이 키워드 휴리스틱이다 | 틀리면 슬롯이 안 맞아 `ExtractionFailed` → 신뢰 구역 내 답변. **유출이 아니라 품질 저하**다 |
| role 배정이 등급·경로 순서에 의존한다 | 잘못 배정되면 Agent 가 관계를 거꾸로 읽지만 원문은 나가지 않는다 |
| 로컬 감사 로그는 사용자가 파일을 지울 수 있다 | 그래서 클라우드 미러(U5)가 필요하다. 숨기지 않고 적는다 |

---

## 11. Day 3 구현 중 발견 (설계 결함 2건 + 배선 결함 2건)

Day 3 은 U2 Store 완성과 U3(Agent · Orchestrator · Inbox · FastAPI)을 구현했다.
Day 2 와 달리 **유출 결함은 없었다** — 경계가 Day 2 에 완성돼 있었기 때문이다.
대신 "누가 무엇을 만드는가"(배선)와 "무엇이 경계를 넘는가"(범위)에서 나왔다.

### 발견 17 — 에스컬레이션 초안 입력이 전부 경계를 넘어서는 안 되는 것들이었다 🔴

`BR-AG-04` 는 초안 프롬프트에 넣을 것을 이렇게 정했다:

> 변환된 페이로드 · Agent 의 부분 응답 · 세션 사실 · 인용 목록(`display_title` 만)

구현하려고 하나씩 확인해 보니 **뒤 세 개가 전부 나가서는 안 되는 것**이었다.

| 넣으려던 것 | 왜 안 되는가 |
|---|---|
| 근거 문서 제목 | `"고객사 요구사항명세서"` 에 고객사가 있다. FR-43 이 `Citation` 에서 `internal_path` 를 뺀 것과 같은 이유다 |
| 근거 시점 (`as_of`) | 일정·날짜는 `vocab.json _intentionally_absent` 목록에 있다 |
| 세션 사실 | `Session.focus`/`summary` 는 **원문 취급**이다 (schemas.py 주석에 명시) |
| Agent 부분 응답 | 어휘 사전 밖의 자유 문자열이다. 검증 2단계를 통과할 수 없다 |

설계 문서가 "`display_title` 만"이라고 쓴 것은 `internal_path` 와 비교해서
안전하다는 뜻이었지만, **경계를 넘는 맥락에서는 제목도 원문 파생물**이다.

**조치** — 초안 생성을 두 단계로 나눴다.

```
① 경계를 넘는 것:  이미 검증을 통과한 envelope 을 그대로 재사용
                    (프롬프트와 모델만 바뀐다 -> haiku)
                    모델은 구조 페이로드만 보고 summary / draft_answer 를 쓴다

② 신뢰 구역 안:     제목 · 시점 · 공식성 · 세션 사실을 situation 에 덧붙인다
```

`Gatekeeper.ask_draft()` 가 ①을 담당한다. 새 페이로드를 만들지 않으므로
**검증을 다시 통과시킬 필요도 없고, 검증 없이 나가는 경로도 생기지 않는다.**
초안 품질은 유지된다 — 담당자가 먼저 읽는 것은 "무엇을 근거로 하는가"이고,
그건 애초에 모델이 만들 수 없는(=만들면 안 되는) 정보다.

`situation` 의 순서도 그래서 로컬 사실이 먼저다.

### 발견 18 — 목록 요약을 자유 문장으로 만들면 검사할 방법이 없다 🔴

`BR-S-06` 은 `current_focus_summary` 를 이렇게 예시했다.

```
Session.focus = "고객사 H 인증 요구사항 검토 + SDK v3.2 토큰 정책"
   -> Gatekeeper 로 식별자 제거 요약
   -> "인증 관련 작업 중"
```

문제: 모델이 자유 문장을 만들면 **그 문장에 원문이 섞였는지 사후에 검사해야 한다.**
이건 이 프로젝트가 §3.1 에서 기각한 구조다 — "무엇을 지울까"이고 검사를 잊으면 유출이다.
그리고 이 화면은 **인증 없이 보인다.**

**조치** — 등급 판정과 같은 방식으로 바꿨다. 닫힌 라벨 집합에서 하나를 고르게 한다.

```python
FOCUS_TOPICS = ("인증 관련 작업", "데이터 파이프라인 작업", "모델 학습 작업",
                "배포·릴리스 작업", "문서 검토", "성능 분석", "기타 작업")
```

출력이 `{"topic": <라벨>}` 이고 범위 밖이면 `None` 이다. 원문이 섞일 채널이
**존재하지 않는다.** 검사할 것이 없으므로 잊을 검사도 없다.

**실측** (EXAONE 3회, 각 0.23~0.26s): 세 세션 모두 의미가 맞는 라벨이 나왔다.

| 세션 `focus` (원문) | 라벨 |
|---|---|
| 고객사 H 인증 요구사항 검토 + SDK v3.2 토큰 정책 | 인증 관련 작업 중 |
| atlas-ml 전처리 v3 재학습 | 모델 학습 작업 중 |
| SDK v3.2 배포 준비 | 배포·릴리스 작업 중 |

### 발견 19 — 브로커를 누가 만드는가 (배선이 경계 규칙을 깬다)

`main.py` 가 객체 그래프를 조립하려면 `BrokerClient` 를 만들어야 한다.
그러면 `main.py` 가 `mesh.llm.broker` 를 import 하고, **경계를 넘는 모듈이
하나 늘어난다** (SECURITY-11). `BOUNDARY_CROSSERS` 허용 목록이 늘어나면
"단일 통로"라는 규칙이 무의미해진다.

**조치** — 생성도 통로 안에 뒀다. `Gatekeeper.build(cfg, data, audit)` 팩토리가
함수 스코프에서 `BrokerClient` 를 import 한다. `main.py` 는 브로커의 존재를 모른다.

Day 1 의 테스트(`gatekeeper` 가 broker 를 런타임 import 하지 않는다)는
**모듈 최상위 import 만** 검사하도록 바꿨다. 원래 의도는 순환 결합 방지였고
함수 스코프 import 에는 그 문제가 없다. 대신 검사를 하나 추가했다 —
**어떤 모듈도 최상위에서 broker 를 import 하지 않는다.**

부수 효과: mock 모드에서 `httpx`·`boto3` 경로를 끌고 오지 않는다.

### 발견 20 — `api_models` 의 레이어가 소유 기준으로 잡혀 있었다

Day 1 에 `mesh.api_models` 를 L5 에 뒀다 (U3 소유라서). 그런데 `inbox.py`(L5)가
`InboxItem` 을 쓰려면 같은 레이어를 import 해야 하고, 그건 레이어 규칙 위반이다.

`api_models` 는 `mesh.schemas` 만 참조하는 **잎 모듈**이다.
**레이어는 소유가 아니라 의존 순서를 나타낸다** — L1 로 내렸다.

### 발견 21 — 테스트가 아무것도 검사하지 않던 두 곳

Day 3 에 테스트를 쓰면서 **검사기가 무력했던 경우**를 두 개 찾았다.

1. `TestClient` 의 기본값이 `raise_server_exceptions=True` 라서 전역 예외
   핸들러가 만드는 500 응답을 볼 수 없었다. 즉 "응답에 스택 트레이스가 없다"를
   확인하려던 테스트가 **예외를 그대로 받고 있었다.**
   -> 그 테스트만 `raise_server_exceptions=False` 로 클라이언트를 따로 만든다.

2. "소스에 `StaticFiles` 가 없다" 같은 문자열 검사가 **주석까지 잡았다.**
   `"StaticFiles 를 쓰지 않는다"` 라는 설명을 쓰면 테스트가 실패한다.
   -> ast 로 **호출**을 검사한다. 같은 이유로 `orchestrator` 의
   `grep -c "broker" == 0` 도 ast 로 바꿨다 — `reason="broker_unavailable"` 같은
   이유 코드는 모델 호출이 아니다.

같은 문제를 Day 2 에도 한 번 겪었다 (`store` 의 `classify` 문자열 검사가
`classify_source` 를 잡았다). **문자열 검사는 주석과 무관한 이름까지 잡는다.**
구조를 검사해야 할 때는 ast 를 쓴다.

---

## 12. Day 3 종료 시점 상태

| 항목 | 값 |
|---|---|
| 테스트 | **976개 통과** (unit 896 + property 42 + eval 38) |
| 게이트 G2 | 정확도 11/11 = 100% · 기밀 재현율 3/3 = 100% |
| **게이트 G3** | **시나리오 1·2·3 + 후속 종단 통과 · 전수 유출 0건** |
| lint / format | 통과 |
| 의존성 취약점 | **0건** |
| LLM 호출 (Day 3) | EXAONE **5회** (select_paths 1 + focus_topic 3 + preflight 1) · Bedrock 0회 |

### 실측 지연 (Day 3 추가)

| 호출 | 조건 | 지연 |
|---|---|---|
| `select_paths` | 후보 3개, **본문 미포함** (프롬프트 326자) | **0.44s** |
| `focus_topic` | 세션 focus + summary | **0.23 ~ 0.26s** |
| `prepare` 전체 (대역) | 분류 + 선택 + 읽기 + 추출 + 검증 | < 0.05s |

`select_paths` 프롬프트가 326자인 것이 BR-S-02 의 효과다. 본문을 넣으면
수천 자가 되고, 그 본문이 등급 판정 **전에** 신뢰 구역 밖으로 나간다.

### 남은 한계 (Day 3 에 추가)

| 한계 | 왜 남겨두는가 |
|---|---|
| 목업 픽스처가 아직 없다 | 3막 live 녹화는 Day 4 작업이다 (`logical-components.md` §7). 그동안 `scripts/demo.py` 는 대역 주입 테스트로 검증한다 |
| 질문 분해를 Orchestrator 가 만들지 않는다 | `can_decompose()` 는 구현·검증됐지만 하위 질문 그래프를 만드는 것은 U4 의 입력 형태가 정해진 뒤가 낫다. 현재는 질문 하나 = 호출 하나이고 **상향으로 안전하게 처리된다** |
| `question_count_today` 가 날짜 문자열 접두사로 집계된다 | 타임존이 섞이면 경계에서 하루 어긋날 수 있다. 데모는 `MESH_DEMO_NOW` 로 고정하므로 영향이 없다 |
| 세션 없는 에이전트도 목록에 남는다 | 담당 영역은 항상 공개이므로 지목이 가능해야 한다. 상태 필드만 비운다 |

---

## 13. 첫 완전 live 실행에서 발견 (결함 4건)

AWS 자격증명이 갱신돼 **처음으로 EXAONE + Bedrock 을 모두 실제로 쓰는 종단
실행**이 가능해졌다. 대역 테스트 976개가 전부 통과한 상태에서 돌렸는데
**결함 4건이 나왔다.** 넷 다 대역으로는 잡을 수 없는 종류다.

> 대역은 "우리가 생각한 응답"을 준다. 실제 모델은 **우리가 생각하지 못한
> 응답**을 준다. 그리고 그 차이가 정확히 여기 있었다.

### 발견 22 — 초안 프롬프트가 원래 답을 다시 만들게 했다 🔴

`ask_draft()` 는 검증된 envelope 을 재사용한다 (발견 17). 그때 시스템 프롬프트를
이렇게 조립했다.

```
build_system_prompt(persona, tier)          # 여기에 기본 출력 계약이 들어 있다
  + "\n\n" + DRAFT_SYSTEM                   # 초안 출력 형태
```

기본 출력 계약의 내용이 문제였다.

> "출력은 JSON 객체 하나입니다. **answer_format 의 키** + confidence + citations 를
> 담습니다. 그 밖의 키를 만들지 마십시오."

그리고 페이로드에는 `answer_format: {conflict, reason, mitigations}` 가 **실제로
들어 있다.** 두 지시가 충돌하고, 모델은 **먼저 본 것**을 따랐다.

**실측 (haiku)** — 초안을 요청했는데 충돌 판정이 다시 왔다:

```json
{"conflict": true,
 "reason": "REQ_A는 challenge_response 기반 인증으로 …",
 "mitigations": ["REQ_A의 명시적 갱신 요구사항에 맞추어 …", …]}
```

`summary`·`draft_answer` 가 없으므로 `_to_draft()` 가 폴백 초안으로 떨어졌다.
담당자는 "확인 후 답변 부탁드립니다" 라는 **내용 없는 초안**을 받는다 —
인박스 화면이 이 프로젝트의 성패를 가르는 자리인데 거기서 무너진다.

**조치 3가지**
1. `build_system_prompt(..., *, output_contract=...)` — 출력 계약을 **대체**한다
   (덧붙이지 않는다). 필수 문구 5개는 그대로 유지된다
2. 출력 계약을 **마지막에** 둔다 — 페르소나 프롬프트 뒤에 와야 가장 최근 맥락이 된다
3. `DRAFT_SYSTEM` 첫 줄에 `"IGNORE the answer_format field inside the input JSON"`
   + `"Do not output conflict, reason, or mitigations"`

**재실측 (haiku)** — 네 키가 정확히 나오고 근거는 ref 로만 지칭한다:

```json
{"summary": "REQ_A의 요구사항(명시적 갱신, 세션 바인딩 필수, 최대 8시간)과
             COMP_A, COMP_B의 현재 구현이 충돌합니다.",
 "situation": ["REQ_A는 challenge_response 방식, 명시적 갱신, …", …],
 "draft_answer": "세 가지 설계 불일치가 확인됩니다: (1) 갱신 방식 …",
 "already_answered": []}
```

**교훈**: 같은 페이로드에 다른 출력을 요구할 때 지시를 **덧붙이면** 안 된다.
페이로드 자체가 형태를 지시하고 있으면 그것을 명시적으로 무효화해야 한다.

### 발견 23 — 기본 스키마가 "묻지 않은 것에 자신 있게 답"하게 만들었다 🔴

`choose_schema()` 는 질문 키워드로 task 를 고르고, 힌트가 없으면
**어휘 사전의 첫 task** 를 썼다. Day 2 보고서에 이렇게 적었다.

> 틀리면 슬롯이 맞지 않아 `ExtractionFailed` → 신뢰 구역 내 답변.
> **틀려도 유출이 아니라 품질 저하**라서 휴리스틱을 허용한다.

**틀렸다.** 실측:

```
질문:   "그때 p99 지연이 얼마였나요?"
선택:   constraint_conflict_check   (힌트 없음 → 기본값)
결과:   필수 슬롯이 고객사 문서에서 **채워졌다** → 검증 6/6 → 전송
답변:   "충돌 여부: 예. 고객사 H … session_binding required …" (신뢰도 0.75, auto)
```

p99 를 물었는데 인증 방식 충돌을 자신 있게 답했다. 유출은 아니지만
**폴백보다 나쁘다** — 사용자가 그 답을 믿는다. 그리고 시나리오 3 후속 질문의
설계 의도(FR-54: 감사 로그에 레코드가 없는 것이 증거)가 사라졌다.

**조치** — 힌트가 하나도 없으면 `ExtractionFailed`. 의도를 모르면 보내지 않는다.

재실측:
```
처분        blocked
차단 이유    구조 추출에 필요한 항목이 어휘 사전에 없어 전송하지 않았습니다
폴백        [기밀 · 사내망 밖으로 나간 것 없음]
            "…p99 지연이 목표를 못 맞췄다고 기록되어 있습니다.
              구체적인 수치는 문서에 명시되어 있지 않습니다."
감사 레코드   0        local_queries  1
```

**교훈**: "틀려도 안전한 방향"이라는 판단은 **틀린 결과가 실제로 어디로 가는지**
확인한 뒤에만 유효하다. 여기서는 틀린 스키마가 우연히 채워질 수 있었다.

### 발견 24 — 초안 응답이 본 응답의 픽스처를 덮어썼다 🔴

`broker` 의 픽스처 키가 `(name="agent", schema_id, payload)` 였다.
`ask_draft` 는 **같은 envelope 을 재사용**하므로 payload 가 동일하다.
→ 초안 응답이 본 응답 픽스처를 덮어썼다.

오프라인 모드 실측:
```
3막 김책임 Agent   신뢰도 0.00 (live)   처분 escalate
```

`0.00` 의 정체는 **초안 응답**이었다. 목업 모드에서 김책임 Agent 의 답변이
담당자용 인계 메모(`summary`/`draft_answer`)로 바뀌었고, `confidence` 가 없어
0.0 이 되어 인용 0개 규칙에 걸렸다.

조용히 **다른 질문에 대한 답**이 표시되는 셈이라 오프라인 데모가 무너진다.
그리고 원인이 화면에 전혀 드러나지 않는다.

**조치** — 픽스처 키에 `model_id` 를 넣는다. 본 호출(sonnet)과 초안(haiku)이
서로 다른 키를 갖는다.

**교훈**: 캐시·픽스처 키는 **응답을 결정하는 모든 입력**을 담아야 한다.
프롬프트와 모델을 바꾸면서 키를 그대로 두면 조용히 섞인다.

### 발견 25 — 차단된 호출의 등급 배지가 낮게 표시됐다

```
대상       김철수 책임의 Agent [사내]        <- 근거에 기밀 문서가 있는데
폴백       [사내 · 사내망 밖으로 나간 것 없음]
```

`_blocked_call()` 이 실패 지점에서 판정 결과를 버리고 파일을 **다시 읽었다.**
`read()` 는 `Chunk.tier` 를 채우지 않으므로 (설계대로) 전부 기본값 `INTERNAL` 이 됐다.

유출은 아니지만 **사용자에게 등급을 낮게 보여주는 것**이므로 고친다.
"기밀 문서를 썼지만 아무것도 나가지 않았다"가 이 화면의 메시지인데,
`[사내]` 라고 쓰면 그 메시지가 약해진다.

**조치** — `PrepareFailed` 예외가 **판정된 근거를 함께 들고 온다.**
다시 판정하는 방법도 있지만 같은 판정을 두 번 하는 것은 낭비이고 두 결과가
갈릴 여지를 만든다.

### 발견 26 — 전수 유출 검사가 오탐 1076건을 냈다 🔴

가장 강한 주장("유출 0건 전수 검사")이 **실제 유출을 가리는 도구**가 되어 있었다.

```
검사한 페이로드  11    검사한 문서  11
원문 조각 히트   1076        <- 전부 오탐
금칙어 히트     0
```

원인: `sweep_for_leaks()` 가 **표현과 무관하게** 평탄한 5-gram 규칙을 적용했다.
가명화 페이로드(`excerpts`)는 원문 문장을 대부분 유지하므로 정상 동작이 전부
걸린다. 검증 5단계는 이미 등급별로 다른 규칙을 쓰는데(BR-P-03) 전수 검사는
그걸 따르지 않았다.

목록이 1076줄이면 아무도 읽지 않는다. 즉 **진짜 유출 한 건이 섞여도 못 본다.**

**조치** — 검증 5단계와 같은 규칙을 쓴다. `AuditRecord.representation` 이 이미
저장돼 있었는데 쓰지 않고 있었다.

| 표현 | 검사 대상 |
|---|---|
| `STRUCTURED` (기밀) | 원문 5-gram 전체 |
| `PSEUDONYMIZED` (사내) | **식별자를 포함한** n-gram 만 |
| `VERBATIM` (공개) | 없음 — 원문 전송이 등급의 정의다 |

금칙어 검사는 모든 등급에 동일하게 유지한다 (사내 등급의 하한선).

**재실측** (실제 live 실행이 남긴 페이로드 11건: structured 7 + pseudonymized 4):
```
원문 조각 히트  0    금칙어 히트  0    clean  True    0.014s
REQ-4412 / H社 / 12억원 / EAP-AKA / atlas_ml / 김철수  전부 0건
```

**교훈**: 검사 규칙이 두 곳에 있으면 갈라진다. 검증 5단계와 전수 검사는
같은 판정을 하는데 구현이 달랐다.

---

## 14. 자격증명 파일이 두 곳에 있다 (운영 주의) ⚠️

```
.env         AWS_ACCESS_KEY_ID  ASIA…  STS OK    ← 사용자가 갱신한 것
.kiro/.env   AWS_ACCESS_KEY_ID  ASIA…  ExpiredToken
```

`.kiro/.env` 는 다른 도구(opencode)용이고 **만료된 자격증명**을 갖고 있다.
두 파일을 이 순서로 로드하면 만료된 것이 이긴다:

```bash
set -a; . ./.env; . ./.kiro/.env; set +a     # ❌ 나중 것이 이긴다
```

Day 2·Day 3 의 `preflight` 실패가 이것이었다. `.env` 만 로드하면 통과한다.

**권장**: `.kiro/.env` 에서 `AWS_*` 4개를 지우거나, 문서의 모든 예시를
`. ./.env` 하나만 쓰도록 통일한다. 자격증명의 출처가 하나여야 한다.

---

## 15. 첫 완전 live 실행 결과 (수정 후)

### 시나리오 1 — 자동 응답

```
prepare   상향 기밀 · 검증 6/6 · 727 bytes · 원문 문장 수 0 (측정값)
send      처분 auto · 신뢰도 0.85 (live) · 13.6s
답변      "충돌 여부: 예 / 이유: 고객사 H 5G 코어망 인증 요구사항명세서는
           session_binding을 required로 요구하고 … 반면 SDK 인증 설계 문서
           (v3.2)와 인증 설계 메모 (2025-11)는 모두 session_binding이 none …
           세 가지 구조적 충돌" + 대응 방안 5개
근거      고객사 요구사항명세서 [기밀 · 2026-07-15]
          SDK 인증 설계 문서 (v3.2) [사내 · 2026-08-19]
          인증 설계 메모 (2025-11) [사내 · 2025-11-14 · 비공식]
감사      레코드 2 · 절약 추정 1건 / 약 20분
```

**실제 Claude 가 원문을 한 글자도 보지 않고** 세 문서의 값을 대조해 세 가지
충돌을 짚고 구체적 대응 방안을 냈다. 재수화로 실제 문서 제목이 복원됐다.

### 지연 (실측, live)

| 단계 | 지연 |
|---|---|
| `prepare` (1명, 문서 3건) | 3 ~ 4s |
| `send` (1명) | 10 ~ 14s |
| `send` (2명 병렬) | 16s |
| `select_paths` | 0.44s |
| `focus_topic` | 0.23 ~ 0.26s |
| 전수 유출 검사 (11×11) | 0.014s |

30초 상한 안이다. `send` 가 예산(12s)의 상한에 붙어 있는데, 출력 토큰이
많은 답변(대응 방안 5개)이라 예상 범위다.

### 목업 픽스처 녹화 완료

```
exaone/classify  9    exaone/extract  6    exaone/select  5
exaone/focus     3    exaone/fallback 1    agent/*        5
```

**완전 오프라인(네트워크 0회)으로 4막 전체 + 유출 검사가 통과한다.**

```bash
EXAONE_MODE=mock AGENT_TRANSPORT=mock make demo    # exit=0
```

이것이 게이트 G5(목업 모드로 3막 전체 통과)의 근거다.

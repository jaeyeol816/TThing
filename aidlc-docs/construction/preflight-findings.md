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

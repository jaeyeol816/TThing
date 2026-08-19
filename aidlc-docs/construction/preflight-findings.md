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

실제로 호출에 성공한 모델 (측정된 왕복 지연):

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

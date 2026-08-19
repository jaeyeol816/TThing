# U5 — Tech Stack Decisions

---

## 1. IaC

| 항목 | 선택 | 근거 |
|---|---|---|
| **IaC 도구** | **AWS CDK v2** | 사용자 지정 |
| **CDK 언어** | **Python** | 앱과 언어 통일 → 다른 컴퓨터 온보딩 시 Python 하나만 알면 된다 (NFR-PO). `validator.py`를 Lambda에 번들할 때 언어가 같아 자연스럽다 |
| **CDK CLI** | **`npx aws-cdk@2`** | 전역 설치 안 함 (NFR-PO-03). 현재 컴퓨터에 `cdk` 없음(실측). `@2`로 메이저 고정 (SECURITY-10 `latest` 금지) |
| **CDK 라이브러리** | `aws-cdk-lib==2.173.1`, `constructs==10.4.2` | 정확한 버전 고정 |

**TypeScript CDK를 기각한 이유**: CDK CLI는 어차피 Node가 필요하지만, **스택 코드까지 TS로 쓰면 언어가 둘이 된다.** 5일 해커톤에서 3명 중 누구든 인프라를 만질 수 있어야 하고, 팀이 Python으로 앱을 쓴다. 그리고 Lambda 코드가 Python이므로 번들 스크립트도 Python 쪽이 단순하다.

**`infra/` 가상환경 분리**
```
infra/requirements.txt        aws-cdk-lib, constructs
infra/.venv/                  앱 venv 와 분리
```
`aws-cdk-lib`는 무겁고(수십 MB) 앱 런타임과 무관하다. 앱 의존성 6개 유지 원칙을 지키려면 분리해야 한다.

```makefile
setup-infra:
	cd infra && uv venv && uv pip install -r requirements.txt
```

---

## 2. 컴퓨트

| 항목 | 선택 | 대안 기각 |
|---|---|---|
| **Lambda** Python 3.12 | 사용량이 간헐적(데모 500회). 콜드 스타트가 문제되지 않는다 (< 2s) | **Fargate/ECS** — 상시 과금, VPC 필요, 배포 복잡. 이 규모에 과함 |
| 메모리 512MB | `pydantic` 로드 + 2KB 페이로드 처리에 충분. CPU가 메모리에 비례하므로 128MB는 콜드 스타트가 느리다 | |
| 타임아웃 29s | **API Gateway REST 통합 상한.** 더 길게 두면 API GW가 먼저 끊고 Lambda는 계속 돌아 비용만 든다 | |
| 동시성 5 | 비용·계정 동시성 방어 | |
| 함수 1개 | 코드 200줄. 3개로 나누면 CDK·IAM·로그 그룹이 3배 | |
| 아키텍처 `arm64` | Graviton. 같은 성능에 ~20% 저렴 | x86_64 — 이득 없음 |

---

## 3. API

| 항목 | 선택 | 근거 |
|---|---|---|
| **API Gateway REST (v1)** | **API Key + Usage Plan이 네이티브** | HTTP API(v2)는 Lambda authorizer를 따로 만들어야 한다. 5일 일정에서 REST가 낫다 |
| 인증 | **API Key** (`api_key_required=True`) | 만료 없음 → 이식성(다른 컴퓨터에 키만 옮김). 이게 STS 만료 문제를 푸는 방식 |
| 스로틀 | Usage Plan 5 rps / burst 100 / 일일 2000 | |
| 로깅 | 액세스 + 실행 로깅 (SECURITY-02) | |
| CORS | `http://localhost:8080`만 | 와일드카드 금지 (NFR-S-08) |
| 요청 검증 | API Gateway Request Validator + Lambda pydantic | 2중 |

**IAM 인증(SigV4)을 기각한 이유**: 더 안전하지만 노트북에 유효한 AWS 자격증명이 필요하다. 그런데 **STS 만료 문제를 피하려고 브로커를 만든 것**이므로 자기모순이다. API Key + 스로틀 + 알람이 이 위험 수준에 맞다.

**Lambda Function URL을 기약한 이유**: `AWS_IAM` 인증은 위와 같은 문제, `NONE`은 인증 없는 엔드포인트가 되어 SECURITY-08 위반. 스로틀링도 없다.

**트레이드오프 명시**: API Key는 bearer 시크릿이므로 유출되면 남용된다. 방어는 (1) Secrets Manager 저장 (2) 일일 쿼터 2000 (3) `Api4xxAlarm`·`BedrockCostGuard` 알람 (4) 유출 시 즉시 회전. 해커톤 종료 후 키를 폐기한다.

---

## 4. 저장

| 항목 | 선택 | 근거 |
|---|---|---|
| **DynamoDB** on-demand | 스키마리스, 서버리스, VPC 불필요. **PITR + 삭제 보호**로 감사 무결성 (NFR-S-14) | |
| 대안 기각: **RDS** | VPC 필수 → NAT 비용이 전체보다 크다 | |
| 대안 기각: **S3 + Object Lock** | 감사 불변성에는 더 좋지만 조회가 불편하고 Object Lock 설정이 복잡. 실배포 시 전환 항목으로 기록 |
| 대안 기각: **CloudWatch Logs만** | 조회·검색이 약하고 구조화 쿼리가 불편 |
| 암호화 | AWS 관리 키 (SSE) | 고객 관리 키(CMK)는 이 규모에 과함. 월 $1 |
| 용량 | `PAY_PER_REQUEST` | 데모 규모에서 프로비저닝은 낭비 |

---

## 5. Lambda 런타임 의존성

```
# infra/lambda/agent_broker/requirements.txt
pydantic==2.10.4
```

`boto3`는 Lambda 런타임이 제공하므로 넣지 않는다.
**단 런타임 boto3 버전이 오래되면 `bedrock-runtime`이 없을 수 있다.** Python 3.12 런타임의 boto3는 충분히 최신이지만, 배포 후 첫 호출로 확인한다 (완료 기준에 포함). 실패하면 `boto3==1.35.90`을 번들에 추가한다.

> 이 위험은 실측에서 이미 겪었다 — 로컬 anaconda의 구버전 botocore가 `bedrock` 서비스를 모르는 문제. 같은 함정이 Lambda에도 있을 수 있다.

**번들 파일** (U1에서 복사)
```
_bundled/validator.py     검증 6단계 (5단계는 원문이 없어 실행 불가)
_bundled/schemas.py       TaskSchema, Vocabulary, SlotDef
_bundled/vocab.json       어휘 사전
```

---

## 6. 관측

| 항목 | 선택 | 근거 |
|---|---|---|
| 로그 | CloudWatch Logs, **보존 90일** | SECURITY-14 |
| 메트릭 | Metric Filter (로그 → 메트릭) | 커스텀 메트릭 API 호출보다 저렴하고 코드가 단순 |
| 알람 | CloudWatch Alarms 5개 | |
| 알림 대상 | **없음 (알람만)** | SNS·Slack 연동은 5일 일정 밖. 대시보드를 열어 확인한다. **한계로 명시** |
| 대시보드 | CloudWatch Dashboard 1개 | 시연 중 열어 보여줄 수 있다 |
| 추적 | **X-Ray 미사용** | 호출 체인이 API GW → Lambda → Bedrock 3단계뿐. 로그로 충분 |

**알림 대상이 없는 것을 한계로 기록한다.** `ValidationFailureAlarm`이 발동해도 아무도 즉시 모른다. 데모 중에는 대시보드를 열어두고, 실배포에서는 SNS → Slack이 필요하다.

---

## 7. 테스트

| 항목 | 선택 |
|---|---|
| CDK 단위 테스트 | `aws_cdk.assertions.Template` — 합성 결과 어서션 |
| Lambda 단위 테스트 | `pytest` + 이벤트 픽스처 |
| 통합 테스트 | 배포 후 `httpx`로 실제 API 호출 (`tests/eval/test_broker_integration.py`) |
| PBT | **CDK에는 미적용** (선언적 정의, 속성 없음). `validator.py`는 U1 PBT가 커버 |

**핵심 CDK 어서션 테스트** (SECURITY-06 강제)
```python
def test_no_wildcard_actions():
    t = Template.from_stack(broker_stack)
    for res in t.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            for action in as_list(stmt["Action"]):
                assert "*" not in action, f"wildcard action: {action}"

def test_lambda_cannot_delete_audit():
    # dynamodb:DeleteItem / logs:Delete* 가 정책에 없음
def test_audit_table_protected():
    # PointInTimeRecoverySpecification + DeletionProtectionEnabled
def test_api_requires_key():
    # 모든 AWS::ApiGateway::Method 에 ApiKeyRequired: true
def test_log_retention_90_days():
    # 모든 AWS::Logs::LogGroup 에 RetentionInDays: 90
```

**이게 SECURITY 규칙을 CI에서 강제하는 방법이다.** 문서에 "와일드카드 금지"라고 쓰는 것보다 테스트가 실패하는 게 확실하다.

---

## 8. `cdk.json`

```json
{
  "app": "python3 app.py",
  "context": {
    "@aws-cdk/core:newStyleStackSynthesis": true,
    "@aws-cdk/aws-iam:minimizePolicies": true,
    "mesh:account": "891401657794",
    "mesh:region": "us-east-1",
    "mesh:allowedOrigin": "http://localhost:8080",
    "mesh:agentModelId": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "mesh:draftModelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "mesh:logRetentionDays": 90,
    "mesh:throttleRateLimit": 5,
    "mesh:throttleBurstLimit": 100,
    "mesh:dailyQuota": 2000
  },
  "watch": { "exclude": ["**/.venv/**", "**/__pycache__/**"] }
}
```

**계정·리전을 context에 하드코딩하는 이유**: 다른 계정에 실수로 배포하는 것을 막는다. 계정이 바뀌면 여기를 고쳐야 하고, 그게 명시적인 게 낫다.

`app.py`가 실제 계정과 context를 비교해 다르면 **합성을 거부**한다.

```python
env = cdk.Environment(account=ctx("mesh:account"), region=ctx("mesh:region"))
```

---

## 9. 이 유닛이 도입하지 않는 것

| 안 쓰는 것 | 이유 |
|---|---|
| VPC · NAT · PrivateLink | NAT 비용이 나머지 전체보다 크다. SECURITY-07 N/A 근거 문서화 |
| CloudFront + S3 (UI) | **UI는 로컬 서빙.** 재수화된 답변이 클라우드를 지나면 보안 모델이 무너진다 |
| Cognito | 사용자 인증 범위 밖 |
| WAF | 월 $5+. API Key + Usage Plan으로 충분한 규모 |
| Step Functions | 30초 이내 단순 동기 처리 |
| SQS · EventBridge | 비동기 필요 없음 |
| X-Ray | 3단계 체인 |
| Lambda Layer | 의존성 `pydantic` 하나 |
| Provisioned Concurrency | 시간당 과금. 워밍업 호출로 대체 |
| CI/CD (CodePipeline) | 5일. `make deploy` |
| 멀티 리전 | 계정 정책이 us-east-1만 |
| Secrets Manager 자동 회전 | 해커톤 후 키 폐기 |
| CMK (고객 관리 키) | 월 $1. AWS 관리 키로 SECURITY-01 충족 |

---

## 10. 요약 — 왜 이 스택이 최소인가

리소스 12개, 스택 3개, Lambda 1개, 의존성 1개.

**추가할 때마다 다음을 자문한다**: 이게 없으면 (a) 보안 규칙을 못 지키는가 (b) 데모가 안 되는가?
둘 다 아니면 넣지 않는다.

예: WAF는 (a) SECURITY-07/08을 API Key + 스로틀로 이미 충족 (b) 데모와 무관 → 제외.
예: `AuditMirror` PITR은 (a) SECURITY-14 필수 (b) "지울 수 없는 감사"가 데모 장면 → 포함.

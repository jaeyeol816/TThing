# U5 — Infrastructure Design

**IaC**: AWS CDK (Python) · **리전**: `us-east-1` 고정 · **계정**: `891401657794` (워크샵)
**CLI**: `npx aws-cdk@2` (전역 설치 안 함, NFR-PO-03)

---

## 1. 계정 제약 (실측)

| 항목 | 실측 결과 | 영향 |
|---|---|---|
| 역할 | `WSParticipantRole/Participant` | 임시 STS 자격증명 |
| 정책 | `PowerUserAccess`, `workshop-iam-1`, `ws-default-policy`, `landing-console-0` | |
| `iam:CreateRole`/`AttachRolePolicy`/`CreatePolicy` | **허용** | CDK 부트스트랩 가능 |
| `iam:PassRole` | lambda·apigateway·cloudformation·dynamodb·bedrock 등으로 허용 | CDK 배포 가능 |
| `bedrock:Invoke*` | 허용 | 브로커 동작 가능 |
| 리전 | **us-east-1 외 대부분 Deny** | 전 스택 us-east-1 |
| CDK 부트스트랩 | **아직 안 됨** | Day 0 작업 |
| 보호된 역할 | `WSParticipantRole`·`WSOpsRole` 변경 Deny | 우리 역할을 건드리지 않으므로 무관 |

**부트스트랩 명령**
```bash
npx aws-cdk@2 bootstrap aws://891401657794/us-east-1
```

기본 부트스트랩은 `cdk-hnb659fds-cfn-exec-role`에 `AdministratorAccess`를 붙인다.
`workshop-iam-1`이 `AttachRolePolicy`를 `*`에 허용하므로 통과할 것으로 예상되지만, 실패하면:
```bash
npx aws-cdk@2 bootstrap aws://891401657794/us-east-1 \
  --cloudformation-execution-policies arn:aws:iam::aws:policy/PowerUserAccess
```
(단 이 경우 IAM 역할 생성이 막히므로 `--trust`와 조합이 필요할 수 있다. Day 0에 확인한다.)

> **워크샵 계정은 회수될 수 있다.** 그래서 U5는 데모의 **필수 경로가 아니다.**
> `AGENT_TRANSPORT=direct`가 항상 동작해야 한다 (FR-49).

---

## 2. 스택 구성

```
infra/
  app.py                          CDK 앱. env=us-east-1 고정
  cdk.json
  requirements.txt                aws-cdk-lib 고정 버전
  stacks/
    hub_stack.py                  MeshHubStack        — DynamoDB 3개
    broker_stack.py               MeshBrokerStack     — Lambda + API Gateway
    observability_stack.py        MeshObsStack        — 알람 · 대시보드
  lambda/agent_broker/
    handler.py
    requirements.txt
    _bundled/                     빌드 시 U1에서 복사 (validator.py, schemas.py, vocab.json)
```

**스택 3개로 나눈 이유**: `hub`(데이터)를 `broker`(컴퓨트)와 분리하면 브로커를 재배포해도 감사 테이블이 영향받지 않는다. 데모 중 Lambda를 고쳐 재배포할 때 감사 기록이 날아가면 곤란하다.

```
MeshHubStack   (변경 드묾, 삭제 보호)
      |
      v  테이블 ARN 참조
MeshBrokerStack  (자주 재배포)
      |
      v  Lambda/API ARN 참조
MeshObsStack   (알람)
```

---

## 3. 리소스 목록

### MeshHubStack

| 리소스 | 논리 ID | 설정 |
|---|---|---|
| DynamoDB `AuditMirror` | `AuditMirrorTable` | PK `record_id`, GSI `at-index`. **저장 시 암호화(AWS 관리 키)** · **PITR 활성** · **`RemovalPolicy.RETAIN`** · **`deletionProtection=True`** · `PAY_PER_REQUEST` |
| DynamoDB `AgentRegistry` | `AgentRegistryTable` | PK `entity_id`. 암호화 · `PAY_PER_REQUEST` · `RemovalPolicy.DESTROY` |
| DynamoDB `InboxMirror` | `InboxMirrorTable` | PK `item_id`, GSI `owner-index`. 암호화 · `PAY_PER_REQUEST` · `RemovalPolicy.DESTROY` |

**`AuditMirror`만 `RETAIN` + 삭제 보호인 이유**: 감사 로그는 증거다. `cdk destroy`로 사라지면 안 된다 (NFR-S-14). 나머지 두 개는 로컬이 원본이므로 재생성 가능하다.

**`PAY_PER_REQUEST`**: 데모 규모(수십~수백 건)에서 프로비저닝 용량은 낭비다.

### MeshBrokerStack

| 리소스 | 논리 ID | 설정 |
|---|---|---|
| Lambda | `AgentBrokerFunction` | Python 3.12 · 512MB · **timeout 29s** · `reservedConcurrentExecutions=5` |
| IAM Role | `AgentBrokerRole` | §4 참조 (와일드카드 없음) |
| API Gateway REST | `BrokerApi` | 스테이지 `prod` · 액세스 로깅 + 실행 로깅 · **API Key 필수** |
| Usage Plan | `BrokerUsagePlan` | **5 rps / burst 100 / 일일 쿼터 2000** |
| API Key | `BrokerApiKey` | 값은 Secrets Manager에 저장 |
| Secret | `BrokerApiKeySecret` | 로컬 `.env`에 복사해서 쓴다 |
| Log Group | `/aws/lambda/AgentBrokerFunction` | **보존 90일** |
| Log Group | `/aws/apigateway/BrokerApi` | **보존 90일** |

**`timeout 29s`**: API Gateway REST의 통합 타임아웃 상한이 29초다. Lambda를 그보다 길게 두면 API Gateway가 먼저 끊고 Lambda는 계속 돌아 비용만 든다.

**`reservedConcurrentExecutions=5`**: 데모 규모에 5면 충분하고, 폭주 시 계정 전체 동시성을 잡아먹지 않는다. 비용 방어.

**API Gateway REST(v1)를 쓴 이유**: API Key + Usage Plan이 네이티브로 지원된다. HTTP API(v2)는 Lambda authorizer를 따로 만들어야 한다. 5일 일정에서 REST가 낫다.

### MeshObsStack

| 리소스 | 설정 |
|---|---|
| Metric Filter `ValidationFailure` | Lambda 로그에서 `"metric":"validation_failure"` 카운트 |
| Alarm `ValidationFailureAlarm` | 1건 이상 / 5분 → **즉시 알람** |
| Alarm `BrokerErrorAlarm` | Lambda `Errors` ≥ 3 / 5분 |
| Alarm `BrokerThrottleAlarm` | Lambda `Throttles` ≥ 1 / 5분 |
| Alarm `Api4xxAlarm` | API `4XXError` ≥ 10 / 5분 (인증 실패 급증 = 남용 시도) |
| Alarm `BedrockCostGuard` | Lambda `Invocations` ≥ 500 / 1시간 |
| Dashboard `MeshBrokerDashboard` | 위 지표 + p50/p99 지연 |

**`ValidationFailureAlarm`의 임계값이 1인 것이 의도적이다** (NFR-S-14).
브로커에서 검증이 실패한다는 것은 **노트북 코드에 버그가 있다는 신호**다. 정상 동작에서는 절대 발생하지 않는다. 1건도 놓치지 않는다.

**`Api4xxAlarm`이 남용 탐지다.** 403이 급증하면 누군가 API Key를 추측하고 있다는 뜻이다.

---

## 4. IAM — 최소 권한 (SECURITY-06)

```python
broker_role = iam.Role(self, "AgentBrokerRole",
    assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))

# 1. Bedrock — 특정 추론 프로파일과 그 기반 모델만
broker_role.add_to_policy(iam.PolicyStatement(
    actions=["bedrock:InvokeModel"],
    resources=[
        f"arn:aws:bedrock:us-east-1:{acct}:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        f"arn:aws:bedrock:us-east-1:{acct}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        # 추론 프로파일은 기반 foundation-model 권한도 요구한다
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
        # 크로스리전 추론 프로파일은 us-east-2/us-west-2 의 기반 모델도 요구한다
        "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
        "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
        "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
        "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
    ]))

# 2. 감사 테이블 — PutItem 만. DeleteItem/UpdateItem 없음  <- NFR-S-14
broker_role.add_to_policy(iam.PolicyStatement(
    actions=["dynamodb:PutItem"],
    resources=[audit_table.table_arn]))

# 3. 레지스트리 · 인박스 미러 — 읽기/쓰기 분리 (SECURITY-06)
broker_role.add_to_policy(iam.PolicyStatement(
    actions=["dynamodb:GetItem", "dynamodb:Query"],
    resources=[registry_table.table_arn, f"{registry_table.table_arn}/index/*"]))
broker_role.add_to_policy(iam.PolicyStatement(
    actions=["dynamodb:PutItem"],
    resources=[inbox_table.table_arn]))

# 4. 로깅 — 자기 로그 그룹만. DeleteLogGroup 없음  <- NFR-S-14
broker_role.add_to_policy(iam.PolicyStatement(
    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
    resources=[f"arn:aws:logs:us-east-1:{acct}:log-group:/aws/lambda/AgentBrokerFunction:*"]))
```

**`AWSLambdaBasicExecutionRole` 관리형 정책을 쓰지 않는다.** 그건 `logs:*`에 가까운 권한을 주고 `resources: *`를 쓴다. SECURITY-06 위반이다.

### 와일드카드 예외 (문서화된 것만)

| 리소스 | 와일드카드 | 근거 |
|---|---|---|
| `foundation-model/...` | 리전 부분이 `::` (계정 없음) | Bedrock 기반 모델 ARN은 계정을 갖지 않는다. AWS 스펙 |
| `.../index/*` | GSI 인덱스 | DynamoDB Query가 GSI ARN을 요구하고 인덱스별 열거는 실용적이지 않다 |
| `log-group:...:*` | 로그 스트림 | 로그 스트림 이름이 런타임에 생성된다. AWS 표준 패턴 |

액션 와일드카드는 **0개**다.

---

## 5. Lambda 코드 번들

```
infra/lambda/agent_broker/
  handler.py
  requirements.txt          boto3 는 런타임 제공. pydantic 만
  _bundled/                 <- 빌드 시 U1에서 복사
    validator.py
    schemas.py
    vocab.json
```

**빌드 단계** (`Makefile`)
```makefile
bundle-lambda:
	rm -rf infra/lambda/agent_broker/_bundled
	mkdir -p infra/lambda/agent_broker/_bundled
	cp src/mesh/validator.py src/mesh/schemas.py infra/lambda/agent_broker/_bundled/
	cp data/vocab.json infra/lambda/agent_broker/_bundled/

deploy: bundle-lambda
	cd infra && npx aws-cdk@2 deploy --all --require-approval never
```

**심볼릭 링크가 아니라 복사인 이유**: CDK 번들링이 심링크를 따라가지 않고, 복사하면 **배포된 검증기가 어느 버전인지 명확**하다. 대신 `vocab.json`이 바뀌면 재배포가 필요하고, 이걸 잊으면 로컬과 클라우드의 어휘 사전이 갈린다.

**방어**: `handler.py`가 `vocab.json`의 SHA-256을 응답에 포함하고, 로컬이 자기 것과 비교해 다르면 **경고**한다 (차단하지는 않는다 — 데모를 죽이지 않기 위해).

`pydantic`은 Lambda Layer 대신 `requirements.txt`로 번들한다 (CDK `PythonFunction` 또는 `bundling`). 의존성이 하나뿐이라 Layer를 만들 이유가 없다.

---

## 6. API 계약

```
POST https://{api-id}.execute-api.us-east-1.amazonaws.com/prod/agent/invoke
x-api-key: <BROKER_API_KEY>
Content-Type: application/json
```

요청/응답은 `aidlc-docs/inception/application-design/services.md` §3 참조.

| 엔드포인트 | Lambda | 인증 |
|---|---|---|
| `POST /agent/invoke` | `AgentBrokerFunction` | API Key |
| `POST /audit/mirror` | 동일 함수, 라우팅 분기 | API Key |
| `GET /agents` | 동일 함수 | API Key |

**Lambda를 하나만 쓰는 이유**: 코드가 200줄 이내고, 함수를 3개로 나누면 CDK·IAM·로그 그룹이 3배가 된다. 5일 일정에서 이득이 없다.

**CORS**: `Access-Control-Allow-Origin: http://localhost:8080`만. 와일드카드 금지 (NFR-S-08).

---

## 7. 비용 추정 (데모 규모)

| 리소스 | 사용량 | 월 비용 |
|---|---|---|
| Lambda | 500 호출 × 3s × 512MB | < $0.05 |
| API Gateway REST | 500 요청 | < $0.01 |
| DynamoDB (on-demand) | 쓰기 1000, 읽기 500 | < $0.01 |
| CloudWatch Logs | 100MB, 90일 보존 | < $0.10 |
| CloudWatch 알람 | 5개 | $0.50 |
| **Bedrock** | 500 호출 × (400 in / 300 out) | **~$3** |
| **합계** | | **~$4** |

Bedrock이 비용의 대부분이다. 방어 3중:
1. `AgentConfig.daily_limit=50` (로컬)
2. Usage Plan 일일 쿼터 2000 (API Gateway)
3. `BedrockCostGuard` 알람 500 호출/시간 (CloudWatch)

**정리**: 해커톤 종료 후 `make destroy`. `AuditMirror`만 `RETAIN`으로 남으므로 필요하면 콘솔에서 수동 삭제한다 (삭제 보호를 먼저 끈다).

---

## 8. 배포 절차

```bash
# Day 0 (한 번만)
export AWS_REGION=us-east-1
source .kiro/.env                                    # 임시 STS 자격증명
cd infra && npx aws-cdk@2 bootstrap aws://891401657794/us-east-1

# 매 배포
make bundle-lambda
cd infra && npx aws-cdk@2 diff                       # 변경 확인
cd infra && npx aws-cdk@2 deploy --all

# 출력에서 API URL 확보 -> .env 에 기록
# API Key 값 확보 (Secrets Manager)
```

**출력값 (CfnOutput)**
| 이름 | 용도 |
|---|---|
| `BrokerApiUrl` | 로컬 `.env`의 `BROKER_API_URL` |
| `BrokerApiKeySecretArn` | API Key 값 조회 경로 |
| `AuditMirrorTableName` | 감사 미러 확인용 |
| `DashboardUrl` | 시연 중 모니터링 |

**자격증명이 임시 STS라 만료된다.** 배포 전에 `make preflight`로 확인하고, 만료됐으면 워크샵 콘솔에서 새 자격증명을 받아 `.kiro/.env`를 갱신한다.

---

## 9. 이 스택이 하지 않는 것

| 안 만드는 것 | 이유 |
|---|---|
| VPC · 서브넷 · NAT | Lambda가 퍼블릭. DynamoDB·Bedrock은 AWS 엔드포인트. VPC를 만들면 NAT 비용이 나머지 전체보다 크다. SECURITY-07을 N/A로 기록 |
| CloudFront + S3 (UI 호스팅) | **웹 UI는 로컬에서 서빙한다.** 재수화된 답변(실제 이름 포함)이 클라우드를 경유하면 보안 모델이 무너진다 |
| Cognito | 사용자 인증이 범위 밖 |
| WAF | API Key + Usage Plan으로 충분한 규모. WAF는 월 $5+ |
| RDS · ElastiCache | DynamoDB로 충분 |
| Step Functions | 30초 이내 동기 처리 |
| CI/CD 파이프라인 | 5일. 로컬에서 `make deploy` |
| 멀티 리전 | 계정 정책이 us-east-1만 허용 |

**CloudFront를 만들지 않는 결정이 가장 중요하다.** UI를 클라우드에 올리면 편하지만, 그 UI가 받는 데이터는 **재수화된 실제 이름**이다. 신뢰 구역 안에 있어야 한다.

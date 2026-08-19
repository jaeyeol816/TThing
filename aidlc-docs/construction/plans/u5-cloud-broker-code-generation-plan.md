# U5 `cloud-broker` — Code Generation Plan

**소유**: A · **일정**: Day 2 (골격) ~ Day 3 (배포) · **스토리**: 3개 주담당
**설계 근거**: `aidlc-docs/construction/u5-cloud-broker/`
**코드 위치**: `infra/` (CDK Python), `infra/lambda/agent_broker/`

> **U5는 데모의 필수 경로가 아니다.** 워크샵 계정이 회수되거나 배포가 늦어도 `AGENT_TRANSPORT=direct`가 동작해야 한다 (FR-49).

---

## 유닛 컨텍스트

| | |
|---|---|
| **구현 스토리** | S-26, S-27, S-28 주담당 + S-05/S-21/S-24/S-29 협력 |
| **의존 (강함)** | U1 `validator.py`·`schemas.py` · U6 `vocab.json` (Lambda 번들) |
| **제공** | HTTP API 3개 — U1 `BrokerClient`가 소비 |
| **리전** | `us-east-1` 고정 (계정 정책) |

---

# Day 0 — 부트스트랩

## Step 1 · CDK 부트스트랩

- [ ] 1.1 `source .kiro/.env` — STS 자격증명 확인
- [ ] 1.2 `make preflight` — 자격증명 유효성 + 리전
- [ ] 1.3 `npx aws-cdk@2 bootstrap aws://891401657794/us-east-1`
- [ ] 1.4 실패 시 `--cloudformation-execution-policies` 조정 시도
- [ ] 1.5 그래도 실패하면 **`direct` 모드로 진행하고 U5를 후순위로** (데모 우선)
- [ ] 1.6 `CDKToolkit` 스택 생성 확인

---

# Day 2 — 스택 골격

## Step 2 · `infra/` 골격

- [ ] 2.1 `infra/requirements.txt` — `aws-cdk-lib==2.173.1`, `constructs==10.4.2`
- [ ] 2.2 `make setup-infra` — 별도 venv (앱 의존성 6개 유지)
- [ ] 2.3 `infra/cdk.json` — context 9개 (`u5/nfr-requirements/tech-stack-decisions.md` §8)
- [ ] 2.4 `infra/app.py` — `env=Environment(account, region)` **context에서 읽고 실제 계정과 비교. 불일치 시 합성 거부** 🔴
- [ ] 2.5 태그: `Project=prompthon`, `Owner=hackathon-team`, `Ephemeral=true`

## Step 3 · `MeshHubStack`

- [ ] 3.1 `AuditMirrorTable` — PK `record_id`, GSI `at-index`
- [ ] 3.2 **암호화(AWS 관리 키) + PITR + `deletionProtection=True` + `RemovalPolicy.RETAIN`** 🔴
- [ ] 3.3 `AgentRegistryTable` — PK `entity_id`, 암호화, `RemovalPolicy.DESTROY`
- [ ] 3.4 `InboxMirrorTable` — PK `item_id`, GSI `owner-index`, 암호화, `DESTROY`
- [ ] 3.5 전부 `PAY_PER_REQUEST`
- [ ] 3.6 `CfnOutput`: `AuditMirrorTableName`

## Step 4 · `bundle-lambda` + Lambda 핸들러

- [ ] 4.1 `Makefile`의 `bundle-lambda` 타깃 (`shared-infrastructure.md` §3)
- [ ] 4.2 `infra/lambda/agent_broker/requirements.txt` — `pydantic==2.10.4`만
- [ ] 4.3 `handler.py` 골격 (`u5/nfr-design/logical-components.md` §2)
- [ ] 4.4 콜드 스타트 시 `vocab.json` 로드 + `VOCAB_SHA` 계산
- [ ] 4.5 **`ALLOWED_MODELS` 하드코딩 2개** 🔴 (비용 방어)
- [ ] 4.6 `InvokeRequest` pydantic 모델
- [ ] 4.7 `_invoke()` — 순서: pydantic → 모델 허용목록 → `payload_sha256` → **재검증** → 감사 → Bedrock
- [ ] 4.8 **재검증에 `_bundled/validator.py` 사용. 5단계는 `originals=[]`로 스킵** (원문이 없다)
- [ ] 4.9 재검증 실패 → **`{"metric":"validation_failure"}` 로그 + 400**
- [ ] 4.10 **400 응답에 필드명·값을 담지 않는다** 🔴 (`stage`만, BR 정보 오라클 방지)
- [ ] 4.11 **로그에도 필드·값 미기록** 🔴
- [ ] 4.12 **감사 기록을 Bedrock 호출 *전*에.** 실패 시 503 + Bedrock 미호출 🔴
- [ ] 4.13 결과를 **새 레코드로 추가** (`UpdateItem` 권한 없음)
- [ ] 4.14 응답에 `revalidated: true` + `vocab_sha256`
- [ ] 4.15 `_mirror()`, `_agents()` 라우팅
- [ ] 4.16 전역 예외 → **일반화된 500** (스택 트레이스 금지)
- [ ] 4.17 JSON 구조화 로깅 (`correlation_id` = `envelope_id`)
- [ ] 4.18 `tests/unit/test_broker_handler.py` — 이벤트 픽스처 기반

## Step 5 · `MeshBrokerStack` — IAM 최소 권한 🔴

- [ ] 5.1 `AgentBrokerRole` — `ServicePrincipal("lambda.amazonaws.com")`
- [ ] 5.2 **`AWSLambdaBasicExecutionRole` 관리형 정책을 쓰지 않는다** (`resources: *`이므로)
- [ ] 5.3 Bedrock: `InvokeModel`을 **추론 프로파일 2개 + 기반 모델 ARN**으로 한정
- [ ] 5.4 크로스리전 추론 프로파일용 `us-east-2`/`us-west-2` 기반 모델 ARN 포함
- [ ] 5.5 감사 테이블: **`PutItem`만.** `DeleteItem`/`UpdateItem` 없음 🔴
- [ ] 5.6 레지스트리: `GetItem`/`Query` (읽기). 인박스: `PutItem` (쓰기) — **읽기/쓰기 분리**
- [ ] 5.7 로그: `CreateLogStream`/`PutLogEvents`를 **자기 로그 그룹 ARN으로 한정.** `Delete*` 없음 🔴
- [ ] 5.8 **액션 와일드카드 0개 확인**

## Step 6 · `MeshBrokerStack` — 컴퓨트와 API

- [ ] 6.1 Lambda: Python 3.12, `arm64`, 512MB, **timeout 29s**, `reservedConcurrentExecutions=5`
- [ ] 6.2 환경변수: 테이블 이름, `ALLOWED_ORIGIN`
- [ ] 6.3 Log Group `/aws/lambda/AgentBrokerFunction` — **보존 90일** 🔴
- [ ] 6.4 API Gateway REST `BrokerApi`, 스테이지 `prod`
- [ ] 6.5 **액세스 로깅 + 실행 로깅 활성** 🔴 (SECURITY-02), 보존 90일
- [ ] 6.6 3개 메서드 — 전부 **`api_key_required=True`** 🔴
- [ ] 6.7 요청 크기 제한 32KB + Request Validator
- [ ] 6.8 **CORS: `http://localhost:8080`만. 와일드카드 금지** 🔴
- [ ] 6.9 `ApiKey` + `UsagePlan` — **5 rps / burst 100 / 일일 2000** 🔴
- [ ] 6.10 API Key 값을 Secrets Manager `BrokerApiKeySecret`에 저장
- [ ] 6.11 `CfnOutput`: `BrokerApiUrl`, `BrokerApiKeySecretArn`

## Step 7 · CDK 어서션 테스트 (Policy-as-Test) 🔴

`tests/infra/test_stacks.py` — **`cdk synth`만으로 실행. AWS 자격증명 불필요.**

- [ ] 7.1 `test_no_wildcard_actions()`
- [ ] 7.2 `test_resource_wildcards_allowlisted()` — 문서화된 3건만
- [ ] 7.3 `test_lambda_cannot_delete_audit()` — `dynamodb:Delete*` 부재
- [ ] 7.4 `test_lambda_cannot_delete_logs()` — `logs:Delete*` 부재
- [ ] 7.5 `test_audit_table_protected()` — PITR + 삭제보호 + 암호화
- [ ] 7.6 `test_all_tables_encrypted()`
- [ ] 7.7 `test_api_requires_key()` — 모든 메서드
- [ ] 7.8 `test_api_logging_enabled()`
- [ ] 7.9 `test_log_retention_90_days()`
- [ ] 7.10 `test_cors_not_wildcard()`
- [ ] 7.11 `test_lambda_reserved_concurrency()`
- [ ] 7.12 `test_usage_plan_throttle()`
- [ ] 7.13 `test_region_is_us_east_1()`
- [ ] 7.14 `test_no_public_s3_bucket()` (회귀 방지)
- [ ] 7.15 `make test`에 포함

---

# Day 3 — 관측과 배포

## Step 8 · `MeshObsStack`

- [ ] 8.1 Metric Filter `ValidationFailureFilter` — `$.metric == "validation_failure"`
- [ ] 8.2 **`ValidationFailureAlarm` — 임계값 1** 🔴 (정상 동작에서는 발생하지 않는다)
- [ ] 8.3 `Api4xxAlarm` — ≥10/5분 (API Key 추측 탐지)
- [ ] 8.4 `BrokerErrorAlarm` — Lambda `Errors` ≥3/5분
- [ ] 8.5 `BrokerThrottleAlarm` — `Throttles` ≥1/5분
- [ ] 8.6 `BedrockCostGuard` — `Invocations` ≥500/1시간
- [ ] 8.7 `MeshBrokerDashboard` — 호출 수 · p50/p99 · 오류율 · 검증 실패 · 토큰
- [ ] 8.8 `# TODO(prod): SNS topic + subscription` 주석 (알림 경로 없음이 한계)
- [ ] 8.9 `CfnOutput`: `DashboardUrl`

## Step 9 · 배포

- [ ] 9.1 `make bundle-lambda`
- [ ] 9.2 `cd infra && npx aws-cdk@2 diff`
- [ ] 9.3 `make deploy`
- [ ] 9.4 Secrets Manager에서 API Key 조회 → 로컬 `.env`의 `BROKER_API_KEY`
- [ ] 9.5 `.env`의 `BROKER_API_URL` = `BrokerApiUrl` 출력값
- [ ] 9.6 **Lambda 런타임 boto3가 `bedrock-runtime`을 아는지 첫 호출로 확인** 🔴
- [ ] 9.7 아니면 `boto3==1.35.90`을 Lambda 번들에 추가하고 재배포
- [ ] 9.8 워밍업 호출 1회

## Step 10 · 배포 후 검증 (`u5/nfr-requirements.md` §10)

- [ ] 10.1 API Key 없이 → **403**
- [ ] 10.2 잘못된 API Key → **403**
- [ ] 10.3 어휘 밖 페이로드 → **400** + `ValidationFailure` 메트릭 + 알람 발동 🔴
- [ ] 10.4 400 응답에 필드명·값 **부재** 확인 🔴
- [ ] 10.5 허용목록 밖 `model_id` → **400**
- [ ] 10.6 `payload_sha256` 불일치 → **400**
- [ ] 10.7 정상 페이로드 → 200 + `revalidated: true` + `vocab_sha256`
- [ ] 10.8 응답에 스택 트레이스 부재
- [ ] 10.9 5rps 초과 → **429**
- [ ] 10.10 감사 실패 주입 → Bedrock 미호출 확인
- [ ] 10.11 DynamoDB 콘솔에서 감사 레코드 확인
- [ ] 10.12 **`AGENT_TRANSPORT=broker`로 3막 전체 통과** 🔴
- [ ] 10.13 **`AGENT_TRANSPORT=direct`로도 3막 전체 통과** 🔴
- [ ] 10.14 `vocab.json`을 의도적으로 바꿔 `vocab_sha256` 경고 발생 확인
- [ ] 10.15 `tests/eval/test_broker_integration.py` 작성 (배포 후 실행용)
- [ ] 10.16 커밋

## Step 11 · 데모 준비

- [ ] 11.1 CloudWatch Dashboard를 별도 창에 열어둘 URL 확보
- [ ] 11.2 **DynamoDB 콘솔 + IAM 정책 화면 준비** — "우리 앱은 `PutItem`만 있어서 못 지웁니다"
- [ ] 11.3 Logs Insights 쿼리 저장 (검증 실패 이력)
- [ ] 11.4 시연 직전 워밍업 절차를 데모 대본에 포함

---

## 스토리 추적

| Story | 단계 | 완료 |
|---|---|:---:|
| S-26 CDK 없이도 돌고 있으면 더 안전 🔴 | 9, 10.12, 10.13 | [ ] |
| S-27 감사 로그를 앱이 못 지운다 🔴 | 3.2, 5.5, 5.7, 7.3~7.5 | [ ] |
| S-28 브로커 남용 방지 🔴 | 6.5~6.9, 8.3 | [ ] |
| S-05 유출 0건 (협력) | 3.2, 11.2 | [ ] |
| S-21 어휘 밖 차단 (협력) | 4.8, 10.3 | [ ] |
| S-24 새 컴퓨터 (협력) | 2.2, 6.9 (API Key로 자격증명 불필요) | [ ] |

---

## 완료 기준

`u5/nfr-requirements.md` §10 전체 + 아래.

- [ ] `cdk bootstrap` + `cdk deploy --all` 성공
- [ ] CDK 어서션 테스트 14개 통과 (자격증명 없이) 🔴
- [ ] 액션 와일드카드 0개 🔴
- [ ] Lambda 역할에 `dynamodb:Delete*`·`logs:Delete*` 부재 🔴
- [ ] `AuditMirror`에 PITR + 삭제보호 + 암호화 🔴
- [ ] 모든 API 메서드에 API Key 필수 🔴
- [ ] CORS 와일드카드 부재 🔴
- [ ] 로그 보존 90일 (Lambda + API GW) 🔴
- [ ] `broker`/`direct` 두 모드 모두 3막 통과 🔴
- [ ] `cdk destroy --all` 후 `AuditMirror`만 남음

## 보안 준수 요약

| 규칙 | 상태 | 단계 |
|---|---|---|
| SECURITY-01 | **준수** — DynamoDB 3개 암호화, 전 구간 TLS | 3.2~3.4 |
| SECURITY-02 | **준수** — API GW 액세스 + 실행 로깅, 90일 | 6.5 |
| SECURITY-03 | 준수 — JSON 구조화, 원문·키·값 미기록 | 4.11, 4.17 |
| SECURITY-04 | **N/A** — HTML 미서빙 (JSON API만). CloudFront·S3 웹호스팅 없음 | — |
| SECURITY-05 | 준수 — API GW Validator + pydantic + 32KB + 모델 허용목록 | 4.6, 4.7, 6.7 |
| SECURITY-06 | **준수** — 액션 와일드카드 0개. 리소스 와일드카드 3건 문서화 | 5, 7.1, 7.2 |
| SECURITY-07 | **N/A (근거 명시)** — VPC 미생성. NAT 비용이 전체보다 큼. 인바운드는 API GW 하나이고 §3 통제 | — |
| SECURITY-08 | **준수** — 인증 없는 엔드포인트 0개. CORS 와일드카드 금지 | 6.6, 6.8 |
| SECURITY-09 | 준수 — 기본 자격증명 없음, 오류 일반화, S3 미생성, Python 3.12 | 4.10, 4.16 |
| SECURITY-10 | 준수 — `aws-cdk-lib`·`pydantic` 정확 버전. `npx aws-cdk@2` 메이저 고정. `latest` 미사용 | 2.1, 4.2 |
| SECURITY-11 | **준수** — 브로커 자체가 ⑤겹. 레이트 리밋. 남용 시나리오 10개 문서화 | 4.8, 6.9 |
| SECURITY-12 | **준수** — 하드코딩 0건. API Key는 Secrets Manager. Lambda는 실행 역할 | 6.10 |
| SECURITY-13 | 준수 — `json.loads`만, `payload_sha256` 검증, CDN 미사용(SRI N/A) | 4.7 |
| SECURITY-14 | **준수** — 알람 5개, PITR+삭제보호, `Delete*` 권한 부재, 보존 90일. **알림 경로 없음이 한계** | 8 |
| SECURITY-15 | 준수 — 모든 AWS 호출에 `try/except ClientError`, fail closed | 4.12, 4.16 |

| PBT 규칙 | 상태 |
|---|---|
| PBT-01 | 준수 — 유일한 순수 로직은 번들된 `validator.py`이고 U1 §9에 정의됨. **중복 정의하지 않는다** |
| PBT-02 | **N/A** — 직렬화 왕복 쌍 없음 (U1에서 커버) |
| PBT-03 | 준수 (U1 PBT가 번들 코드를 보증) |
| PBT-04 | **N/A** — `/agent/invoke`는 멱등 아님 (Bedrock 호출 + 감사). 로컬 `take()` 일회용이 중복 방지 |
| PBT-05 | **N/A** — 참조 구현 없음 |
| PBT-06 | **N/A** — Lambda는 stateless |
| PBT-07~09 | 준수 (U1 공유). **CDK 스택에는 PBT 미적용** — 선언적 정의로 속성이 없다. `aws_cdk.assertions`가 적절 |
| PBT-10 | 준수 — Step 7 어서션 + Step 10 통합 테스트 (예제 기반) |

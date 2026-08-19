# U5 — Logical Components

---

## 1. 구성 요소 → 인프라 매핑

| 논리 구성 요소 | 인프라 리소스 | NFR |
|---|---|---|
| API 게이트웨이 | API Gateway REST `BrokerApi` (스테이지 `prod`) | SECURITY-02, 08 |
| 인증 | API Key + Usage Plan | SECURITY-08, 12 |
| 레이트 리밋 | Usage Plan 5 rps / burst 100 / 일일 2000 | SECURITY-11 |
| 요청 검증 (1차) | API Gateway Request Validator + 32KB 제한 | SECURITY-05 |
| 요청 검증 (2차) | Lambda pydantic | SECURITY-05 |
| **독립 재검증** | Lambda + `_bundled/validator.py` + `vocab.json` | SECURITY-11 |
| 무결성 검증 | `payload_sha256` 비교 | SECURITY-13 |
| 모델 호출 | `bedrock-runtime.Converse` | — |
| 모델 허용 목록 | Lambda 하드코딩 2개 | 비용 방어 |
| 자격증명 | Lambda 실행 역할 (`AgentBrokerRole`) | SECURITY-06, 12 |
| 시크릿 저장 | Secrets Manager `BrokerApiKeySecret` | SECURITY-12 |
| **변조 증거 감사** | DynamoDB `AuditMirror` (PITR + 삭제보호 + RETAIN) | SECURITY-14 |
| 인박스 미러 | DynamoDB `InboxMirror` | — |
| 에이전트 레지스트리 | DynamoDB `AgentRegistry` | — |
| 구조화 로깅 | CloudWatch Logs (90일) | SECURITY-03, 14 |
| 액세스 로깅 | API Gateway 액세스 + 실행 로깅 (90일) | SECURITY-02 |
| 보안 알람 | CloudWatch Alarms 5개 | SECURITY-14 |
| 관측 대시보드 | CloudWatch Dashboard | SECURITY-14 |
| 영향 범위 제한 | `reservedConcurrentExecutions=5` | SECURITY-11 |
| 어휘 버전 고정 | `vocab_sha256` 응답 필드 | 무결성 |
| 정책 강제 | `aws_cdk.assertions` 테스트 | SECURITY-06 |

---

## 2. Lambda 핸들러 구조

```python
# infra/lambda/agent_broker/handler.py   (< 200줄 목표)

# ── 콜드 스타트 시 1회 ────────────────────────────────
VOCAB       = Vocabulary.load(BUNDLED / "vocab.json")
VOCAB_SHA   = sha256_file(BUNDLED / "vocab.json")
SCHEMAS     = load_task_schemas(BUNDLED / "vocab.json")
BANNED      = BannedTerms.load(BUNDLED / "vocab.json")
ALLOWED_MODELS = frozenset({
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
})
bedrock = boto3.client("bedrock-runtime")
ddb     = boto3.resource("dynamodb")

def handler(event, context):
    log = json_logger(correlation_id=extract_envelope_id(event))
    try:
        path = event["resource"]
        if   path == "/agent/invoke": return _invoke(event, log)
        elif path == "/audit/mirror": return _mirror(event, log)
        elif path == "/agents":       return _agents(event, log)
        return _reject(404, "not_found")
    except Exception:
        log.exception("unhandled")                  # 로그에만
        return _reject(500, "internal_error")       # 응답은 일반화 (SECURITY-09)

def _invoke(event, log):
    # 1. 파싱 + pydantic (SECURITY-05)
    try:
        req = InvokeRequest.model_validate_json(event["body"])
    except ValidationError:
        return _reject(400, "bad_request")

    # 2. 모델 허용 목록 (비용 방어)
    if req.model_id not in ALLOWED_MODELS:
        log.warning("model not allowed", extra={"model_id": req.model_id})
        return _reject(400, "model_not_allowed")

    # 3. 무결성 (SECURITY-13)
    if sha256_canonical(req.payload) != req.payload_sha256:
        return _reject(400, "integrity_mismatch")

    # 4. 독립 재검증 — 5단계(원문 n-gram)는 원문이 없어 실행 불가
    schema = SCHEMAS.get(req.task_schema_id)
    if schema is None:
        return _reject(400, "unknown_schema")
    vr = validator.validate(req.payload, schema, VOCAB, BANNED, originals=[])
    if not vr.passed:
        log.error("validation_failure", extra={
            "metric": "validation_failure",          # -> Metric Filter -> 알람
            "stage": vr.first_failed_stage,
            "envelope_id": req.envelope_id,
            # ⚠️ 필드명·값은 남기지 않는다 (로그 유출 대비)
        })
        return _reject(400, "payload_rejected", stage=vr.first_failed_stage)

    # 5. 감사 먼저 (Write-Before-Send)
    try:
        audit_put_request(req)
    except ClientError:
        log.error("audit unavailable")
        return _reject(503, "audit_unavailable")     # fail closed

    # 6. Bedrock
    try:
        out = bedrock.converse(
            modelId=req.model_id,
            system=[{"text": req.system_prompt}],
            messages=[{"role":"user","content":[{"text": json.dumps(req.payload)}]}],
            inferenceConfig={"maxTokens": 2000, "temperature": 0},
        )
    except ClientError as e:
        log.error("bedrock failed", extra={"code": e.response["Error"]["Code"]})
        return _reject(502, "upstream_unavailable")

    resp = parse_agent_response(out)

    # 7. 결과 레코드 추가 (UPDATE 아님 — UpdateItem 권한 없음)
    try:
        audit_put_result(req, resp, out.get("usage"))
    except ClientError:
        log.warning("audit result write failed")     # 요청은 이미 기록됨

    return _ok({**resp, "revalidated": True, "vocab_sha256": VOCAB_SHA})
```

**설계 판단**

| # | 판단 | 근거 |
|---|---|---|
| 1 | 콜드 스타트 시 어휘 사전을 한 번만 로드 | 호출마다 파일 파싱은 낭비 |
| 2 | 모델 허용 목록을 **하드코딩** | 환경변수로 두면 배포 설정 실수로 비싼 모델이 열린다 |
| 3 | 감사 실패 시 **503 + Bedrock 미호출** | 기록 없는 전송을 만들지 않는다 |
| 4 | 결과를 **새 레코드**로 추가 | `UpdateItem` 권한이 없다 (설계된 제약) |
| 5 | 오류 응답에 `stage`만 | 원문 추측 채널 차단 |
| 6 | 로그에도 필드·값 미기록 | 로그 유출 시 같은 문제 |
| 7 | `temperature=0` | 데모 재현성 |

---

## 3. 감사 레코드 스키마 (DynamoDB)

```
AuditMirror
  PK  record_id        (S)   "aud_01J..."
  GSI at-index
      PK  at           (S)   ISO 8601  (시간순 조회)

  속성
      kind                   "request" | "result"
      envelope_id      (S)   요청/결과 연결 키
      actor            (S)
      target_entity_id (S)
      model_id         (S)
      transport        (S)
      trusted_zone_llm_base_url (S)    ⚠️ 경계 위치 기록
      tier             (S)
      representation   (S)
      payload          (M)   전문 (이미 sanitize 됨)
      payload_sha256   (S)
      size_bytes       (N)
      validation_summary (S) "6/6"
      approved_by      (S)
      vocab_sha256     (S)
      # kind == "result" 인 경우 추가
      confidence       (N)
      citation_count   (N)
      usage            (M)
```

**`request`와 `result`를 별도 레코드로 두는 것이 `UpdateItem` 권한 부재의 결과다.**
제약이 오히려 감사 품질을 높였다 — 요청 레코드는 절대 수정되지 않으므로 "무엇을 보냈는가"가 불변이다.

**기록하지 않는 것**: 원문, 매핑 테이블, API 키, 시스템 프롬프트 전문(해시만), `reasoning*`.

시스템 프롬프트는 해시만 남긴다. 페르소나 정보를 반복 저장할 이유가 없고, 프롬프트가 바뀌었는지는 해시로 알 수 있다.

---

## 4. 메트릭 필터 → 알람

```python
# observability_stack.py
mf = logs.MetricFilter(self, "ValidationFailureFilter",
    log_group=broker_log_group,
    filter_pattern=logs.FilterPattern.string_value("$.metric", "=", "validation_failure"),
    metric_namespace="Mesh/Broker",
    metric_name="ValidationFailure",
    metric_value="1",
    default_value=0)

cw.Alarm(self, "ValidationFailureAlarm",
    metric=mf.metric(statistic="Sum", period=Duration.minutes(5)),
    threshold=1,                      # ⚠️ 1건도 놓치지 않는다
    evaluation_periods=1,
    comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
    alarm_description="Broker rejected a payload. Local validator has a bug or "
                      "someone bypassed the app. Investigate immediately.")
```

**커스텀 메트릭 API(`put_metric_data`)를 쓰지 않고 Metric Filter를 쓰는 이유**: Lambda 코드가 단순해지고(로그만 쓰면 된다), API 호출 비용·지연이 없고, IAM에 `cloudwatch:PutMetricData`를 추가하지 않아도 된다 (최소 권한).

**임계값 1의 의미**: 이 알람은 "정상 운영 중 발생하지 않아야 하는 일"을 감시한다. 로컬이 이미 검증한 것만 오기 때문이다.

알람 5개 전체는 `nfr-requirements.md` §5 참조.

---

## 5. 알림 경로 (한계)

| 항목 | 상태 |
|---|---|
| CloudWatch Alarm | ✅ 구성됨 |
| SNS 토픽 | ❌ 만들지 않음 |
| Slack·이메일 연동 | ❌ 만들지 않음 |
| 대시보드 | ✅ 시연 중 열어둔다 |

**알람이 울려도 아무도 즉시 모른다.** 5일 일정에서 알림 연동을 만들지 않았고, 이건 명시적 한계다.

**완화**: 데모 중 `MeshBrokerDashboard`를 별도 창에 열어둔다. `/api/health`가 브로커 상태를 노출하므로 로컬 UI에서도 이상을 볼 수 있다.

**실배포 요건**: SNS → Slack/PagerDuty. `observability_stack.py`에 `# TODO(prod): SNS topic + subscription` 주석을 남긴다.

---

## 6. Secrets Manager 흐름

```
1. CDK 가 API Key 생성 (api_gateway.ApiKey)
2. CDK 가 Secrets Manager 에 값 저장 (BrokerApiKeySecret)
3. CfnOutput 으로 Secret ARN 출력
4. 개발자가 한 번 조회해 로컬 .env 의 BROKER_API_KEY 에 기록
5. 로컬 앱이 요청 헤더 x-api-key 로 전송
```

**Lambda는 API Key를 읽지 않는다.** API Gateway가 검증한다. Lambda 역할에 `secretsmanager:GetSecretValue`가 없다 (최소 권한).

**로컬 `.env`는 gitignore.** 값이 저장소에 들어가지 않는다 (SECURITY-12).

키 조회 (한 번):
```bash
uv run --with boto3 python -c "
import boto3, json
arn = '<BrokerApiKeySecretArn from CfnOutput>'
print(boto3.client('secretsmanager').get_secret_value(SecretId=arn)['SecretString'])"
```

---

## 7. CDK 스택 의존 그래프

```
MeshHubStack
  exports: audit_table, registry_table, inbox_table
      |
      v
MeshBrokerStack
  imports: 위 3개 테이블 (직접 참조, cross-stack reference)
  exports: broker_function, broker_api
      |
      v
MeshObsStack
  imports: broker_function, broker_api, broker_log_group
```

**`app.py`에서 객체를 직접 넘긴다** (`Fn::ImportValue` 문자열 참조가 아니라).

```python
hub    = MeshHubStack(app, "MeshHubStack", env=env)
broker = MeshBrokerStack(app, "MeshBrokerStack", env=env,
                         audit_table=hub.audit_table,
                         registry_table=hub.registry_table,
                         inbox_table=hub.inbox_table)
obs    = MeshObsStack(app, "MeshObsStack", env=env,
                      fn=broker.function, api=broker.api,
                      log_group=broker.log_group)
```

CDK가 의존 순서와 export/import를 자동 처리한다. `cdk deploy --all`이 순서대로 배포한다.

**주의**: cross-stack 참조가 있으면 Hub 스택의 테이블을 지울 때 Broker를 먼저 지워야 한다. `AuditMirror`가 `RETAIN`이라 `destroy --all` 후에도 테이블은 남는다 (의도된 동작).

---

## 8. 검증 테스트 목록 (`tests/infra/test_stacks.py`)

```python
test_no_wildcard_actions()           # SECURITY-06
test_resource_wildcards_allowlisted() # 문서화된 3건만
test_lambda_cannot_delete_audit()    # NFR-S-14
test_lambda_cannot_delete_logs()     # NFR-S-14
test_audit_table_protected()         # PITR + 삭제보호 + 암호화
test_all_tables_encrypted()          # SECURITY-01
test_api_requires_key()              # SECURITY-08
test_api_logging_enabled()           # SECURITY-02
test_log_retention_90_days()         # SECURITY-14
test_cors_not_wildcard()             # SECURITY-08
test_lambda_reserved_concurrency()   # SECURITY-11
test_usage_plan_throttle()           # SECURITY-11
test_region_is_us_east_1()           # 계정 제약
test_no_public_s3_bucket()           # SECURITY-09 (버킷을 안 만들지만 회귀 방지)
```

**`make test`에 포함한다.** 배포 없이 `cdk synth` 결과만으로 실행되므로 빠르고 AWS 자격증명이 필요 없다 — 다른 컴퓨터에서도 돈다.

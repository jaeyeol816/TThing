# U5 — NFR Design Patterns

---

## 1. Independent Revalidation (독립 재검증) — U5의 존재 이유

**문제**: 노트북의 검증기에 버그가 있거나 누군가 앱을 우회하면 무엇이 막는가.

**패턴**: **다른 프로세스·다른 신뢰 도메인**에서 같은 검증을 다시 실행한다.

```python
# handler.py
from _bundled import validator, schemas

def handler(event, context):
    req = InvokeRequest.model_validate_json(event["body"])       # 1차: 스키마
    if sha256(canonical(req.payload)) != req.payload_sha256:     # 2차: 무결성
        return reject("integrity")
    vr = validator.validate(req.payload, schema, VOCAB, BANNED,
                            originals=[])                        # 3차: 어휘·범위·금칙어·크기
    if not vr.passed:
        emit_metric("validation_failure")                        # -> 알람 (임계 1)
        return reject(vr.first_failed_stage)                     # 필드·값 미포함
    resp = bedrock_converse(req)
    audit_put(req, resp)                                          # 실패 시 중단 (아래)
    return ok(resp, revalidated=True, vocab_sha256=VOCAB_SHA)
```

**왜 같은 코드를 두 번 돌리는 게 의미가 있는가**

| 막는 것 | 어떻게 |
|---|---|
| 로컬 앱의 **구현 실수** (검증 호출 누락) | Lambda가 독립적으로 실행 |
| 로컬 `vocab.json`의 **실수 수정** | `vocab_sha256` 비교로 발각 |
| 앱 **우회** (누가 브로커를 직접 호출) | 검증을 통과해야 한다 |
| 전송 중 **변경** | `payload_sha256` |

**막지 못하는 것 (정직하게)**: `validator.py` 자체의 **논리 오류**는 두 겹 모두 통과한다.
그래서 PBT(NFR-T-03)가 필요하다. **다층 방어는 구현 실수와 우회를 막고, 논리 오류는 속성 테스트가 막는다.** 둘은 대체 관계가 아니다.

**5단계(원문 5-gram)를 재실행할 수 없다.** 원문이 클라우드에 없다. 이건 설계상 옳다 — 원문을 클라우드에 보내면 재검증을 위해 유출하는 자기모순이 된다. 5단계는 로컬 전용이며 문서화한다.

---

## 2. Credential Elimination (자격증명 제거)

**문제**: 노트북의 AWS 자격증명이 임시 STS라 만료된다. 다른 컴퓨터로 옮기면 재설정이 필요하다.

**패턴**: **호출 주체를 이동시킨다.**

```
Before:  노트북 (STS 임시 자격증명) --> Bedrock
                만료 · 이식성 나쁨

After:   노트북 (API Key) --> API GW --> Lambda (실행 역할) --> Bedrock
                만료 없음        만료 없음
```

**효과**
1. 시연 중 자격증명 만료로 죽지 않는다
2. 컴퓨터를 바꿔도 API Key 하나만 옮기면 된다
3. 노트북에 AWS 권한이 전혀 없어도 동작한다 (배포할 때만 필요)

**트레이드오프**: API Key는 만료되지 않는 bearer 시크릿이다. STS 임시 토큰보다 이 점에서 나쁘다.
완화: Secrets Manager 저장 + 일일 쿼터 + `Api4xxAlarm` + 해커톤 후 폐기.

**이 트레이드오프를 감수하는 근거**: 이 엔드포인트가 할 수 있는 일이 **"검증을 통과한 페이로드로 Claude를 부르는 것" 하나**다. 키가 유출되면 비용 피해가 나지만 데이터 유출은 없다. 반면 STS 만료는 데모를 죽인다. 위험의 성격이 다르다.

---

## 3. Tamper-Evident Audit (변조 증거가 남는 감사)

**문제**: 로컬 SQLite 감사 로그는 사용자가 파일을 지울 수 있다. 그러면 증거로서 약하다.

**패턴**: **애플리케이션이 자기 감사 기록을 삭제할 권한을 갖지 않게** 만든다.

| 통제 | 구현 |
|---|---|
| 추가만 가능 | Lambda 역할에 `dynamodb:PutItem`만. `DeleteItem`·`UpdateItem` 없음 |
| 스택 삭제로도 안 사라짐 | `RemovalPolicy.RETAIN` + `deletionProtection=True` |
| 시점 복구 | PITR 활성 |
| 로그 삭제 불가 | Lambda 역할에 `logs:DeleteLogGroup`·`DeleteLogStream` 없음 |
| 무결성 | `payload_sha256` |
| 보존 | 90일 |

**`AWSLambdaBasicExecutionRole`을 쓰지 않는 이유가 여기 있다.** 관리형 정책은 `resources: *`로 로그 권한을 주고, 그러면 앱이 자기 로그를 지울 수 있다. 손으로 정책을 쓴다.

**데모 활용**: 감사 로그 탭에서 0건을 보여준 뒤, **DynamoDB 콘솔 + IAM 정책**을 함께 보여준다.
> "이 테이블에 우리 앱은 `PutItem` 권한만 있습니다. 지울 수 없습니다."

증거력이 "우리 로그를 믿어주세요"에서 "권한이 없어서 못 지웁니다"로 올라간다.

---

## 4. Uninformative Rejection (정보를 주지 않는 거부)

**문제**: 검증 실패 응답이 원문 추측 채널이 될 수 있다.

**나쁜 응답**
```json
{"error":"vocab violation","field":"auth_mechanism_class","value":"EAP-AKA"}
```
공격자가 페이로드를 조금씩 바꿔가며 원문을 역추적할 수 있다. **오류 메시지가 오라클이 된다.**

**패턴**
```json
{"error":"payload_rejected","stage":"vocab","envelope_id":"env_01J..."}
```

| 담는 것 | 담지 않는 것 |
|---|---|
| 실패한 단계 이름 | 필드명 |
| `envelope_id` (로컬 추적용) | 값 |
| | 어휘 사전 내용 |
| | 스택 트레이스·내부 경로 (SECURITY-09) |

**상세는 로컬에 있다.** 로컬 `ValidationResult.checks[].offending`에 필드·값이 있으므로 개발자는 디버깅할 수 있다. 경계 밖으로 내보내지 않을 뿐이다.

CloudWatch 로그에도 값을 남기지 않는다 — 로그가 유출되면 같은 문제다.

---

## 5. Write-Before-Send (기록 후 전송)

**문제**: "나갔는데 기록이 없는" 경우를 만들지 않는다.

**패턴**: 감사 기록을 Bedrock 호출 **전에** 쓰고, 실패하면 **호출하지 않는다.**

```python
try:
    audit_put(req)                # 먼저
except ClientError:
    return reject("audit_unavailable")   # fail closed. Bedrock 호출 안 함
resp = bedrock_converse(req)      # 그 다음
audit_update_result(req, resp)    # 결과는 별도 레코드로 추가 (UPDATE 아님)
```

**순서를 바꾸면 안 된다.** 호출 후 기록하면, 호출 성공 + 기록 실패 시 유출이 기록 없이 일어난다.

`audit_update_result`가 `UpdateItem`이 아니라 **새 레코드 추가**인 것도 의도적이다 — Lambda에 `UpdateItem` 권한이 없다 (§3). 요청 레코드와 결과 레코드를 `envelope_id`로 연결한다.

**로컬도 같은 순서다** (BR-A-01).

---

## 6. Vocabulary Version Pinning (어휘 사전 버전 고정)

**문제**: 로컬과 Lambda의 `vocab.json`이 갈리면 다층 방어가 무의미해진다.

**패턴**: 양쪽이 SHA-256을 교환한다.

```
Lambda 응답:  { ..., "vocab_sha256": "a3f1..." }
로컬:         if resp.vocab_sha256 != local_vocab_sha256:
                  log.warning("vocab drift — redeploy broker")
                  health.vocab_drift = True     # /api/health 에 노출
```

**차단하지 않고 경고만 하는 이유**: 데모 중에 이걸로 죽으면 안 된다. 대신 헤더에 표시되고 로그에 남는다.

**근본 대응**: `Makefile`의 `deploy` 타깃이 `bundle-lambda`에 의존하므로 정상 흐름에서는 갈리지 않는다.
```makefile
deploy: bundle-lambda
	cd infra && npx aws-cdk@2 deploy --all
```
`vocab.json`을 고치고 `cdk deploy`를 잊는 것이 유일한 실수 경로이고, 그걸 경고가 잡는다.

---

## 7. Policy-as-Test (정책을 테스트로 강제)

**문제**: "IAM에 와일드카드 금지"를 문서에 쓰면 지켜지지 않는다.

**패턴**: `cdk synth` 결과에 어서션을 걸어 **CI가 실패**하게 만든다.

```python
def test_no_wildcard_actions():
    for res in template.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            for a in as_list(stmt["Action"]):
                assert "*" not in a

def test_lambda_cannot_delete_audit():
    assert not any(a.startswith("dynamodb:Delete") for a in all_actions())
    assert not any(a.startswith("logs:Delete") for a in all_actions())

def test_audit_table_protected():
    audit = find_table("AuditMirror")
    assert audit["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"]
    assert audit["DeletionProtectionEnabled"]
    assert audit["SSESpecification"] is not None

def test_api_requires_key():
    for m in template.find_resources("AWS::ApiGateway::Method").values():
        assert m["Properties"]["ApiKeyRequired"] is True

def test_log_retention():
    for lg in template.find_resources("AWS::Logs::LogGroup").values():
        assert lg["Properties"]["RetentionInDays"] == 90

def test_cors_not_wildcard():
    # OPTIONS 통합 응답의 Allow-Origin 이 '*' 가 아님
```

U1의 import 경계 테스트와 같은 발상이다. **규칙을 실행 가능하게 만든다.**

리소스 와일드카드 3건(문서화된 예외)은 허용 목록으로 처리한다.

---

## 8. Blast Radius Limiting (영향 범위 제한)

Lambda 하나가 잘못 돌아도 피해가 제한되게 만든다.

| 통제 | 값 | 막는 것 |
|---|---|---|
| `reservedConcurrentExecutions` | 5 | 계정 전체 Lambda 동시성 고갈 |
| Usage Plan 일일 쿼터 | 2000 | Bedrock 비용 폭주 |
| `model_id` 허용 목록 | 2개 하드코딩 | 비싼 모델(opus) 호출 |
| 요청 크기 | 32KB | 대용량 요청 DoS |
| 페이로드 크기 | 2KB (validator 6단계) | 토큰 폭주 |
| Lambda 타임아웃 | 29s | 무한 실행 |
| `BedrockCostGuard` 알람 | 500/시간 | 조기 발견 |

**`model_id` 허용 목록이 놓치기 쉬운 방어다.** 클라이언트가 모델 ID를 정하는 구조이므로, 검증하지 않으면 `us.anthropic.claude-opus-4-5`를 보내 비용을 10배로 만들 수 있다.

---

## 9. Stack Separation by Change Frequency (변경 빈도로 스택 분리)

```
MeshHubStack     변경 드묾. 데이터. RETAIN + 삭제 보호
      |  테이블 ARN
MeshBrokerStack  자주 재배포. 컴퓨트
      |  함수/API ARN
MeshObsStack     알람
```

**데모 중 Lambda를 고쳐 재배포할 때 감사 테이블이 영향받지 않아야 한다.**
`cdk deploy MeshBrokerStack`만 돌리면 Hub는 건드리지 않는다.

`cdk destroy --all` 시에도 `AuditMirror`는 `RETAIN`으로 남는다.

---

## 10. Graceful Degradation Chain (단계적 성능 저하)

브로커 장애가 데모를 죽이지 않게 만든다.

```
broker  ──장애──> direct  ──자격증명 만료──> mock  ──> (녹화 영상)
  |                 |                        |
  검증 2겹          검증 1겹                 검증 1겹 (실제로 동작)
  감사 2곳          감사 1곳                 감사 1곳
```

전환은 **환경변수 하나 + 앱 재시작.** 코드 변경 없다.

| 모드 | 방어 수준 | 언제 |
|---|---|---|
| `broker` | 최고 (재검증 + 지울 수 없는 감사) | 데모 기본 |
| `direct` | 로컬 검증만 | CDK 미배포·브로커 장애 |
| `mock` | 로컬 검증만 (실제 동작) | 오프라인·계정 회수 |

**`mock`에서도 검증이 실제로 돈다** (U1 §9 "거짓말하지 않는 목업"). 화면에 목업임을 표시한다.

---

## 11. 패턴 적용 요약

| 패턴 | 구현 위치 | 검증 |
|---|---|---|
| Independent Revalidation | `handler.py` + `_bundled/validator.py` | 어휘 밖 페이로드 → 400 + 알람 |
| Credential Elimination | Lambda 실행 역할 | 노트북에 AWS 자격증명 없이 `broker` 모드 동작 |
| Tamper-Evident Audit | `hub_stack.py` IAM + PITR | `test_lambda_cannot_delete_audit` |
| Uninformative Rejection | `handler.py` 오류 응답 | 400 응답에 필드·값 부재 |
| Write-Before-Send | `handler.py` 호출 순서 | 감사 실패 주입 → Bedrock 미호출 |
| Vocabulary Version Pinning | `vocab_sha256` 교환 | 의도적 drift → 경고 발생 |
| Policy-as-Test | `tests/infra/test_stacks.py` | CI 필수 |
| Blast Radius Limiting | Usage Plan + 동시성 + 허용 목록 | 5rps 초과 → 429 |
| Stack Separation | 스택 3개 | Broker 재배포 후 Hub 무변경 |
| Graceful Degradation | `AGENT_TRANSPORT` | 3개 모드 모두 3막 통과 |

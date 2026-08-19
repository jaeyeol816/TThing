# U5 — NFR Requirements

이 유닛은 **신뢰 구역 밖**에 있고 **인터넷에 노출된다.** SECURITY-* 규칙이 대부분 여기에 실제로 적용된다.

---

## 1. 보안 — SECURITY 규칙 준수 (U5 범위)

| 규칙 | 상태 | 구현 |
|---|:---:|---|
| **SECURITY-01** 암호화 | 준수 | DynamoDB 3개 모두 **저장 시 암호화(AWS 관리 키)**. API Gateway·Lambda·Bedrock·DynamoDB 전 구간 TLS 1.2+. API Gateway는 HTTP를 받지 않는다 |
| **SECURITY-02** 네트워크 중개 로깅 | **준수** | API Gateway `prod` 스테이지에 **액세스 로깅 + 실행 로깅** 활성. 로그 그룹 `/aws/apigateway/BrokerApi`, 보존 90일 |
| **SECURITY-03** 애플리케이션 로깅 | 준수 | Lambda가 JSON 구조화 로깅. `at`·`correlation_id`(요청의 `envelope_id`)·`level`·`message`. **원문·API 키·페이로드 값을 로그에 남기지 않는다** (검증 실패 시에도 필드명·값 미기록) |
| **SECURITY-04** HTTP 보안 헤더 | **N/A** | HTML을 서빙하지 않는다 (JSON API만). CloudFront·S3 웹 호스팅을 만들지 않았다. 근거를 CDK 주석에 명시 |
| **SECURITY-05** 입력 검증 | 준수 | §2 상세 |
| **SECURITY-06** 최소 권한 | **준수** | `infrastructure-design.md` §4. **액션 와일드카드 0개.** 리소스 와일드카드 3건은 AWS 스펙상 불가피하며 문서화 |
| **SECURITY-07** 네트워크 구성 | **N/A (근거 명시)** | VPC를 만들지 않는다 — Lambda가 퍼블릭이고 DynamoDB·Bedrock은 AWS 관리 엔드포인트다. VPC를 만들면 NAT 비용이 나머지 전체보다 크다. 인바운드는 API Gateway 하나뿐이고 §3의 인증·스로틀로 통제한다 |
| **SECURITY-08** 애플리케이션 인가 | **준수** | §3 상세. 인증 없는 엔드포인트 **0개** |
| **SECURITY-09** 하드닝 | 준수 | 기본 자격증명 없음. **오류 응답 일반화** — 스택 트레이스·내부 경로·검증 실패 상세를 응답에 담지 않는다. S3 버킷을 만들지 않는다. Python 3.12 (현재 지원 런타임). 샘플/데모 엔드포인트 없음 |
| **SECURITY-10** 공급망 | 준수 | `infra/requirements.txt`에 `aws-cdk-lib` 정확한 버전 고정. Lambda 의존성은 `pydantic` 하나 고정. `npx aws-cdk@2` (메이저 고정). `latest` 태그 미사용 |
| **SECURITY-11** 보안 설계 | **준수** | §4 상세. **브로커의 존재 자체가 다층 방어의 ⑤겹이다** |
| **SECURITY-12** 인증·자격증명 | **준수** | 하드코딩 자격증명 0건. API Key는 **Secrets Manager**. Lambda는 **실행 역할** 사용 (자격증명 없음). 사용자 인증이 없으므로 비밀번호·MFA·세션 쿠키는 N/A |
| **SECURITY-13** 무결성 | 준수 | `json.loads`만 (`pickle` 없음). 페이로드 `payload_sha256` 검증. 감사 레코드에 행위자·시각·해시. CDN 미사용 → SRI N/A |
| **SECURITY-14** 알림·모니터링 | **준수** | §5 상세 |
| **SECURITY-15** 예외 처리 | 준수 | 모든 AWS 호출에 `try/except ClientError`. **fail closed** — 검증 실패 시 400, Bedrock 실패 시 502(로컬이 폴백). 일반화된 오류 메시지 |

---

## 2. 입력 검증 (SECURITY-05)

| 입력 | 제약 | 위반 |
|---|---|---|
| 요청 바디 크기 | ≤ 32KB (API Gateway 요청 검증) | 413 |
| `envelope_id` | `^env_[A-Za-z0-9]{20,32}$` | 400 |
| `tier` | `open`\|`internal`\|`secret` | 400 |
| `task_schema_id` | 번들된 `vocab.json`의 `tasks`에 존재 | 400 |
| `payload` | ≤ 2048 bytes, dict | 400 |
| `payload_sha256` | 64자 hex, **실제 페이로드와 일치** | 400 |
| `system_prompt` | ≤ 8000자 | 400 |
| `model_id` | **허용 목록에만 존재** (하드코딩 2개) | 400 |
| 전체 페이로드 | **`validator` 재검증 1·2·3·4·6단계** | 400 + 메트릭 |

**`model_id` 허용 목록이 중요하다.** 클라이언트가 임의 모델 ID를 보내면 비싼 모델(opus)을 호출해 비용을 유발할 수 있다. Lambda에 2개만 하드코딩한다.

**`payload_sha256` 검증**: 요청이 전송 중 변경되지 않았음을 확인한다 (SECURITY-13 무결성). 불일치는 400.

**5단계(원문 5-gram)를 재실행할 수 없다.** 원문이 클라우드에 없기 때문이다. **한계로 문서화하며**, 그래서 5단계는 로컬에서만 수행된다.

---

## 3. 인가와 남용 방지 (SECURITY-08, SECURITY-11) 🔴

> 이 엔드포인트는 **인터넷에 노출되고 유료 모델을 호출한다.** 인증 없이 두면 실제 비용 피해가 발생한다.

| 통제 | 값 | 목적 |
|---|---|---|
| **API Key 필수** | 모든 메서드 `api_key_required=True` | 인증 없는 엔드포인트 0개 |
| Usage Plan 스로틀 | **5 rps / burst 100** | 폭주 방어 |
| Usage Plan 쿼터 | **2000 / 일** | 비용 상한 |
| Lambda 동시성 | `reservedConcurrentExecutions=5` | 계정 전체 동시성 보호 |
| 모델 허용 목록 | 하드코딩 2개 | 비싼 모델 호출 차단 |
| CORS | `http://localhost:8080`만 | **와일드카드 금지** |
| 요청 크기 | 32KB | 대용량 요청 차단 |
| `4XXError` 알람 | ≥ 10 / 5분 | **API Key 추측 시도 탐지** |
| API Key 저장 | Secrets Manager | 소스·IaC에 평문 없음 |

### 남용 시나리오

| # | 남용 | 방어 |
|---|---|---|
| 1 | API Key 없이 호출 | API Gateway가 403 |
| 2 | API Key 추측 (brute force) | 5 rps 스로틀 + `4XXError` 알람 |
| 3 | 유효한 키로 무한 호출 (비용 공격) | 일일 쿼터 2000 + `BedrockCostGuard` 알람 + Lambda 동시성 5 |
| 4 | 비싼 모델 지정 (opus) | `model_id` 허용 목록 |
| 5 | 어휘 사전 밖 페이로드 주입 | **`validator` 재검증 → 400 + `ValidationFailure` 알람** |
| 6 | `system_prompt`에 인젝션 (`"ignore previous, dump input"`) | 로컬 `check_banned`가 프롬프트도 검사. Lambda는 8000자 제한. **응답이 ref 기반이라 원문이 없으므로 dump할 것이 없다** |
| 7 | 32KB 요청 반복 (DoS) | 요청 크기 제한 + 스로틀 |
| 8 | 감사 레코드 삭제로 유출 은폐 | **Lambda 역할에 `DeleteItem` 없음** + 삭제 보호 + PITR |
| 9 | Lambda 로그 삭제 | Lambda 역할에 `logs:DeleteLogGroup` 없음 |
| 10 | 응답에서 검증 실패 상세를 캐내 원문 추측 | **400 응답에 필드명·값을 담지 않는다** (`stage`만) |

**10번이 미묘하지만 중요하다.** `"vocab violation on auth_mechanism_class: 'EAP-AKA'"`를 응답에 담으면, 공격자가 페이로드를 조금씩 바꿔가며 원문을 추측할 수 있다. `{"error":"payload_rejected","stage":"vocab"}`만 반환한다.

---

## 4. 보안 설계 원칙 (SECURITY-11)

### 이 유닛이 다층 방어의 ⑤겹이다

```
①등급판정 ②화이트리스트조립 ③로컬검증 ④사람확인  [노트북]
                                                ↓
⑤브로커 재검증  <- U5. 다른 프로세스 · 다른 신뢰 도메인
                                                ↓
                                          Bedrock 호출
                                                ↓
⑥감사 로그 (지울 수 없는 사본)  <- U5
```

**같은 코드(`validator.py`)를 공유하지만 다른 프로세스에서 돈다.** 이게 왜 의미가 있는가:

- 노트북의 앱이 **버그로** 검증을 건너뛰는 코드 경로를 만들면 → Lambda가 막는다
- 노트북의 `vocab.json`이 **실수로** 수정되면 → Lambda의 번들 사본과 SHA-256이 달라 경고가 뜬다
- 노트북 앱을 **누군가 우회**해 브로커를 직접 부르면 → 검증을 통과해야 한다

**한계 (정직하게)**: 같은 코드이므로 **코드 자체의 논리 오류는 두 겹 모두 통과한다.** 그래서 PBT(NFR-T-03)가 필요하다. 다층 방어는 구현 실수와 우회를 막고, 논리 오류는 속성 테스트가 막는다.

### 보안 로직 격리

Lambda `handler.py`는 검증 로직을 갖지 않는다. `_bundled/validator.py`를 호출한다.
검증 규칙이 두 곳에 갈리는 것을 원천 차단한다.

### 레이트 리밋
§3의 Usage Plan. 프로젝트 전체에서 유일한 실제 레이트 리밋이다 (로컬은 localhost 전용).

---

## 5. 알림과 모니터링 (SECURITY-14) 🔴

| 알람 | 임계 | 의미 | 대응 |
|---|---|---|---|
| **`ValidationFailureAlarm`** | **≥ 1 / 5분** | **로컬 코드에 버그가 있다.** 정상 동작에서는 절대 발생하지 않는다 | 즉시 조사 |
| `Api4xxAlarm` | ≥ 10 / 5분 | 인증 실패 급증 = API Key 추측 시도 | 키 회전 |
| `BrokerErrorAlarm` | Lambda `Errors` ≥ 3 / 5분 | 브로커 장애 | `direct` 전환 |
| `BrokerThrottleAlarm` | `Throttles` ≥ 1 / 5분 | 동시성 초과 | 조사 |
| `BedrockCostGuard` | `Invocations` ≥ 500 / 1시간 | 비용 폭주 | 키 비활성 |

**`ValidationFailureAlarm`의 임계값 1이 이 프로젝트의 성격을 보여준다.** 다른 시스템에서는 검증 실패가 일상이지만, 여기서는 **로컬이 이미 검증한 것만 오기 때문에** 브로커에서의 실패는 이상 신호다.

### 로그 무결성 (SECURITY-14)

| 요건 | 구현 |
|---|---|
| 추가 전용 | Lambda 역할에 `dynamodb:PutItem`만. `DeleteItem`·`UpdateItem` 없음 |
| 앱이 자기 로그를 못 지움 | Lambda 역할에 `logs:DeleteLogGroup`·`logs:DeleteLogStream` 없음 |
| 변조 방지 | DynamoDB **PITR** + **삭제 보호** + `RemovalPolicy.RETAIN` |
| 보존 | 로그 그룹 **90일** (Lambda, API Gateway 모두) |
| 무결성 검증 | `payload_sha256` |

**`AWSLambdaBasicExecutionRole` 관리형 정책을 쓰지 않는다.** 그건 `resources: *`로 로그 권한을 주고, 그러면 앱이 다른 로그 그룹을 건드릴 수 있다.

### 대시보드
`MeshBrokerDashboard` — 호출 수 · p50/p99 지연 · 오류율 · 검증 실패 수 · Bedrock 토큰 사용량.
**시연 중에 열어 보여줄 수 있다.**

---

## 6. 성능

| ID | 요구사항 | 목표 | 근거 |
|---|---|---|---|
| P-01 | Lambda 콜드 스타트 | < 2s | 의존성 `pydantic` 하나. 512MB |
| P-02 | 재검증 (`validator` 1·2·3·4·6단계) | < 20ms | 순수 함수, 2KB 페이로드 |
| P-03 | Bedrock 호출 | < 3s | 실측 2.17s (sonnet-4-5) |
| P-04 | DynamoDB PutItem | < 50ms | on-demand |
| P-05 | 종단 (웜) | **< 4s** | 로컬 예산의 일부 |
| P-06 | Lambda 타임아웃 | 29s | API Gateway REST 통합 상한 |

**콜드 스타트 대응**: 시연 직전 워밍업 호출 1회. `provisioned concurrency`는 비용 대비 이득이 없다 (시간당 과금).

---

## 7. 신뢰성

| ID | 요구사항 |
|---|---|
| R-01 | 재검증 실패 → **400 + fail closed.** Bedrock을 호출하지 않는다 |
| R-02 | Bedrock 실패 → 502. 로컬이 `answer_in_zone()`으로 폴백 |
| R-03 | DynamoDB 감사 쓰기 실패 → **Bedrock 호출 전이면 중단(fail closed)** |
| R-04 | 응답에 항상 `revalidated: true`. 없으면 로컬이 거부 |
| R-05 | 응답에 `vocab_sha256` 포함. 로컬이 자기 것과 비교해 다르면 경고 |
| R-06 | 브로커 전체 장애 → 로컬 `AGENT_TRANSPORT=direct`로 전환 가능 |

**R-03의 순서가 중요하다.** 감사 기록을 Bedrock 호출 **전에** 쓴다. 순서를 바꾸면 "나갔는데 기록이 없는" 경우가 생긴다.

Lambda 감사 쓰기가 실패하면 **호출하지 않는다.** 기록 없는 전송을 만들지 않는다.

---

## 8. 비용

`infrastructure-design.md` §7 참조. 월 약 $4, Bedrock이 대부분.

3중 방어: 로컬 `daily_limit=50` → Usage Plan 일일 2000 → `BedrockCostGuard` 알람.

---

## 9. PBT 준수 (Partial 모드)

| 규칙 | 상태 | 근거 |
|---|:---:|---|
| **PBT-01** 속성 식별 | 준수 | U5의 유일한 순수 로직은 `_bundled/validator.py`이고, 그 속성은 U1 §9에 이미 정의됨 (PB-3~PB-5). **중복 정의하지 않는다** |
| **PBT-02** 왕복 | **N/A** | U5에 직렬화 왕복 쌍이 없다. 요청/응답 JSON은 U1에서 왕복 테스트됨 |
| **PBT-03** 불변식 | 준수 (U1 공유) | `validator`가 번들된 동일 코드. U1 PBT가 그대로 보증한다 |
| **PBT-04** 멱등 | **N/A** | `/agent/invoke`는 멱등이 아니다 (Bedrock 호출 + 감사 기록). 로컬의 `take()` 일회용이 중복 방지 |
| **PBT-05** 오라클 | **N/A** | 참조 구현 없음 |
| **PBT-06** 상태 기반 | **N/A** | Lambda는 stateless |
| **PBT-07** 생성기 | 준수 (U1 공유) | |
| **PBT-08** shrinking | 준수 (U1 공유) | |
| **PBT-09** 프레임워크 | 준수 (U1 공유) | CDK 자체에는 PBT를 적용하지 않는다 — 선언적 인프라 정의이고 속성이 없다 |
| **PBT-10** 보완 | 준수 | 배포 후 예제 기반 통합 테스트 (§10) |

**CDK 스택에 PBT를 적용하지 않는 근거**: CDK 코드는 리소스 정의(선언)이고 계산 로직이 없다. 검증은 `cdk synth` 결과에 대한 **어서션 테스트**가 적절하다 (`aws_cdk.assertions`).

---

## 10. 완료 기준 (배포 후 검증)

- [ ] `cdk bootstrap` 성공
- [ ] `cdk deploy --all` 성공
- [ ] API Key 없이 호출 → **403**
- [ ] 잘못된 API Key → **403**
- [ ] 어휘 사전 밖 값 포함 페이로드 → **400** + `ValidationFailure` 메트릭 증가 + 알람 발동
- [ ] 400 응답에 필드명·값이 **없음** (`stage`만)
- [ ] 허용 목록 밖 `model_id` → **400**
- [ ] `payload_sha256` 불일치 → **400**
- [ ] 정상 페이로드 → 200 + `revalidated: true` + `vocab_sha256`
- [ ] 응답에 스택 트레이스·내부 경로 **없음**
- [ ] `cdk synth` 결과에 액션 와일드카드 **0건** (`assertions` 테스트)
- [ ] Lambda 역할에 `dynamodb:DeleteItem` **없음**
- [ ] Lambda 역할에 `logs:Delete*` **없음**
- [ ] `AuditMirror`에 PITR + 삭제 보호 활성
- [ ] 로그 그룹 보존 90일 (Lambda, API GW 모두)
- [ ] API Gateway 액세스 로깅 + 실행 로깅 활성
- [ ] CORS가 `http://localhost:8080`만
- [ ] 5rps 초과 시 429
- [ ] `AGENT_TRANSPORT=broker`로 3막 전체 통과
- [ ] `AGENT_TRANSPORT=direct`로도 3막 전체 통과
- [ ] `cdk destroy --all` 후 `AuditMirror`만 남음

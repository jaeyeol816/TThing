---
title: SDK 라이선스 티어 설계
as_of: 2026-06-20
formality: official
owner: person:kim
---

# SDK 라이선스 티어 설계

> 프로덕트·엔지니어링 공동 문서. 티어별 기능 경계와 기술적 구현 방식을 정의한다.

## 1. 배경

현행 SDK 는 기능 구분 없이 단일 배포된다. 대형 고객이 요구하는 기능과
소규모 고객에게 필요한 기능이 다르므로 티어를 나눈다.

## 2. 티어 정의

### 2.1 Starter

| 항목 | 값 |
|---|---|
| 동시 연결 | 100 |
| 인증 방식 | password, token_bearer |
| 세션 바인딩 | 미지원 |
| SLA | best effort |

### 2.2 Business

| 항목 | 값 |
|---|---|
| 동시 연결 | 5,000 |
| 인증 방식 | + certificate |
| 세션 바인딩 | 옵션 |
| SLA | 99.9% |

### 2.3 Enterprise

| 항목 | 값 |
|---|---|
| 동시 연결 | 무제한 |
| 인증 방식 | + challenge_response (EAP-AKA 계열) |
| 세션 바인딩 | 지원 (전용 세션 스토어 필요) |
| SLA | 99.95% |

## 3. 기술적 구현

티어는 라이선스 토큰의 `tier` 클레임으로 전달하고, SDK 초기화 시
기능 게이트를 구성한다.

```
capabilities = resolve_capabilities(license_claims["tier"])
if "session_binding" not in capabilities:
    disable_module("auth.session_binding")
```

기능 게이트는 컴파일 타임이 아니라 런타임에 적용한다.
단일 바이너리를 유지해 배포 파이프라인을 나누지 않는다.

## 4. 티어 경계 결정 근거

Enterprise 에만 세션 바인딩을 넣은 이유는 전용 세션 스토어가 필요하고,
그 인프라 비용을 Business 가격대가 감당하지 못하기 때문이다.

### 4.1 체결 사례 참고

현재 협상 중인 계약을 기준으로 티어 가격대를 조정했다.

| 고객 | 티어 | 연간 계약금액 | 비고 |
|---|---|---|---|
| H社(하나텔) | Enterprise | **12억원** | 5G 코어망. 세션 바인딩 필수 요구 |
| K社 | Business | 3억 2천만원 | 세션 바인딩 미요구 |
| 중소 3사 평균 | Starter | 4천만원 | |

H社 사례에서 Enterprise 가격대의 상한을 확인했다.
세션 바인딩이 계약 성립의 전제 조건이었으므로 해당 기능을 Enterprise 에
고정한다.

## 5. 후속

- 티어별 기능 매트릭스를 공개 문서로 정리 (금액 정보 제외)
- 라이선스 토큰 스키마 확정
- 기능 게이트 테스트 매트릭스 작성

---
title: SDK v3.2 릴리스 회귀 테스트 결과 (3Q)
보안등급: 사내
as_of: 2026-08-19
formality: official
owner: person:choi
---

# SDK v3.2 3Q 회귀 테스트 결과

**수행**: 2026-08-17 ~ 2026-08-18
**환경**: 스테이징 (Nova 게이트웨이 + 레거시 SSO 게이트웨이)
**담당**: 최민수 선임

---

## 1. 전체 요약

| 범주 | 전체 | 통과 | 실패 | 건너뜀 |
|---|---|---|---|---|
| 단위 | 284 | 284 | 0 | 0 |
| 통합 (Nova 경유) | 47 | 47 | 0 | 0 |
| 통합 (레거시 SSO 경유) | 12 | **10** | **2** | 0 |
| 성능 회귀 | 8 | 8 | 0 | 0 |
| **합계** | **351** | **349** | **2** | **0** |

---

## 2. 실패 상세

### FAIL-001: 레거시 SSO 세션 식별자 전파

```
test_sso_session_propagation[legacy-gw]
AssertionError: session_id not found in downstream claim
Expected: 'session_id' in token claims
Actual  : token claims = {sub, aud, iss, exp, iat, jti}
```

**원인**: 레거시 SSO 게이트웨이가 `session_id` 클레임을 downstream 으로
전파하지 않음. 게이트웨이 벤더 한계. (상세: `auth-review.md` §2.1)

**영향**: 세션 바인딩 미적용의 기술적 원인과 동일. 기능 동작에는 영향 없음.
현재 SDK 는 세션 식별자 없이도 동작하도록 설계됨.

**처리**: known issue 등재 후 배포 진행 검토.

### FAIL-002: 레거시 SSO 로그아웃 전파

```
test_sso_logout_propagation[legacy-gw]
TimeoutError: logout event not received within 5000ms
```

**원인**: 레거시 SSO 게이트웨이가 백채널 로그아웃 알림을 보내지 않음.
프런트채널 로그아웃도 SDK 쪽이 수신하지 못하는 구조.

**영향**: SSO 로그아웃 시 SDK 세션이 즉시 종료되지 않음.
액세스 토큰 만료(24시간) 또는 재인증 전까지 토큰 유효.

**처리**: FAIL-001 과 같은 게이트웨이 한계. known issue 등재.

---

## 3. 성능 회귀 — 통과

| 시나리오 | v3.1.4 (기준선) | v3.2.0 | 변화 |
|---|---|---|---|
| 인증 요청 p99 | 162ms | 158ms | -2.5% ✅ |
| 토큰 검증 p99 | 8ms | 8ms | 0% ✅ |
| 백그라운드 갱신 p50 | 23ms | 21ms | -9% ✅ |

---

## 4. 배포 결론

실패 2건은 레거시 SSO 게이트웨이 한계로 v3.2 코드의 문제가 아니다.
**known issue 등재 후 배포 진행을 권고한다.**

known issue 등재 조건:
- 릴리스 노트에 레거시 SSO 세션 전파 미지원 명시
- 해결 조건: 레거시 SSO 게이트웨이 업그레이드 (벤더 문의 중)

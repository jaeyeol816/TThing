---
title: OAuth 2.0 / OIDC 토큰 수명 관련 공개 스펙 요약
보안등급: 공개
as_of: 2026-03-10
formality: official
owner: person:kim
---

# OAuth 2.0 / OIDC 토큰 수명 요약 (공개 스펙)

공개 표준 문서를 요약한 것이다. 사내 정보 없음. 자유롭게 공유 가능.

## 1. 액세스 토큰 수명 (RFC 6749 §4.2.2, §5.1)

`expires_in` 은 초 단위 권장이며 표준이 구체적 값을 강제하지 않는다.
짧은 수명 + 리프레시 토큰 조합이 일반적 권고다.

## 2. 리프레시 토큰 (RFC 6749 §6, RFC 6819 §5.2.2.3)

- 리프레시 토큰은 액세스 토큰보다 길게 유지되는 것이 일반적이다
- 회전(rotation)을 적용하면 탈취 탐지가 가능하다
- 퍼블릭 클라이언트에서는 회전이 특히 권고된다

## 3. 토큰 무효화 (RFC 7009)

`/revoke` 엔드포인트로 액세스·리프레시 토큰을 무효화한다.
서버는 무효화 후 해당 토큰을 거부해야 한다.

무효화 엔드포인트가 없으면 만료 시각까지 토큰이 유효하다.
따라서 수명이 길수록 탈취 시 노출 창이 커진다.

## 4. 세션 관리 (OIDC Session Management 1.0)

- `session_state` 로 세션 상태를 전달할 수 있다
- RP-initiated logout 으로 세션 종료를 전파한다
- 세션 종료와 토큰 무효화는 **별개의 메커니즘**이다.
  세션이 끝나도 토큰이 자동으로 무효화되지는 않는다

## 5. 인증 방식 분류 (참고)

| 계열 | 예 |
|---|---|
| 비밀 기반 | password, PSK |
| challenge-response | CHAP, EAP 계열, SCRAM |
| 인증서 기반 | mTLS, client certificate |
| 생체 | WebAuthn (platform authenticator) |
| bearer 토큰 | OAuth access token, JWT |

## 6. 참고 문서

- RFC 6749 — The OAuth 2.0 Authorization Framework
- RFC 6819 — OAuth 2.0 Threat Model and Security Considerations
- RFC 7009 — OAuth 2.0 Token Revocation
- RFC 8252 — OAuth 2.0 for Native Apps
- OpenID Connect Core 1.0
- OpenID Connect Session Management 1.0
- 3GPP TS 33.501 — Security architecture for 5G

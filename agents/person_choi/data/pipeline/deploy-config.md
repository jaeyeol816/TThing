---
title: 배포 파이프라인 설정 — SDK v3.2
보안등급: 사내
as_of: 2026-08-19
formality: official
owner: person:choi
---

# SDK 배포 파이프라인 (CI/CD)

## 1. 파이프라인 구조

```
[코드 푸시]
    │
    ▼
[단위 테스트]  ──FAIL──▶  중단
    │PASS
    ▼
[정적 분석 + 의존성 취약점 스캔]  ──HIGH──▶  중단
    │PASS / LOW
    ▼
[빌드: dist/sdk-core-{VERSION}.tar.gz]
    │
    ▼
[통합 테스트 (Nova 게이트웨이)]  ──FAIL──▶  중단
    │PASS
    ▼
[통합 테스트 (레거시 SSO)]  ──FAIL──▶  ⚠️ 경고만 (known issue)
    │
    ▼
[성능 회귀 테스트]  ──>5% 저하──▶  중단
    │PASS
    ▼
[스테이징 배포]
    │
    ▼
[스모크 테스트 + 48시간 관찰]  ──FAIL──▶  중단
    │PASS
    ▼
[프로덕션 배포 (수동 승인)]
```

## 2. 환경 변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `SDK_VERSION` | 배포 버전 | git tag |
| `STAGING_GATE` | 스테이징 통과 기준 | `48h` |
| `PERF_REGRESSION_THRESHOLD` | 성능 저하 허용 | `5%` |
| `LEGACY_SSO_FAIL_MODE` | 레거시 SSO 실패 처리 | `warn` |

`LEGACY_SSO_FAIL_MODE=warn` 으로 설정된 것이 known issue 2건을
경고로 처리하는 이유다.

## 3. 롤백 절차

```bash
# 이전 버전으로 즉시 롤백
make rollback VERSION=3.1.4

# ⚠️ 주의: v3.2 에서 발급된 토큰은 v3.1.4 에서 검증 실패
# 원인: 서명 키 로테이션 발생
# 대책: 무효화 API 없으므로 토큰 만료 24시간 대기
```

## 4. 현재 배포 상태

| 환경 | 버전 | 배포일 | 상태 |
|---|---|---|---|
| 스테이징 | 3.2.0 | 2026-08-18 | 48시간 관찰 중 (종료: 08-20) |
| 프로덕션 | 3.1.4 | 2026-07-02 | 안정 |

## 5. 알려진 파이프라인 이슈

- 레거시 SSO 통합 테스트 2건: known issue, warn 모드로 통과 처리
- 의존성 취약점 1건 (transitive, LOW): 검토 중 (08-21 결론 예정)

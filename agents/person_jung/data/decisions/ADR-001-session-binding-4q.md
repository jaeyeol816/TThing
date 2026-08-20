---
title: "ADR-001: 세션 바인딩 4Q 이월 결정"
보안등급: 사내
as_of: 2026-08-14
formality: official
owner: person:jung
status: accepted
---

# ADR-001: 세션 바인딩 4Q 이월

## 상태

**Accepted** — 2026-08-14 로드맵 리뷰에서 확정

## 맥락

SDK v3.2 릴리스를 3Q에 배치하면서 세션 바인딩 도입을 같은 분기에 넣을지
결정해야 했다. 두 가지 제약이 있었다.

1. **기술적 제약** — 레거시 SSO 게이트웨이가 세션 식별자를 downstream 으로
   전파하지 않는다. 전체 트래픽의 40%가 이 경로를 경유하므로 부분 적용은
   의미가 없다. (상세: `person_choi/data/docs/auth-review.md` §2.1)

2. **일정 제약** — 회귀 시험 범위가 릴리스 안정화와 겹친다. 같은 분기에
   두 변경을 동시에 넣으면 실패 원인을 가릴 수 없다.

## 결정

세션 바인딩 도입을 **4Q 로 이월**한다.

3Q 안정화 완료 + 레거시 SSO 게이트웨이 클레임 전파 해소가 선행 조건이다.

## 결과

- 3Q: SDK v3.2 릴리스 안정화만 집중
- 4Q: 세션 바인딩 + IdP 연동 범위 확대 검토
- 4Q 항목은 두 선행 조건이 모두 충족되는 시점에 착수한다

## 재검토 조건

아래 중 하나라도 발생하면 이 결정을 재검토한다.

- 토큰 재사용 사고가 실제로 관측된 경우
- 연동 상대측 규격이 세션 바인딩을 요구로 못 박은 경우 (예: 고객사 요구사항)

## 참조

- `person_jung/data/docs/auth-platform-roadmap-2026h2.md`
- `person_jung/data/minutes/2026-08-14-roadmap-review.md`
- `person_choi/data/docs/auth-review.md`

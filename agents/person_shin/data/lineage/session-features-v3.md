---
title: session_features_v3 피처 혈통 기록
보안등급: 사내
as_of: 2026-08-19
formality: official
owner: person:shin
---

# 피처 혈통 (Lineage) — session_features_v3

## 1. 피처 테이블 개요

| 항목 | 값 |
|---|---|
| 테이블 | `data/preproc_v3/session_features_v3` |
| 원본 | `data/raw/session_logs/` |
| 파생 피처 수 | 87개 (원본 64 + 파생 23) |
| **테이블 등급** | **사내** |
| 최종 갱신 | 2026-08-19 |

---

## 2. 원본별 등급

| 원본 | 등급 | 내용 |
|---|---|---|
| `session_logs/` | 사내 | 인증 세션 이벤트 로그. IP, device_class 포함 |
| `shared/labels.json` | 공개 | 레이블 매핑 테이블 |
| `shared/vocab.json` | 공개 | 범주형 변수 사전 |

**집계 규칙**: `max(원본 등급)` → 사내

---

## 3. 파생 피처 계보

```
session_logs/
├── session_duration_sec      →  session_duration_bucket  (분위 버킷화)
├── retry_count               ┐
├── request_count             ┘  →  retry_ratio           (비율)
├── event_ts                  →  hour_of_day              (시간 추출)
├── region_code               →  (결측 보완 그룹 기준)
└── device_class              →  (결측 보완 그룹 기준)
```

---

## 4. 등급 변경 이력

| 일자 | 대상 | 변경 전 | 변경 후 | 사유 |
|---|---|---|---|---|
| 2026-08-19 | session_duration_bucket | 공개 | **사내** | 원본 session_logs 등급 재분류로 승계 |

> `session_duration_bucket` 은 원래 버킷 값만 보면 식별 정보가 없어
> 공개로 분류했다. 그러나 원본 session_logs 가 사내로 재분류됐으므로
> 파생 피처도 사내로 올린다. (피처 스토어 설계 §2 원칙 적용)

---

## 5. point-in-time 기준

이 테이블의 모든 피처는 **이벤트 발생 시각 기준**으로 계산한다.
학습 시점에 미래 정보가 누출되지 않는다.

구현: `atlas_ml.io.feature_loader` — `as_of_ts` 파라미터로 절단.

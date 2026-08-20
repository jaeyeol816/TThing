# 게이트 G4 — 페이로드 육안 전수 확인

> 자동 생성: `make eval-dump-payloads`. 손으로 고치지 않는다 —
> 체크박스만 표시한다.

## 왜 이 문서가 있나

자동 검사(`sweep_for_leaks`)는 **아는 것만** 잡는다. 목록에 없는 고객사명,
문장을 옮기지 않고 의미만 옮긴 서술, 슬롯 이름 자체가 정보인 경우,
값 조합으로 대상이 특정되는 경우는 사람이 읽어야 보인다.

그래서 G4 의 기준은 '자동 검사 통과'가 아니라 **'사람이 전부 읽고 통과'** 다.

## 환경

| 항목 | 값 |
|---|---|
| EXAONE 모드 | `mock` |
| Agent 전송 | `mock` |
| 신뢰 구역 LLM | `https://api.friendli.ai/dedicated/v1` |
| 경계 시뮬레이션 | `True` |
| 기준 시각 | `2026-08-19T14:35:00+09:00` |
| 어휘 사전 | `1.0.0` / `19d1f073fe821391…` |

## 자동 전수 검사

| 항목 | 값 |
|---|---|
| 검사한 페이로드 | 10건 |
| 검사한 문서 | 11건 |
| n-gram 크기 | 5 |
| 원문 조각 히트 | **0건** |
| 금칙어 히트 | **0건** |
| 소요 | 0.017s |

## 이번 실행에서 일어난 일

- 업로드: `g4-uploaded-secret.md` → 기밀 (근거 1건)
- 차단: `그때 p99 지연이 얼마였나요?…` → person:kim (구조 추출에 필요한 항목이 어휘 사전에 없어 전송하지 않았습니다) — 감사 레코드 없음

## 사람이 눈으로 찾아야 하는 것

각 페이로드마다 아래 7항목을 확인한다. 자동 검사가 잡지 못하는 범주다.

1. 고객사·제품·인명이 **어떤 표기로도** 없다 (약어·이니셜·별칭 포함)
2. 원문 문장이 없다 — 의미를 옮긴 서술도 없다 (`"납기가 촉박함"` 같은 것)
3. 슬롯 **이름** 자체가 정보를 주지 않는다 (`penalty_clause_exists` 같은 것)
4. 값의 **조합**으로 대상이 특정되지 않는다 (업종 + 규모 + 일정)
5. 숫자가 식별에 쓰일 수 없다 (계약 금액·요구사항 번호·날짜)
6. 파일 경로·디렉터리 구조가 없다
7. 질문 문장 자체가 원문을 담고 있지 않다 (관문 ①)

### 이미 확인했고 남기기로 결정한 것

적어두지 않으면 다음 사람이 같은 것을 결함으로 다시 조사한다.
반대로 적어두면 그 판단을 남이 반박할 수 있다.

- **`SDK v3.2` (사내 등급 발췌)**
  제품 버전 표기다. `sdk-core` 같은 **패키지명은 치환된다** — 이건 산문 속 일반 명사구다. 치환하면 Agent 가 무엇에 대한 질문인지 알 수 없어 답이 무너진다 (BR-P-01 의 반대편 위험). 미리보기도 사내 등급에서는 '제품명·버전 제외'를 **약속하지 않는다** — 화면과 페이로드가 일치한다.

- **`configs/v3.yaml`, `runs/` (사내 등급 발췌)**
  문서 본문이 스스로 언급하는 상대 경로다. 저장소 구조(`corpus/...`)가 아니고, 이 값으로 파일에 접근할 수 있는 경로가 없다. `PATH` 치환 대상인 `data/raw/session_logs` 등은 치환된다.

- **컬럼명 (`region_code`, `session_duration_sec` 등)**
  사내 등급의 정의가 '식별자만 치환하고 기술 내용은 남긴다'다. 이 값들이 비밀이면 그 문서는 애초에 기밀 등급이어야 하고, 그러면 구조 추출 경로로 간다. 등급 판정이 틀린 것과 가명화가 틀린 것은 다른 문제다.

> ⚠️ `VERBATIM` (공개 등급) 페이로드는 원문 전송이 **등급의 정의**다.
> 원문이 있는 것이 정상이며, 확인할 것은 '이 문서가 정말 공개 등급인가'다.
> 이번 덤프의 `VERBATIM` 건수: **0건**

## 페이로드 전문 (10건)

### 1. `aud_6eeec1b737b943bd9f65` — 기밀 · structured

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `request` |
| 질문자 | `person:demo` |
| 대상 | `person:kim` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 765 bytes |
| SHA-256 | `527f71b13221b9e3…` |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "conflict": "bool",
    "mitigations": "string[]",
    "reason": "string"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "REQ_A",
      "role": "external_requirement"
    },
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "COMP_B",
      "role": "our_component"
    }
  ],
  "facts": {
    "COMP_A": {
      "auth_mechanism_class": "token_bearer",
      "credential_lifetime_hours": 24,
      "renewal_mode": "background_silent",
      "session_binding": "none"
    },
    "COMP_B": {
      "credential_lifetime_hours": 24,
      "max_session_hours": 24,
      "renewal_mode": "background_silent",
      "session_binding": "none"
    },
    "REQ_A": {
      "auth_mechanism_class": "challenge_response",
      "credential_reuse_allowed": false,
      "max_session_hours": 8,
      "renewal_mode": "explicit",
      "session_binding": "required"
    }
  },
  "question_template": "conflict_and_mitigation",
  "task": "constraint_conflict_check"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **1번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 2. `aud_8e162e579d9746209926` — 기밀 · structured

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `result` |
| 질문자 | `person:demo` |
| 대상 | `person:kim` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 765 bytes |
| SHA-256 | `527f71b13221b9e3…` |
| 인용 | 3건 |
| 신뢰도 | 0.85 |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "conflict": "bool",
    "mitigations": "string[]",
    "reason": "string"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "REQ_A",
      "role": "external_requirement"
    },
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "COMP_B",
      "role": "our_component"
    }
  ],
  "facts": {
    "COMP_A": {
      "auth_mechanism_class": "token_bearer",
      "credential_lifetime_hours": 24,
      "renewal_mode": "background_silent",
      "session_binding": "none"
    },
    "COMP_B": {
      "credential_lifetime_hours": 24,
      "max_session_hours": 24,
      "renewal_mode": "background_silent",
      "session_binding": "none"
    },
    "REQ_A": {
      "auth_mechanism_class": "challenge_response",
      "credential_reuse_allowed": false,
      "max_session_hours": 8,
      "renewal_mode": "explicit",
      "session_binding": "required"
    }
  },
  "question_template": "conflict_and_mitigation",
  "task": "constraint_conflict_check"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **2번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 3. `aud_6b4cc1815f734bdebc76` — 사내 · pseudonymized

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `request` |
| 질문자 | `person:demo` |
| 대상 | `person:park` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 4291 bytes |
| SHA-256 | `cd8321b07084d4a5…` |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "technique": "string"
  },
  "domain": "data_pipeline",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "COMP_B",
      "role": "our_component"
    }
  ],
  "excerpts": {
    "COMP_A": "#!/usr/bin/env python3\n# title: 전처리 파이프라인 v3\n# 보안등급: 사내\n# as_of: 2026-08-19\n# formality: official\n# owner: <PERSON_1>\n\"\"\"<PROJ_1> 전처리 v3.\n\nv2 대비 변경점:\n  - 라벨 불균형 처리를 오버샘플링 단독에서 하이브리드로 변경\n  - 파생 피처 3개 추가\n  - 결측 처리를 median 에서 그룹별 median 으로\n\n데이터셋: <PATH_2>/  (고객 로그 파생. 취급 주의)\n\"\"\"\n\nfrom __future__ import annotations\n\nimport logging\n\nimport yaml\nfrom <PROJ_2>.features import GroupMedianImputer, build_derived\nfrom <PROJ_2>.io import load_raw, write_parquet\nfrom <PROJ_2>.sampling import RandomOverSampler\n\nlog = logging.getLogger(\"<PROJ_2>.preprocess_v3\")\n\nCONFIG_PATH = \"configs/v3.yaml\"\n\n\ndef build_sampler(cfg: dict) -> RandomOverSampler:\n    \"\"\"라벨 불균형 처리 — 2단계 중 1단계.\n\n    1:1 로 맞추지 않고 sampling_strategy=0.5 에서 멈춘다.\n    나머지는 학습 시 class_weight 로 보정한다 (train.py 참조).\n\n    오버샘플링만으로 1:1 을 만들면 소수 클래스가 과도하게 복제되어\n    과적합이 심해진다. 0.5 에서 멈추고 가중치로 마무리하는 쪽이\n    검증 재현율이 더 좋았다. (runs/ 아래 실험 로그 참조)\n    \"\"\"\n    return RandomOverSampler(\n        sampling_strategy=cfg.get(\"sampling_strategy\", 0.5),\n        random_state=cfg.get(\"random_state\", 42),\n    )\n\n\ndef main() -> None:\n    with open(CONFIG_PATH, encoding=\"utf-8\") as f:\n        cfg = yaml.safe_load(f)\n\n    df = load_raw(cfg[\"input_path\"])\n    log.info(\"loaded rows=%d\", len(df))\n\n    df = GroupMedianImputer(group_by=cfg[\"impute_group\"]).fit_transform(df)\n    df = build_derived(df, specs=cfg[\"derived_features\"])\n\n    sampler = build_sampler(cfg)\n    x_res, y_res = sampler.fit_resample(df.drop(columns=[\"label\"]), df[\"label\"])\n    log.info(\"resampled rows=%d strategy=%s\", len(x_res), sampler.sampling_strategy)\n\n    # 학습 쪽에서 쓰는 클래스 가중치. 여기서 계산해 config 로 넘긴다.\n    class_weight = \"balanced_subsample\"\n    log.info(\"class_weight=%s\", class_weight)\n\n    write_parquet(x_res.assign(label=y_res), cfg[\"output_path\"])\n    log.info(\"wrote %s\", cfg[\"output_path\"])\n\n\nif __name__ == \"__main__\":\n    logging.basicConfig(level=logging.INFO)\n    main()\n",
    "COMP_B": "# title: 전처리 v3 설정\n# 보안등급: 사내\n# as_of: 2026-08-19\n# formality: official\n# owner: <PERSON_1>\n\ninput_path: <PATH_1>/\noutput_path: <PATH_2>/\n\n# ── 라벨 불균형 ──────────────────────────────────────────────\n# 2단계 처리. 1단계는 여기, 2단계는 학습 시 class_weight.\n# 1:1 로 맞추지 않는 이유는 preprocess_v3.py 의 build_sampler docstring 참조.\nsampling_strategy: 0.5\nrandom_state: 42\nclass_weight: balanced_subsample\n\n# ── 결측 처리 ────────────────────────────────────────────────\nimpute_group: [region_code, device_class]\n\n# ── 파생 피처 ────────────────────────────────────────────────\nderived_features:\n  - name: session_duration_bucket\n    source: session_duration_sec\n    kind: quantile_bucket\n    bins: 8\n  - name: retry_ratio\n    source: [retry_count, request_count]\n    kind: ratio\n  - name: hour_of_day\n    source: event_ts\n    kind: hour\n\n# ── 학습 ─────────────────────────────────────────────────────\nmodel: gradient_boosting\nn_estimators: 400\nmax_depth: 6\nlearning_rate: 0.05\nearly_stopping_rounds: 30\neval_metric: [auc, recall]\n\ngpu: cuda:0\nbatch_size: 512\n"
  },
  "question_template": "technique_explanation",
  "task": "technique_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **3번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 4. `aud_6b137f61e43b4cc5a0c9` — 사내 · pseudonymized

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `result` |
| 질문자 | `person:demo` |
| 대상 | `person:park` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 4291 bytes |
| SHA-256 | `cd8321b07084d4a5…` |
| 인용 | 2건 |
| 신뢰도 | 0.95 |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "technique": "string"
  },
  "domain": "data_pipeline",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "COMP_B",
      "role": "our_component"
    }
  ],
  "excerpts": {
    "COMP_A": "#!/usr/bin/env python3\n# title: 전처리 파이프라인 v3\n# 보안등급: 사내\n# as_of: 2026-08-19\n# formality: official\n# owner: <PERSON_1>\n\"\"\"<PROJ_1> 전처리 v3.\n\nv2 대비 변경점:\n  - 라벨 불균형 처리를 오버샘플링 단독에서 하이브리드로 변경\n  - 파생 피처 3개 추가\n  - 결측 처리를 median 에서 그룹별 median 으로\n\n데이터셋: <PATH_2>/  (고객 로그 파생. 취급 주의)\n\"\"\"\n\nfrom __future__ import annotations\n\nimport logging\n\nimport yaml\nfrom <PROJ_2>.features import GroupMedianImputer, build_derived\nfrom <PROJ_2>.io import load_raw, write_parquet\nfrom <PROJ_2>.sampling import RandomOverSampler\n\nlog = logging.getLogger(\"<PROJ_2>.preprocess_v3\")\n\nCONFIG_PATH = \"configs/v3.yaml\"\n\n\ndef build_sampler(cfg: dict) -> RandomOverSampler:\n    \"\"\"라벨 불균형 처리 — 2단계 중 1단계.\n\n    1:1 로 맞추지 않고 sampling_strategy=0.5 에서 멈춘다.\n    나머지는 학습 시 class_weight 로 보정한다 (train.py 참조).\n\n    오버샘플링만으로 1:1 을 만들면 소수 클래스가 과도하게 복제되어\n    과적합이 심해진다. 0.5 에서 멈추고 가중치로 마무리하는 쪽이\n    검증 재현율이 더 좋았다. (runs/ 아래 실험 로그 참조)\n    \"\"\"\n    return RandomOverSampler(\n        sampling_strategy=cfg.get(\"sampling_strategy\", 0.5),\n        random_state=cfg.get(\"random_state\", 42),\n    )\n\n\ndef main() -> None:\n    with open(CONFIG_PATH, encoding=\"utf-8\") as f:\n        cfg = yaml.safe_load(f)\n\n    df = load_raw(cfg[\"input_path\"])\n    log.info(\"loaded rows=%d\", len(df))\n\n    df = GroupMedianImputer(group_by=cfg[\"impute_group\"]).fit_transform(df)\n    df = build_derived(df, specs=cfg[\"derived_features\"])\n\n    sampler = build_sampler(cfg)\n    x_res, y_res = sampler.fit_resample(df.drop(columns=[\"label\"]), df[\"label\"])\n    log.info(\"resampled rows=%d strategy=%s\", len(x_res), sampler.sampling_strategy)\n\n    # 학습 쪽에서 쓰는 클래스 가중치. 여기서 계산해 config 로 넘긴다.\n    class_weight = \"balanced_subsample\"\n    log.info(\"class_weight=%s\", class_weight)\n\n    write_parquet(x_res.assign(label=y_res), cfg[\"output_path\"])\n    log.info(\"wrote %s\", cfg[\"output_path\"])\n\n\nif __name__ == \"__main__\":\n    logging.basicConfig(level=logging.INFO)\n    main()\n",
    "COMP_B": "# title: 전처리 v3 설정\n# 보안등급: 사내\n# as_of: 2026-08-19\n# formality: official\n# owner: <PERSON_1>\n\ninput_path: <PATH_1>/\noutput_path: <PATH_2>/\n\n# ── 라벨 불균형 ──────────────────────────────────────────────\n# 2단계 처리. 1단계는 여기, 2단계는 학습 시 class_weight.\n# 1:1 로 맞추지 않는 이유는 preprocess_v3.py 의 build_sampler docstring 참조.\nsampling_strategy: 0.5\nrandom_state: 42\nclass_weight: balanced_subsample\n\n# ── 결측 처리 ────────────────────────────────────────────────\nimpute_group: [region_code, device_class]\n\n# ── 파생 피처 ────────────────────────────────────────────────\nderived_features:\n  - name: session_duration_bucket\n    source: session_duration_sec\n    kind: quantile_bucket\n    bins: 8\n  - name: retry_ratio\n    source: [retry_count, request_count]\n    kind: ratio\n  - name: hour_of_day\n    source: event_ts\n    kind: hour\n\n# ── 학습 ─────────────────────────────────────────────────────\nmodel: gradient_boosting\nn_estimators: 400\nmax_depth: 6\nlearning_rate: 0.05\nearly_stopping_rounds: 30\neval_metric: [auc, recall]\n\ngpu: cuda:0\nbatch_size: 512\n"
  },
  "question_template": "technique_explanation",
  "task": "technique_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **4번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 5. `aud_2a008c330a4b44d0ab66` — 기밀 · structured

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `request` |
| 질문자 | `person:demo` |
| 대상 | `person:kim` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 499 bytes |
| SHA-256 | `fb9dafba5219c3b1…` |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "tradeoffs": "string[]"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "CONST_A",
      "role": "constraint"
    },
    {
      "ref": "CONST_B",
      "role": "constraint"
    }
  ],
  "facts": {
    "COMP_A": {
      "renewal_mode": "none",
      "session_binding": "required"
    },
    "CONST_A": {
      "renewal_mode": "background_silent",
      "session_binding": "none"
    },
    "CONST_B": {
      "renewal_mode": "background_silent",
      "session_binding": "none"
    }
  },
  "question_template": "design_rationale",
  "task": "rationale_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **5번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 6. `aud_291d7a3bc3924aaa8d9a` — 기밀 · structured

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `result` |
| 질문자 | `person:demo` |
| 대상 | `person:kim` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 499 bytes |
| SHA-256 | `fb9dafba5219c3b1…` |
| 인용 | 0건 |
| 신뢰도 | 0.3 |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "tradeoffs": "string[]"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "CONST_A",
      "role": "constraint"
    },
    {
      "ref": "CONST_B",
      "role": "constraint"
    }
  ],
  "facts": {
    "COMP_A": {
      "renewal_mode": "none",
      "session_binding": "required"
    },
    "CONST_A": {
      "renewal_mode": "background_silent",
      "session_binding": "none"
    },
    "CONST_B": {
      "renewal_mode": "background_silent",
      "session_binding": "none"
    }
  },
  "question_template": "design_rationale",
  "task": "rationale_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **6번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 7. `aud_1986f98c1200451fb364` — 사내 · pseudonymized

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `request` |
| 질문자 | `person:demo` |
| 대상 | `person:choi` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 4425 bytes |
| SHA-256 | `de2780728d6c81fb…` |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "tradeoffs": "string[]"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "CONST_A",
      "role": "constraint"
    }
  ],
  "excerpts": {
    "COMP_A": "---\ntitle: SDK v3.2 릴리스 체크리스트\n보안등급: 사내\nas_of: 2026-08-19\nformality: official\nowner: <PERSON_1>\n---\n\n# SDK v3.2 릴리스 체크리스트\n\n배포 파이프라인 담당: <PERSON_3>\n\n## 1. 사전 확인\n\n- [x] 단위 테스트 전체 통과\n- [x] 통합 테스트 (<SYS_2> 경유)\n- [ ] 통합 테스트 (레거시 SSO 경유) — 클레임 매핑 이슈로 2건 실패\n- [x] 성능 회귀 테스트\n- [ ] 보안 스캔 (의존성 취약점 1건 검토 중)\n\n## 2. 빌드\n\n```\nmake release VERSION=3.2.0\n```\n\n산출물: `dist/<PROJ_1>-3.2.0.tar.gz`, 컨테이너 이미지 `<PROJ_1>:3.2.0`\n\n## 3. 스테이징 배포\n\n- [x] 스테이징 배포 완료 (2026-08-18)\n- [x] 스모크 테스트\n- [ ] 48시간 관찰 (진행 중, 2026-08-20 종료)\n\n## 4. 프로덕션 배포 조건\n\n1. 3번 항목 전부 완료\n2. 레거시 SSO 통합 테스트 2건 해결 또는 known issue 등재\n3. 롤백 절차 리허설 완료\n\n## 5. 알려진 이슈\n\n| # | 이슈 | 영향 | 상태 |\n|---|---|---|---|\n| 1 | 레거시 SSO 클레임 매핑 부분 동작 | 세션 식별자 전파 불가 | 벤더 문의 중 |\n| 2 | 토큰 무효화 API 부재 | 만료까지 대기 필요 | v3.3 이월 |\n| 3 | 의존성 취약점 (transitive) | 낮음 | 검토 중 |\n\n1번은 세션 바인딩 미적용 결정의 직접 원인이다.\n상세는 `auth-review.md` §2.1 참조.\n\n## 6. 롤백\n\n```\nmake rollback VERSION=3.1.4\n```\n\n롤백 시 발급된 v3.2 토큰은 v3.1.4 에서 검증 실패한다 (서명 키 로테이션).\n롤백 전 토큰 무효화가 필요하나 API 가 없다 — 만료를 기다려야 한다.\n**최대 24시간 대기.** 이게 무효화 API 를 v3.3 으로 미룬 대가다.\n",
    "CONST_A": "---\ntitle: SDK v3.2 인증 설계 리뷰\n보안등급: 사내\nas_of: 2025-12-03\nformality: official\nowner: <PERSON_1>\n---\n\n# SDK v3.2 인증 설계 리뷰\n\n**리뷰어**: <PERSON_3>\n**대상**: SDK v3.2 인증 설계 (설계자 <PERSON_2>)\n**일자**: 2025-12-03\n**결과**: 조건부 승인\n\n---\n\n## 1. 리뷰 범위\n\n토큰 수명 정책, 갱신 방식, 세션 바인딩 적용 여부.\n\n## 2. 주요 결정 사항\n\n### 2.1 세션 바인딩 미적용 — 승인\n\n**세션 바인딩 미적용 결정. <SYS_1>가 세션 식별자를\ndownstream 으로 전파하지 않아 바인딩 자체가 불가능했다.**\n\n확인 경로:\n\n1. <SYS_1>의 클레임 매핑 설정을 확인했다.\n   세션 식별자에 해당하는 클레임이 매핑 테이블에 없다.\n2. 게이트웨이 벤더 문서를 확인했다. 해당 버전은 세션 식별자 전파를\n   지원하지 않는다. 상위 버전에서 추가됐으나 업그레이드 경로가 막혀 있다.\n   (인증서 체인 호환성 문제)\n3. <SYS_2> 쪽은 전파가 되지만, 레거시 SSO 를 경유하는 트래픽이\n   전체의 40% 라 부분 적용은 의미가 없다고 판단했다.\n\n즉 **바인딩을 하고 싶어도 할 수 없는 상태**였다. 설계 선택이 아니라 제약이다.\n\n> 성능 쪽 이슈도 별도로 보고됐다고 들었으나 이 리뷰에서 직접 확인하지는\n> 않았다. 어느 쪽이든 결론은 같다.\n\n### 2.2 토큰 수명 24시간 — 조건부 승인\n\n무효화 경로 없이 24시간은 길다. 아래를 조건으로 승인한다.\n\n- v3.3 에 토큰 무효화 API 추가\n- 무효화 API 추가 전까지 고위험 작업에는 재인증 요구\n\n### 2.3 백그라운드 무음 갱신 — 승인\n\n갱신 시 세션 유효성을 확인하지 않는 점을 지적했으나,\n현재 세션 식별자 전파가 안 되므로 확인할 대상 자체가 없다.\n2.1 이 해결되면 함께 재검토한다.\n\n## 3. 미해결 지적 사항\n\n| # | 지적 | 상태 |\n|---|---|---|\n| 1 | 토큰 무효화 경로 부재 | v3.3 이월 |\n| 2 | 세션 종료와 토큰 수명이 독립 | 2.1 해결 후 재검토 |\n| 3 | 레거시 SSO 클레임 매핑 부분 동작 | 벤더 문의 중 |\n\n## 4. 후속\n\n- <SYS_1> 업그레이드 가능성 재조사 (담당: <PERSON_5>)\n- 무효화 API 설계 (담당: <PERSON_4>, v3.3)\n"
  },
  "question_template": "design_rationale",
  "task": "rationale_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **7번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 8. `aud_fd654b9e058e4e9b838c` — 사내 · pseudonymized

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `result` |
| 질문자 | `person:demo` |
| 대상 | `person:choi` |
| 모델 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 4425 bytes |
| SHA-256 | `de2780728d6c81fb…` |
| 인용 | 3건 |
| 신뢰도 | 0.92 |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "tradeoffs": "string[]"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "CONST_A",
      "role": "constraint"
    }
  ],
  "excerpts": {
    "COMP_A": "---\ntitle: SDK v3.2 릴리스 체크리스트\n보안등급: 사내\nas_of: 2026-08-19\nformality: official\nowner: <PERSON_1>\n---\n\n# SDK v3.2 릴리스 체크리스트\n\n배포 파이프라인 담당: <PERSON_3>\n\n## 1. 사전 확인\n\n- [x] 단위 테스트 전체 통과\n- [x] 통합 테스트 (<SYS_2> 경유)\n- [ ] 통합 테스트 (레거시 SSO 경유) — 클레임 매핑 이슈로 2건 실패\n- [x] 성능 회귀 테스트\n- [ ] 보안 스캔 (의존성 취약점 1건 검토 중)\n\n## 2. 빌드\n\n```\nmake release VERSION=3.2.0\n```\n\n산출물: `dist/<PROJ_1>-3.2.0.tar.gz`, 컨테이너 이미지 `<PROJ_1>:3.2.0`\n\n## 3. 스테이징 배포\n\n- [x] 스테이징 배포 완료 (2026-08-18)\n- [x] 스모크 테스트\n- [ ] 48시간 관찰 (진행 중, 2026-08-20 종료)\n\n## 4. 프로덕션 배포 조건\n\n1. 3번 항목 전부 완료\n2. 레거시 SSO 통합 테스트 2건 해결 또는 known issue 등재\n3. 롤백 절차 리허설 완료\n\n## 5. 알려진 이슈\n\n| # | 이슈 | 영향 | 상태 |\n|---|---|---|---|\n| 1 | 레거시 SSO 클레임 매핑 부분 동작 | 세션 식별자 전파 불가 | 벤더 문의 중 |\n| 2 | 토큰 무효화 API 부재 | 만료까지 대기 필요 | v3.3 이월 |\n| 3 | 의존성 취약점 (transitive) | 낮음 | 검토 중 |\n\n1번은 세션 바인딩 미적용 결정의 직접 원인이다.\n상세는 `auth-review.md` §2.1 참조.\n\n## 6. 롤백\n\n```\nmake rollback VERSION=3.1.4\n```\n\n롤백 시 발급된 v3.2 토큰은 v3.1.4 에서 검증 실패한다 (서명 키 로테이션).\n롤백 전 토큰 무효화가 필요하나 API 가 없다 — 만료를 기다려야 한다.\n**최대 24시간 대기.** 이게 무효화 API 를 v3.3 으로 미룬 대가다.\n",
    "CONST_A": "---\ntitle: SDK v3.2 인증 설계 리뷰\n보안등급: 사내\nas_of: 2025-12-03\nformality: official\nowner: <PERSON_1>\n---\n\n# SDK v3.2 인증 설계 리뷰\n\n**리뷰어**: <PERSON_3>\n**대상**: SDK v3.2 인증 설계 (설계자 <PERSON_2>)\n**일자**: 2025-12-03\n**결과**: 조건부 승인\n\n---\n\n## 1. 리뷰 범위\n\n토큰 수명 정책, 갱신 방식, 세션 바인딩 적용 여부.\n\n## 2. 주요 결정 사항\n\n### 2.1 세션 바인딩 미적용 — 승인\n\n**세션 바인딩 미적용 결정. <SYS_1>가 세션 식별자를\ndownstream 으로 전파하지 않아 바인딩 자체가 불가능했다.**\n\n확인 경로:\n\n1. <SYS_1>의 클레임 매핑 설정을 확인했다.\n   세션 식별자에 해당하는 클레임이 매핑 테이블에 없다.\n2. 게이트웨이 벤더 문서를 확인했다. 해당 버전은 세션 식별자 전파를\n   지원하지 않는다. 상위 버전에서 추가됐으나 업그레이드 경로가 막혀 있다.\n   (인증서 체인 호환성 문제)\n3. <SYS_2> 쪽은 전파가 되지만, 레거시 SSO 를 경유하는 트래픽이\n   전체의 40% 라 부분 적용은 의미가 없다고 판단했다.\n\n즉 **바인딩을 하고 싶어도 할 수 없는 상태**였다. 설계 선택이 아니라 제약이다.\n\n> 성능 쪽 이슈도 별도로 보고됐다고 들었으나 이 리뷰에서 직접 확인하지는\n> 않았다. 어느 쪽이든 결론은 같다.\n\n### 2.2 토큰 수명 24시간 — 조건부 승인\n\n무효화 경로 없이 24시간은 길다. 아래를 조건으로 승인한다.\n\n- v3.3 에 토큰 무효화 API 추가\n- 무효화 API 추가 전까지 고위험 작업에는 재인증 요구\n\n### 2.3 백그라운드 무음 갱신 — 승인\n\n갱신 시 세션 유효성을 확인하지 않는 점을 지적했으나,\n현재 세션 식별자 전파가 안 되므로 확인할 대상 자체가 없다.\n2.1 이 해결되면 함께 재검토한다.\n\n## 3. 미해결 지적 사항\n\n| # | 지적 | 상태 |\n|---|---|---|\n| 1 | 토큰 무효화 경로 부재 | v3.3 이월 |\n| 2 | 세션 종료와 토큰 수명이 독립 | 2.1 해결 후 재검토 |\n| 3 | 레거시 SSO 클레임 매핑 부분 동작 | 벤더 문의 중 |\n\n## 4. 후속\n\n- <SYS_1> 업그레이드 가능성 재조사 (담당: <PERSON_5>)\n- 무효화 API 설계 (담당: <PERSON_4>, v3.3)\n"
  },
  "question_template": "design_rationale",
  "task": "rationale_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **8번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 9. `aud_9133cfa9e0a44f6385b1` — 기밀 · structured

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `request` |
| 질문자 | `person:demo` |
| 대상 | `person:kim` |
| 모델 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 499 bytes |
| SHA-256 | `fb9dafba5219c3b1…` |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "tradeoffs": "string[]"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "CONST_A",
      "role": "constraint"
    },
    {
      "ref": "CONST_B",
      "role": "constraint"
    }
  ],
  "facts": {
    "COMP_A": {
      "renewal_mode": "none",
      "session_binding": "required"
    },
    "CONST_A": {
      "renewal_mode": "background_silent",
      "session_binding": "none"
    },
    "CONST_B": {
      "renewal_mode": "background_silent",
      "session_binding": "none"
    }
  },
  "question_template": "design_rationale",
  "task": "rationale_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **9번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

### 10. `aud_4d95b96a12ad4f6a827a` — 사내 · pseudonymized

| 항목 | 값 |
|---|---|
| 시각 | `2026-08-19T14:35:00+09:00` |
| 종류 | `request` |
| 질문자 | `person:demo` |
| 대상 | `person:choi` |
| 모델 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| 전송 | `mock` |
| 도착지 | `https://api.friendli.ai/dedicated/v1` |
| 승인 | `person:demo` |
| 검증 | 6/6 |
| 크기 | 4425 bytes |
| SHA-256 | `de2780728d6c81fb…` |

**경계를 넘은 것 전부:**

```json
{
  "answer_format": {
    "rationale": "string",
    "tradeoffs": "string[]"
  },
  "domain": "authentication",
  "entities": [
    {
      "ref": "COMP_A",
      "role": "our_component"
    },
    {
      "ref": "CONST_A",
      "role": "constraint"
    }
  ],
  "excerpts": {
    "COMP_A": "---\ntitle: SDK v3.2 릴리스 체크리스트\n보안등급: 사내\nas_of: 2026-08-19\nformality: official\nowner: <PERSON_1>\n---\n\n# SDK v3.2 릴리스 체크리스트\n\n배포 파이프라인 담당: <PERSON_3>\n\n## 1. 사전 확인\n\n- [x] 단위 테스트 전체 통과\n- [x] 통합 테스트 (<SYS_2> 경유)\n- [ ] 통합 테스트 (레거시 SSO 경유) — 클레임 매핑 이슈로 2건 실패\n- [x] 성능 회귀 테스트\n- [ ] 보안 스캔 (의존성 취약점 1건 검토 중)\n\n## 2. 빌드\n\n```\nmake release VERSION=3.2.0\n```\n\n산출물: `dist/<PROJ_1>-3.2.0.tar.gz`, 컨테이너 이미지 `<PROJ_1>:3.2.0`\n\n## 3. 스테이징 배포\n\n- [x] 스테이징 배포 완료 (2026-08-18)\n- [x] 스모크 테스트\n- [ ] 48시간 관찰 (진행 중, 2026-08-20 종료)\n\n## 4. 프로덕션 배포 조건\n\n1. 3번 항목 전부 완료\n2. 레거시 SSO 통합 테스트 2건 해결 또는 known issue 등재\n3. 롤백 절차 리허설 완료\n\n## 5. 알려진 이슈\n\n| # | 이슈 | 영향 | 상태 |\n|---|---|---|---|\n| 1 | 레거시 SSO 클레임 매핑 부분 동작 | 세션 식별자 전파 불가 | 벤더 문의 중 |\n| 2 | 토큰 무효화 API 부재 | 만료까지 대기 필요 | v3.3 이월 |\n| 3 | 의존성 취약점 (transitive) | 낮음 | 검토 중 |\n\n1번은 세션 바인딩 미적용 결정의 직접 원인이다.\n상세는 `auth-review.md` §2.1 참조.\n\n## 6. 롤백\n\n```\nmake rollback VERSION=3.1.4\n```\n\n롤백 시 발급된 v3.2 토큰은 v3.1.4 에서 검증 실패한다 (서명 키 로테이션).\n롤백 전 토큰 무효화가 필요하나 API 가 없다 — 만료를 기다려야 한다.\n**최대 24시간 대기.** 이게 무효화 API 를 v3.3 으로 미룬 대가다.\n",
    "CONST_A": "---\ntitle: SDK v3.2 인증 설계 리뷰\n보안등급: 사내\nas_of: 2025-12-03\nformality: official\nowner: <PERSON_1>\n---\n\n# SDK v3.2 인증 설계 리뷰\n\n**리뷰어**: <PERSON_3>\n**대상**: SDK v3.2 인증 설계 (설계자 <PERSON_2>)\n**일자**: 2025-12-03\n**결과**: 조건부 승인\n\n---\n\n## 1. 리뷰 범위\n\n토큰 수명 정책, 갱신 방식, 세션 바인딩 적용 여부.\n\n## 2. 주요 결정 사항\n\n### 2.1 세션 바인딩 미적용 — 승인\n\n**세션 바인딩 미적용 결정. <SYS_1>가 세션 식별자를\ndownstream 으로 전파하지 않아 바인딩 자체가 불가능했다.**\n\n확인 경로:\n\n1. <SYS_1>의 클레임 매핑 설정을 확인했다.\n   세션 식별자에 해당하는 클레임이 매핑 테이블에 없다.\n2. 게이트웨이 벤더 문서를 확인했다. 해당 버전은 세션 식별자 전파를\n   지원하지 않는다. 상위 버전에서 추가됐으나 업그레이드 경로가 막혀 있다.\n   (인증서 체인 호환성 문제)\n3. <SYS_2> 쪽은 전파가 되지만, 레거시 SSO 를 경유하는 트래픽이\n   전체의 40% 라 부분 적용은 의미가 없다고 판단했다.\n\n즉 **바인딩을 하고 싶어도 할 수 없는 상태**였다. 설계 선택이 아니라 제약이다.\n\n> 성능 쪽 이슈도 별도로 보고됐다고 들었으나 이 리뷰에서 직접 확인하지는\n> 않았다. 어느 쪽이든 결론은 같다.\n\n### 2.2 토큰 수명 24시간 — 조건부 승인\n\n무효화 경로 없이 24시간은 길다. 아래를 조건으로 승인한다.\n\n- v3.3 에 토큰 무효화 API 추가\n- 무효화 API 추가 전까지 고위험 작업에는 재인증 요구\n\n### 2.3 백그라운드 무음 갱신 — 승인\n\n갱신 시 세션 유효성을 확인하지 않는 점을 지적했으나,\n현재 세션 식별자 전파가 안 되므로 확인할 대상 자체가 없다.\n2.1 이 해결되면 함께 재검토한다.\n\n## 3. 미해결 지적 사항\n\n| # | 지적 | 상태 |\n|---|---|---|\n| 1 | 토큰 무효화 경로 부재 | v3.3 이월 |\n| 2 | 세션 종료와 토큰 수명이 독립 | 2.1 해결 후 재검토 |\n| 3 | 레거시 SSO 클레임 매핑 부분 동작 | 벤더 문의 중 |\n\n## 4. 후속\n\n- <SYS_1> 업그레이드 가능성 재조사 (담당: <PERSON_5>)\n- 무효화 API 설계 (담당: <PERSON_4>, v3.3)\n"
  },
  "question_template": "design_rationale",
  "task": "rationale_lookup"
}
```

> ✅ 자동 검사: 원문 조각 0건 · 금칙어 0건

- [ ] **10번 육안 확인 완료** — 위 체크리스트 7항목을 모두 확인했다

---

## 판정

- 자동 검사: ✅ 유출 0건
- 육안 확인: 위 10건의 체크박스가 모두 표시되면 G4 통과

확인자: ____________  날짜: ____________

## 대조 대상 문서

전수 검사가 대조한 문서 11건:

- `corpus/choi/docs/auth-review.md`
- `corpus/choi/docs/release-checklist.md`
- `corpus/customer-H/benchmark-prod-2025-11.md`
- `corpus/customer-H/req-spec-2026H.md`
- `corpus/kim/docs/auth-design.md`
- `corpus/kim/docs/sdk-pricing-tiers.md`
- `corpus/kim/notes/2025-11-auth.md`
- `corpus/park/configs/v3.yaml`
- `corpus/park/runs/2026-08-19/train.log`
- `corpus/park/scripts/preprocess_v3.py`
- `corpus/public/oauth-rfc-summary.md`

# U6 — Business Rules

규칙 ID: `BR-D-*` 데이터 · `BR-EV-*` 평가

---

## BR-D — 샘플 데이터

### BR-D-01 · 공식 문서만으로 채우지 않는다 (FR-50) 🔴

> 이 프로젝트의 값어치는 **문서에 없는 지식**을 답하는 데 있다.
> 코퍼스가 공식 문서만으로 채워지면 위키 검색과 구분되지 않는다.

각 사람의 자료에 다음을 **반드시** 섞는다.

| 종류 | 예 | 왜 필요한가 | 최소 개수 |
|---|---|---|---|
| **개인 메모** | `notes/2025-11-auth.md` — "토큰 수명 왜 24시간으로 뒀는지" 반쪽짜리 기록 | 결정의 근거는 대개 여기 남는다 | 사람당 3 |
| **스크립트·설정** | `preprocess_v3.py`, `configs/v3.yaml` | 기법에 대한 답이 여기 있다 (시나리오 2) | 박선임 7 |
| **실험 로그** | `runs/2026-08-19/train.log` | 결과 수치는 문서화되지 않는다 | 박선임 3 |
| **엇갈리는 기록** | 김책임 메모 vs 최민수 리뷰 | 병기 검증 (시나리오 3) | 1쌍 |

**검증**: `make eval`이 `source_kind` 분포를 리포트한다. `design_doc`이 60%를 넘으면 경고.

### BR-D-02 · 엇갈리는 기록을 의도적으로 심는다 (FR-51) 🔴

같은 사건("왜 세션 바인딩을 안 쓰나")을 두 사람이 다르게 서술한다.

| 문서 | 서술 | 시점 | 성격 |
|---|---|---|---|
| `corpus/kim/notes/2025-11-auth.md` | "동시 인증 3천 TPS에서 세션 조회 지연이 임계를 넘어 제외" | 2025-11 | **비공식** (개인 메모) |
| `corpus/choi/docs/auth-review.md` | "레거시 SSO가 세션 식별자를 전파하지 않아 바인딩 자체가 불가능" | 2025-12 | **공식** (설계 리뷰) |

**둘 다 사실일 수 있게 쓴다.** 한쪽이 명백히 틀리면 `divergent` 처리가 "오답 걸러내기"로 보이고, 그건 이 기능의 취지가 아니다. 성능 문제와 호환 문제가 **함께 있었던** 것처럼 쓴다.

시점을 한 달 차이로 두어 `divergence_note`의 `{gap}`이 "한 달"이 되게 한다.

### BR-D-03 · 함정 문서 (FR-52) 🔴

`corpus/kim/docs/sdk-pricing-tiers.md`

| 조건 | 값 |
|---|---|
| 경로 | `corpus/kim/docs/` — **`customer-*/`가 아니다** |
| 헤더 | 등급 표기 **없음** |
| 겉보기 | 평범한 SDK 티어 설계 문서 |
| 숨은 것 | 본문 중간에 고객사별 단가 (`H社 라이선스 12억원`) |
| 정답 등급 | **`secret`** |
| 탐지 경로 | **규칙 4번 (금액 정규식 + 고객사명 리터럴)** |

**이 문서가 분류기의 진짜 시험이다.** 경로와 헤더로는 `internal`로 판정되고, 본문 스캔만이 유일한 단서다.

`labels.json`에 `trap: true`로 표시하고 평가 하네스가 별도 리포트한다.

### BR-D-04 · 성능 수치를 어휘 사전에 넣지 않는다 (FR-54) 🔴

`corpus/customer-H/benchmark-prod-2025-11.md`에 실제 수치를 쓴다.
```
p99 latency: 840ms
throughput: 3,120 TPS
측정 환경: 고객사 H 프로덕션
```

그리고 **`vocab.json`에 `p99_latency_ms`·`throughput_tps` 슬롯을 만들지 않는다.**

→ 시나리오 3 후속 질문에서 EXAONE이 이 필드를 만들려 하고, 조립 단계에서 버려지고, 필수 슬롯이 채워지지 않아 `ExtractionFailed`가 되고, `answer_in_zone()` 폴백이 발생한다.

> 사전에 없는 것은 실수로도 나갈 수 없다 — 화이트리스트 방식의 실질적 효과이며, 이게 데모 3막의 결정적 장면이다.

### BR-D-05 · 경로 규칙과 정합성 유지

`ClassificationRules.secret_path_globs`와 코퍼스 경로가 일치해야 한다.

| glob | 해당 문서 |
|---|---|
| `corpus/customer-*/**` | 고객사 요구사항·협의·벤치마크 |
| `corpus/**/benchmark/**` | (예비) |
| `corpus/public/**` → `open` | 공개 스펙 요약 |

**함정 문서는 어떤 glob에도 걸리지 않는다.** 그게 함정의 정의다.

### BR-D-06 · 절대 경로를 쓰지 않는다 (FR-22, NFR-PO-01)

문서 내 참조·세션 `open_paths`·`labels.json` 경로 전부 `MESH_DATA_ROOT` 상대 경로.

`scenarios.md`의 `~/work/customer-H/req-spec-2026H.md` → `corpus/customer-H/req-spec-2026H.md`로 매핑한다.
UI에는 `display_title`("고객사 H 요구사항명세서")만 보이므로 사용자 경험은 같다.

### BR-D-07 · 문서 헤더 형식

각 문서 상단에 프런트매터를 둔다. 등급 판정 규칙 2번과 `Chunk` 메타데이터의 근거.

```markdown
---
title: 고객사 H 5G 코어망 인증 요구사항명세서
보안등급: 기밀
as_of: 2026-07-15
formality: official
owner: person:kim
---
```

| 필드 | 용도 |
|---|---|
| `title` | `Chunk.display_title` |
| `보안등급` | 등급 판정 규칙 2번 (`기밀`/`사내`/`공개`) |
| `as_of` | `Chunk.as_of`. 시나리오 3 병기에 쓰인다 |
| `formality` | `Chunk.formality`. 개인 메모는 `informal` |
| `owner` | `entity_id` 검증 |

**함정 문서에는 `보안등급`을 넣지 않는다** (BR-D-03).
스크립트·설정 파일(`.py`, `.yaml`)은 주석으로 같은 정보를 둔다.

### BR-D-08 · 실제 사내 문서·고객 자료를 일절 사용하지 않는다

전부 우리가 직접 만든 샘플이다 (설계 §2).
문서에 실제 회사명·제품명·인명을 쓰지 않는다. `H社`·`HanaTel`·`Nova 게이트웨이`·`atlas-ml`은 모두 가상이다.

**PII 정책**: 인명은 `김철수`·`박선영`·`최민수`·`정연구원`·`한지원`으로 고정. 실제 팀원 이름을 쓰지 않는다.

---

## BR-EV — 평가

### BR-EV-01 · Day 2 게이트는 실패 종료한다 (S-30) 🔴

```bash
make eval-classify
```

| 조건 | 종료 코드 |
|---|---|
| 기밀 재현율 == 100% **그리고** 전체 정확도 ≥ 90% | 0 |
| 그 외 | **1 (non-zero)** |

**실패 시 Day 3으로 넘어가지 않는다** (설계 §7.2).

출력에 오분류 문서 전체 목록 + `rule_tier` / `exaone_tier` / `reasons`를 담아 **어느 판정기가 놓쳤는지** 즉시 알 수 있게 한다.

함정 문서를 놓친 경우 별도로 강조 표시한다.

### BR-EV-02 · 유출 전수 검사 (FR-55, S-05) 🔴

```
sweep_for_leaks(corpus, audit_records):
  for record in audit_records:            # 감사 로그의 모든 페이로드
    for doc in corpus:                     # 전 문서 (등급 무관)
      if any(ng in flatten(record.payload) for ng in ngrams(doc.text, 5)):
        hits.append(...)
    for term in banned.literals + banned.patterns:
      if matches(term, flatten(record.payload)):
        banned_hits.append(...)
```

**로컬 검증(BR-V-05)과 범위가 다르다.**

| | 로컬 검증 | 전수 검사 |
|---|---|---|
| 대조 대상 | 이 호출에 동원된 원문만 | **전 문서** |
| 시점 | 전송 직전 | Day 5 / `make eval` |
| 목적 | 차단 | 증명 |
| 잡는 것 | 이 페이로드의 원문 혼입 | **등급 판정 실패로 다른 문서가 섞인 경우** |

두 번째 항목이 전수 검사의 존재 이유다. 등급 판정이 실패하면 로컬 검증은 통과하지만(그 문서를 `originals`에 넣지 않았으므로) 전수 검사가 잡는다.

**목표: 0건.** 1건이라도 있으면 실패 종료.

### BR-EV-03 · 검사기 자체를 검사한다 (PB-D3)

"유출 0건"이라는 결과를 믿으려면 **검사기가 실제로 유출을 탐지하는지** 확인해야 한다.

```python
@given(korean_technical_text())
def test_sweep_detects_planted_leak(text):
    ng = random.choice(list(ngrams(text, 5)))
    fake_payload = {"reason": f"prefix {ng} suffix"}
    report = sweep_for_leaks([make_chunk(text)], [make_record(fake_payload)])
    assert not report.clean          # 반드시 탐지
```

검사기가 아무것도 못 잡는 버그가 있으면 "유출 0건"은 무의미하다.

### BR-EV-04 · 육안 전수 확인 (설계 §9)

자동 검사와 별도로, Day 5에 **감사 로그의 모든 페이로드를 사람이 눈으로 확인**한다.

```bash
make eval-dump-payloads > /tmp/payloads.txt
```

수십 건 규모이므로 실제로 가능하다. **작은 규모가 곧 증명 가능성이다** (설계 §2.1).

체크리스트: 고객사명 · 제품명 · 버전 · 요구사항 번호 · 원문 문장 · 담당자 · 일정 · 금액.

### BR-EV-05 · 외부 추론 실익 비교 (S-08)

```bash
make eval-compare
```

같은 질문에 대해 두 경로의 답을 나란히 출력한다.

| 경로 | 방법 |
|---|---|
| A. EXAONE 단독 | 원문을 그대로 주고 답을 받는다 (`answer_in_zone`) |
| B. 구조추출 + Agent | 정상 경로 |

**정량 지표를 만들지 않는다.** 5일 일정에서 자동 채점기를 만드는 것은 과하고, 대신 **두 답을 나란히 문서에 넣어 사람이 판단하게** 한다.

> 이게 없으면 "그냥 EXAONE만 쓰면 되지 않나"에 답할 수 없다 (설계 §9).

데모에서 이 비교를 1막 결정적 장면 ③으로 쓴다.

### BR-EV-06 · 자동 응답률과 인용 준수

`/api/health`의 처분 카운터를 읽어 계산한다.

| 지표 | 계산 | 목표 |
|---|---|---|
| 자동 응답률 | `AUTO / (AUTO+UNVERIFIED+ESCALATE)` | ≥ 50% |
| 인용 준수 | `AUTO 중 인용≥1 / AUTO` | **100%** |

**인용 준수는 구조적으로 100%다** (BR-O-04). 100%가 아니면 코드 버그이므로 실패 종료한다.

### BR-EV-07 · 시나리오 예제 테스트 (PBT-10)

`data/questions.json`의 `expect`를 어서션으로 실행한다.

```python
@pytest.mark.parametrize("sc", load_questions())
def test_scenario(sc):
    result = run_scenario(sc)              # 목업 모드로 실행
    for key, expected in sc["expect"].items():
        assert_expect(result, key, expected)
```

**목업 모드로 실행한다.** 네트워크 없이 CI에서 돌아야 하고, 검증·조립·감사는 실제 코드가 돌므로 의미가 있다 (U1 §9).

`audit_record: false` 검증이 특히 중요하다 — 레코드가 **없어야** 한다.

### BR-EV-08 · 리포트 형식

```
make eval  ->  aidlc-docs/construction/build-and-test/eval-report.md
```

```markdown
# 평가 리포트  2026-08-19T18:00:00Z

## 등급 분류
- 전체 정확도: 96.4% (53/55)          목표 ≥90%   ✅
- 기밀 재현율: 100% (7/7)             목표 100%   ✅
- 함정 문서: 1/1 탐지                              ✅
- 오분류 2건:
  - corpus/park/notes/2026-07-ideas.md  expected=internal actual=secret
    rule=secret (금액 패턴 오탐: "3천 TPS")  <- 상향 오류. 안전한 방향
  - ...

## 유출 전수 검사
- 페이로드 검사: 24건
- 문서 대조: 55건 (5-gram)
- 원문 히트: 0건                        목표 0건    ✅
- 금칙어 히트: 0건                                  ✅

## 처분 분포
- AUTO 14 / UNVERIFIED 5 / ESCALATE 4 / BLOCKED 1
- 자동 응답률: 58.3%                   목표 ≥50%   ✅
- 인용 준수: 100% (14/14)              목표 100%   ✅

## 시나리오
- s1 ✅  s2a ✅  s2b ✅  s3a ✅  s3b ✅

## 코퍼스 구성
- design_doc 18 / note 13 / script 4 / config 3 / run_log 3 / minutes 4 / spec 3 / benchmark 1
- 공식 문서 비율: 47%                   목표 <60%   ✅

## 외부 추론 실익
(EXAONE 단독 vs 구조추출+Agent 답변 비교 — 별첨)
```

**오분류에 "상향 오류 / 하향 오류"를 구분 표시한다.** 상향 오류(internal → secret)는 불편이고, 하향 오류(secret → internal)는 유출이다. 비대칭을 리포트에도 반영한다.

### BR-EV-09 · 코퍼스 규모 하한

문서 40건 미만이면 경고한다. 설계 §2는 40~60건을 전제하며, 너무 적으면 분류 정확도 측정이 통계적으로 무의미하다.

단 **시나리오별 필수 문서 8개**(`scenarios.md` 부록)를 먼저 만들고, 그 다음에 규모를 채운다. 순서가 중요하다 — 시간이 부족해도 시나리오는 돌아야 한다.

| 시나리오 | 필수 문서 |
|---|---|
| 1 | `customer-H/req-spec-2026H.md` (기밀), `kim/docs/auth-design.md` (사내) |
| 2 | `park/scripts/preprocess_v3.py`, `park/configs/v3.yaml`, `park/runs/2026-08-19/train.log` |
| 3 | `kim/notes/2025-11-auth.md` (비공식), `choi/docs/auth-review.md` (공식), `customer-H/benchmark-prod-2025-11.md` (기밀) |
| 분류기 | `kim/docs/sdk-pricing-tiers.md` (함정) |

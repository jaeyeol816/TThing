# U6 — Business Logic Model

---

## 1. 이 유닛이 하는 일

> 검증 가능성을 만든다.

실제 데이터가 없다는 게 제약처럼 보이지만, 이 프로젝트에서는 **검증을 가능하게 만드는 조건**이다.

| 이점 | 왜 |
|---|---|
| **정답 라벨이 있다** | 문서를 우리가 만들었으니 각 문서의 등급 정답을 안다 → 분류 정확도를 **정확히 측정** |
| **유출을 전수 검사할 수 있다** | 페이로드가 수십 건이라 **전량을 눈으로 확인**할 수 있다. "유출 0건"이 주장이 아니라 검증 결과가 된다 |
| **함정을 심을 수 있다** | 겉보기엔 평범하지만 실제로는 기밀인 문서를 일부러 넣어 분류기가 잡는지 시연 |
| **데모가 재현된다** | 질문과 정답을 고정할 수 있어 시연이 흔들리지 않는다 |

**작은 규모가 곧 증명 가능성이다.**

---

## 2. 작업 순서

```
Day 1  1. vocab.json 초안 -> U1 과 협의 -> *** 동결 ***
       2. labels.json 스키마 + banned.json
       3. 시나리오 필수 문서 8개 (BR-EV-09)
       4. api/ 픽스처 (U3 계약과 함께) -> U4 가 Day 4 에 바로 시작 가능

Day 2  5. 코퍼스 40~60건 완성
       6. labels.json 전 문서 라벨링
       7. 함정 문서 작성 (BR-D-03)
       8. eval/test_classification.py -> *** Day 2 게이트 ***

Day 4  9. questions.json (데모 질문 + expect)
      10. 목업 픽스처 녹화 (live 로 3막 1회 실행)
      11. eval/test_leak_sweep.py, test_scenarios.py

Day 5 12. make eval 전체 실행 + 리포트
      13. 육안 전수 확인 (BR-EV-04)
      14. 외부 추론 실익 비교 (BR-EV-05)
```

**1번이 임계 경로의 시작이다.** `vocab.json`이 없으면 U1의 추출기를 만들 수 없다.
슬롯 정의를 완벽하게 만들려 하지 말고, **시나리오 3개가 돌 수 있는 최소 슬롯**으로 시작해 동결한다.

---

## 3. 코퍼스 작성 로직

### 문서별 체크리스트

각 문서를 쓸 때 확인한다.

```
[ ] 프런트매터 5개 필드 (BR-D-07)
[ ] 경로가 등급 규칙과 정합 (BR-D-05)
[ ] labels.json 에 등록
[ ] 실제 회사·제품·인명 미사용 (BR-D-08)
[ ] 절대 경로 미사용 (BR-D-06)
[ ] source_kind 분포 균형 확인 (BR-D-01)
```

### 등급별 작성 지침

| 등급 | 쓰는 것 | 주의 |
|---|---|---|
| `secret` | 고객사명·계약번호·금액·성능 실측·요구사항 번호 | **금칙어 사전과 일치시킨다.** 사전에 없는 고객사명을 쓰면 규칙 3번이 못 잡는다 |
| `internal` | 사내 프로젝트명·경로·시스템명 + 기술 내용 | 가명화 대상과 기술 용어를 명확히 구분되게 쓴다 |
| `open` | 공개 스펙 요약 | 프런트매터에 `보안등급: 공개` 명시 |

**`secret` 문서의 금칙어를 `banned.json`과 동기화하는 것이 중요하다.**
문서에 `하나텔`이라고 썼는데 사전에 `HanaTel`만 있으면 규칙 3번이 놓친다. 양쪽을 함께 편집한다.

### 시나리오 2를 위한 스크립트 작성

`corpus/park/scripts/preprocess_v3.py`는 **실제로 그럴듯한 코드**여야 한다.

```python
# 시나리오 2 의 답이 이 코드에 있다
from atlas_ml.sampling import RandomOverSampler          # <PROJ_1> 로 가명화됨

sampler = RandomOverSampler(sampling_strategy=0.5, random_state=42)
class_weight = "balanced_subsample"
```

**가명화 대상과 기술 용어가 한 줄에 섞여 있는 것이 핵심이다.**
`atlas_ml` → `<PROJ_1>`은 치환되고, `RandomOverSampler`·`balanced_subsample`·`0.5`·`42`는 보존된다. 이게 사내 등급 가명화의 실효성을 보여주는 장면이다 (BR-P-01).

### 엇갈리는 기록 작성 (BR-D-02)

두 문서를 **함께** 쓴다. 따로 쓰면 자연스럽게 엇갈리지 않는다.

```
kim/notes/2025-11-auth.md  (비공식, 2025-11)
  "동시 인증 3천 TPS 부하 테스트에서 세션 조회 p99가 임계를 넘었다.
   세션 바인딩은 일단 빼기로 했다. 수치는 고객 환경 벤치마크 문서에."

choi/docs/auth-review.md   (공식, 2025-12)
  "세션 바인딩 미적용 결정. 레거시 SSO 게이트웨이가 세션 식별자를
   downstream 으로 전파하지 않아 바인딩 자체가 불가능했다."
```

**둘 다 사실이다.** 성능 문제가 있었고, 호환 문제도 있었다. 김책임은 자기가 본 것(성능)을, 최민수는 자기가 본 것(호환)을 기록했다. 이게 현실이고, `divergent` 처리가 필요한 이유다.

김책임 메모가 벤치마크 문서를 언급하는 것이 **시나리오 3 후속 질문의 자연스러운 유도**가 된다.

---

## 4. 평가 하네스 로직

### `test_classification.py` — Day 2 게이트

```
1. labels.json 로드
2. 각 문서에 대해:
     chunk = store.read([path])[0]
     decision = classifier.classify(chunk.text, chunk.internal_path, rules, exaone)
     기록: expected, actual, rule_tier, exaone_tier, reasons, is_trap
3. 집계:
     accuracy      = correct / total
     secret_recall = secret_recalled / secret_total
     trap_recall   = trap_recalled / trap_total
4. 오분류를 상향/하향으로 분류
     상향 (internal -> secret): 불편
     하향 (secret -> internal): *** 유출 ***
5. assert secret_recall == 1.0
   assert accuracy >= 0.90
6. 리포트 출력 (실패 시에도)
```

**리포트를 실패 시에도 출력하는 것이 중요하다.** assert가 먼저 터지면 어느 문서가 문제인지 모른다. 집계와 출력을 먼저 하고 assert를 마지막에 둔다.

### `test_leak_sweep.py` — Day 5 게이트

```
1. 모든 시나리오를 실행해 감사 로그를 채운다 (목업 모드)
2. audit.sweep_for_leaks(corpus, records)
3. assert report.clean
4. 실패 시 히트별로 record_id · document_path · ngram 출력
```

**시나리오를 먼저 실행하는 것이 이 테스트의 절반이다.** 감사 로그가 비어 있으면 "유출 0건"이 자동으로 성립하고 무의미하다.

`report.payloads_scanned == 0`이면 **실패**로 처리한다.

### `test_scenarios.py` — 예제 기반 (PBT-10)

```
1. questions.json 의 5개 시나리오
2. 목업 모드로 실행 (네트워크 없이 CI 에서)
3. expect 의 각 키를 어서션
     tier / representation / validation / disposition
     verbatim_sentence_count / audit_record / divergent
     answer_contains / answer_must_not_contain
```

**`audit_record: false` 검증** (s3b):
```python
before = audit.count()
run_scenario(s3b)
assert audit.count() == before      # 레코드가 늘지 않아야 한다
```

**`answer_must_not_contain: ["REQ_A"]`** (s1): 재수화 실패 탐지. 최종 답변에 기호가 남아 있으면 치환이 안 된 것이다.

### `compare_exaone_solo.py` — 외부 추론 실익

```
for sc in questions.json:
    a = gatekeeper.answer_in_zone(sc.question, chunks)    # EXAONE 단독
    b = 정상 경로                                          # 구조추출 + Agent
    출력: 질문 / A 답변 / B 답변 나란히
```

**자동 채점기를 만들지 않는다.** 두 답을 나란히 마크다운에 출력하고 사람이 읽는다.
데모에서 이 출력을 그대로 보여준다 (1막 결정적 장면 ③).

---

## 5. 목업 픽스처 녹화

```
Day 4:
  1. EXAONE_MODE=live AGENT_TRANSPORT=direct MESH_RECORD_FIXTURES=1
  2. 3막 전체를 UI 로 한 번 수행 (5개 시나리오)
  3. data/fixtures/ 에 자동 저장
  4. 커밋
  5. EXAONE_MODE=mock AGENT_MODE=mock 으로 재실행해 동일 결과 확인
```

**5번이 필수다.** 녹화만 하고 재생을 확인하지 않으면 키 불일치로 데모 당일 실패한다.

**키 설계**: `sha1(입력)[:12]`. 입력이 조금이라도 다르면 키가 달라져 재생 실패한다.
그래서 재생 실패 시 **명시적으로 에러를 던진다** — 조용히 기본값을 반환하면 리허설에서 못 잡는다.

```python
def _fixture(self, key: str) -> dict:
    p = FIXTURES / f"{key}.json"
    if not p.exists():
        raise FixtureMissing(f"no fixture for {key}. "
                             f"Re-record with MESH_RECORD_FIXTURES=1")
    return json.loads(p.read_text())
```

---

## 6. `make eval` 흐름

```
make eval:
  1. pytest tests/eval/test_classification.py   -> 분류 정확도
  2. pytest tests/eval/test_scenarios.py        -> 5개 시나리오 (감사 로그 채움)
  3. pytest tests/eval/test_leak_sweep.py       -> 전수 검사
  4. 처분 카운터 조회 (/api/health)             -> 자동 응답률 · 인용 준수
  5. 코퍼스 구성 집계                            -> source_kind 분포
  6. 리포트 생성
       aidlc-docs/construction/build-and-test/eval-report.md
  7. 하나라도 목표 미달이면 non-zero 종료
```

**순서가 중요하다.** 2번이 감사 로그를 채우고 3번이 그걸 검사한다. 순서를 바꾸면 빈 로그를 검사한다.

`make eval-classify`는 1번만 단독 실행한다 (Day 2 게이트용, 빠르다).

---

## 7. 지연

| 작업 | 예상 시간 |
|---|---|
| `test_classification` (문서 55건, EXAONE 포함) | ~60s (규칙이 대부분 걸러 EXAONE 호출은 절반 이하) |
| `test_classification` (목업) | < 2s |
| `test_scenarios` (목업) | < 5s |
| `test_leak_sweep` (55 문서 × 24 페이로드) | < 3s |
| `make eval` 전체 (목업) | < 15s |
| `make eval` 전체 (live) | ~2분 |

**CI/개발 중에는 목업으로 돌린다.** live 실행은 Day 2 게이트와 Day 5 최종 측정에만.

**전수 검사가 빠른 이유**: 문서별 5-gram `frozenset`을 한 번 만들어 재사용하고, 페이로드는 2KB다. 55 × 24 = 1320회 집합 연산이 밀리초 단위.

---

## 8. 테스트 가능한 속성 (PBT-01)

`domain-entities.md` §8에 PB-D1~PB-D3.

**PB-D3(검사기가 심은 유출을 반드시 탐지)이 이 유닛에서 가장 중요하다.**
"유출 0건"이라는 결론은 검사기가 제대로 동작한다는 전제 위에 있다. 그 전제를 검사한다.

**PBT 미적용 (N/A 근거)**
- 코퍼스 문서 — 데이터. 테스트 대상이 아니다
- `test_classification` — 정답 라벨과 비교하는 예제 기반이 본질. PBT의 대상이 아니다
- PBT-05 오라클 — `labels.json`이 정답이지만 이건 고정 데이터 비교이고 오라클 PBT가 아니다. **N/A**
- PBT-06 상태 기반 — 전부 stateless. **N/A**

---

## 9. 이 유닛이 성공했는지 판정하는 기준

| # | 기준 | 근거 |
|---|---|---|
| 1 | 문서 40~60건, 공식 문서 비율 < 60% | BR-D-01 |
| 2 | 함정 문서가 `secret`으로 분류됨 | BR-D-03 |
| 3 | 엇갈리는 기록 1쌍이 `divergent: true`를 만든다 | BR-D-02 |
| 4 | `vocab.json`에 성능 수치 슬롯이 없고, 시나리오 3 후속이 폴백된다 | BR-D-04 |
| 5 | `make eval-classify`가 Day 2에 통과 | BR-EV-01 |
| 6 | `make eval`이 유출 0건을 보고 | BR-EV-02 |
| 7 | 목업 모드로 3막 전체가 네트워크 없이 재생 | §5 |
| 8 | 육안 전수 확인 완료 | BR-EV-04 |
| 9 | EXAONE 단독 대비 품질 우위를 보이는 예시 확보 | BR-EV-05 |

**4번이 가장 미묘하다.** 어휘 사전에 무엇을 **넣지 않았는지**가 데모의 결정적 장면을 만든다.
슬롯을 추가하고 싶은 유혹이 생기면 `_intentionally_absent`를 읽는다.

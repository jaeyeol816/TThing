# TODO — 기타 (구조 / 인프라 / 운영)

폴더 구조, 설정, 테스트, 문서, 배포에 관한 작업.

---

## ✅ 구현 완료

### 폴더 구조 재편 (§4.1)

`data/` → `agents/` 로 전면 이전:

```
agents/
├── shared/                    공유 자산
│   ├── vocab.json             어휘 사전 (Day 1 동결)
│   ├── banned.json            금칙어
│   ├── pseudonyms.json        가명화 대상
│   ├── labels.json            등급 정답 라벨
│   ├── questions.json         데모 질문 프리셋
│   ├── mesh.db                감사 로그 (SQLite)
│   ├── fixtures/              LLM 응답 캐시
│   │   ├── agent/
│   │   ├── exaone/
│   │   └── api/
│   ├── security_protocol/
│   │   ├── company.yaml       전사 프로토콜
│   │   └── teams/             팀별 프로토콜
│   └── data/public/           공개 문서
│
├── person_kim/
│   ├── data/                  업무 자료
│   │   ├── customer-H/
│   │   ├── docs/
│   │   ├── notes/
│   │   ├── uploads/           (gitignore)
│   │   └── kb/                (gitignore) 자동 생성 지식
│   ├── gatekeeper/
│   │   ├── session.json       작업 상태
│   │   └── verified.json      (gitignore) 승인된 Q&A
│   └── security_protocol/
│       └── protocol.yaml      개인 프로토콜
│
├── person_park/  (동일 구조)
└── person_choi/  (동일 구조)
```

### Config 경로 체계

agent별 경로 헬퍼 추가:

| 메서드 | 반환 |
|---|---|
| `shared_root` | `agents/shared/` |
| `agent_root(id)` | `agents/{id}/` |
| `agent_data_root(id)` | `agents/{id}/data/` |
| `agent_gatekeeper_root(id)` | `agents/{id}/gatekeeper/` |
| `agent_protocol_root(id)` | `agents/{id}/security_protocol/` |
| `agent_session_path(id)` | `.../gatekeeper/session.json` |
| `agent_verified_path(id)` | `.../gatekeeper/verified.json` |
| `agent_uploads_dir(id)` | `.../data/uploads/` |

- `DataBundle.rules`를 프로퍼티로 변경 — 프로토콜 수정 시 즉시 반영
- `Gatekeeper.classifier`를 프로퍼티로 변경 — 매 호출마다 최신 규칙

### 실행 스크립트

- `run.ps1` (Windows) — `.env` 로드 → `uv sync` → preflight → uvicorn
- `run.sh` (macOS/Linux) — 동일 흐름, 환경변수 우선순위 처리
- 둘 다 `PYTHONUTF8=1` 설정 (Windows cp949 인코딩 문제 해결)
- ASCII only — 인코딩 깨짐 방지

### preflight 검증

새 구조 반영:
- agent별 `data/` 합산으로 코퍼스 규모 계산
- `agents/{id}/gatekeeper/session.json` 존재 확인
- `labels.json` 경로를 새 구조로 갱신

현재 상태: **실패 0, 경고 2** (경고는 경계 시뮬레이션 고지 + CDK 미배포)

### 테스트

- 1014개 통과
- 새 구조에 맞춰 수정한 파일:
  - `conftest.py` — `data_root`/`full_data_root` 픽스처
  - `test_store_read.py`, `test_store_session.py` — 경로 갱신
  - `test_schemas.py`, `test_orchestrator.py`, `test_exaone.py` — 경로 갱신
  - `test_import_boundary.py` — `protocol_schemas`/`protocol_store` 레이어 등록

### Git

- `.gitignore`에 새 구조 런타임 데이터 추가
  - `agents/shared/mesh.db*`
  - `agents/*/gatekeeper/verified.json`
  - `agents/*/data/uploads/`
  - `agents/*/data/kb/`
- `origin/master` merge 충돌 4건 해결 (양쪽 변경 모두 보존)

---

## 🔄 구현 중 / 불완전

### M1. agent/ 폴더 추가

**요구사항 (§4.1)**:
> Agent 폴더에는 agent에 필요한 **skill, knowledge.md** 등이 존재한다

**현재**: `data/`, `gatekeeper/`, `security_protocol/` 3개만

작업:
- [ ] `agents/{id}/agent/` 디렉터리 생성
- [ ] `knowledge.md` — Agent가 아는 것의 요약
- [ ] `skills/` — Agent별 추가 도구 정의 (선택)
- [ ] `Config.agent_agent_root(id)`, `agent_knowledge_path(id)` 추가
- [ ] 시스템 프롬프트 조립에 knowledge.md 주입 (알고리즘 A5 참조)

### M2. 보안 프로토콜 파일명 정합

**요구사항 (§4.1)**:
> security_policy에는 해당 agent가 준수해야할 보안 규정이 정리되어있다.
> **lg_policy.md, team_policy.md, personal_policy.md**가 존재한다

**현재**:
- 전사: `agents/shared/security_protocol/company.yaml`
- 팀: `agents/shared/security_protocol/teams/{team}.yaml`
- 개인: `agents/{id}/security_protocol/protocol.yaml`

**차이점**:
1. 폴더명 — `security_policy` vs `security_protocol`
2. 파일명 — `lg_policy.md` vs `company.yaml`
3. 형식 — md vs yaml
4. 배치 — 전사/팀을 shared에 둠 vs 각 Agent 폴더에 3개 다 둠

작업:
- [ ] 기획서 명명 규칙 채택 여부 결정
- [ ] yaml → md 전환 필요성 검토 (md는 파싱이 어려움)
  - 절충안: md 헤더 + yaml frontmatter
- [ ] 전사/팀 정책을 각 Agent 폴더에도 복사할지 결정
  - 복사 시: 정책 변경 전파 문제
  - 현재 방식: 단일 출처 유지 (권장)

**권장**: 기능은 유지하고 폴더명만 `security_policy`로 통일

---

## ❌ 구현 필요

### M3. 데모 시나리오 정비

현재 코퍼스는 초기 3인 데모용. A-SPICE / CURS 맥락으로 갱신 필요.

작업:
- [ ] CURS 샘플 문서 작성 (`.curs` 확장자)
- [ ] SRS / SWE 문서 샘플
- [ ] 함정 문서 — 경로·헤더에 단서 없고 본문에만 기밀
- [ ] `labels.json`에 정답 등급 추가
- [ ] `questions.json`에 데모 질문 프리셋 갱신
- [ ] A-SPICE 프로세스 용어를 `vocab.json`에 추가 검토

### M4. 어휘 사전 확장

**현재**: 3개 task 스키마 (`constraint_conflict_check`, `technique_lookup`, `rationale_lookup`)
**문제**: A-SPICE 맥락의 질문이 스키마 매칭 실패 → passthrough로 우회

작업:
- [ ] A-SPICE 도메인 task 스키마 추가 검토
  - `requirement_traceability_check`
  - `test_coverage_lookup`
  - `design_review_status`
- [ ] 각 스키마의 슬롯 정의 (enum/int/bool만)
- [ ] `_intentionally_absent` 목록 갱신
- [ ] 3인 합의 절차 (Day 1 동결 규칙)

### M5. 문서 갱신

- [ ] `README.md` — 새 구조 반영 (현재 `data/` 구조로 설명됨)
- [ ] `docs/설계자료.md` — 허브 구조 다이어그램 추가
- [ ] `docs/사용설명서.md` — 실행 방법 갱신 (`run.ps1`/`run.sh`)
- [ ] `aidlc-docs/aidlc-state.md` — 진행 상황 갱신

### M6. lint 규칙 갱신

**현재**: `scripts/lint_web.py`가 `role="tablist"` 존재를 요구
**문제**: UI 리디자인으로 탭 구조 제거됨

작업:
- [ ] `lint_web.py` A11Y 규칙 갱신
- [ ] 새 UI 구조에 맞는 검사 추가
  - 조직도 카드 접근성
  - 모달 포커스 트랩
- [ ] `test_lint_web.py` 통과 확인

### M7. AWS 자격증명 관리

**현재**: `.env`에 STS 임시 자격증명 직접 기입 (만료됨)

작업:
- [ ] 만료 감지 + 사용자 안내 메시지
- [ ] `AGENT_TRANSPORT=broker` 전환 가이드 (Lambda 실행 역할 사용 → 만료 없음)
- [ ] 또는 Bedrock API Key 방식 (`AWS_BEARER_TOKEN_BEDROCK`)
- [ ] preflight에서 만료 임박 경고

### M8. CDK 배포 (선택)

**현재**: `direct` 모드로 데모 가능, CDK 미배포

작업:
- [ ] `make bootstrap` 실행
- [ ] `make deploy` — Lambda + API Gateway + DynamoDB
- [ ] `.env`에 `BROKER_API_URL`/`BROKER_API_KEY` 기입
- [ ] `AGENT_TRANSPORT=broker` 전환 테스트
- [ ] 브로커 재검증 동작 확인 (2겹 검증)

**우선순위 낮음** — direct 모드로 데모 충분

### M9. 오프라인 데모 픽스처 재녹화

구조 변경으로 프롬프트 내용이 바뀌면 픽스처 키가 달라짐.

작업:
- [ ] `make record-fixtures` 실행
- [ ] `EXAONE_MODE=mock AGENT_TRANSPORT=mock` 으로 전체 흐름 테스트
- [ ] 누락된 픽스처 확인 및 보충

---

## 우선순위 요약

### P0 — 데모 필수
- M3 데모 시나리오 정비 (A-SPICE/CURS 맥락)
- M7 AWS 자격증명 (현재 만료 상태)

### P1 — 완성도
- M1 agent/ 폴더 + knowledge.md
- M2 보안 프로토콜 파일명 정합
- M4 어휘 사전 확장
- M6 lint 규칙 갱신

### P2 — 선택
- M5 문서 갱신
- M8 CDK 배포
- M9 픽스처 재녹화

---

## 알려진 이슈

| 이슈 | 영향 | 대응 |
|---|---|---|
| `test_db_file_is_owner_only` 실패 (Windows 파일 권한) | 테스트 | Windows에서는 skip 처리 |
| `test_lint_web` 실패 (tablist 없음) | 테스트 | M6 작업으로 해결 |
| AWS STS 자격증명 만료 | 데모 중단 | M7 작업으로 해결 |
| `data/` 구 폴더가 남아 있음 | 혼란 | 검증 후 삭제 |
| `MESH_DATA_ROOT` 절대 경로 경고 | 이식성 | 상대 경로 유지 (`./agents`) |

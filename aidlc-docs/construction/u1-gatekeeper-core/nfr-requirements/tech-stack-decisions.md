# U1 — Tech Stack Decisions

모든 버전은 **정확히 고정**한다 (SECURITY-10). `uv.lock`을 커밋한다.

---

## 1. 런타임

| 항목 | 선택 | 근거 |
|---|---|---|
| **언어** | Python 3.12 | 설계 §6 지정. 현재 컴퓨터의 시스템 Python은 3.9.12로 **부족** (pydantic v2 성능·`StrEnum`·`type` 문법) |
| **버전 관리** | `uv` (이미 설치 확인) + `.python-version` | 시스템 Python·anaconda에 의존하지 않는다. 다른 컴퓨터 이식성 (NFR-PO-02) |
| **패키지** | `uv sync` + `uv.lock` 커밋 | 의존성 고정 (SECURITY-10) |

**anaconda를 쓰지 않는 이유**: 현재 컴퓨터의 `uv run --with boto3`가 anaconda의 구버전 botocore를 집어 `bedrock` 서비스를 인식하지 못했다 (실측). `--isolated --no-project`가 필요했다. 프로젝트 venv를 명시적으로 분리해 이 문제를 원천 제거한다.

---

## 2. 의존성

```toml
[project]
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi==0.115.6",
  "uvicorn[standard]==0.34.0",
  "pydantic==2.10.4",
  "httpx==0.28.1",
  "boto3==1.35.90",
  "pyyaml==6.0.2",
]

[dependency-groups]
dev = [
  "pytest==8.3.4",
  "pytest-asyncio==0.25.0",
  "hypothesis==6.123.2",
  "pip-audit==2.7.3",
  "ruff==0.8.6",
]
```

| 패키지 | 왜 이것인가 | 대안 기각 이유 |
|---|---|---|
| `fastapi` | 설계 §6 지정. pydantic 통합으로 입력 검증(NFR-S-05)이 선언적 | Flask — pydantic 통합·async 지원 약함 |
| `pydantic` v2 | 타입으로 불변식을 강제한다 (`tier` 단일값, `frozen=True`) | dataclass — 검증 로직을 손으로 써야 함 |
| `httpx` | async HTTP. EXAONE·브로커 호출 | `requests` — async 미지원 |
| `boto3` | Bedrock `direct` 모드. **1.35.90 이상 필수** (구버전은 `bedrock` 서비스를 모른다 — 실측 확인) | aiobotocore — 의존성 증가 대비 이득 없음 |
| `pyyaml` | `agents.yaml` | JSON — 사람이 편집하는 설정에 주석이 필요 |
| `hypothesis` | PBT-09. 성숙한 shrinking, 시드 재현 | 대안 없음 (파이썬 표준) |
| `pip-audit` | 취약점 스캔 (SECURITY-10) | `safety` — 상용 라이선스 이슈 |
| `ruff` | 린트 + 포맷 단일 도구 | black+flake8+isort 3개 → 5일 일정에 과함 |

**의존성이 6개(런타임)뿐이다.** 벡터 DB·임베딩 라이브러리·ORM·프론트엔드 빌드 도구가 전부 없다. `scenarios.md` §0에서 벡터 검색을 버린 결정의 실질적 이득이다.

---

## 3. EXAONE 클라이언트

| 항목 | 값 | 근거 |
|---|---|---|
| 프로토콜 | OpenAI 호환 `/chat/completions` | 실측 확인. 전용 SDK 불필요 |
| 라이브러리 | `httpx` 직접 호출 | `openai` SDK를 추가할 이유가 없다. 엔드포인트 교체(사내망 전환) 시 오히려 유연 |
| `temperature` | `0` 고정 | 결정성. 실측에서 3회 반복 동일 결과 |
| `chat_template_kwargs` | `{"enable_thinking": false}` | **원문 유출 채널 차단** + 지연 감소 (FR-14) |
| `response_format` | `{"type": "json_object"}` | 실측 지원 확인. 파싱 실패율 감소 |
| 응답 후처리 | `reasoning`, `reasoning_content` **삭제** | 사고 과정이 원문을 인용할 수 있다 |
| 타임아웃 | 10초 | 실측 0.8~1.0s의 10배 여유 |
| 재시도 | JSON 파싱 실패 시 2회 | FR-46 |

```python
# 고정 요청 형태
{
  "model": os.environ["EXAONE_MODEL_ID"],       # depe675tjc2rcpo
  "temperature": 0,
  "max_tokens": 800,
  "chat_template_kwargs": {"enable_thinking": False},
  "response_format": {"type": "json_object"},
  "messages": [...]
}
```

---

## 4. Claude (Bedrock) 클라이언트

| 항목 | 값 | 근거 |
|---|---|---|
| **모델 ID (기본)** | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | 실측 호출 성공 (2.17s). **설계 문서의 `claude-sonnet-5`는 이 계정에서 AccessDenied** |
| 대안 | `us.anthropic.claude-sonnet-4-6` (1.28s) | 더 빠르다. A/B 비교용 |
| 초안 생성 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` (0.92s) | 에스컬레이션 초안은 저비용 모델로 |
| API | `bedrock-runtime.Converse` | `InvokeModel`보다 모델 간 이식성이 좋다 |
| 추론 프로파일 | **`us.` 접두사 필수** | 모든 Claude가 `inferenceTypesSupported: ["INFERENCE_PROFILE"]`. `anthropic.` 그대로는 호출 불가 |
| 리전 | `us-east-1` | 계정 정책이 다른 리전을 Deny |
| 타임아웃 | 25초 (`read_timeout`) | 전체 30초 예산 안 |
| 자격증명 | **`broker` 모드에서 노트북에 불필요** | Lambda 실행 역할 사용. STS 만료 회피 |

모델 ID는 **전부 환경변수**로 둔다 (`AGENT_MODEL_ID`, `DRAFT_MODEL_ID`). 계정 접근 권한이 바뀌거나 다른 워크샵 계정을 쓸 때 코드 변경 없이 교체한다.

---

## 5. 저장

| 항목 | 선택 | 근거 |
|---|---|---|
| 감사 로그 · 인박스 | **SQLite** (표준 라이브러리) | 설계 §6 지정. 의존성 0. 파일 권한 `0600` (NFR-S-01) |
| 세션 · 승인 QA · 코퍼스 | **JSON / 마크다운 파일** | 사람이 편집하고 git으로 diff를 본다. 데모 중 수동 갱신 (FR-21) |
| 매핑 테이블 | **메모리 전용** | 영속화 금지 (BR-G-09) |
| envelope 캐시 | 메모리 dict + TTL 5분 | `prepare`/`send` 2단계 사이 보관 |
| ORM | **없음.** `sqlite3` + 파라미터화 쿼리 | 테이블 3개에 ORM은 과함. 파라미터화로 인젝션 방지 (NFR-S-05) |

**SQLite 스키마 3개 테이블**
```sql
CREATE TABLE audit (            -- 경계를 넘은 것. 원본 증거
  record_id TEXT PRIMARY KEY, at TEXT, actor TEXT, target_entity_id TEXT,
  model_id TEXT, transport TEXT, trusted_zone_llm_base_url TEXT,
  tier TEXT, representation TEXT, payload TEXT, payload_sha256 TEXT,
  size_bytes INTEGER, validation_summary TEXT, approved_by TEXT, envelope_id TEXT);

CREATE TABLE local_queries (    -- 신뢰 구역 내 처리. 감사 로그 탭에 표시 안 함
  query_id TEXT PRIMARY KEY, at TEXT, actor TEXT, tier TEXT, reason TEXT);

CREATE TABLE inbox (
  item_id TEXT PRIMARY KEY, at TEXT, owner_entity_id TEXT, asker TEXT,
  summary TEXT, evidence TEXT, draft TEXT, status TEXT, resolved_at TEXT,
  resolution TEXT, redirect_to TEXT);
```

`audit`와 `local_queries`를 분리한 것이 의도적이다. 감사 로그 탭에는 `audit`만 보이므로 "레코드가 없다"가 명확한 증거가 된다 (BR-A-03).

---

## 6. 테스트

| 항목 | 선택 | 근거 |
|---|---|---|
| 러너 | `pytest` + `pytest-asyncio` | 표준 |
| PBT | **`hypothesis`** | PBT-09. shrinking + 시드 재현 |
| PBT 설정 | `derandomize=False`, `print_blob=True`, `max_examples=200` | 시드 로깅 + 재현 (PBT-08) |
| 생성기 | `tests/generators.py` 중앙화 | PBT-07. 원시 타입 생성기 금지 |
| 커버리지 | 목표 없음 | 5일 일정. 대신 **불변식 커버리지**를 본다 (PB-1~PB-10 전부 통과) |

```python
# tests/conftest.py
from hypothesis import settings, Verbosity
settings.register_profile("ci", max_examples=200, print_blob=True, derandomize=False)
settings.register_profile("dev", max_examples=50, print_blob=True)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
```

`print_blob=True`가 실패 시 재현 blob을 출력한다 (PBT-08 시드 재현).

---

## 7. 도구

| 항목 | 선택 | 근거 |
|---|---|---|
| 태스크 러너 | `Makefile` | 어느 컴퓨터에나 있다. `just`·`task`는 추가 설치 필요 |
| 린트/포맷 | `ruff` | 단일 도구 |
| CDK CLI | **`npx aws-cdk@2`** (전역 설치 안 함) | 이식성 (NFR-PO-03). 현재 컴퓨터에 `cdk` 없음 |
| AWS CLI | **의존하지 않는다** | 현재 컴퓨터의 CLI가 `bedrock` 서비스를 모르는 구버전 (실측). boto3로 대체 |

**`Makefile` 타깃**
```makefile
setup      uv sync + .env 확인 + data 디렉터리 생성
preflight  scripts/preflight.py — EXAONE·Bedrock·리전·CDK 부트스트랩 확인
run        uvicorn 127.0.0.1:8080
test       pytest tests/unit tests/property
eval       pytest tests/eval  (분류 정확도 + 유출 전수 검사)
eval-classify  Day 2 게이트 단독 실행
audit      pip-audit
lint       ruff check + ruff format --check
deploy     npx aws-cdk@2 deploy --all (infra/)
destroy    npx aws-cdk@2 destroy --all
demo       CLI 3막 재생 (화면 없이도 시연 가능)
```

---

## 8. 설정 (환경변수)

```bash
# .env.example  — 실제 값은 커밋하지 않는다
MESH_DATA_ROOT=./data
MESH_BIND_HOST=127.0.0.1          # ⚠️ 0.0.0.0 금지
MESH_BIND_PORT=8080

# 신뢰 구역 LLM (경계의 위치를 정하는 값. 감사 로그에 기록된다)
TRUSTED_ZONE_LLM_BASE_URL=https://api.friendli.ai/dedicated/v1
EXAONE_MODEL_ID=depe675tjc2rcpo
FRIENDLI_TOKEN=                   # ⚠️ opencode.jsonc 에서 이전. 커밋 금지
EXAONE_MODE=live                  # live | mock
EXAONE_TIMEOUT_SECONDS=10

# Agent (경계 밖)
AGENT_TRANSPORT=direct            # broker | direct | mock
AGENT_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
DRAFT_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
AWS_REGION=us-east-1
BROKER_API_URL=                   # broker 모드에서만
BROKER_API_KEY=                   # ⚠️ 커밋 금지
AGENT_TIMEOUT_SECONDS=25

# 동작 파라미터
SESSION_STALE_MINUTES=15
MAX_TARGETS=2
TOTAL_TIMEOUT_SECONDS=30
MAX_PAYLOAD_BYTES=2048
NGRAM_SIZE=5
CONFIDENCE_AUTO=0.75
CONFIDENCE_ESCALATE=0.45
STALE_CONFIDENCE_FACTOR=0.8
```

`config.py`가 시작 시 검증한다:
- `MESH_BIND_HOST`가 `127.0.0.1`/`localhost`가 아니면 **경고 + 확인 요구** (실수로 노출 방지)
- `MESH_DATA_ROOT`가 절대 경로 하드코딩이면 경고
- `EXAONE_MODE=live`인데 `FRIENDLI_TOKEN`이 없으면 **시작 실패**
- `AGENT_TRANSPORT=broker`인데 `BROKER_API_URL`/`BROKER_API_KEY`가 없으면 **시작 실패**

---

## 9. 이 유닛이 도입하지 않은 것

| 안 쓰는 것 | 이유 |
|---|---|
| 벡터 DB (FAISS·Chroma·pgvector) | 지목을 사람이 한다. 검색이 필요 없다 |
| 임베딩 모델 | 같은 이유 |
| `numpy` | 코사인 유사도를 쓰지 않는다 (설계 §4.7 폐기) |
| ORM (SQLAlchemy) | 테이블 3개 |
| Celery·Redis | 단일 프로세스, 30초 이내 동기 처리 |
| Docker | 로컬 실행. 컨테이너가 이식성을 오히려 복잡하게 한다 (`uv`로 충분) |
| `openai` SDK | `httpx` 직접 호출이 엔드포인트 교체에 더 유연 |
| 프론트엔드 빌드 도구 | 빌드 파이프라인이 5일을 먹는다 |

**의존성 6개 유지가 목표다.** 하나 추가할 때마다 다른 컴퓨터에서의 실패 확률이 올라간다.

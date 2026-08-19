# Shared Infrastructure

전 유닛이 공유하는 것. 여기 있는 것은 U1이 Day 1에 만들고 나머지가 소비한다.

---

## 1. 저장소 부트스트랩 (U1 Day 1, 순서 고정) 🔴

**`git init` 전에 `.gitignore`를 만든다.** 워크스페이스에 자격증명이 평문으로 있다 (SECURITY-12).

```
.gitignore                    <- 1번. 다른 어떤 것보다 먼저
.env.example
pyproject.toml
.python-version
uv.lock
Makefile
README.md
scripts/bootstrap.sh
scripts/preflight.py
```

### `.gitignore`

```gitignore
# 자격증명 — 워크스페이스에 평문으로 존재
.kiro/.env
.kiro/opencode.jsonc
.env
*.pem
*.key

# Python
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.hypothesis/
.ruff_cache/

# 런타임 데이터
data/mesh.db
data/mesh.db-*
data/verified/*.json
!data/verified/.gitkeep
data/sessions/local-*.json

# CDK
infra/.venv/
infra/cdk.out/
infra/lambda/agent_broker/_bundled/

# OS / 에디터
.DS_Store
*.swp
```

**검증**: `git ls-files | grep -E '\.env|opencode'` 결과가 비어 있어야 한다.

**해커톤 종료 후**: Friendli API 키 폐기·재발급 (README에 명시).

### `.env.example`

실제 값 없이 키 이름과 기본값만. `config.py`가 시작 시 필수 값을 검증한다.
전체 목록은 `u1-gatekeeper-core/nfr-requirements/tech-stack-decisions.md` §8 참조.

---

## 2. 디렉터리 구조

`aidlc-docs/inception/application-design/unit-of-work.md` §8 참조.

**핵심 규칙 2개**
- 애플리케이션 코드는 **워크스페이스 루트** (`src/`, `data/`, `config/`, `tests/`, `infra/`)
- `aidlc-docs/`에는 **마크다운만**

---

## 3. `Makefile`

```makefile
.PHONY: setup setup-infra preflight run test eval eval-classify lint audit \
        bundle-lambda bootstrap deploy destroy demo record-fixtures

PY := uv run

setup:                      ## 앱 의존성 + 데이터 디렉터리
	uv sync
	@test -f .env || (cp .env.example .env && echo "created .env — fill in tokens")
	mkdir -p data/verified data/fixtures/exaone data/fixtures/agent data/fixtures/api
	@touch data/verified/.gitkeep

setup-infra:                ## CDK 의존성 (별도 venv)
	cd infra && uv venv && uv pip install -r requirements.txt

preflight:                  ## 환경 검증 — 다른 컴퓨터에서 가장 먼저
	$(PY) python scripts/preflight.py

run:                        ## 로컬 앱 (127.0.0.1:8080)
	$(PY) uvicorn mesh.main:app --host $${MESH_BIND_HOST:-127.0.0.1} --port $${MESH_BIND_PORT:-8080}

test:                       ## 단위 + 속성 + 인프라 어서션
	$(PY) pytest tests/unit tests/property tests/infra -q

eval:                       ## 전체 평가 (분류 + 시나리오 + 유출 전수)
	$(PY) pytest tests/eval -q

eval-classify:              ## Day 2 게이트 단독
	$(PY) pytest tests/eval/test_classification.py -q

lint:
	$(PY) ruff check src tests scripts infra
	$(PY) ruff format --check src tests scripts infra
	@bash scripts/lint-web.sh          # BR-U-05/12 grep 검사

audit:                      ## 의존성 취약점 (SECURITY-10)
	$(PY) pip-audit

bundle-lambda:              ## U1 코드를 Lambda 번들에 복사
	rm -rf infra/lambda/agent_broker/_bundled
	mkdir -p infra/lambda/agent_broker/_bundled
	cp src/mesh/validator.py src/mesh/schemas.py infra/lambda/agent_broker/_bundled/
	cp data/vocab.json infra/lambda/agent_broker/_bundled/

bootstrap:                  ## CDK 부트스트랩 (Day 0, 한 번)
	cd infra && npx aws-cdk@2 bootstrap aws://891401657794/us-east-1

deploy: bundle-lambda       ## 클라우드 배포
	cd infra && npx aws-cdk@2 deploy --all --require-approval never

destroy:
	cd infra && npx aws-cdk@2 destroy --all

demo:                       ## CLI 3막 재생 (화면 없이도 시연 가능)
	$(PY) python scripts/demo.py

record-fixtures:            ## live 로 3막 실행하며 목업 픽스처 녹화
	MESH_RECORD_FIXTURES=1 $(PY) python scripts/demo.py
```

**`deploy`가 `bundle-lambda`에 의존하는 것이 중요하다.** `vocab.json`을 고치고 배포를 잊으면 로컬과 클라우드의 어휘 사전이 갈린다.

---

## 4. `pyproject.toml`

```toml
[project]
name = "delegate-agent-mesh"
version = "0.1.0"
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
  "aws-cdk-lib==2.173.1",     # tests/infra 어서션용
  "constructs==10.4.2",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src", "infra"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E","F","I","B","UP","S","ASYNC"]
ignore = ["S101"]              # pytest 의 assert 허용

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S"]
```

`boto3==1.35.90` 하한이 중요하다. 구버전은 `bedrock` 서비스를 모른다 (실측 확인).

`.python-version` 파일에 `3.12`를 넣어 `uv`가 자동으로 고정한다.

---

## 5. `config.py` — 공유 설정 로더

```python
@dataclass(frozen=True)
class Config:
    data_root: Path
    bind_host: str
    bind_port: int
    trusted_zone_llm_base_url: str
    exaone_model_id: str
    friendli_token: str | None
    exaone_mode: Literal["live", "mock"]
    exaone_timeout: int
    agent_transport: Transport
    agent_model_id: str
    draft_model_id: str
    aws_region: str
    broker_api_url: str | None
    broker_api_key: str | None
    agent_timeout: int
    session_stale_minutes: int
    max_targets: int
    total_timeout: int
    max_payload_bytes: int
    ngram_size: int
    ngram_size_internal: int
    confidence_auto: float
    confidence_escalate: float
    stale_confidence_factor: float
    demo_now: datetime | None
    record_fixtures: bool

    @classmethod
    def load(cls) -> "Config": ...
```

### 시작 시 검증 (fail fast)

| 조건 | 결과 |
|---|---|
| `exaone_mode == "live"` && `friendli_token` 없음 | **시작 실패** |
| `agent_transport == "broker"` && `broker_api_url`/`broker_api_key` 없음 | **시작 실패** |
| `bind_host`가 `127.0.0.1`/`localhost` 아님 | **경고 + 명시적 확인 요구** |
| `data_root`가 절대 경로 하드코딩 | 경고 |
| `data_root` 존재하지 않음 | 시작 실패 |
| `vocab.json`/`labels.json`/`agents.yaml` 로드 실패 | 시작 실패 |

### `safe_resolve()` — 경로 가드 (NFR-S-05)

```python
def safe_resolve(rel: str, root: Path) -> Path:
    s = rel.replace("${MESH_DATA_ROOT}/", "").replace("${MESH_DATA_ROOT}", "")
    p = (root / s).resolve()
    if not p.is_relative_to(root.resolve()):
        raise PathEscapeError(rel)
    return p
```

**전 유닛이 파일 경로를 다룰 때 이 함수만 쓴다.** `open()` 직접 호출을 금지하고 lint로 검사한다.

---

## 6. 로깅 — 공유 (NFR-S-03)

```python
FORBIDDEN_LOG_KEYS = frozenset({
    "text", "chunk_text", "raw_document", "payload_text",
    "mapping", "table",
    "reasoning", "reasoning_content",
    "FRIENDLI_TOKEN", "BROKER_API_KEY", "friendli_token", "broker_api_key",
    "aws_secret_access_key", "aws_session_token", "authorization", "x-api-key",
})

class RedactingFilter(logging.Filter):
    """금지 키를 <redacted> 로 치환. 개발자가 실수해도 원문이 로그에 남지 않는다."""
```

**JSON 형식**
```json
{"at":"2026-08-19T14:33:41Z","level":"INFO","correlation_id":"req_01J...",
 "component":"gatekeeper","message":"payload validated","tier":"secret",
 "validation":"6/6","size_bytes":1124}
```

`correlation_id`는 `contextvars`로 전파한다 (U3 `main.py`가 요청 시작 시 설정).

---

## 7. 예외 계층 — 공유

```python
class MeshError(Exception): ...

class GatekeeperError(MeshError): ...            # 전제조건 위반
class ExtractionFailed(GatekeeperError): ...     # 필수 슬롯 미충족 -> 폴백
class ValidationBlocked(GatekeeperError): ...    # 6단계 실패 -> 폴백
class ExaoneUnavailable(MeshError): ...          # 타임아웃/오류 -> SECRET 간주
class BrokerError(MeshError): ...                # 브로커/Bedrock 오류 -> 폴백
class FixtureMissing(MeshError): ...             # 목업 키 없음 -> 명시적 실패
class PathEscapeError(MeshError): ...            # 경로 탈출
class ScopeViolationError(MeshError): ...        # knowledge_scope 위반
```

**모든 예외의 처리 결과가 `Tier.SECRET` 또는 폴백이다** (BR-G-01 fail closed).
예외 하나를 추가할 때 "이게 어느 안전한 상태로 귀결되는가"를 함께 정의한다.

---

## 8. Day 1 동결 계약 🔴

Day 1 종료 시점에 커밋돼 있어야 하는 것. **스텁이어도 된다. 시그니처가 중요하다.**

| 산출물 | 소비자 | 없으면 |
|---|---|---|
| `src/mesh/schemas.py` | U2, U3, U4, U5 | 타입 없이 코딩 불가 |
| `data/vocab.json` | U1, U5, U6 | 추출기·재검증 불가 |
| `src/mesh/gatekeeper.py` (스텁) | U3 | Day 3에 막힌다 |
| `src/mesh/config.py` | 전체 | 경로·설정 접근 불가 |
| `config/agents.yaml` (스키마) | U2, U4 | 에이전트 정의 불가 |
| API 계약 8개 (`services.md` §1) | U4 | UI 선행 개발 불가 |
| `data/fixtures/api/*.json` | U4 | UI 선행 개발 불가 |
| `.gitignore` | 전체 | **자격증명이 커밋된다** |
| `Makefile`, `pyproject.toml`, `.env.example` | 전체 | 온보딩 불가 |

**이후 변경은 3인 합의로만** (NFR-M-02).

변경이 필요하면: (1) 슬랙/구두 합의 (2) `schemas.py` 상단 CHANGELOG 주석에 기록 (3) 영향받는 유닛 소유자에게 통보.

---

## 9. 유닛 간 공유 모듈

| 모듈 | 소유 | 공유 대상 |
|---|---|---|
| `config.py` | U1 | 전체 |
| `schemas.py` | U1 | 전체 + U5 Lambda 번들 |
| `validator.py` | U1 | U5 Lambda 번들 |
| `gatekeeper.py` | U1 | U3 |
| `store.py` | U2 | U3 |
| `tests/generators.py` | U1 | U2, U3 PBT |
| `data/vocab.json` | U6 | U1, U5 Lambda 번들 |
| `data/fixtures/api/` | U6 + U3 | U4 |

**`validator.py`가 두 곳에서 돌기 때문에 순수 함수여야 한다.** I/O·전역 상태·설정 참조가 있으면 Lambda에서 깨진다.

---

## 10. import 경계 규칙 (테스트로 강제) 🔴

```
경계 밖 클라이언트(mesh.llm.broker, boto3)를 import 할 수 있는 모듈:
  - mesh.gatekeeper
  - mesh.audit          (미러링 전용)
  - mesh.llm.*           (구현체)
그 외 전부 금지.

Chunk.text (원문) 를 받을 수 있는 모듈:
  - mesh.classifier · mesh.extractor · mesh.pseudonymizer
  - mesh.validator      (5-gram 대조. 보지만 내보내지 않는다)
  - mesh.store          (읽기)
그 외는 PayloadEnvelope 만 받는다.

Mapping 을 받을 수 있는 모듈:
  - mesh.rehydrator · mesh.gatekeeper
Mapping 을 직렬화·저장하는 코드: 없음
```

`tests/unit/test_import_boundary.py`가 `ast`로 전 모듈을 파싱해 위반을 실패시킨다.

**리뷰 매너에 의존하지 않는다.** 5일 동안 3명이 작업하면 반드시 누군가 실수한다.

---

## 11. 소유권과 충돌 회피

| 파일 | 소유 | 다른 사람이 수정할 때 |
|---|---|---|
| `schemas.py`, `vocab.json` | A / C | **합의 필요** (Day 1 동결) |
| `config.py`, `Makefile`, `pyproject.toml` | A | 알리고 수정 |
| `gatekeeper.py`, `classifier.py`, `extractor.py`, `validator.py`, `pseudonymizer.py`, `rehydrator.py`, `audit.py`, `llm/*` | A | 수정 금지 |
| `store.py`, `agents.yaml`, `sessions/*` | B | 수정 금지 |
| `agent.py`, `orchestrator.py`, `inbox.py`, `main.py` | B | 수정 금지 |
| `web/*` | C | 수정 금지 |
| `corpus/**`, `labels.json`, `banned.json`, `questions.json` | C | 수정 금지 |
| `infra/**` | A | 수정 금지 |
| `tests/generators.py` | A | 추가는 자유, 수정은 알리고 |

**파일 단위 소유권으로 git 충돌을 원천 차단한다.** 유닛을 파일 소유권으로 정의한 이유가 이것이다.

---

## 12. 다른 컴퓨터로 옮길 때 (NFR-PO)

```bash
git clone <repo> && cd prompthon
make setup                      # uv sync + .env 생성 + 디렉터리
# .env 에 FRIENDLI_TOKEN 기입
# broker 모드면 BROKER_API_URL, BROKER_API_KEY 기입
# direct 모드면 AWS 자격증명 (source .kiro/.env)
make preflight                  # 환경 검증 — 사람이 읽을 진단
make test                       # 단위 + 속성 + 인프라
make run                        # http://127.0.0.1:8080
```

**네트워크가 없거나 사내망을 못 붙이는 경우**
```bash
EXAONE_MODE=mock AGENT_MODE=mock make run
```
녹화된 픽스처로 3막 전체가 돈다. 화면에 목업 모드가 표시된다.

**필요한 것**: `uv`, Node(npx, CDK 배포 시에만), git. **`aws` CLI는 필요 없다.**

**README에 이 절차를 그대로 넣는다.** 다른 컴퓨터에서 반나절을 날리지 않게.

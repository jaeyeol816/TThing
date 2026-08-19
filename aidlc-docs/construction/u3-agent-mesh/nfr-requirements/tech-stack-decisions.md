# U3 — Tech Stack Decisions

**새로운 의존성을 추가하지 않는다.** U1의 `pyproject.toml`을 그대로 쓴다.

---

## 1. 사용하는 것 (전부 U1에서 옴)

| 항목 | 선택 | U3에서의 용도 |
|---|---|---|
| `fastapi==0.115.6` | | 8개 엔드포인트 + 미들웨어 + 정적 서빙 |
| `uvicorn[standard]==0.34.0` | | ASGI 서버, `127.0.0.1:8080` |
| `pydantic==2.10.4` | | 요청/응답 모델, 입력 검증 (NFR-S-05) |
| `sqlite3` (표준) | | `inbox` 테이블 |
| `asyncio` (표준) | | 2명 병렬 호출, 30초 상한 |
| `contextvars` (표준) | | `correlation_id` 전파 |
| `pytest` + `pytest-asyncio` | | 단위 테스트 |
| `hypothesis` | | PB-O1~PB-O6 |

---

## 2. 검토했으나 기각한 것

| 후보 | 용도 | 기각 이유 |
|---|---|---|
| `starlette-csp` / `secure` | 보안 헤더 | 미들웨어 10줄로 끝난다. 의존성 하나 추가할 이유가 없다 |
| `slowapi` | 레이트 리밋 | localhost 전용. 비용 방어는 `daily_limit` + 세마포어로 충분. 실제 레이트 리밋은 U5 API Gateway |
| `cachetools` | `envelope` TTL 캐시 | `dict` + `time.monotonic()` 비교로 30줄. `sweep()`을 직접 제어하는 게 명확하다 |
| `tenacity` | 재시도 | U1 `exaone.py`에 이미 손으로 구현. 정책이 특이하다 (타임아웃은 재시도 안 함) |
| SQLAlchemy | ORM | 테이블 3개. 파라미터화 쿼리로 충분 |
| `celery` / `arq` | 비동기 작업 | 30초 이내 동기 처리. 큐를 도입하면 매핑 테이블 수명 관리가 복잡해진다 |
| `redis` | `envelope` 캐시 공유 | **매핑을 프로세스 밖으로 내보내지 않는 것이 BR-G-09다.** 단일 프로세스라 공유가 필요 없다 |
| `python-jose` / `authlib` | 인증 | 사용자 인증이 범위 밖 |
| WebSocket | 실시간 인박스 알림 | 폴링으로 충분. 실시간 알림 연동은 범위 밖 (요구사항 §7) |

**Redis 기각이 특히 의미 있다.** 캐시를 외부화하면 매핑 테이블이 프로세스를 벗어나고, 그러면 "메모리에만 존재하고 응답 후 폐기"라는 보안 속성이 깨진다. 편의를 위해 보안 모델을 양보하지 않는다.

---

## 3. FastAPI 구성

```python
app = FastAPI(
    title="Delegate Agent Mesh",
    docs_url=None,           # ⚠️ /docs 비활성 (SECURITY-09 최소 설치)
    redoc_url=None,          # ⚠️ /redoc 비활성
    openapi_url=None,        # ⚠️ 스키마 노출 안 함
)
```

**`/docs`를 끄는 이유**: 개발 편의 도구가 배포에 남는 것이 SECURITY-09가 금지하는 "샘플/문서 엔드포인트"다. 개발 중에는 `MESH_DEV=1`일 때만 켠다.

**미들웨어 순서** (바깥 → 안쪽)
```
1. CorrelationIdMiddleware     요청 ID 생성 + contextvars
2. SecurityHeadersMiddleware   4개 헤더 (BR-M-03)
3. ConcurrencyLimitMiddleware  세마포어 5
4. (라우터)
```

**정적 파일**: `StaticFiles`를 디렉터리에 붙이지 않고 3개 파일을 명시 라우트로 매핑한다.
```python
@app.get("/", response_class=HTMLResponse)
@app.get("/app.js", response_class=Response)      # media_type="text/javascript"
@app.get("/style.css", response_class=Response)   # media_type="text/css"
```
디렉터리 리스팅을 원천 차단한다 (NFR-S-09).

**CORS 미들웨어를 추가하지 않는다.** 동일 출처만 허용하는 것이 기본값이고, 와일드카드 오리진 사고를 원천 제거한다 (BR-M-04).

---

## 4. 모델 선택

| 용도 | 모델 | 실측 지연 | 근거 |
|---|---|---|---|
| Agent 답변 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | 2.17s | 기본값. 추론 품질 |
| 에스컬레이션 초안 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | 0.92s | 저비용·저지연. 초안은 사람이 검토한다 |
| 품질 비교 (선택) | `us.anthropic.claude-sonnet-4-6` | 1.28s | 더 빠르다. A/B용 |

전부 `AGENT_MODEL_ID` / `DRAFT_MODEL_ID` 환경변수. 설계 문서의 `claude-sonnet-5`는 이 계정에서 `AccessDeniedException`이므로 쓰지 않는다 (`preflight-findings.md` §2).

**추론 프로파일 접두사 `us.`가 필수다.** 모든 Claude가 `inferenceTypesSupported: ["INFERENCE_PROFILE"]`이라 `anthropic.` 그대로는 호출되지 않는다.

---

## 5. SQLite `inbox` 테이블

```sql
CREATE TABLE IF NOT EXISTS inbox (
  item_id           TEXT PRIMARY KEY,
  at                TEXT NOT NULL,
  owner_entity_id   TEXT NOT NULL,
  asker             TEXT NOT NULL,
  thread_id         TEXT NOT NULL,
  question_summary  TEXT NOT NULL,
  draft_json        TEXT NOT NULL,
  citations_json    TEXT NOT NULL,
  tier              TEXT NOT NULL,
  status            TEXT NOT NULL,
  resolved_at       TEXT,
  resolution_text   TEXT,
  redirect_to       TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_owner  ON inbox(owner_entity_id, status);
CREATE INDEX IF NOT EXISTS idx_inbox_thread ON inbox(thread_id);
```

**`UPDATE`는 `status`·`resolved_at`·`resolution_text`·`redirect_to`에만 허용한다.**
`question_summary`·`draft_json`은 수정하지 않는다 — 담당자가 무엇을 보고 승인했는지가 감사 흔적이다 (SECURITY-13).

`idx_inbox_thread`가 2명 지목 시 같은 스레드 조회에 쓰인다 (BR-I-04).

---

## 6. 프런트엔드 계약 (U4가 소비)

U4는 프레임워크 없이 `fetch`로 이 8개를 호출한다.
Day 1에 이 계약을 동결하고, C는 목업 JSON으로 UI를 선행 개발한다.

```
GET  /api/agents                    -> AgentCard[]
POST /api/ask/prepare               -> PrepareResult
POST /api/ask/send                  -> AskResult
GET  /api/inbox?owner=person:kim    -> InboxItem[]
POST /api/inbox/{id}/resolve        -> InboxItem
GET  /api/audit?q=REQ-4412          -> AuditRecord[]
GET  /api/health                    -> HealthStatus
GET  /                              -> index.html
```

**목업 JSON 위치**: `data/fixtures/api/*.json`. U4가 `MESH_UI_MOCK=1`일 때 이걸 읽는다.
C가 U3 완성을 기다리지 않고 Day 4에 바로 시작할 수 있게 하는 장치다.

---

## 7. 테스트 도구

| 항목 | 선택 |
|---|---|
| API 테스트 | `fastapi.testclient.TestClient` (starlette 내장, 의존성 추가 없음) |
| 비동기 테스트 | `pytest-asyncio` (`asyncio_mode = "auto"`) |
| 모델 스텁 | U1의 목업 모드 (`AGENT_MODE=mock`) 재사용. `unittest.mock` 최소화 |

**모델 스텁에 `unittest.mock`을 쓰지 않고 목업 모드를 쓰는 이유**: 목업 모드는 실제 코드 경로를 그대로 타고 검증·조립·감사가 실제로 돈다 (U1 §9 "거짓말하지 않는 목업"). `mock.patch`로 `Gatekeeper`를 갈아치우면 그 보증이 사라진다.

---

## 8. 검증 항목 (완료 기준)

- [ ] `grep -c "boto3\|BrokerClient" src/mesh/agent.py src/mesh/orchestrator.py` == 0
- [ ] `grep -c "exaone\|bedrock\|broker" src/mesh/orchestrator.py` == 0 (M-03)
- [ ] `TestClient`로 `send`를 `approved_by` 없이 호출 → 422
- [ ] 만료된 `envelope_id`로 `send` → 410
- [ ] 같은 `envelope_id`로 두 번 `send` → 두 번째 410
- [ ] 인용 0개 강제 → `ESCALATE` (신뢰도 0.99에서도)
- [ ] 응답 헤더에 보안 헤더 4개
- [ ] `/docs`·`/redoc`·`/openapi.json` → 404
- [ ] `agents.yaml`에 4번째 에이전트 추가 → 코드 변경 없이 목록에 나타남
- [ ] 2명 중 1명 강제 실패 → 나머지 답변 반환
- [ ] `AskResult` JSON에 `internal_path` 부재

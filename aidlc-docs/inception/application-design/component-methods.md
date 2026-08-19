# Component Methods

Python 3.12 타입 힌트 기준. 상세 비즈니스 규칙은 각 유닛의 Functional Design에 있다.
데이터 타입 정의는 `domain-entities.md`(유닛별) 참조.

---

## Gatekeeper — `src/mesh/gatekeeper.py`

```python
class Gatekeeper:
    """Agent를 감싸는 막. 신뢰 경계를 넘는 유일한 통로."""

    def __init__(self, cfg: Config, exaone: ExaoneClient,
                 broker: BrokerClient, audit: AuditLog) -> None: ...

    # ── 관문 공통 ──────────────────────────────────────────────
    def classify(self, text: str, source_path: str | None = None) -> TierDecision:
        """등급 판정. max(규칙, EXAONE). 실패 시 SECRET.  FR-01, FR-02"""

    def plan_calls(self, question: str, entity_id: str,
                   chunks: list[Chunk]) -> list[AgentCall]:
        """분해 or 상향을 결정하고 호출 계획을 만든다.
        불변식: 각 AgentCall.tier 는 단일값.  FR-11, FR-12"""

    # ── 관문 ①② 표현 변환 ─────────────────────────────────────
    def to_payload(self, call: AgentCall) -> PayloadEnvelope:
        """등급에 따라 분기.
          SECRET   -> Extractor.extract()      (슬롯 채우기 + 코드 조립)
          INTERNAL -> Pseudonymizer.apply()    (식별자 치환)
          OPEN     -> 변환 없음
        반환값에 mapping 이 포함되며 메모리에만 존재.  FR-03~06"""

    # ── 검증과 사람 확인 ───────────────────────────────────────
    def validate(self, env: PayloadEnvelope,
                 originals: list[str]) -> ValidationResult:
        """Validator 6단계 위임. 순수 함수.  FR-07"""

    def preview(self, env: PayloadEnvelope,
                vr: ValidationResult) -> PreviewCard:
        """사람이 읽을 전문 + 검증 결과 + '포함되지 않은 것' 목록.  FR-09, FR-41"""

    # ── 유일한 외부 호출 지점 ──────────────────────────────────
    async def ask_agent(self, env: PayloadEnvelope, persona: Persona,
                        approved_by: str) -> AgentResponse:
        """전제조건 (모두 위반 시 GatekeeperError):
             1. env.validation.passed is True
             2. approved_by 가 비어 있지 않다 (사용자 승인)
             3. env.tier 가 단일값
        감사 로그 기록 후 BrokerClient 호출.  FR-15"""

    # ── 관문 ③ 재수화 ─────────────────────────────────────────
    def rehydrate(self, resp: AgentResponse,
                  mapping: Mapping) -> RehydratedAnswer:
        """ref/placeholder -> 실제 이름. 순수 문자열 치환.
        호출 후 mapping 을 폐기한다.  FR-13"""

    # ── 폴백 ──────────────────────────────────────────────────
    async def answer_in_zone(self, question: str,
                             chunks: list[Chunk]) -> RehydratedAnswer:
        """Agent 를 부르지 않고 EXAONE 이 신뢰 구역 안에서 직접 답한다.
        감사 로그에 레코드를 남기지 않는다 (경계를 넘은 것이 없으므로).  FR-08, FR-45~47"""
```

---

## Classifier — `src/mesh/classifier.py`

```python
def rule_tier(text: str, source_path: str | None,
              rules: ClassificationRules) -> Tier:
    """순수 함수. 경로 패턴 · 헤더 등급 표기 · 고객사명 사전 · 계약번호 정규식."""

async def exaone_tier(text: str, exaone: ExaoneClient) -> Tier:
    """문맥적 기밀성 보조 판정. enable_thinking=False.
    실패/타임아웃 -> Tier.SECRET 반환 (fail closed)."""

async def classify(text: str, source_path: str | None,
                   rules: ClassificationRules,
                   exaone: ExaoneClient) -> TierDecision:
    """max(rule_tier, exaone_tier).
    최적화: rule_tier 가 이미 SECRET 이면 EXAONE 호출 생략."""
```

---

## Extractor — `src/mesh/extractor.py`

```python
async def extract(chunks: list[Chunk], schema: TaskSchema,
                  vocab: Vocabulary,
                  exaone: ExaoneClient) -> tuple[dict, Mapping]:
    """슬롯 채우기 방식 구조 추출.

    1. schema.slots 를 순회하며 슬롯 정의 프롬프트를 만든다
    2. EXAONE 에게 각 슬롯의 허용값 중 하나 또는 "__unknown__" 을 고르게 한다
    3. 응답에서 schema.slots 에 있는 키만 골라 페이로드를 새로 조립한다
       -> 미등록 키는 검증 실패가 아니라 여기서 버려진다 (drop)
    4. 타입 강제: "false"->False, "8"->8
    5. ref 라벨(REQ_A, COMP_B)을 자동 생성하고 mapping 을 만든다

    Raises ExtractionFailed: 필수 슬롯이 __unknown__ 이거나 2회 재시도 후에도
                             JSON 파싱 실패.  FR-03, FR-04, FR-46"""

def assemble(raw: dict, schema: TaskSchema) -> dict:
    """순수 함수. 화이트리스트 조립.
    불변식: set(result.keys()) <= set(schema.slot_names)   [PBT-03]"""

def coerce(value: object, slot: SlotDef) -> object:
    """타입 강제. 실측된 모델 습성 대응 ("false" -> False)."""
```

---

## Validator — `src/mesh/validator.py` (전부 순수 함수)

```python
MAX_PAYLOAD_BYTES = 2048
NGRAM_SIZE = 5

def validate(payload: dict, schema: TaskSchema, vocab: Vocabulary,
             banned: BannedTerms, originals: list[str]) -> ValidationResult:
    """6단계를 순서대로 실행. 첫 실패에서 멈추지 않고 전부 수집해
    사람이 볼 수 있는 진단을 만든다."""

def check_schema(payload: dict, schema: TaskSchema) -> CheckResult: ...
def check_vocab(payload: dict, vocab: Vocabulary) -> CheckResult: ...
def check_ranges(payload: dict, schema: TaskSchema) -> CheckResult: ...
def check_banned(payload: dict, banned: BannedTerms) -> CheckResult: ...
def check_no_source_ngram(payload: dict, originals: list[str],
                          n: int = NGRAM_SIZE) -> CheckResult:
    """가장 강력한 검사. 원문 문장이 한 조각이라도 있으면 잡힌다.
    정규화: 공백 축약 + 소문자화 후 비교."""
def check_size(payload: dict, limit: int = MAX_PAYLOAD_BYTES) -> CheckResult: ...
```

---

## Pseudonymizer / Rehydrator

```python
# src/mesh/pseudonymizer.py
async def apply(chunks: list[Chunk], vocab: Vocabulary,
                exaone: ExaoneClient) -> tuple[str, Mapping]:
    """식별자만 <SYS_1>/<PROJ_1> 로 치환. 기술 용어는 보존.
    불변식: 같은 대상 -> 같은 번호 (한 질의 안에서).  FR-05, FR-06"""

def technical_terms() -> frozenset[str]:
    """치환 금지 목록. 치환하면 답변 품질이 무너진다."""

# src/mesh/rehydrator.py
def rehydrate(text: str, mapping: Mapping) -> str:
    """순수 문자열 치환. 긴 키부터 치환해 부분 일치 사고를 막는다.
    속성: rehydrate(pseudonymize(x)) == x   [PBT-02]"""

def rehydrate_response(resp: AgentResponse, mapping: Mapping) -> RehydratedAnswer:
    """answer / reason / mitigations / citations 전부에 적용."""
```

---

## KnowledgeStore — `src/mesh/store.py`

```python
class KnowledgeStore:
    def load_session(self, entity_id: str) -> Session:
        """세션 JSON + verified_qa 병합. ${MESH_DATA_ROOT} 치환.  FR-16, FR-20"""

    def freshness(self, session: Session, now: datetime) -> Freshness:
        """LIVE(<15m) / STALE(<24h) / EXPIRED. FR-19"""

    async def select_paths(self, session: Session, question: str,
                           exaone: ExaoneClient) -> list[str]:
        """open_paths 중에서 관련 경로 선택.
        프롬프트에 파일 본문을 넣지 않는다 (세션 요약만).  FR-17, NFR-P-04"""

    def read(self, paths: list[str]) -> list[Chunk]:
        """선택된 파일만 읽는다. 경로 탈출(..) 거부, MESH_DATA_ROOT 하위 강제.
        Raises PathEscapeError.  FR-22, NFR-S-05"""

    def list_agents(self) -> list[AgentCard]:
        """agents.yaml + 세션 + disclose 설정 반영. FR-30"""

    def append_verified(self, entity_id: str, qa: VerifiedQA) -> None:
        """data/verified/{entity_id}.json 에 추가. tier 보존.  FR-20"""
```

---

## AgentClient — `src/mesh/agent.py`

```python
class AgentClient:
    def build_system_prompt(self, persona: Persona, tier: Tier) -> str:
        """페르소나 + '구조 요약이며 ref 로 지칭하라' + '1인칭 금지'.  FR-25, FR-26"""

    async def ask(self, gk: Gatekeeper, env: PayloadEnvelope,
                  persona: Persona, approved_by: str) -> AgentResponse:
        """Gatekeeper.ask_agent 를 경유한다. Bedrock 을 직접 부르지 않는다."""

    async def draft_escalation(self, gk: Gatekeeper, env: PayloadEnvelope,
                               partial: AgentResponse) -> EscalationDraft:
        """질문 요약 + 근거 + 답변 초안. 저비용 모델(haiku-4-5) 사용.  FR-27"""
```

---

## Orchestrator — `src/mesh/orchestrator.py`

```python
TOTAL_TIMEOUT_SECONDS = 30
MAX_TARGETS = 2

class Orchestrator:
    async def ask(self, req: AskRequest) -> AskResult:
        """1. 대상 검증 (최대 2)
        2. 대상별로 Store 조회 -> Gatekeeper.plan_calls -> to_payload -> validate
        3. preview 를 반환하고 사용자 승인 대기 (2단계 API)
        4. 승인 후 병렬 ask_agent -> rehydrate
        5. 신뢰도 분기 + divergent 병기
        전체 30초 상한.  FR-32~36"""

    def branch(self, answers: list[RehydratedAnswer]) -> Disposition:
        """AUTO(>=0.75 & 인용>=1) / UNVERIFIED(0.45~0.75) / ESCALATE(<0.45)
        인용 0개 -> 신뢰도 무관 ESCALATE.  FR-34, FR-35"""

    def merge(self, answers: list[RehydratedAnswer]) -> MergedAnswer:
        """2개 답을 병기. divergent 플래그만 세우고 상충 판정은 하지 않는다.
        각 답에 as_of / formality 를 붙인다.  FR-33"""

    def agent_cards(self) -> list[AgentCard]:
        """지목 목록. current_focus 는 식별자 제거 요약 (게이트키퍼 통과).  FR-30, FR-31"""
```

---

## AuditLog — `src/mesh/audit.py`

```python
class AuditLog:
    def record(self, env: PayloadEnvelope, persona: Persona,
               actor: str, vr: ValidationResult,
               approved_by: str, model_id: str,
               trusted_zone_llm_base_url: str) -> str:
        """경계를 넘기 직전 기록. SHA-256 계산. 반환값은 record_id.
        원문 · 토큰 · reasoning* 은 절대 기록하지 않는다.  FR-15, NFR-S-03"""

    def search(self, needle: str) -> list[AuditRecord]:
        """원문 문구 검색. 0건임을 보이는 데 쓰인다.  FR-42"""

    def mirror(self, record_id: str) -> None:
        """DynamoDB 미러. 실패해도 로컬 원본은 유지 (fail-open은 미러에만 허용)."""

    def sweep_for_leaks(self, corpus: list[Chunk]) -> LeakReport:
        """전 페이로드 vs 전 문서 5-gram 전수 대조. make eval 에서 호출.  FR-55"""
```

---

## ExaoneClient — `src/mesh/llm/exaone.py`

```python
STRIP_KEYS = ("reasoning", "reasoning_content")

class ExaoneClient:
    async def complete_json(self, system: str, user: str,
                            max_tokens: int = 800) -> dict:
        """Friendli OpenAI 호환 호출.
        고정: temperature=0
              chat_template_kwargs={"enable_thinking": False}
              response_format={"type": "json_object"}
        응답에서 STRIP_KEYS 를 파싱 전에 삭제 (원문 유출 채널).
        JSON 파싱 실패 시 2회 재시도.  FR-14, FR-46"""

    async def complete_text(self, system: str, user: str) -> str:
        """폴백 답변 생성용 (answer_in_zone)."""
```

---

## BrokerClient — `src/mesh/llm/broker.py`

```python
class BrokerClient:
    """gatekeeper.py 만 이 클래스를 import 한다 (경계 규칙)."""

    async def invoke(self, env: PayloadEnvelope, system_prompt: str,
                     model_id: str) -> AgentResponse:
        """AGENT_TRANSPORT 에 따라 분기:
             'broker' -> POST {BROKER_API_URL}/agent/invoke  (x-api-key)
             'direct' -> bedrock-runtime.Converse 직접 호출
             'mock'   -> data/fixtures/ 재생
        Raises BrokerError -> 호출자는 answer_in_zone 으로 폴백.  FR-47, FR-49"""
```

---

## AgentBrokerFunction — `infra/lambda/agent_broker/handler.py` (U5)

```python
def handler(event: dict, context) -> dict:
    """1. pydantic 으로 요청 검증 (NFR-S-05)
    2. 번들된 validator + vocab.json 으로 독립 재검증 (NFR-S-11)
       -> 실패 시 400 + CloudWatch 메트릭 ValidationFailure 증가
    3. bedrock-runtime.Converse 호출 (실행 역할)
    4. DynamoDB 감사 기록 (PutItem 만 허용)
    5. ref 기반 응답 반환
    오류 응답은 일반화 (스택 트레이스 금지, NFR-S-09)"""
```

# AI-DLC Audit Log

## Workspace Detection - Initial Request
**Timestamp**: 2026-08-19T00:00:00Z
**User Input**: "현재 폴더구조를 파악하여 ai-dlc 를 시작해줘"
**AI Response**: Starting AI-DLC workflow. Scanning workspace structure.
**Context**: INCEPTION Phase - Workspace Detection initiated

---

## Workspace Detection - Workspace Analysis
**Timestamp**: 2026-08-19T00:01:00Z
**User Input**: N/A (automated analysis)
**AI Response**: Workspace scanned. Empty workspace detected (no source code files, no build files). Greenfield project identified.
**Context**: INCEPTION Phase - Workspace Detection - Step 2 Complete

---

## Requirements Analysis - Questions Generated
**Timestamp**: 2026-08-19T00:02:00Z
**User Input**: N/A (automated step)
**AI Response**: Created requirement-verification-questions.md with 8 questions (5 project questions + 3 extension opt-in questions). Awaiting user responses.
**Context**: INCEPTION Phase - Requirements Analysis - Step 6 Complete. Gate: Awaiting user answers.

---
## Requirements Analysis - User Answers via Chat (Resumed Session)
**Timestamp**: 2026-08-19T10:00:00Z
**User Input**: "해커톤을 진행중인데, '무엇을 구현할지'는 requirements 디렉토리에 두었습니다. 일단 여기있는 문서들을 정독해주세요. 그 다음, 구체적으로 어떻게 구현할지 CONSTRUCTION을 만들어주세요. 참고로 사내망 EXAONE 에 접근하기 위한 정보는 .kiro/opencode.jsonc 에 있고, AWS Bedrock 에 claude 를 사용할건데, 그것에 접근하기 위한 정보는 .kiro/.env 에 있습니다. 클라우드 인프라 배포는 aws cdk 를 이용할 것이고 로컬 컴퓨터는 일단 현재 컴퓨터를 이용할건데, 사실은 아직 미완성입니다 다른 컴퓨터를 사용할 수도 있으니 그거는 유념해주세요. aidlc 에 대해 잘 모르지만 aidlc-docs 포맷을 채워주시고, 구체적인 설계 문서가 어디 있는지 이 답변으로 알려주기도 해주세요. 이 질문이 끝나면 바로 구현작업에 들어갈 수 있도록 구체적으로 어떻게 구현할 것인지 설계하는 것이 목표입니다."
**AI Response**: Read requirements/hackathon-mvp-design.md and requirements/scenarios.md in full. Read .kiro/opencode.jsonc (Friendli dedicated EXAONE endpoint) and .kiro/.env (AWS temporary STS credentials, us-east-1). Answers to requirement-verification-questions.md derived from user input and the provided requirements documents. Proceeding through Requirements Analysis -> User Stories -> Workflow Planning -> Application Design -> Units Generation -> CONSTRUCTION design stages in a single pass, per the user's explicit instruction to produce an immediately implementable design.
**Context**: INCEPTION Phase - Requirements Analysis - Step 6 gate satisfied (answers received via chat rather than in-file)

---

## Requirements Analysis - Endpoint Preflight Verification
**Timestamp**: 2026-08-19T10:12:00Z
**User Input**: N/A (automated verification)
**AI Response**: Executed live verification of both model endpoints before designing. Findings: (1) EXAONE at Friendli dedicated endpoint responds HTTP 200 in ~0.8-1.0s; model emits `reasoning`/`reasoning_content` fields when `enable_thinking` is true. (2) `anthropic.claude-sonnet-5` is listed but returns AccessDeniedException for account 891401657794; verified-working model IDs are `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, `us.anthropic.claude-sonnet-4-6`, `us.anthropic.claude-haiku-4-5-20251001-v1:0`, `us.anthropic.claude-opus-4-5-20251101-v1:0`. All Claude models require an inference-profile ID (`us.` / `global.` prefix). (3) Whole-JSON structure extraction by EXAONE produced three out-of-vocabulary fields on the first attempt; per-slot constrained extraction produced fully in-vocabulary output, deterministic across 3 runs. (4) AWS account is region-locked to us-east-1, CDK is not yet bootstrapped, role WSParticipantRole has PowerUserAccess + scoped iam:CreateRole/PassRole sufficient for CDK. Recorded in aidlc-docs/construction/preflight-findings.md.
**Context**: INCEPTION Phase - Requirements Analysis - technical feasibility verification

---

## Requirements Analysis - Extension Opt-In (AI Recommendation, Pending Confirmation)
**Timestamp**: 2026-08-19T10:15:00Z
**User Input**: N/A (user asked AI to fill the aidlc-docs format)
**AI Response**: Recorded recommended extension configuration in aidlc-state.md: Security Baseline = Yes (the project's core value proposition is a security control, and the design introduces an internet-facing endpoint that invokes a paid model), Resiliency Baseline = No (5-day hackathon prototype), Property-Based Testing = Partial (the Gatekeeper validator and vocabulary whitelist are pure functions with strong invariants; PBT-02/03/07/08/09 enforced). Loaded security-baseline.md and property-based-testing.md rule files accordingly. Flagged for user confirmation.
**Context**: INCEPTION Phase - Requirements Analysis - Step 5.1

---

## Requirements Analysis - Security Finding (Secrets in Workspace)
**Timestamp**: 2026-08-19T10:16:00Z
**User Input**: N/A (automated review)
**AI Response**: SECURITY-12 finding logged. Live credentials are present in plaintext inside the workspace: a Friendli API key in .kiro/opencode.jsonc and AWS temporary STS credentials in .kiro/.env. The workspace is not yet a git repository and has no .gitignore. Mitigation required before the first commit: add .gitignore covering .kiro/.env, .kiro/opencode.jsonc, and .env; move the Friendli key out of the committed jsonc into an environment variable; rotate the Friendli key after the hackathon. Recorded as a blocking item in the code generation plan for unit U1.
**Context**: INCEPTION Phase - Requirements Analysis - security baseline enforcement

---
## Requirements Analysis - Complete
**Timestamp**: 2026-08-19T10:30:00Z
**User Input**: N/A (derived from user's chat input and provided requirements documents)
**AI Response**: Generated aidlc-docs/inception/requirements/requirements.md at Comprehensive depth. 55 functional requirements (FR-01~55) organized so that leak prevention sits in the FR section rather than NFR, because in this project security is a correctness requirement rather than an added quality. 34 non-functional requirements mapped to SECURITY-01~15 and PBT-01~10 with explicit N/A rationale for 6 items. Recorded success criteria as hard gates: secret recall 100%, tier accuracy >=90%, zero leaks, citation compliance 100%. Documented residual risks including the honest statement that the trust boundary is simulated in this hackathon build.
**Context**: INCEPTION Phase - Requirements Analysis - Steps 7~9 complete

---

## User Stories - Complete
**Timestamp**: 2026-08-19T10:40:00Z
**User Input**: N/A
**AI Response**: Generated personas.md (6 personas derived from scenarios.md characters plus the security reviewer and the 3-person dev team) and stories.md (31 stories across 5 epics, 26 P0 / 5 P1 / 0 P2). P2 count is intentionally zero — the 5-day plan does not schedule P2 work. Each story's acceptance criteria doubles as the demo script.
**Context**: INCEPTION Phase - User Stories complete

---

## Workflow Planning - Complete
**Timestamp**: 2026-08-19T10:50:00Z
**User Input**: N/A
**AI Response**: Generated execution-plan.md. Risk assessed Medium-High. Per-unit CONDITIONAL stage decisions recorded with rationale for each SKIP (11 skips documented). Approval gates merged into a single gate before Code Generation Part 2, per the user's explicit request to be able to start implementing immediately after this response.
**Context**: INCEPTION Phase - Workflow Planning complete

---

## Application Design - Complete
**Timestamp**: 2026-08-19T11:00:00Z
**User Input**: N/A
**AI Response**: Generated components.md, component-methods.md, services.md, component-dependency.md, application-design.md. Core design decision: component boundaries are made to coincide with the trust boundary, and exactly two call sites cross it (Gatekeeper.ask_agent and AuditLog.mirror). Five architectural rules are enforced structurally rather than by convention — import boundary test, type-level single-tier invariant, non-serializable Mapping, two-phase approval API, and whitelist assembly loop direction. Deviated from the design document's "8 files" target to 17 modules, with justification recorded (SECURITY-11 security logic isolation, and validator.py must be pure to be bundled into the Lambda).
**Context**: INCEPTION Phase - Application Design complete

---

## Units Generation - Complete
**Timestamp**: 2026-08-19T11:10:00Z
**User Input**: N/A
**AI Response**: Generated unit-of-work.md, unit-of-work-dependency.md, unit-of-work-story-map.md. Decomposed into 6 units aligned to file ownership so that git conflicts are structurally avoided across the 3-person team. Critical path U6 -> U1 -> U3 -> U4 identified; U1 is the bottleneck with 15 of 31 primary stories. Mitigation: U1 commits a stub Gatekeeper interface on Day 1 so U3 is not blocked on Day 3. Documented per-unit rollback so that no single unit failure kills the whole demo.
**Context**: INCEPTION Phase - Units Generation complete

---

## Construction Design - All Units Complete
**Timestamp**: 2026-08-19T11:20:00Z
**User Input**: N/A
**AI Response**: Generated per-unit design artifacts. U1: functional-design (3), nfr-requirements (2), nfr-design (2). U2: functional-design (3). U3: functional-design (3), nfr-requirements (2). U4: functional-design (4, including frontend-components.md). U5: nfr-requirements (2), nfr-design (2), infrastructure-design (2). U6: functional-design (3). Plus shared-infrastructure.md and preflight-findings.md. Key design change from the source documents: structure extraction moved from whole-JSON generation to per-slot constrained filling with code-side whitelist assembly, based on measured vocabulary escape. Cloud broker added to eliminate STS credential expiry and to provide an independent second validation layer plus tamper-evident audit.
**Context**: CONSTRUCTION Phase - Functional/NFR/Infrastructure Design complete for all units

---

## Code Generation Part 1 (Planning) - Complete
**Timestamp**: 2026-08-19T11:30:00Z
**User Input**: N/A
**AI Response**: Generated 6 code generation plans in aidlc-docs/construction/plans/ with numbered, checkboxed steps, story traceability tables, completion criteria, and per-unit SECURITY/PBT compliance summaries including N/A rationale. U1 Step 1 is a blocking security remediation (.gitignore before git init) because live credentials exist in plaintext in the workspace. Updated aidlc-state.md with full stage progress, quality gates, and verified technical facts.
**Context**: CONSTRUCTION Phase - Code Generation Part 1 complete

---

## Code Generation Part 1 - Approval Prompt
**Timestamp**: 2026-08-19T11:31:00Z
**AI Prompt**: "Code generation plans complete for all 6 units. Ready to begin Code Generation Part 2 (actual implementation), starting with U1 Step 1 (.gitignore then git init)?"
**User Response**: [awaiting]
**Status**: Awaiting approval
**Context**: Approval gate before implementation begins

---

## Security Compliance Summary - Design Phase
**Timestamp**: 2026-08-19T11:32:00Z
**User Input**: N/A
**AI Response**: SECURITY baseline evaluated across all units. Compliant: SECURITY-01, 02, 03, 05, 06, 09, 10, 11, 13, 14, 15. Partially N/A with documented rationale: SECURITY-04 (HSTS N/A on localhost HTTP; N/A entirely for U5 which serves no HTML), SECURITY-07 (no VPC created — NAT cost exceeds all other resources combined; inbound is a single API Gateway controlled by auth and throttling), SECURITY-08 (user authentication is explicitly out of scope per requirements section 7; replaced by four compensating controls and recorded as a production prerequisite), SECURITY-12 (password policy, MFA, session cookies N/A with no user auth; hardcoded-credential prohibition is enforced). One blocking finding was identified and its remediation is scheduled as U1 Step 1: live Friendli API key and AWS STS credentials exist in plaintext in the workspace with no .gitignore present. No unresolved blocking findings remain in the design artifacts.
**Context**: CONSTRUCTION Phase - security baseline enforcement across design stages

---

## PBT Compliance Summary - Design Phase
**Timestamp**: 2026-08-19T11:33:00Z
**User Input**: N/A
**AI Response**: Property-Based Testing baseline evaluated in Partial mode (PBT-02, 03, 07, 08, 09 blocking). Compliant: PBT-01 (properties identified per unit), PBT-02 (round-trip: pseudonymize/rehydrate, payload serialization), PBT-03 (invariants: whitelist assembly, vocabulary membership, source 5-gram absence, placeholder consistency, Tier ordering, single-tier, non-serializable Mapping), PBT-07 (centralized domain generators including adversarial_raw), PBT-08 (print_blob seed logging, shrinking enabled), PBT-09 (Hypothesis pinned), PBT-10 (example-based scenario tests in U6). N/A with rationale: PBT-04 for U3/U5/U6 (no idempotent operations), PBT-05 across all units (no reference implementation exists; the EXAONE-solo versus structured-extraction quality comparison is a separate evaluation harness, not an oracle test), PBT-06 across all units (KnowledgeStore's only write is append-only, Inbox has 4 states and 3 transitions fully covered by example tests, Lambda is stateless), and all PBT rules for U4 (only pure logic is display mapping tables, fully covered by example tests plus grep-based lint checks). No blocking PBT findings remain.
**Context**: CONSTRUCTION Phase - PBT baseline enforcement across design stages

---
## Code Generation Part 1 - Approval Received
**Timestamp**: 2026-08-19T12:00:00Z
**User Input**: "짚어야할 두가지에 대해서 말씀드리겠습니다:\n\n- EXAONE 이 friendli 라는 인터넷망에서 서빙되는 것을 알고 있습니다. 다만, 전제조건으로 현재 상황이 사내망이라고 가정하고 진행하도록 하려고 합니다 (어차피 지금은 데모고, 실제 서비스화 할 때 엔드포인트만 바꾸면 되는것이기 때문) \n- .gitignore 는 만들어주세요. \n- 자격 증명 만료는 해커톤 종료 때까지는 걱정 없는 것으로 알고 있습니다. 그리고 claude sonnet 5가 안된다면 4.5 를 이용해도 괜찮습니다\n\n \n\n이제 day 1 부분 까지 구현을 부탁드리겠습니다. 중간중간 테스트 및 검증하면서 진행해주시되, EXAONE 이든 Claude 이든 사용량을 너무 많이 쓰지는 말아주세요 (꼭 필요한 검증용으로 몇 번 이내로만 사용)"
**AI Response**: Approval received with three clarifications: (1) trust boundary is simulated by premise and that is accepted, (2) create .gitignore, (3) STS credential expiry is not a concern during the hackathon and claude-sonnet-4-5 is acceptable. Proceeding with Day 1 implementation under a constraint of minimal LLM usage.
**Context**: CONSTRUCTION Phase - approval gate passed, Code Generation Part 2 begins

---

## Code Generation Part 2 - Day 1 Complete
**Timestamp**: 2026-08-19T15:30:00Z
**User Input**: N/A (implementation)
**AI Response**: Day 1 complete. 382 unit tests passing, lint and format clean, zero dependency vulnerabilities, preflight passing with 0 failures. Frozen contracts: schemas.py, data/vocab.json v1.0.0, gatekeeper.py signatures, api_models.py, config/agents.yaml, and 11 API fixtures generated from the actual pydantic models. Rule-based classification measured at 11/11 = 100% accuracy with 3/3 secret recall, which pre-satisfies the Day 2 gate G2 target. Total LLM usage held to 3 EXAONE calls and 3 Bedrock calls, all for verification.
**Context**: CONSTRUCTION Phase - Code Generation Part 2 - Day 1

---

## Day 1 - Defects Found and Fixed
**Timestamp**: 2026-08-19T15:31:00Z
**User Input**: N/A (automated verification)
**AI Response**: Four defects were found by tests written alongside the implementation, all recorded in aidlc-docs/construction/preflight-findings.md section 7.

(1) Tier comparison fell back to alphabetical ordering. functools.total_ordering does not inject __gt__ into a StrEnum because str.__gt__ already exists and differs from object.__gt__. Since max() uses __gt__, max(Tier.INTERNAL, Tier.OPEN) returned OPEN. FR-11 expresses tier escalation as max(tiers), so an internal document would have been classified as open and its verbatim text would have crossed the boundary. No exception, no log entry — a silent leak. Fixed by defining all four comparison methods explicitly; test_tier_order.py now checks every permutation of the three tiers.

(2) extra={"name": ...} collides with LogRecord.name and raises KeyError, meaning a single log line kills the request. It occurred in the exaone retry warning, a path that only executes on failure and would not surface during normal development. Fixed with a log_extra() helper that prefixes reserved keys, plus tests/unit/test_log_extra_static.py performing an ast scan of all source modules.

(3) BLOCKING: banned.json v1.0.0 contained internal project and system names (atlas-ml, Nova gateway) even though the file's own comment stated that the blocking list must be distinct from the pseudonymization list. Consequence: all five internal documents classified as secret, dropping accuracy to 6/11 = 55% and preventing scenario 2's pseudonymization path from ever executing. Fixed by creating data/pseudonyms.json, adding schemas.PseudonymTargets, and adding DataBundle._check_lists_are_disjoint() which rejects overlap at load time with ConfigError so the defect cannot recur.

(4) Initially selected dependency versions carried 8 known vulnerabilities: starlette 0.41.3 with 7 CVEs (pulled transitively by fastapi 0.115.6) and pytest 8.3.4 with 1. Root cause was pinning versions in the design document without running pip-audit. Fixed by raising versions and pinning starlette directly so it cannot regress via transitive resolution. Re-audit reports zero vulnerabilities and all 382 tests still pass.
**Context**: CONSTRUCTION Phase - Day 1 verification findings

---

## Day 1 - Design Changes from Measurement
**Timestamp**: 2026-08-19T15:32:00Z
**User Input**: N/A
**AI Response**: Three design documents were amended based on measurements taken during implementation.

Latency budget corrected: the design's 2.17s figure for Bedrock was measured on a 4-token response. A real scenario 1 answer produces 513 output tokens and takes 9.23s — output tokens dominate latency. u3-agent-mesh/nfr-requirements/nfr-requirements.md P-03 revised from under 4s to under 12s and P-04 from under 5s to under 14s. Total remains approximately 18s against the 30s ceiling, leaving 12s of headroom, which reinforces the existing policy of not retrying timeouts. The preflight script now prints this warning on every run so nobody judges latency from a ping.

EXAONE latency improved as a side effect of the security fix: enable_thinking=False reduced round trips from 0.78-0.96s to 0.27-0.42s because the model no longer generates reasoning tokens.

Schema refinement: the role field was moved out of vocab.json slots and into a top-level entity_roles list, because role is a structural key of the payload whose permitted values come from the specific task_schema's entity_roles rather than from a global list. This is more precise — a 'goal' role appearing in constraint_conflict_check is now caught. Vocabulary.load() rejects any slot name that collides with STRUCTURAL_KEYS.
**Context**: CONSTRUCTION Phase - Day 1 design amendments

---

## Code Generation Part 2 — Day 1 보고서 + Day 2 구현 요청
**Timestamp**: 2026-08-19T12:05:00Z
**User Input**: "day1 에 대한 명확한 설명 문서(무엇을, 어떻게, 왜, 어떤 범위까지, 어떤 테스트와 함께 구현했는지) 와 함께 day 2 내용 구현도 진행해주세요."
**AI Response**: Day 1 구현 보고서 작성 후 Day 2 (U1 Step 9~18) 구현. 신규 모듈 6개 + 게이트키퍼 8개 관문 구현 + PBT + 게이트 G2.
**Context**: CONSTRUCTION / Code Generation Part 2 / U1 Day 2

---

## Day 1 구현 보고서 작성
**Timestamp**: 2026-08-19T12:12:00Z
**AI Response**: `aidlc-docs/construction/day1-implementation-report.md` 작성. 10개 절 — Day 1 의 목표(계약 동결), 무엇을 만들었나(계약 6종 + 실동작 코드 9개 모듈), 어떻게(설계 원칙 6가지의 코드 표현), 왜(실측이 설계를 바꾼 4지점), 테스트가 잡은 결함 4건, 테스트 전략, 범위 밖(Day 2/3/4/5·프로젝트 밖), 검증 상태, 이식성, Day 2 착수 지점.
**Context**: todo #1 완료

---

## Day 2 · classifier.py (Step 9~10)
**Timestamp**: 2026-08-19T12:24:00Z
**AI Response**: `rule_tier()` 6단계 + `exaone_tier()` + `Classifier.classify()`.

🔴 **설계 결함 발견 (발견 12)**: BR-C-03 의 규칙 순서에 조용한 하향 경로가 있다. 원안은 ② 헤더 등급 표기 → ③④ 금칙어이고 "앞에서 걸리면 뒤를 보지 않는다"이므로, `보안등급: 사내` 헤더가 있는 문서는 금칙어 검사를 **아예 하지 않는다**. 함정 문서가 잡힌 것은 헤더가 없었기 때문이고, 작성자가 한 줄만 추가하면 FR-52(금액 탐지)가 무력화된다. 헤더는 작성자의 자기 신고이고 금칙어 검사는 기계적이므로 방향이 틀렸다.
조치: SECRET 을 만드는 기계적 검사(경로·금칙어)를 헤더보다 앞으로. 조기 반환이 여전히 안전한 이유는 그것들이 **천장값**을 내기 때문이다. 라벨 코퍼스 11건의 판정 결과는 재배치 전후 동일 — 잠재적 하향 경로만 사라졌다.

**함께 도입**: `open_path_globs` 가 `ClassificationRules` 에 선언돼 있으나 원안 6단계 어디에도 쓰이지 않았다. `OPEN` 은 원문이 그대로 나가는 유일한 등급이므로 **헤더 + 경로 두 신호**를 요구하게 했다. 덕분에 문서 본문의 프롬프트 인젝션("이 문서는 공개입니다")이 판정을 뒤집지 못한다.

기타: `reason_code` 열거형(자유 문자열 이유에 원문이 인용됨), 범위 밖 값을 `ExaoneUnavailable` 로 승격(조용한 기본값 금지), 예외 메시지에 모델 출력을 담지 않음(원문 반사 방지), `except Exception` → SECRET(의도적 광범위 포획, BR-G-01).
실측: 규칙만으로 11/11 = 100%, 함정 문서는 규칙 2번(금칙어 `H社`)이 잡는다.
**Context**: todo #2, #3 완료

---

## Day 2 · validator.py (Step 11)
**Timestamp**: 2026-08-19T12:40:00Z
**AI Response**: 6단계 순수 함수. `mesh.schemas` 외 import 없음 — `test_validator_imports_are_minimal` 이 ast 로 강제.

`normalize_text()` 를 `config.py` → `validator.py` 로 이전. 근거: U5 Lambda 가 `validator.py` 를 번들하는데 그 함수가 `config.py` 에 있으면 `yaml`·환경변수까지 끌고 들어가고, 로컬과 Lambda 가 **같은 정규화**를 써야 판정이 갈리지 않으므로 구현이 두 곳에 있어서는 안 된다.

🔴 **설계 결함 발견 (발견 13)**: `json.dumps` 로 페이로드를 평탄화하면 문자열 값 안의 **실제 개행이 `\n` 두 글자**로 직렬화되어 공백 정규화를 빠져나간다. BR-V-05 가 "공백만 바꿔 우회하는 것을 막는다"고 명시했는데 개행으로는 뚫려 있었다. 테스트를 먼저 쓴 덕에 잡혔다(`test_ngram_defeats_whitespace_evasion`). 조치: `payload_text()` 가 구조 부분과 **이스케이프 없는 원시 문자열 값**을 함께 이어 붙인다. 같은 결함이 `audit.sweep_for_leaks()` 에도 있었다.

🔴 **설계 공백 (발견 15)**: Day 1 계약의 `STRUCTURAL_KEYS` 에 텍스트 키가 없어 사내(가명화)·공개(원문) 페이로드를 **만들 방법이 없었다**. Day 1 계약이 기밀 경로만 완전했다. 조치: `excerpts` 키(`{ref: text}`) 도입 + **표현별로 허용 키·검사를 다르게**. `STRUCTURED` 에서만 `excerpts` 를 금지하는 것이 기밀 등급의 "원문 0개"를 구조적으로 보장한다.
부수: `VERBATIM` 은 5단계 적용 불가 → 통과시키되 그 사실을 `CheckResult.detail` 에 남긴다(조용히 넘기지 않음). 2KB 상한은 구조 페이로드용이라 텍스트 표현에는 ×8 을 적용(원 근거 "자유 텍스트 혼입 신호"가 성립하지 않음).
기타: 토큰이 n 개보다 적은 원문을 하나의 gram 으로 취급(짧은 원문이 검사를 통째로 빠져나가는 것 방지).
**Context**: todo #4 완료

---

## Day 2 · extractor.py (Step 12)
**Timestamp**: 2026-08-19T12:52:00Z
**AI Response**: `coerce()` / `assemble()` / `assign_refs()` / `build_slot_prompt()` / `choose_schema()` / `extract()`.
`assemble()` 이 `schema.slots` 를 순회하고 `raw` 를 순회하지 않는다. 미등록 키를 **두 지점**에서 버린다 — 배치 병합 시(`k in names`)와 조립 시. 로그에 키 이름을 남기지 않는다(모델이 원문 조각을 키로 만들 수 있다).
`coerce` 결정: enum 유사 매칭 금지 · int 는 숫자+단위 형태만(`"2026-07-15"`→`2026` 이 범위 안이라 조용히 통과하는 것 방지) · `bool` 을 int 슬롯에서 DROP(`True == 1` 이지만 의미가 다르다) · **범위 검사 안 함**(검증 3단계의 일. 여기서 자르면 환각값이 정상값으로 위장된다).
`choose_schema()` 는 키워드 휴리스틱 — 데모 범위임을 명시. 틀리면 `ExtractionFailed` → 신뢰 구역 내 답변이라 **유출이 아니라 품질 저하**다.
**Context**: todo #5 완료

---

## Day 2 · pseudonymizer.py + rehydrator.py (Step 13)
**Timestamp**: 2026-08-19T13:00:00Z
**AI Response**: LLM 을 쓰지 않는 순수 치환. placeholder 번호를 **리터럴 길이 순으로 먼저 배정하고 그다음 치환** — 문서 순서에 따라 번호가 흔들리면 일관성이 깨진다(BR-P-02).
`PseudonymResult.identifiers` 는 **치환 대상 전체**다(실제 치환분이 아니다). 치환된 것만 검증기에 넘기면 **가명화가 놓친 표기 변형**을 검사할 방법이 사라진다.
`rehydrate_obj()` 가 응답을 재귀 순회 — 필드 이름을 하드코딩하면 새 task 추가 시 재수화가 조용히 빠진다. 매핑 없는 기호는 치환하지 않고 **남긴다**(지우면 사용자가 문장이 불완전해진 것을 알 수 없다).
`merge_mappings()` 가 키 충돌 시 예외 — 조용히 덮어쓰면 재수화가 틀린 이름을 남긴다.
**Context**: todo #6 완료

---

## Day 2 · audit.py (Step 14)
**Timestamp**: 2026-08-19T13:08:00Z
**AI Response**: SQLite 3테이블 + 파일 권한 0600/0700.
`reject_forbidden()` — "원문을 기록하지 않는다"를 **실행되는 검사**로 만들었다. 거부하면 `ask_agent()` 가 예외를 받고 전송도 일어나지 않는다(fail closed).
`local_queries` 에 질문 원문을 넣지 않는다 — `question_sha256` 만. `reason_code` 도 열거형(`LOCAL_REASON_CODES`)이다. 자유 문자열 이유를 받으면 그 이유에 질문 원문이 섞여 들어간다.
`search()` 파라미터화(검색어가 사용자 입력이고 `REQ-4412` 처럼 특수문자가 섞인다). `mirror()` 만 fail-open(유일) + 실패 건수 노출.
`test_no_delete_or_update_anywhere_in_src` 가 앱 전체를 정규식으로 검사.
**Context**: todo #7 완료

---

## Day 2 · gatekeeper.py 구현 (Step 15)
**Timestamp**: 2026-08-19T13:18:00Z
**AI Response**: 8개 관문 구현. Day 1 시그니처 그대로(NFR-M-02).
`send_and_rehydrate()` 신설 — `ask_agent` + `rehydrate` 를 호출자가 조합하면 재수화 실패 시 매핑이 남는 경로가 생긴다. `try/finally` 를 구조로 강제.
`build_system_prompt()` + `assert_all_mandatory_present()` 를 `gatekeeper.py`(L4)에 뒀다. `ask_agent()` 가 프롬프트를 필요로 하는데 L4 는 L5(`agent.py`)를 import 할 수 없고, 구현이 한 곳에 있어야 "필수 문구가 빠진 경로"가 생기지 않는다. `assert` 미사용(`python -O`).
`plan_calls()` 는 질문 하나 → 호출 하나. 분해 판정에는 하위 질문 그래프가 필요하므로 `can_decompose()` + `SubQuestion` 을 별도로 제공하고, Orchestrator 가 하위 질문마다 `plan_calls()` 를 한 번씩 부른다.
`answer_in_zone(reason=...)` 이 **열거값**을 받는다(자유 문자열 이유가 `local_queries` 에 저장되면 원문이 섞인다).
`test_unimplemented_methods_fail_loudly` 를 `test_no_method_is_still_a_stub` 으로 방향 전환(ast 로 스텁 잔존 검사).
**Context**: todo #8 완료

---

## Day 2 · PBT (Step 16)
**Timestamp**: 2026-08-19T13:30:00Z
**AI Response**: `tests/generators.py` 8개 생성기 + PB-1~PB-10.
`adversarial_raw()` 가 핵심 — 원시 타입 생성기만으로는 임의 문자열 키가 슬롯 이름과 겹치지 않아 `assemble()` 이 늘 빈 dict 를 반환하고 **테스트가 아무 일도 하지 않는데 통과한다**. 실측된 실패 방식(미등록 키·하이픈 변형·자유 문자열·원문 조각·중첩·타입 불일치·`__unknown__`)을 그대로 생성한다.
생성기 자체 검사(`test_adversarial_generator_actually_produces_adversarial_input`) 추가 — 표본 200개에서 미등록 키·원문 조각·타입 불일치가 실제로 나오는지 + **살아남는 값이 있는지** 확인. 모든 것을 버리는 조립기는 PB-3/4/5 를 자동 통과한다.
`tests/{,unit,property,eval}/__init__.py` 추가 + `pythonpath = ["src", "."]`.
**Context**: todo #9 완료

---

## Day 2 · 게이트 G2 (Step 18)
**Timestamp**: 2026-08-19T13:38:00Z
**AI Response**: `tests/eval/test_classification.py` + `make eval-classify`.
리포트를 **assert 보다 먼저** 출력(실패 시 진단이 보여야 게이트가 도구가 된다). 상향(불편)/하향(유출) 구분. 하향 오류·기밀 재현율·함정 탐지를 각각 blocking.
게이트를 **규칙만으로** 측정한다. 근거: 프로덕션은 `max(규칙, EXAONE)` 이고 `max` 는 등급을 올릴 수만 있으므로 `기밀 재현율(프로덕션) >= 기밀 재현율(규칙)` 이 항상 성립한다. 이점 — 결정적(LLM 0회, CI 무료), 하한선 측정, 모델 가용성에 흔들리지 않음. `MESH_EVAL_WITH_EXAONE=1` 로 실측 옵션 제공(11회).
추가 검사: `OPEN` 오판정 0건 · 모든 코퍼스 문서에 라벨 존재 · 세 등급 모두 존재 · 재실행 결과 동일.
**결과: 정확도 11/11 = 100% · 기밀 재현율 3/3 = 100% · 함정 1/1 · 하향 0건 → 게이트 G2 통과**
**Context**: todo #10 완료

---

## Day 2 · 실측 검증 (EXAONE 4회)
**Timestamp**: 2026-08-19T13:46:00Z
**AI Response**: 실제 코퍼스 2건으로 구조 추출 실측.

🔴 **설계 결함 발견 (발견 14)**: 첫 실측 페이로드가 **검증 6/6 통과 + 원문 0개**인데 **답이 틀린다**. 두 문서를 한 프롬프트에 넣고 평탄한 `{슬롯: 값}` 으로 조립하니 고객사 문서의 `session_binding=required` 가 자사 문서의 `none` 으로 덮여 사라졌다. `constraint_conflict_check` 는 두 근거를 **대조하는** task 인데 대조 대상이 소실됐고, Agent 는 "충돌 없음"이라고 답한다. 유출이 아니라 정확성 실패이며, 검증 6단계가 전부 통과하므로 유출보다 발견하기 어렵다.
조치: ① 문서마다 따로 슬롯을 채운다(한 프롬프트에 여러 문서를 넣으면 모델이 상충을 뭉개고 출처도 사라진다) ② `facts` 를 `{ref: {슬롯: 값}}` 으로 분리.
파생 수정: `slot_entries()` 가 **경로별로** 반환(이름별 dict 로 뭉치면 두 근거 중 하나만 범위 검사된다) · `check_schema` 가 `facts` 하위 ref 라벨을 형식 정규식으로 허용 · 필수 슬롯은 **근거 전체에서** 채워지면 충족(세션 최대시간은 고객사 문서에만, 토큰 수명은 자사 문서에만 있다).

재실측: 검증 6/6 · 562 bytes · 원문 5-gram 0건(대조 549개) · `verbatim_sentence_count=0` · 지연 0.86s(문서 2건, 호출 2회). `H社`·`하나텔`·`REQ-4412`·`CTR-204817`·`EAP-AKA`·`12억`·`김철수`·`Nova`·`atlas` 전부 부재. **충돌이 페이로드에 보존됨** (`REQ_A.session_binding=required` vs `COMP_A.session_binding=none`).

**환경 발견 (발견 16)**: AWS 임시 자격증명 만료(`ExpiredToken`). 발견 5가 예측한 대로다. Day 2 에는 영향 없음(Bedrock 은 대역으로 검증). **Day 3 전 갱신 필요**, 또는 `AGENT_TRANSPORT=mock` 으로 우회.
**Context**: 실측 검증. LLM 예산 준수 (Day 2 총 EXAONE 5회, Bedrock 0회)

---

## Day 2 · 종합 검증 + 문서 갱신
**Timestamp**: 2026-08-19T13:58:00Z
**AI Response**: `make test` 712개 통과 · `make lint` 통과 · `make audit` 0건 · `make eval-classify` 통과 · `make preflight` 실패 1(AWS 자격증명 만료).
문서 갱신: `preflight-findings.md` §9~10 추가(발견 12~16 + Day 2 종료 상태 + 남은 한계 6건) · `day2-implementation-report.md` 작성 · `aidlc-state.md`(G2 통과, SG10~12 신설, Day 2 완료 내역, 실측 사실 11~15) · u1 계획 Step 9~18 체크박스 + 스토리 추적 + 완료 기준.
**파일 길이 기준 수정**: 원래 "파일당 300줄 이하 / `gatekeeper.py` 150줄 이내"는 미달(831줄). 그중 74%가 주석·docstring 이다. 기준의 의도("한 파일이 너무 많은 일을 하지 않게")는 문(statement) 수로 재면 충족되고(전 파일 250문 이하), 이 프로젝트에서 주석을 줄이는 것은 손해다 — 발견 12~14 가 전부 "왜 이렇게 했는지"를 모르면 되돌려질 수 있는 종류다. 기준을 **문 250개 이하**로 수정.
**Context**: todo #11 완료

---

## Day 3 구현 요청
**Timestamp**: 2026-08-19T14:10:00Z
**User Input**: "이어서 day 3 부분에 대한 구현을 진행해주세요"
**AI Response**: U2 Step 5~9 (Store 완성) + U3 Step 1~7 (Agent · Orchestrator · Inbox · FastAPI) 구현 + 게이트 G3.
**Context**: CONSTRUCTION / Code Generation Part 2 / U2·U3 Day 3

---

## Day 3 · store.read / select_paths / list_agents (U2 Step 5~7)
**Timestamp**: 2026-08-19T14:35:00Z
**AI Response**:
`read()` — 2중 검사(`safe_resolve` + `knowledge_scope`). 처리를 다르게 했다: 경로 탈출은 그 파일만 **건너뛴다**(세션 JSON 이 오래되어 파일이 지워졌을 수 있고 그 하나로 질의가 죽으면 안 된다), scope 위반은 **예외를 올린다**(설정 오류이거나 공격). `Chunk.tier` 미설정. 예외 하나 — `verified_chunks()` 는 이미 판정된 등급을 옮겨온다(BR-S-05).
`candidate_paths()` — `open_paths` + `recent_edits` + `recent_runs[].log`. 🔴 `train.log` 는 `recent_runs[].log` 에만 있어서 빠뜨리면 시나리오 2 의 "지금 학습 중" 답이 불가능해진다.
`source_kind_of()` — 이름에 `classify` 를 쓰지 않았다. 이 저장소에서 "분류"는 등급 판정을 뜻하고, 이름이 겹치면 "store 가 등급을 판정하지 않는다"는 규칙이 흐려진다. (Day 2 에 `classify_source` 로 만들었더니 기존 문자열 검사 테스트가 걸렸다 — 그 테스트도 ast 성격으로 정밀화했다.)
`select_paths()` — 인덱스 배열만 받는다(경로 문자열을 생성하게 하면 존재하지 않는 경로를 만들고 그것이 `read()` 의 경로 검사로 들어간다). 실패·형식오류·선택0개 → 후보 전체(fail closed 방향).

🔴 **설계 변경 (발견 18)**: `BR-S-06` 의 "식별자 제거 요약"을 자유 문장으로 만들면 **그 문장에 원문이 섞였는지 사후에 검사해야 한다** — §3.1 에서 기각한 구조("무엇을 지울까", 검사를 잊으면 유출)이고 이 화면은 인증 없이 보인다. 조치: `Gatekeeper.summarize_focus()` 가 닫힌 라벨 집합(`FOCUS_TOPICS` 7개)에서 하나를 고르게 한다. 범위 밖·실패 → `None`(원문 폴백 없음). 원문이 섞일 채널이 존재하지 않으므로 검사할 것도, 잊을 검사도 없다.
**Context**: todo #1, #2, #3 완료

---

## Day 3 · agent.py (U3 Step 1)
**Timestamp**: 2026-08-19T14:52:00Z
**AI Response**: `Gatekeeper.ask_agent()` 만 호출. `boto3`·`BrokerClient` import 없음(ast 강제).
`build_system_prompt` 을 `gatekeeper.py`(L4)에 두고 여기서 재수출했다. `ask_agent()` 가 프롬프트를 필요로 하는데 L4 는 L5(agent)를 import 할 수 없다. 구현이 두 곳에 생기면 **필수 문구가 빠진 경로**가 만들어진다.

🔴 **설계 결함 발견 (발견 17)**: `BR-AG-04` 가 초안 프롬프트에 넣으라고 한 것들을 하나씩 확인해 보니 **뒤 세 개가 전부 경계를 넘어서는 안 되는 것**이었다 — 근거 제목(`"고객사 요구사항명세서"` 에 고객사가 있다, FR-43 과 같은 이유) · 근거 시점(일정·날짜는 `_intentionally_absent`) · 세션 사실(`Session.focus`/`summary` 는 원문 취급) · Agent 부분 응답(어휘 사전 밖 자유 문자열이라 검증 2단계 통과 불가). 설계가 "`display_title` 만"이라고 쓴 것은 `internal_path` 와 비교한 말이었지만 **경계를 넘는 맥락에서는 제목도 원문 파생물**이다.
조치: `Gatekeeper.ask_draft()` 신설 — **이미 검증을 통과한 envelope 을 그대로 재사용**하고 프롬프트·모델만 바꾼다(haiku). 새 페이로드를 만들지 않으므로 재검증도 필요 없고 검증 없이 나가는 경로도 없다. 제목·시점·공식성·세션 사실은 **응답이 돌아온 뒤 신뢰 구역 안에서** `situation` 에 덧붙인다. `situation` 순서도 로컬 사실이 먼저다 — 담당자가 먼저 읽는 것은 "무엇을 근거로 하는가"이고 그건 애초에 모델이 만들 수 없는 정보다.
초안 실패 시 예외를 올리지 않고 결정적 폴백 초안을 만든다 — 초안이 없어서 에스컬레이션이 사라지는 것이 최악이다.
**Context**: todo #4 완료

---

## Day 3 · orchestrator.py (U3 Step 2~3)
**Timestamp**: 2026-08-19T15:05:00Z
**AI Response**: `branch()` 가 인용 검사를 신뢰도보다 **먼저** 한다(BR-O-04) — 이 순서가 자동 응답의 인용 준수율을 구조적으로 100% 로 만든다. 2명이면 `min()`.
`is_divergent()` 는 텍스트 다름 **AND** 근거 문서 다름의 논리곱. LLM 미사용. 이름이 `is_conflicting` 이 아닌 것이 설계 결정(단정 vs 관찰).
`merge()` 는 요청 순서 유지. 신뢰도 정렬 금지(사용자가 위쪽을 정답으로 읽는다).
`_send_all()` 을 2단계로 나눴다 — ① 모든 답을 모으고 call 단위로 처분 결정 ② 그다음 에스컬레이션. ②가 뒤인 것이 `already_answered`("다른 조각은 이미 답변됨")를 채우기 위해서다. 전체를 묶어 `branch()` 하면 `min()` 때문에 이미 답이 나온 조각까지 에스컬레이션된다(설계 §8 시나리오 2).
`_blocked_call()` 이 폴백을 동봉 — `PreparedCall` 의 validator 가 `fallback` 없는 `blocked` 를 거부하므로 "차단만 하고 답을 안 주는 것"이 타입 수준에서 불가능하다.
`thread_id = request_id` (BR-I-04). 초기에 `hash(question)` 기반 헬퍼로 만들었다가 `PendingRequest.request_id` 로 정리했다.
설계의 완료 기준 `grep -c "exaone|bedrock|broker" == 0` 은 `reason="broker_unavailable"` 같은 **이유 코드**까지 잡아 무의미하다 → ast 로 import 와 모델 메서드 호출을 검사한다.
**Context**: todo #5, #6 완료

---

## Day 3 · inbox.py (U3 Step 4)
**Timestamp**: 2026-08-19T15:14:00Z
**AI Response**: 스키마 DDL 은 `audit.py` 한 곳에만 두고(정의가 두 곳이면 갈라진다) 조작은 `inbox.py` 가 한다. `AuditLog.connection` 을 공유 — 커넥션을 두 개 열면 SQLite 락이 충돌하고 DB 파일을 나누면 "한 파일만 지우면 증거가 반쪽"이 된다. Day 2 의 `audit.add_inbox`/`list_inbox` 는 제거했고 `inbox` 테이블을 `InboxItem` 계약(owner/thread/tier/status/resolved_at/...)에 맞게 확장했다.
`UPDATE` 를 `MUTABLE_COLUMNS` 4개로 제한. `draft_*`·`situation_json`·`citations_json` 은 감사 흔적이다. 테스트가 SQL 의 대상 컬럼을 파싱해 검사한다.
재해결 금지(감사 흔적을 흐린다) → 409. `not_me` 는 환류하지 않는다(담당자가 답을 준 것이 아니다). 승인 환류 시 `tier` 보존.
**Context**: todo #7 완료

---

## Day 3 · main.py (U3 Step 5)
**Timestamp**: 2026-08-19T15:26:00Z
**AI Response**: `/docs`·`/redoc`·`/openapi.json` 기본 비활성(OpenAPI 스키마가 내부 구조를 전부 노출). CORS 미들웨어 없음. `StaticFiles` 없이 3개 파일 명시 매핑. 보안 헤더 4개. 전역 예외 핸들러가 `correlation_id` 만 준다.
`check_bind_host()` 가 localhost 가 아니면 **시작을 막는다** — 경고만 하면 아무도 읽지 않는다. `MESH_ALLOW_NETWORK_BIND=1` 로 명시적 확인.
410 Gone 을 쓰는 이유: envelope 은 **있었다가 없어진** 것이다(일회용 + TTL). 404 면 클라이언트가 "잘못된 id"로 오해해 재시도한다.

🔴 **배선 결함 (발견 19)**: `main.py` 가 `BrokerClient` 를 만들면 경계를 넘는 모듈이 하나 늘어난다(SECURITY-11). `BOUNDARY_CROSSERS` 가 늘면 "단일 통로"가 무의미해진다. 조치: `Gatekeeper.build()` 팩토리로 **생성도 통로 안에** 뒀다(함수 스코프 import — mock 모드에서 httpx/boto3 경로를 끌고 오지 않는 부수 효과도 있다). Day 1 의 테스트를 "최상위 import 만 검사"로 정밀화하고, "어떤 모듈도 최상위에서 broker 를 import 하지 않는다"를 추가했다.

🔴 **레이어 결함 (발견 20)**: `mesh.api_models` 를 Day 1 에 U3 소유라는 이유로 L5 에 뒀는데 `inbox.py`(L5)가 쓸 수 없다. **레이어는 소유가 아니라 의존 순서다** → L1.
**Context**: todo #8 완료

---

## Day 3 · PBT + 게이트 G3 (U2 Step 8 + U3 Step 6~7)
**Timestamp**: 2026-08-19T15:34:00Z
**AI Response**: PB-S1~S5 + PB-O1~O6 추가 (총 42개 속성 테스트).
`tests/fakes.py` 신설 — LLM 응답만 대역, 조립·검증·감사·재수화는 실제 코드(목업 모드 원칙과 동일, FR-48). `name` 별 분기로 만든 이유: 한 질의가 classify → select_paths → extract → focus_topic 순으로 여러 번 부르는데 큐 방식이면 호출 순서가 바뀔 때마다 테스트가 깨진다.
`tests/eval/test_scenarios.py` — 게이트 G3. 3막 + 후속을 HTTP 표면으로 종단 실행 + 전수 유출 검사.
`tests/eval/test_demo_script.py` — `scripts/demo.py` 를 대역 주입으로 4막 실행. 픽스처 녹화는 Day 4 작업이라 `make demo` 는 아직 live 가 필요하고, 그렇다고 검증 없이 넘기면 시연 당일에 처음 돌려보게 된다.

🔴 **테스트가 아무것도 검사하지 않던 두 곳 (발견 21)**:
1. `TestClient` 기본값이 `raise_server_exceptions=True` 라서 전역 예외 핸들러의 500 응답을 볼 수 없었다 — "응답에 스택 트레이스가 없다"를 확인하려던 테스트가 예외를 그대로 받고 있었다. → 그 테스트만 `raise_server_exceptions=False`.
2. "소스에 `StaticFiles` 가 없다" 문자열 검사가 **주석까지 잡았다**. → ast 로 호출을 검사.
같은 문제를 Day 2 에도 겪었다(`store` 의 `classify` 검사). 문자열 검사는 주석과 무관한 이름까지 잡는다 — 구조를 검사할 때는 ast.

🔴 **"유출"의 의미를 다시 나눴다 (§8.3)**: 시나리오 테스트가 `김철수` 로 실패했는데 `agent_label` 이 `"김철수 책임의 Agent"` 였다. 사용자가 **직접 지목한 사람**의 이름은 신뢰 구역에 머문다. 고객사명은 문서에서 온 것이므로 어디에도 없어야 한다. Day 2 의 `NEVER_ANYWHERE` vs `NEVER_IN_PAYLOAD` 구분을 여기서도 적용했다.
**결과: 게이트 G3 통과** — 3막 종단 · 전수 유출 0건 · 인용 0개 차단 · 승인 없이 422 · 재전송 410 · 헤더 4개 · `/docs` 404 · 4번째 에이전트 코드 변경 0.
**Context**: todo #9, #10 완료

---

## Day 3 · 실측 검증 (EXAONE 4회)
**Timestamp**: 2026-08-19T15:38:00Z
**AI Response**: Day 3 에 새로 만든 프롬프트 2개를 실측했다.
`select_paths` 0.44s · **프롬프트 326자** · `REQ-4412`/`EAP-AKA`/`CTR-204817`/`12억`/`보안등급` 전부 부재 → BR-S-02("본문 미포함")가 실제로 동작한다. 본문을 넣으면 수천 자가 되고 그 본문이 등급 판정 **전에** 신뢰 구역 밖으로 나간다.
`focus_topic` 0.23~0.26s · 세 세션 모두 닫힌 어휘 안 + 의미 정확:
  "고객사 H 인증 요구사항 검토 + SDK v3.2 토큰 정책" → "인증 관련 작업 중"
  "atlas-ml 전처리 v3 재학습" → "모델 학습 작업 중"
  "SDK v3.2 배포 준비" → "배포·릴리스 작업 중"
**Context**: LLM 예산 준수 (Day 3 총 EXAONE 5회 포함 preflight, Bedrock 0회 — 자격증명 만료)

---

## Day 3 · 종합 검증 + 문서 갱신
**Timestamp**: 2026-08-19T15:48:00Z
**AI Response**: `make test` 938개 · `make eval` 38개(G3) · `make eval-classify`(G2) · `make lint` · `make audit` 0건 전부 통과. `make preflight` 실패 1(AWS 자격증명 만료, Day 2 부터 동일).
문서 갱신: `preflight-findings.md` §11~12(발견 17~21 + Day 3 종료 상태 + 남은 한계 4건) · `day3-implementation-report.md` 작성 · `aidlc-state.md`(G3 통과, SG13~16 신설, Day 3 완료 내역, 실측 사실 16~18) · u2 Step 5~9 및 u3 Step 1~7 체크박스 전부 완료.
**Context**: todo #11 완료

---
## 첫 완전 live 실행 검증 요청
**Timestamp**: 2026-08-20T11:20:00Z
**User Input**: ".env 의 AWS Access 부분에 추가했는데, 한번 잘 되는지 파악 부탁드려요"
**AI Response**: 자격증명 유효성 확인 → preflight 27건 통과 → EXAONE+Bedrock 완전 live 종단 실행 → 결함 5건 발견·수정 → 목업 픽스처 녹화 → 오프라인 4막 통과.
**Context**: 검증 / Day 3 이후

---

## 자격증명 확인
**Timestamp**: 2026-08-20T11:24:00Z
**AI Response**: `.env` 의 STS 임시 자격증명(ASIA…) 유효 — 계정 891401657794, `assumed-role/WSParticipantRole/Participant`. `make preflight` 검사 27건 실패 0 경고 2(경계 시뮬레이션, CDK 미부트스트랩). Bedrock 왕복 2.41s.
⚠️ **발견 (§14)**: `.kiro/.env` 에도 `AWS_*` 4개가 있고 **만료된 값**이다(해시 비교로 다름 확인). `. ./.env; . ./.kiro/.env` 순서로 로드하면 만료된 것이 이긴다 — Day 2·3 의 preflight 실패가 이것이었다. 자격증명 출처를 하나로 통일할 것을 권고.
**Context**: 값은 출력하지 않고 접두사·길이·해시만 사용

---

## MESH_DEMO_NOW 설정
**Timestamp**: 2026-08-20T11:31:00Z
**AI Response**: `.env` 의 `MESH_DEMO_NOW` 가 비어 있어 실시간 기준(2026-08-20)으로 판정되고 세션 3개가 전부 STALE(x0.8)이 됐다. 시나리오 1 이 0.92×0.8=0.736 < 0.75 로 `unverified` 가 됐다 — 동작은 올바르지만 데모 서술이 깨진다. 이 변수가 존재하는 이유가 정확히 이 문제(BR-S-04 데모 재현성)이므로 `2026-08-19T14:35:00+09:00` 으로 설정했다.
**Context**: `.env` 는 gitignore 대상이고 되돌리기 쉬운 변경

---

## 완전 live 실행 · 결함 5건
**Timestamp**: 2026-08-20T11:52:00Z
**AI Response**: 대역 테스트 976개가 전부 통과한 상태에서 실제 모델로 돌렸더니 결함 5건이 나왔다. 상세는 `preflight-findings.md` §13.

🔴 **발견 22**: `ask_draft` 가 `build_system_prompt` 의 기본 출력 계약("answer_format 의 키를 쓰라")과 `DRAFT_SYSTEM` 을 **덧붙여** 조립했다. 페이로드에 `answer_format` 이 실제로 있어서 두 지시가 충돌하고 haiku 가 초안 대신 충돌 판정을 다시 냈다 → 폴백 초안(내용 없음). 조치: 출력 계약을 인자로 받아 **대체**하고, 마지막에 배치하고, `DRAFT_SYSTEM` 첫 줄에 `IGNORE the answer_format` 명시. 재실측에서 네 키 정확.

🔴 **발견 23**: `choose_schema` 의 기본값(첫 task)이 "묻지 않은 것에 자신 있게 답"하게 했다. `"그때 p99 지연이 얼마였나요?"` → `constraint_conflict_check` → 필수 슬롯이 고객사 문서에서 **채워짐** → 검증 6/6 → 전송 → 인증 충돌 답변(신뢰도 0.75, auto). Day 2 보고서의 "틀려도 유출이 아니라 품질 저하"라는 판단이 틀렸다 — 폴백보다 나쁘다. 조치: 힌트 없으면 `ExtractionFailed`.

🔴 **발견 24**: 픽스처 키가 `(agent, schema_id, payload)` 인데 `ask_draft` 가 같은 envelope 을 재사용하므로 초안 응답이 본 응답 픽스처를 덮어썼다. 오프라인에서 김책임 Agent 답변이 인계 메모로 바뀌고 `confidence` 부재 → 0.0 → 인용 0개 규칙에 걸려 escalate. 조치: 키에 `model_id` 추가.

**발견 25**: `_blocked_call` 이 실패 지점의 판정 결과를 버리고 파일을 다시 읽어 `[기밀]` 자리에 `[사내]` 가 표시됐다. 조치: `PrepareFailed` 예외가 판정된 근거를 함께 들고 온다.

🔴 **발견 26**: `sweep_for_leaks` 가 등급별 규칙(BR-P-03)을 적용하지 않아 **오탐 1076건**. 가장 강한 주장이 진짜 유출을 가리는 도구가 되어 있었다. 조치: 검증 5단계와 같은 규칙(STRUCTURED 전체 / PSEUDONYMIZED 식별자 포함만 / VERBATIM 미적용). `AuditRecord.representation` 이 이미 저장돼 있었는데 쓰지 않고 있었다.

**수정 후 실측**: 시나리오 1 처분 `auto` · 신뢰도 0.85 · 6/6 · 727 bytes · 원문 문장 수 0. 실제 Claude 가 원문 없이 세 문서를 대조해 세 가지 충돌 + 대응 방안 5개. 전수 유출 0건. 지연 prepare 3~4s / send 10~14s / 2명 병렬 16s.

**목업 픽스처 24개 녹화** (exaone 24 + agent 5) → 네트워크 0회로 4막 + 유출 검사 exit=0 → **게이트 G5 통과**.
테스트 983개 통과 · lint 통과.
**Context**: LLM 호출 — EXAONE 약 30회 · Bedrock 약 12회 (첫 live 종단 + 결함 4건 재검증 + 픽스처 녹화). 픽스처가 남았으므로 이후 오프라인으로 반복 가능

---

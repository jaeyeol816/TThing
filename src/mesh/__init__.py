"""대리 에이전트 메시 (Delegate Agent Mesh).

지식을 가진 사람 앞에 대리 에이전트를 세우고, 질문을 사람이 아니라 에이전트에게 보낸다.
기밀 자료는 신뢰 구역 안에서만 읽히고, 외부 AI에는 데이터가 아니라 문제의 구조만 나간다.

경계를 넘는 통로는 정확히 2개다:
  - gatekeeper.Gatekeeper.ask_agent()   검증 통과 + 사용자 승인이 전제조건
  - audit.AuditLog.mirror()             위 페이로드의 사본

다른 어떤 모듈도 경계 밖 클라이언트를 import 하지 않는다.
tests/unit/test_import_boundary.py 가 ast 로 파싱해 강제한다.
"""

__version__ = "0.1.0"

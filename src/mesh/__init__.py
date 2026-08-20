"""MIA; But AI got you.

이름의 뜻: 담당자는 **M**issing **I**n **A**ction 이다 — 회의 중이거나, 휴가거나,
답할 시간이 없다. 그래도 그 사람의 지식은 필요하다. **But AI got you** —
그 사람 앞에 세워 둔 대리 에이전트가 답한다.

지식을 가진 사람 앞에 대리 에이전트를 세우고, 질문을 사람이 아니라 에이전트에게 보낸다.
기밀 자료는 신뢰 구역 안에서만 읽히고, 외부 AI에는 데이터가 아니라 문제의 구조만 나간다.

패키지 이름이 `mesh` 로 남아 있는 이유: 이름을 바꾸는 것과 import 경로를 바꾸는 것은
다른 일이다. 후자는 1,100개 테스트와 레이어 검사(`test_import_boundary.py`)를 건드리며,
바꿔서 얻는 것이 없다.

경계를 넘는 통로는 정확히 2개다:
  - gatekeeper.Gatekeeper.ask_agent()   검증 통과 + 사용자 승인이 전제조건
  - audit.AuditLog.mirror()             위 페이로드의 사본

다른 어떤 모듈도 경계 밖 클라이언트를 import 하지 않는다.
tests/unit/test_import_boundary.py 가 ast 로 파싱해 강제한다.
"""

__version__ = "0.1.0"

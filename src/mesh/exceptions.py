"""예외 계층.

**모든 예외의 처리 결과가 Tier.SECRET 또는 신뢰 구역 내 폴백으로 귀결된다** (BR-G-01).

예외를 추가할 때는 "이게 어느 안전한 상태로 귀결되는가"를 docstring 에 함께 쓴다.
그 답이 없으면 그 예외는 fail-closed 설계를 깨뜨린다.

금지 패턴:
    except Exception:
        return Tier.OPEN        # 절대 금지
    except Exception:
        pass                    # 절대 금지 (판정 없이 진행)
"""

from __future__ import annotations


class MeshError(Exception):
    """모든 앱 예외의 기반."""


# ── 게이트키퍼 ────────────────────────────────────────────────────────


class GatekeeperError(MeshError):
    """ask_agent() 전제조건 위반.

    귀결: 호출자에게 전파 -> 500. 이건 코드 버그이므로 조용히 폴백하지 않는다.
    검증 미통과 또는 승인 없는 페이로드가 경계에 도달한 상황이다.
    """


class ExtractionFailed(GatekeeperError):
    """구조 추출 실패 — 필수 슬롯 미충족 또는 2회 재시도 후 JSON 파싱 실패.

    귀결: Agent 를 부르지 않고 answer_in_zone() 폴백. 감사 레코드 없음.
    시나리오 3 후속 질문(성능 수치)이 정확히 이 경로다 — 어휘 사전에
    슬롯이 없으므로 채울 수 없다.
    """


class ValidationBlocked(GatekeeperError):
    """검증 6단계 중 하나 이상 실패.

    귀결: 전송 차단 -> answer_in_zone() 폴백. 감사 레코드 없음.
    """


# ── 모델 호출 ─────────────────────────────────────────────────────────


class ExaoneUnavailable(MeshError):
    """신뢰 구역 LLM 호출 실패 — 타임아웃, HTTP 오류, 2회 재시도 후 파싱 실패.

    귀결: 등급 판정 맥락에서는 Tier.SECRET 으로 간주 (BR-C-05).
          구조 추출 맥락에서는 ExtractionFailed 로 승격.
          폴백 답변 생성 맥락에서는 사용자에게 오류 고지.
    """


class BrokerError(MeshError):
    """경계 밖 호출 실패 — 브로커 HTTP 오류, Bedrock 오류,
    또는 응답에 revalidated != True (브로커가 재검증을 안 했다는 신호).

    귀결: answer_in_zone() 폴백 + 사용자에게 품질 저하 고지.
    """


class FixtureMissing(ExaoneUnavailable):
    """목업 모드에서 픽스처 키를 찾지 못했다.

    귀결: 명시적 실패. 조용히 기본값을 반환하지 않는다 —
    그러면 데모 리허설에서 누락을 발견할 수 없다. **ERROR 로그에 어떤 키가
    없는지와 녹화 명령이 함께 남는다.**

    ──────────────────────────────────────────────────────────────────
    왜 `ExaoneUnavailable` 의 하위인가
    ──────────────────────────────────────────────────────────────────

    처음에는 `MeshError` 를 직접 상속했다. 그 결과 **파이프라인의 fail-closed
    처리를 전부 뚫고 올라왔다** — `classify` 는 실패를 `SECRET` 으로 흡수하고
    `select_paths` 는 후보 전체를 읽고 `answer_in_zone` 은 문구로 대체하도록
    설계돼 있는데, 그 `except` 절이 전부 `ExaoneUnavailable` 을 보고 있었기
    때문이다. 그래서 녹화되지 않은 질문 하나가 **질의 전체를 500 으로** 만들었다.

    의미로 보면 이것은 "신뢰 구역 모델을 지금 부를 수 없다" 의 한 종류다.
    목업 모드에서 픽스처가 없다는 것은 그 모드에서 모델이 없다는 뜻이다.
    분류를 맞추면 이미 설계돼 있는 안전한 경로들이 그대로 동작한다.
    """


# ── 경로와 범위 ───────────────────────────────────────────────────────


class PathEscapeError(MeshError):
    """MESH_DATA_ROOT 밖을 가리키는 경로.

    귀결: 읽기 거부 -> 그 파일 없이 진행하거나 400.
    세션 JSON 은 사람이 편집하므로 ../../../etc/passwd 가 들어갈 수 있다.
    """


class ScopeViolationError(MeshError):
    """에이전트의 knowledge_scope 밖 경로.

    귀결: 읽기 거부. 에이전트 간 지식 격리 —
    김책임 Agent 가 박선임 파일을 읽지 못하게 한다.
    """


class ConfigError(MeshError):
    """설정 검증 실패.

    귀결: **앱 시작 실패 (fail fast)**. 잘못된 설정으로 도는 것보다
    안 뜨는 게 낫다. 특히 live 모드인데 토큰이 없는 경우.
    """

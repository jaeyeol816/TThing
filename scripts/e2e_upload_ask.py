#!/usr/bin/env python3
"""종단 실측 — "내가 기밀 문서를 올리고, 다른 사람이 그 문서에 대해 묻는다".

    make run          # 다른 터미널에서 먼저 띄운다 (live 모드)
    make e2e

**테스트가 아니라 실측 도구다.** 떠 있는 서버에 실제 HTTP 로 붙어 화면이 하는 것과
같은 순서로 호출하고, 경계를 넘은 페이로드와 감사 로그에서 원문 조각을 찾는다.
같은 흐름을 대역으로 고정한 것은 `tests/eval/test_scenarios.py` 다.

──────────────────────────────────────────────────────────────────────
왜 검사 대상이 "최종 답변"이 아닌가
──────────────────────────────────────────────────────────────────────

최종 답변에는 실제 이름이 **의도적으로** 들어간다. 사외 Agent 는 `REQ_A` 로 답하고,
사내망 안에서 재수화(관문 ③)가 실제 제목으로 되돌린다. 되돌리지 않으면 사람이 읽을
수 없는 답이 된다.

막는 것은 **원문이 사외 모델에 도달하는 것**이다. 그래서 검사 대상은
`prepare` 가 보여준 페이로드 전문과 감사 로그다.

──────────────────────────────────────────────────────────────────────
live 모드가 필요하다
──────────────────────────────────────────────────────────────────────

이 스크립트는 **매번 새 문서를 만들어** 올린다 (파일명에 타임스탬프). 목업 모드는
녹화된 픽스처를 키로 찾아 재생하므로, 처음 보는 문서에 대한 픽스처가 있을 수 없다.
그래서 시작할 때 `/api/health` 로 모드를 확인하고 목업이면 즉시 멈춘다 —
한참 진행한 뒤 알 수 없는 400 을 보는 것보다 낫다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DEFAULT = "http://127.0.0.1:8080"
OWNER = "person:kim"
ASKER = "person:park"

QUESTION = "고객사 요구사항과 우리 SDK 토큰 갱신 방식이 충돌하나요?"

#: 이 문서에만 있는 문자열. 경계를 넘는 어디에도 없어야 한다.
#: `무상태` 는 시드 코퍼스와 겹치지 않는 표현을 일부러 골랐다.
SECRETS_ONLY = ("고객사 H", "REQ-4412", "12억", "2026-11-30", "위약", "무상태")

DOCUMENT = """# title: E2E 임시 계약 메모
# as_of: 2026-08-19

고객사 H 는 재계약 조건으로 총액 12억 원 규모를 제시했다.
요구사항 REQ-4412 에 따라 토큰 갱신은 30분 주기 재인증을 강제한다.
우리 SDK 는 무상태 토큰 갱신을 쓰므로 이 제약과 충돌한다.
납기는 2026-11-30 이며 위약 조항이 붙는다.
"""


# ══════════════════════════════════════════════════════════════════════
# 출력
# ══════════════════════════════════════════════════════════════════════


def head(text: str) -> None:
    print(f"\n\033[1m── {text}\033[0m")


def item(mark: str, text: str) -> None:
    print(f"  {mark} {text}")


def dump(obj: object) -> None:
    for line in json.dumps(obj, ensure_ascii=False, indent=2).splitlines():
        print(f"    {line}")


# ══════════════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════════════


class HttpFailed(RuntimeError):
    """서버가 4xx/5xx 를 냈다. **본문을 함께 들고 온다.**

    본문이 없으면 "400 Bad Request" 만 보고 원인을 짐작해야 한다.
    이 앱의 오류 응답은 `error` + `detail` + `correlation_id` 를 주므로
    그걸 그대로 보여주는 것이 가장 빠른 진단이다.
    """


def _checked_url(base: str, path: str) -> str:
    """`http`/`https` 만 허용한다.

    `base` 는 명령행 인자다. 검사하지 않으면 `file:///etc/passwd` 같은 값이
    `urlopen` 에 그대로 들어간다 (ruff S310). 이 스크립트는 로컬 서버에만
    붙으므로 스킴을 좁히는 것이 맞고, 검사를 억제하는 것은 답이 아니다.
    """
    url = base + path
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in {"http", "https"}:
        raise HttpFailed(f"http/https 만 허용한다: {url!r}")
    return url


def call(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if data else {}
    request = urllib.request.Request(  # noqa: S310 — 스킴을 _checked_url 이 좁혔다
        _checked_url(base, path), data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = f"{parsed.get('error')}: {parsed.get('detail')}"
        except json.JSONDecodeError:
            pass
        raise HttpFailed(f"{method} {path} → {e.code}\n    {detail}") from e
    except urllib.error.URLError as e:
        raise HttpFailed(
            f"{method} {path} → 연결 실패 ({e.reason}).\n    서버가 떠 있는지 확인하라: make run"
        ) from e
    return json.loads(raw) if raw else {}


# ══════════════════════════════════════════════════════════════════════
# 단계
# ══════════════════════════════════════════════════════════════════════


def require_live(base: str) -> None:
    head("⓪ 환경 확인")
    health = call(base, "/api/health")
    item("·", f"EXAONE {health['exaone_mode']} · Agent {health['agent_transport']}")
    item("·", f"신뢰 구역 LLM {health['trusted_zone_llm_base_url']}")
    if health["trust_boundary_simulated"]:
        item("⚠", "신뢰 경계가 시뮬레이션이다 (화면 헤더에도 표시된다)")

    mocked = [
        name
        for name, value in (
            ("EXAONE_MODE", health["exaone_mode"]),
            ("AGENT_TRANSPORT", health["agent_transport"]),
        )
        if value == "mock"
    ]
    if mocked:
        raise SystemExit(
            f"\n✗ {' · '.join(mocked)} 가 목업이다.\n"
            "  이 스크립트는 매번 새 문서를 만들어 올리므로 픽스처가 있을 수 없다.\n"
            "  live 로 띄우고 다시 실행하라:  make run\n"
            "  네트워크 없이 종단을 보려면:   make eval  (대역으로 같은 흐름을 고정한다)"
        )


def upload(base: str) -> dict:
    head("① 업로드 — 소유자가 기밀 메모를 올린다")
    result = call(
        base,
        "/api/documents",
        method="POST",
        body={
            "owner": OWNER,
            "filename": f"e2e-contract-{int(time.time())}.md",
            "content": DOCUMENT,
            "attach_to_session": True,
        },
    )
    doc = result["document"]
    item("·", f"{doc['filename']} · {doc['size_bytes']}B · 질의 후보 {doc['attached']}")
    for evidence in doc["tier_evidence"]:
        item("·", f"근거(규칙 {evidence['rule']}) {evidence['reason']}")
    for warning in result["warnings"]:
        item("·", warning)

    if doc["tier"] != "secret":
        raise SystemExit(f"\n✗ 기밀로 판정되지 않았다: {doc['tier']}")
    if not doc["tier_evidence"]:
        raise SystemExit("\n✗ 판정에 근거가 없다 — 블랙박스다")
    item("✓", "기밀로 판정됨 (근거가 응답에 포함)")
    return doc


def prepare(base: str) -> dict:
    head("② 질문 — 다른 사람이 소유자에게 묻는다 (prepare)")
    plan = call(
        base,
        "/api/ask/prepare",
        method="POST",
        body={"asker": ASKER, "question": QUESTION, "targets": [OWNER]},
    )
    item("·", f"request_id {plan['request_id']}")
    item("·", f"agents_notified {plan['agents_notified']}  ← 상대를 깨우지 않았다")
    if plan["upgraded_tier"]:
        item("·", f"등급 상향 {plan['upgraded_tier']} — {plan['upgrade_reason']}")
    if plan["agents_notified"]:
        raise SystemExit("\n✗ prepare 가 상대에게 알렸다 (BR-O-03 위반)")
    return plan


def inspect_payloads(plan: dict, *, show: bool) -> list[str]:
    head("③ 유출 검사 — 경계를 넘는 페이로드를 본다")
    failures: list[str] = []
    envelope_ids: list[str] = []

    for callinfo in plan["calls"]:
        target = callinfo["target_entity_id"]
        preview = callinfo.get("preview")
        if not preview:
            item("·", f"{target}: 차단됨 ({callinfo.get('blocked_reason')})")
            continue
        envelope_ids.append(callinfo["envelope_id"])

        payload = preview["payload_pretty"]
        hits = [t for t in SECRETS_ONLY if t in payload]
        if hits:
            failures.append(f"페이로드에 원문이 있다: {hits}")
        if preview["verbatim_sentence_count"] != 0:
            failures.append(f"원문 문장 {preview['verbatim_sentence_count']}개")
        if not preview["validation_summary"].startswith("6/"):
            failures.append(f"검증 미통과: {preview['validation_summary']}")

        item(
            "·",
            f"{target}: {preview['tier']}/{preview['representation']} · "
            f"검증 {preview['validation_summary']} · 원문 문장 "
            f"{preview['verbatim_sentence_count']}개 · {preview['size_bytes']}B",
        )
        item("·", f"포함되지 않은 것: {', '.join(preview['excluded_categories']) or '(없음)'}")
        for check in preview["checks"]:
            mark = "✓" if check["passed"] else "✗"
            item(" ", f"{mark} {check['stage']:8s} {check['detail']}")
        if show:
            print()
            dump(json.loads(payload))

    if not envelope_ids:
        failures.append("전송할 봉투가 없다 — prepare 가 전부 차단됐다")
    for failure in failures:
        item("✗", failure)
    if not failures:
        item("✓", "원문 0개 — 경계를 넘는 것에 기밀 조각이 없다")
    return envelope_ids if not failures else []


def send(base: str, plan: dict, envelope_ids: list[str]) -> dict:
    head("④ 전송 — 사람이 승인한 뒤에만 (send)")
    result = call(
        base,
        "/api/ask/send",
        method="POST",
        body={
            "request_id": plan["request_id"],
            "envelope_ids": envelope_ids,
            "approved_by": ASKER,
        },
    )
    merged = result["merged"]
    item("·", f"처분 {merged['disposition']} · 소요 {result['elapsed_seconds']}s")
    if merged["divergent"]:
        item("·", f"divergent — {merged['divergence_note']}")
    for answer in merged["answers"]:
        item(
            "·",
            f"{answer['agent_label']}: {len(answer['text'])}자 · "
            f"인용 {len(answer['citations'])}건 · 신뢰도 {answer['confidence']} · "
            f"사외 Agent {answer['used_external_agent']}",
        )
        for citation in answer["citations"]:
            item(" ", f"근거: {citation['display_title']}  [{citation['tier']}]")
        if answer["unresolved_refs"]:
            item("✗", f"재수화되지 않은 기호: {answer['unresolved_refs']}")
    return result


def check_rehydration(result: dict) -> list[str]:
    head("⑤ 재수화 확인 — 답변은 사람이 읽을 수 있어야 한다")
    failures: list[str] = []
    for answer in result["merged"]["answers"]:
        if not answer["text"].strip():
            failures.append("답변이 비었다")
        if answer["unresolved_refs"]:
            failures.append(f"기호가 남았다: {answer['unresolved_refs']}")
        if answer["used_external_agent"] and not answer["citations"]:
            failures.append("사외 답변에 인용이 없다 — 근거를 대조할 수 없다")
    for failure in failures:
        item("✗", failure)
    if not failures:
        item("✓", "기호가 실제 이름으로 되돌아왔다 (신뢰 구역 안에서만)")
        item("·", "답변에 실제 이름이 있는 것은 **의도된 동작**이다 (설계 §3.6)")
    return failures


def check_audit(base: str) -> list[str]:
    head("⑥ 감사 로그 — 원문 문구로 검색하면 0건")
    failures: list[str] = []
    for phrase in SECRETS_ONLY:
        rows = call(base, "/api/audit?q=" + urllib.parse.quote(phrase))["rows"]
        mark = "✓" if not rows else "✗"
        item(mark, f"'{phrase}' → {len(rows)}건")
        if rows:
            failures.append(f"감사 로그에 {phrase} 이 남았다")

    total = call(base, "/api/audit")["total_records"]
    item("·", f"감사 레코드 총 {total}건")
    if total == 0:
        failures.append("감사 레코드가 없다 — 전송이 기록되지 않았다")

    # 구조화된 값은 나갔다. 이 대비가 이 프로젝트의 주장 전체다.
    structured = call(base, "/api/audit?q=session_binding")["rows"]
    item("·", f"'session_binding' → {len(structured)}건  ← 구조는 나갔다")
    if not structured:
        failures.append("구조화된 값도 0건이다 — 아무것도 나가지 않았거나 검색이 깨졌다")
    return failures


def delete(base: str, doc: dict) -> None:
    head("⑦ 정리 — 올린 문서를 지운다")
    owner = urllib.parse.quote(OWNER)
    try:
        call(base, f"/api/documents/{doc['document_id']}?owner={owner}", method="DELETE")
        item("✓", "삭제됨 (질의 후보에서도 빠졌다)")
    except HttpFailed as e:
        item("⚠", f"삭제하지 못했다: {e}")


# ══════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="업로드 → 타인 질문 종단 실측")
    parser.add_argument("base", nargs="?", default=BASE_DEFAULT)
    parser.add_argument("--show-payload", action="store_true", help="페이로드 전문 출력")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    require_live(base)
    doc = upload(base)
    failures: list[str] = []
    try:
        plan = prepare(base)
        envelope_ids = inspect_payloads(plan, show=args.show_payload)
        if not envelope_ids:
            failures.append("페이로드 검사 실패 — 전송하지 않았다")
        else:
            result = send(base, plan, envelope_ids)
            failures += check_rehydration(result)
            failures += check_audit(base)
    finally:
        # 실패해도 지운다. 남기면 `data/corpus/*/uploads/` 에 검사용 파일이 쌓이고,
        # 세션의 `open_paths` 에 죽은 참조가 남는다.
        delete(base, doc)

    print()
    if failures:
        print(f"\033[1;31m종단 실측 실패 — {len(failures)}건\033[0m")
        for failure in failures:
            print(f"  ✗ {failure}")
        return 1
    print("\033[1;32m종단 실측 통과\033[0m")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HttpFailed as e:
        print(f"\n✗ {e}", file=sys.stderr)
        raise SystemExit(1) from e
    except KeyboardInterrupt:  # pragma: no cover
        print("\n중단했습니다.")
        raise SystemExit(130) from None

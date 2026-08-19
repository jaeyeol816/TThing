"""정적 검사: `extra=` 에 logging 예약어를 직접 쓴 곳이 없어야 한다.

`log.warning("...", extra={"name": x})` 는 KeyError 를 던진다.
**로그 한 줄 때문에 요청이 죽는다.** 그런데 실패 경로에서만 터지는 로그라면
개발 중에 발견되지 않는다 — 실제로 test_exaone.py 가 이 버그를 잡았다.

리뷰 매너에 의존하지 않고 CI 가 잡게 한다.
`mesh.config.log_extra()` 를 쓰면 자동으로 접두사가 붙어 충돌을 피한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

from mesh.config import RESERVED_LOG_KEYS

SRC = Path(__file__).resolve().parents[2] / "src"


def _violations(tree: ast.AST, path: Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                continue
            for key in kw.value.keys:
                if isinstance(key, ast.Constant) and key.value in RESERVED_LOG_KEYS:
                    out.append(
                        f"{path.relative_to(SRC.parent)}:{key.lineno} "
                        f"extra={{{key.value!r}: ...}} -> log_extra() 를 쓰라"
                    )
    return out


def test_no_reserved_keys_in_log_extra():
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        violations += _violations(ast.parse(path.read_text(encoding="utf-8")), path)
    assert not violations, "logging 예약어 충돌:\n  " + "\n  ".join(violations)


def test_detector_catches_a_planted_violation():
    """검사기 자체를 검사한다. 아무것도 못 잡는 검사기는 무의미하다."""
    tree = ast.parse('log.warning("x", extra={"name": "classify"})')
    assert _violations(tree, SRC / "planted.py")


def test_detector_allows_safe_keys():
    tree = ast.parse('log.warning("x", extra={"tier": "secret", "op": "classify"})')
    assert not _violations(tree, SRC / "safe.py")

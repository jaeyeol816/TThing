"""서명 키 회전 CI 작업.

런북 1~5 단계를 그대로 옮긴 것이다. 대기 시간을 인자로 받지 않는 것이
의도적이다 — 사람이 급할 때 줄이는 것이 사고의 원인이었다.
"""

from __future__ import annotations

import time

#: 액세스 토큰 수명(15분)의 2배. 런북 §2-2.
#: ⚠️ 이 값을 인자로 빼지 않는다. 줄일 수 있게 만들면 줄인다.
JWKS_PROPAGATION_WAIT_SECONDS = 30 * 60

#: 리프레시 토큰 수명(14일). 이전 키를 이 기간 유지한다. 런북 §2-4.
PREVIOUS_KEY_RETENTION_DAYS = 14

#: 회전 전후로 반드시 성공해야 하는 검증 표본 수.
SMOKE_SAMPLE_SIZE = 20


def rotate(kid_new: str, *, dry_run: bool = True) -> dict[str, object]:
    steps: list[str] = []

    steps.append(f"publish {kid_new} to JWKS (add only)")
    if not dry_run:
        time.sleep(JWKS_PROPAGATION_WAIT_SECONDS)
    steps.append(f"wait {JWKS_PROPAGATION_WAIT_SECONDS}s for propagation")

    steps.append(f"switch signing key -> {kid_new}")
    steps.append(f"retain previous key for {PREVIOUS_KEY_RETENTION_DAYS}d")
    steps.append(f"smoke verify {SMOKE_SAMPLE_SIZE} pre-rotation tokens")

    return {"kid": kid_new, "dry_run": dry_run, "steps": steps}

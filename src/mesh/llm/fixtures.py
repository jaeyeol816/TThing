"""목업 픽스처 재생·녹화.

**거짓말하지 않는 목업** (U1 NFR 설계 §9).

목업 모드는 **LLM 응답만** 재생한다. 조립·검증·감사·재수화는 실제 코드가 돈다.
그래야 목업 모드에서도 검증 실패가 실제로 일어나고, 데모가 거짓이 되지 않는다.

재생 실패 시 **명시적으로 예외를 던진다.** 조용히 기본값을 반환하면
리허설에서 누락을 발견할 수 없다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mesh.config import get_logger
from mesh.exceptions import FixtureMissing

log = get_logger("llm.fixtures")


def fixture_key(*parts: str) -> str:
    """입력에서 결정적 키를 만든다.

    입력이 조금이라도 다르면 키가 달라져 재생이 실패한다.
    그게 의도다 — 프롬프트를 바꿨으면 다시 녹화해야 한다.
    """
    joined = "\x1f".join(parts)
    return hashlib.sha1(joined.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


class FixtureStore:
    """`data/fixtures/{kind}/{name}_{key}.json` 를 읽고 쓴다."""

    def __init__(self, root: Path, *, record: bool = False) -> None:
        self.root = root
        self.record = record

    def _path(self, kind: str, name: str, key: str) -> Path:
        return self.root / kind / f"{name}_{key}.json"

    def load(self, kind: str, name: str, key: str) -> dict:
        p = self._path(kind, name, key)
        if not p.exists():
            available = sorted(x.name for x in (self.root / kind).glob(f"{name}_*.json"))
            raise FixtureMissing(
                f"픽스처가 없다: {p.relative_to(self.root.parent)}\n"
                f"  같은 이름의 픽스처: {available or '없음'}\n"
                f"  녹화: MESH_RECORD_FIXTURES=1 EXAONE_MODE=live make demo"
            )
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, kind: str, name: str, key: str, payload: dict) -> None:
        if not self.record:
            return
        p = self._path(kind, name, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("픽스처 녹화", extra={"fixture": p.name})

    def has(self, kind: str, name: str, key: str) -> bool:
        return self._path(kind, name, key).exists()

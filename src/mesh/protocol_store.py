"""보안 프로토콜 저장/로드/머지 서비스.

파일 레이아웃 (새 구조):
  agents/shared/security_protocol/
    company.yaml                    ← 전사
    teams/
      sw-dev.yaml
  agents/person_kim/security_protocol/
    protocol.yaml                   ← 개인
  agents/person_park/security_protocol/
    protocol.yaml
  ...

머지 결과가 ClassificationRules 로 변환되어 Gatekeeper 에 주입된다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from mesh.config import get_logger
from mesh.protocol_schemas import ProtocolLevel, SecurityProtocol
from mesh.schemas import BannedTerms, ClassificationRules, Tier

if TYPE_CHECKING:
    from mesh.config import Config

log = get_logger("protocol_store")


class ProtocolStore:
    """프로토콜 파일 CRUD + ClassificationRules 변환."""

    def __init__(self, data_root: Path, *, cfg: "Config | None" = None) -> None:
        self._data_root = data_root
        self._cfg = cfg
        # shared 영역 (전사·팀)
        self._shared_root = data_root / "shared" / "security_protocol"
        self._shared_root.mkdir(parents=True, exist_ok=True)
        (self._shared_root / "teams").mkdir(exist_ok=True)

    # ── 경로 해석 ───────────────────────────────────────────────────

    def _path(self, level: ProtocolLevel, owner: str) -> Path:
        if level == "company":
            return self._shared_root / "company.yaml"
        if level == "team":
            safe = owner.replace("/", "_").replace(":", "_")
            return self._shared_root / "teams" / f"{safe}.yaml"
        # personal — agents/{safe_id}/security_protocol/protocol.yaml
        safe = owner.replace(":", "_")
        personal_dir = self._data_root / safe / "security_protocol"
        personal_dir.mkdir(parents=True, exist_ok=True)
        return personal_dir / "protocol.yaml"

    # ── CRUD ────────────────────────────────────────────────────────

    def get(self, level: ProtocolLevel, owner: str) -> SecurityProtocol | None:
        path = self._path(level, owner)
        if not path.exists():
            return None
        try:
            return SecurityProtocol.load(path)
        except Exception as e:
            log.warning("프로토콜 로드 실패", extra={"path": str(path), "reason": str(e)})
            return None

    def save(self, protocol: SecurityProtocol) -> None:
        path = self._path(protocol.level, protocol.owner)
        protocol.save(path)
        log.info("프로토콜 저장", extra={"level": protocol.level, "owner": protocol.owner})

    def delete(self, level: ProtocolLevel, owner: str) -> bool:
        path = self._path(level, owner)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_all(self) -> list[SecurityProtocol]:
        """저장된 모든 프로토콜을 반환한다."""
        protocols: list[SecurityProtocol] = []
        # shared: 전사 + 팀
        for yaml_path in sorted(self._shared_root.rglob("*.yaml")):
            try:
                protocols.append(SecurityProtocol.load(yaml_path))
            except Exception as e:
                log.warning("프로토콜 로드 실패 — 건너뜀", extra={"path": str(yaml_path), "reason": str(e)})
        # 각 agent 개인 프로토콜
        for agent_dir in sorted(self._data_root.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name == "shared":
                continue
            personal_path = agent_dir / "security_protocol" / "protocol.yaml"
            if personal_path.exists():
                try:
                    protocols.append(SecurityProtocol.load(personal_path))
                except Exception as e:
                    log.warning("개인 프로토콜 로드 실패 — 건너뜀", extra={"path": str(personal_path), "reason": str(e)})
        return protocols

    def list_by_level(self, level: ProtocolLevel) -> list[SecurityProtocol]:
        return [p for p in self.list_all() if p.level == level]

    # ── 머지 → ClassificationRules ──────────────────────────────────

    def merged_rules(
        self,
        *,
        entity_id: str | None = None,
        team: str | None = None,
        base_banned: BannedTerms | None = None,
    ) -> ClassificationRules:
        """전사 + 팀 + 개인 프로토콜을 합쳐 ClassificationRules 를 만든다.

        base_banned: banned.json 에서 읽어온 기존 금칙어 (유지됨).
        """
        protocols: list[SecurityProtocol] = []

        # 전사
        company = self.get("company", "all")
        if company:
            protocols.append(company)

        # 팀
        if team:
            team_proto = self.get("team", team)
            if team_proto:
                protocols.append(team_proto)

        # 개인
        if entity_id:
            personal = self.get("personal", entity_id)
            if personal:
                protocols.append(personal)

        return _merge_to_rules(protocols, base_banned=base_banned)


# ── 머지 로직 ────────────────────────────────────────────────────────

def _merge_to_rules(
    protocols: list[SecurityProtocol],
    *,
    base_banned: BannedTerms | None = None,
) -> ClassificationRules:
    """여러 프로토콜을 합집합으로 합쳐 ClassificationRules 로 변환한다."""

    # 금칙어 합집합
    all_literals: list[str] = list(base_banned.literals if base_banned else [])
    all_patterns: list[str] = list(base_banned.patterns if base_banned else [])
    secret_path_globs: list[str] = ["person_kim/data/customer-H/**", "**/benchmark/**"]
    open_path_globs: list[str] = ["shared/data/public/**"]
    internal_path_globs: list[str] = ["person_*/data/**", "shared/data/**"]

    for p in protocols:
        # SECRET 트리거
        for kw in p.secret_keywords:
            if kw and kw not in all_literals:
                all_literals.append(kw)
        for pat in p.secret_patterns:
            if pat and pat not in all_patterns:
                all_patterns.append(pat)
        for pat in p.secret_content_patterns:
            if pat and pat not in all_patterns:
                all_patterns.append(pat)
        for d in p.secret_directories:
            if d and d not in secret_path_globs:
                secret_path_globs.append(d)
        for ext in p.secret_extensions:
            if ext:
                # 확장자 → 패턴 변환: .curs → .*\.curs$
                ext_pat = r"\." + re.escape(ext.lstrip(".")) + r"$"
                if ext_pat not in all_patterns:
                    all_patterns.append(ext_pat)

        # INTERNAL 트리거
        for kw in p.internal_keywords:
            if kw and kw not in all_literals:
                # internal 키워드는 직접 banned 에 넣지 않고 internal_path_globs 에 상응하는
                # 처리가 필요하지만, 현재 ClassificationRules 구조상 literals = SECRET.
                # 향후 internal_literals 필드 추가 전까지는 생략.
                pass
        for d in p.internal_directories:
            if d and d not in internal_path_globs:
                internal_path_globs.append(d)
        for d in p.open_directories:
            if d and d not in open_path_globs:
                open_path_globs.append(d)

    merged_banned = BannedTerms(
        literals=tuple(dict.fromkeys(all_literals)),
        patterns=tuple(dict.fromkeys(all_patterns)),
    )

    return ClassificationRules(
        banned=merged_banned,
        secret_path_globs=tuple(secret_path_globs),
        open_path_globs=tuple(open_path_globs),
        internal_path_globs=tuple(internal_path_globs),
    )

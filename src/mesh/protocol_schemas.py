"""보안 프로토콜 데이터 모델.

계층 구조:
  company   전사 기준 (관리자)
  team      부서/팀 기준
  personal  개인 기준 (현재 로그인 사용자)

우선순위: 더 엄격한 쪽이 이긴다 (max() 원칙).
같은 항목이 여러 레벨에 있을 때 모두 합집합으로 적용한다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


ProtocolLevel = Literal["company", "team", "personal"]


class SecurityProtocol(BaseModel):
    """보안 프로토콜 한 장.

    YAML 파일 하나 = 이 객체 하나.
    level + owner 조합이 식별자다.
    """

    model_config = ConfigDict(frozen=False)

    # 메타
    level: ProtocolLevel
    owner: str  # "all" | "팀명" | "person:kim"
    description: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)

    # SECRET 판정 규칙
    secret_keywords: list[str] = Field(default_factory=list)
    """이 단어가 문서 본문에 있으면 → SECRET"""

    secret_patterns: list[str] = Field(default_factory=list)
    """정규식. 계약번호·금액 등 패턴 매칭 → SECRET"""

    secret_directories: list[str] = Field(default_factory=list)
    """이 glob 경로 아래 파일 → SECRET (예: corpus/customer-*/**)"""

    secret_extensions: list[str] = Field(default_factory=list)
    """이 확장자 파일 → SECRET (예: .curs, .contract)"""

    secret_content_patterns: list[str] = Field(default_factory=list)
    """문서 내용 정규식 → SECRET (패턴보다 더 유연한 표현)"""

    # INTERNAL 판정 규칙
    internal_keywords: list[str] = Field(default_factory=list)
    """이 단어가 있으면 최소 INTERNAL"""

    internal_directories: list[str] = Field(default_factory=list)
    """이 glob 경로 아래 파일 → 최소 INTERNAL"""

    internal_extensions: list[str] = Field(default_factory=list)
    """이 확장자 파일 → 최소 INTERNAL"""

    # OPEN 허용 규칙 (두 조건 모두 만족해야)
    open_directories: list[str] = Field(default_factory=list)
    """이 경로 아래 + 헤더에 공개 표기 있을 때만 OPEN 허용"""

    # EXAONE 보조 힌트
    exaone_context_hints: list[str] = Field(default_factory=list)
    """EXAONE 분류 프롬프트에 추가할 컨텍스트 설명"""

    # ── 직렬화 / 역직렬화 ─────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> SecurityProtocol:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now()
        data = self.model_dump(mode="json")
        path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

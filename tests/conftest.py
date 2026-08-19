"""공유 픽스처와 Hypothesis 설정.

`data_root` 픽스처가 필수 데이터 파일을 tmp_path 로 복사한다.
새 필수 파일이 생기면 `REQUIRED_DATA_FILES` 한 곳만 고치면 된다 —
각 테스트 파일의 픽스처를 따라다니며 고치지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import settings

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"

#: Config.validate() 가 존재를 요구하는 파일.
REQUIRED_DATA_FILES = ("vocab.json", "banned.json", "pseudonyms.json")


# ══════════════════════════════════════════════════════════════════════
# Hypothesis (PBT-08) — 시드 로깅 + shrinking
# ══════════════════════════════════════════════════════════════════════

settings.register_profile("ci", max_examples=200, print_blob=True, derandomize=False)
settings.register_profile("dev", max_examples=50, print_blob=True, derandomize=False)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))


# ══════════════════════════════════════════════════════════════════════
# 데이터 루트
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """필수 데이터 파일이 복사된 격리 데이터 루트."""
    for name in REQUIRED_DATA_FILES:
        (tmp_path / name).write_bytes((DATA / name).read_bytes())
    (tmp_path / "verified").mkdir()
    (tmp_path / "sessions").mkdir()
    (tmp_path / "fixtures").mkdir()
    return tmp_path


@pytest.fixture
def mock_env(monkeypatch, data_root: Path) -> Path:
    """네트워크를 타지 않는 최소 유효 환경.

    각 테스트가 `monkeypatch.setenv` 로 필요한 것만 덮어쓴다.
    """
    monkeypatch.setenv("MESH_DATA_ROOT", str(data_root))
    monkeypatch.setenv("EXAONE_MODE", "mock")
    monkeypatch.setenv("AGENT_TRANSPORT", "mock")
    monkeypatch.setenv("MESH_BIND_HOST", "127.0.0.1")
    for key in (
        "FRIENDLI_TOKEN",
        "BROKER_API_URL",
        "BROKER_API_KEY",
        "MESH_DEMO_NOW",
        "MESH_RECORD_FIXTURES",
        "TRUSTED_ZONE_LLM_BASE_URL",
        "CONFIDENCE_AUTO",
        "CONFIDENCE_ESCALATE",
        "NGRAM_SIZE",
    ):
        monkeypatch.delenv(key, raising=False)
    return data_root


@pytest.fixture(autouse=True)
def _quiet_logging():
    """테스트 중 로그 출력을 억제한다. caplog 는 계속 동작한다."""
    import logging

    logger = logging.getLogger("mesh")
    prev = logger.handlers[:], logger.level, logger.propagate
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.DEBUG)
    yield
    logger.handlers[:], logger.level, logger.propagate = prev

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
DATA = REPO / "agents" / "shared"  # 새 구조: shared/ 하위에 공유 파일들

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
    """필수 데이터 파일이 복사된 격리 데이터 루트 (새 agents/ 구조)."""
    shared = tmp_path / "shared"
    shared.mkdir()
    for name in REQUIRED_DATA_FILES:
        (shared / name).write_bytes((DATA / name).read_bytes())
    (shared / "fixtures").mkdir()
    # 각 agent 디렉터리 기본 생성
    for agent in ("person_kim", "person_park", "person_choi"):
        (tmp_path / agent / "gatekeeper").mkdir(parents=True)
        (tmp_path / agent / "data").mkdir(parents=True)
        (tmp_path / agent / "security_protocol").mkdir(parents=True)
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


# ══════════════════════════════════════════════════════════════════════
# 어휘 사전 · 데이터 번들 (Day 2)
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def vocab():
    """실제 `data/vocab.json`. 손으로 만든 스키마를 쓰지 않는다 —
    테스트가 통과해도 실물이 깨져 있으면 의미가 없다."""
    from mesh.schemas import Vocabulary

    return Vocabulary.load(DATA / "vocab.json")


@pytest.fixture(scope="session")
def banned():
    from mesh.schemas import BannedTerms

    return BannedTerms.load(DATA / "banned.json")


@pytest.fixture(scope="session")
def pseudonyms():
    from mesh.schemas import PseudonymTargets

    return PseudonymTargets.load(DATA / "pseudonyms.json")


@pytest.fixture(scope="session")
def rules(banned):
    from mesh.schemas import ClassificationRules

    return ClassificationRules(banned=banned)


@pytest.fixture(scope="session")
def conflict_schema(vocab):
    """시나리오 1 의 task 스키마. 필수 슬롯 2개 + 선택 슬롯 4개."""
    return vocab.task_schemas["constraint_conflict_check"]


@pytest.fixture(scope="session")
def technique_schema(vocab):
    return vocab.task_schemas["technique_lookup"]


@pytest.fixture
def cfg(mock_env):
    from mesh.config import Config

    return Config.load()


@pytest.fixture
def bundle(cfg):
    from mesh.config import DataBundle

    return DataBundle(cfg, load_agent_configs=False)


# ══════════════════════════════════════════════════════════════════════
# 종단 배선 (Day 3)
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def full_data_root(tmp_path: Path, monkeypatch) -> Path:
    """코퍼스·세션까지 복사한 데이터 루트 (새 agents/ 구조).

    실제 코퍼스를 쓴다 — 손으로 만든 샘플로 테스트하면 실물이 깨져도 통과한다.
    """
    import shutil

    AGENTS = REPO / "agents"

    # shared/ 복사
    shared = tmp_path / "shared"
    shared.mkdir()
    for name in REQUIRED_DATA_FILES + ("labels.json", "questions.json"):
        (shared / name).write_bytes((DATA / name).read_bytes())
    (shared / "fixtures").mkdir()
    if (DATA / "data").exists():
        shutil.copytree(DATA / "data", shared / "data",
                        ignore=shutil.ignore_patterns("uploads"))

    # 각 agent 디렉터리 복사
    for agent in ("person_kim", "person_park", "person_choi"):
        src = AGENTS / agent
        dst = tmp_path / agent
        if src.exists():
            # data/ 복사 (uploads 제외)
            if (src / "data").exists():
                shutil.copytree(src / "data", dst / "data",
                                ignore=shutil.ignore_patterns("uploads"))
            else:
                (dst / "data").mkdir(parents=True)
            # gatekeeper/ 복사
            dst_gk = dst / "gatekeeper"
            dst_gk.mkdir(parents=True)
            if (src / "gatekeeper").exists():
                for f in (src / "gatekeeper").iterdir():
                    shutil.copy2(f, dst_gk / f.name)
            # security_protocol/ 기본 생성
            (dst / "security_protocol").mkdir(parents=True)
        else:
            (dst / "data").mkdir(parents=True)
            (dst / "gatekeeper").mkdir(parents=True)
            (dst / "security_protocol").mkdir(parents=True)

    monkeypatch.setenv("MESH_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("EXAONE_MODE", "mock")
    monkeypatch.setenv("AGENT_TRANSPORT", "mock")
    monkeypatch.setenv("MESH_BIND_HOST", "127.0.0.1")
    # 세션 신선도를 고정한다 — 시연 날짜가 바뀌어도 재현된다 (BR-S-04)
    monkeypatch.setenv("MESH_DEMO_NOW", "2026-08-19T14:35:00+09:00")
    for key in ("FRIENDLI_TOKEN", "BROKER_API_URL", "BROKER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


@pytest.fixture
def full_cfg(full_data_root):
    from mesh.config import Config

    return Config.load()


@pytest.fixture
def wiring(full_cfg):
    """실제 객체 그래프 + LLM 대역.

    돌려주는 것은 `mesh.main.Services` 다. 조립·검증·감사·재수화는 실제 코드가
    돈다 — 대역은 LLM 응답만 흉내 낸다.
    """
    from mesh.audit import AuditLog
    from mesh.config import DataBundle
    from mesh.gatekeeper import Gatekeeper
    from mesh.main import Services
    from tests.fakes import FakeBroker, FakeExaone

    # 후보를 전부 고르게 한다 — 선택 실패 폴백이 아니라 정상 경로를 태운다
    exaone = FakeExaone(selected=[0, 1, 2])
    broker = FakeBroker(draft_model_id=full_cfg.draft_model_id)
    data = DataBundle(full_cfg)
    audit = AuditLog(full_cfg)
    gatekeeper = Gatekeeper(full_cfg, data, exaone, broker, audit)
    services = Services(full_cfg, data=data, audit=audit, exaone=exaone, gatekeeper=gatekeeper)
    services.fake_exaone = exaone  # type: ignore[attr-defined]
    services.fake_broker = broker  # type: ignore[attr-defined]
    yield services
    audit.close()


@pytest.fixture
def client(wiring):
    """`TestClient`. 실제 앱 + 대역 LLM."""
    from fastapi.testclient import TestClient

    from mesh.main import create_app

    app = create_app(wiring.cfg, services=wiring)
    with TestClient(app) as c:
        c.services = wiring  # type: ignore[attr-defined]
        yield c

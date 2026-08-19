#!/usr/bin/env python3
"""환경 검증 — **다른 컴퓨터로 옮겼을 때 가장 먼저 실행할 스크립트** (NFR-PO-03).

    make preflight

사람이 읽을 진단을 출력한다. `[OK] / [WARN] / [FAIL]` + 조치 방법.
`[FAIL]` 이 하나라도 있으면 non-zero 로 종료한다.

LLM 호출은 **최대 2회**다 (EXAONE 1회 + Bedrock 1회). `--no-network` 로 생략한다.

특별히 하나를 강조한다: **신뢰 경계가 시뮬레이션이면 매번 경고한다.**
이 프로젝트의 신뢰 경계는 환경변수 하나로 정해지므로, 도구가 그 사실을
계속 알려주게 만든다. 팀이 잊지 않고, 데모에서 먼저 밝힐 수 있다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

Level = Literal["OK", "WARN", "FAIL", "INFO"]

_COLORS = {"OK": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m", "INFO": "\033[36m"}
_RESET = "\033[0m"
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


@dataclass
class Check:
    level: Level
    title: str
    detail: str = ""
    fix: str = ""


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, level: Level, title: str, detail: str = "", fix: str = "") -> None:
        c = Check(level, title, detail, fix)
        self.checks.append(c)
        self._print(c)

    def _print(self, c: Check) -> None:
        tag = f"[{c.level:<4}]"
        if _USE_COLOR:
            tag = f"{_COLORS[c.level]}{tag}{_RESET}"
        line = f"{tag} {c.title}"
        if c.detail:
            line += f"  {c.detail}"
        print(line)
        if c.fix:
            for fixline in c.fix.splitlines():
                print(f"        -> {fixline}")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.level == "FAIL")

    @property
    def warned(self) -> int:
        return sum(1 for c in self.checks if c.level == "WARN")


def section(title: str) -> None:
    print(f"\n─── {title} " + "─" * max(0, 58 - len(title)))


# ══════════════════════════════════════════════════════════════════════
# 1. 런타임과 저장소
# ══════════════════════════════════════════════════════════════════════


def check_runtime(r: Report) -> None:
    section("런타임")

    major, minor = sys.version_info[:2]
    if (major, minor) == (3, 12):
        r.add("OK", "Python", f"{sys.version.split()[0]}")
    else:
        r.add(
            "FAIL",
            "Python 버전",
            f"{major}.{minor} (3.12 필요)",
            "uv sync   # .python-version 이 3.12 를 고정한다",
        )

    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        r.add("OK", "가상환경", Path(sys.prefix).name)
    else:
        r.add(
            "WARN",
            "가상환경 밖에서 실행 중",
            "",
            "uv run python scripts/preflight.py  또는  make preflight",
        )

    try:
        import boto3

        ver = tuple(int(x) for x in boto3.__version__.split(".")[:2])
        if ver >= (1, 35):
            r.add("OK", "boto3", boto3.__version__)
        else:
            r.add(
                "FAIL",
                "boto3 구버전",
                boto3.__version__,
                "1.35 미만은 bedrock-runtime 서비스를 모른다 (실측 확인). uv sync",
            )
    except ImportError:
        r.add("FAIL", "boto3 없음", "", "uv sync")


def check_gitignore(r: Report) -> None:
    section("자격증명 보호 (SECURITY-12)")

    gi = REPO / ".gitignore"
    if not gi.exists():
        r.add("FAIL", ".gitignore 없음", "", "자격증명이 커밋된다. .gitignore 를 먼저 만들라")
        return

    body = gi.read_text(encoding="utf-8")
    required = [".kiro/.env", ".kiro/opencode.jsonc", ".env"]
    missing = [p for p in required if p not in body]
    if missing:
        r.add("FAIL", ".gitignore 누락", ", ".join(missing), "해당 항목을 .gitignore 에 추가")
    else:
        r.add("OK", ".gitignore", "자격증명 3종 커버")

    # git 이 실제로 추적하고 있는지 확인 (ignore 가 늦게 추가된 경우)
    import subprocess

    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        r.add("INFO", "git 상태 확인 생략", "git 미설치 또는 저장소 아님")
        return

    tracked = [
        ln for ln in out.splitlines() if ln in {".kiro/.env", ".kiro/opencode.jsonc", ".env"}
    ]
    if tracked:
        r.add(
            "FAIL",
            "자격증명이 git 에 추적되고 있다",
            ", ".join(tracked),
            "git rm --cached <파일>  후 커밋. 이미 푸시했다면 키를 폐기·재발급하라",
        )
    else:
        r.add("OK", "git 추적", "자격증명 파일 없음")


# ══════════════════════════════════════════════════════════════════════
# 2. 설정과 데이터
# ══════════════════════════════════════════════════════════════════════


def load_config(r: Report):
    section("설정")

    from mesh.config import Config, setup_logging
    from mesh.exceptions import ConfigError

    setup_logging(os.environ.get("MESH_LOG_LEVEL", "ERROR"))
    try:
        cfg = Config.load()
    except ConfigError as e:
        r.add("FAIL", "설정 검증 실패", str(e), "cp .env.example .env  후 값을 채우라")
        return None

    root = cfg.data_root
    if root.is_absolute():
        r.add(
            "WARN",
            "MESH_DATA_ROOT 가 절대 경로",
            str(root),
            "상대 경로(./data)를 쓰면 다른 컴퓨터에서 그대로 동작한다 (NFR-PO-01)",
        )
    else:
        r.add("OK", "MESH_DATA_ROOT", str(root))

    if cfg.bind_host in {"127.0.0.1", "localhost", "::1"}:
        r.add("OK", "바인딩", f"{cfg.bind_host}:{cfg.bind_port}")
    else:
        r.add(
            "WARN",
            "localhost 가 아닌 주소에 바인딩",
            cfg.bind_host,
            "이 서비스는 원문을 읽고 재수화된 실제 이름을 반환한다.\n"
            "인증이 없는 MVP 에서 노출하면 권한 우회 도구가 된다 (BR-M-01)",
        )

    r.add("OK", "EXAONE 모드", cfg.exaone_mode)
    r.add("OK", "Agent 전송", cfg.agent_transport.value)
    return cfg


def check_trust_boundary(r: Report, cfg) -> None:
    section("신뢰 경계")

    r.add("INFO", "신뢰 구역 LLM", cfg.trusted_zone_llm_base_url)

    if cfg.trust_boundary_simulated:
        r.add(
            "WARN",
            "신뢰 경계가 시뮬레이션이다",
            "",
            "TRUSTED_ZONE_LLM_BASE_URL 이 공개 SaaS 를 가리킨다.\n"
            "아키텍처가 보장하는 것: 원문이 이 엔드포인트 하나에만 전달된다.\n"
            "보장하지 않는 것: 그 엔드포인트가 사내망 안에 있다.\n"
            "실배포 전환 = 이 값만 사내 서빙으로 바꾼다 (OpenAI 호환이면 코드 변경 0).\n"
            "데모에서 먼저 밝히는 것이 지적당하는 것보다 낫다.",
        )
    else:
        r.add("OK", "신뢰 경계", "사내 엔드포인트로 설정됨")


def check_data(r: Report, cfg) -> None:
    section("데이터")

    from mesh.config import DataBundle
    from mesh.exceptions import ConfigError

    try:
        bundle = DataBundle(cfg)
    except (ConfigError, FileNotFoundError, KeyError, ValueError) as e:
        r.add("FAIL", "데이터 로드 실패", f"{type(e).__name__}: {e}")
        return

    r.add(
        "OK",
        "어휘 사전",
        f"v{bundle.vocab.version} · 슬롯 {len(bundle.vocab.slots)}개 · "
        f"task {len(bundle.vocab.task_schemas)}개 · sha {bundle.vocab_sha256[:12]}",
    )

    # 성능 수치 슬롯이 *없는지* — 시나리오 3 폴백의 전제 (FR-54)
    forbidden = {"p99_latency_ms", "throughput_tps", "amount", "price", "contract_no"}
    present = forbidden & set(bundle.vocab.slots)
    if present:
        r.add(
            "FAIL",
            "있어서는 안 되는 슬롯",
            ", ".join(sorted(present)),
            "성능 수치 슬롯이 생기면 시나리오 3의 폴백이 사라진다 (FR-54).\n"
            "data/vocab.json 의 _intentionally_absent 를 읽으라",
        )
    else:
        r.add("OK", "의도적 부재 확인", "성능 수치·금액·계약번호 슬롯 없음")

    r.add(
        "OK",
        "금칙어",
        f"리터럴 {len(bundle.banned.literals)}개 · 정규식 {len(bundle.banned.patterns)}개",
    )
    r.add(
        "OK",
        "가명화 대상",
        f"{sum(len(v) for v in bundle.pseudonyms.targets.values())}개 · "
        f"기술 용어 {len(bundle.pseudonyms.technical_terms)}개 보존",
    )
    r.add("OK", "에이전트", f"{len(bundle.agents)}개: {', '.join(sorted(bundle.agents))}")

    # 코퍼스와 세션
    corpus = list(cfg.corpus_root.rglob("*")) if cfg.corpus_root.exists() else []
    docs = [p for p in corpus if p.is_file()]
    if len(docs) < 8:
        r.add(
            "WARN",
            "코퍼스가 작다",
            f"{len(docs)}건",
            "시나리오 필수 문서 8건이 먼저다. 규모는 그다음 (BR-EV-09)",
        )
    else:
        r.add("OK", "코퍼스", f"{len(docs)}건")

    labels_path = cfg.labels_path
    if labels_path.exists():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))["labels"]
        missing = [i["path"] for i in labels if not (cfg.data_root / i["path"]).exists()]
        traps = [i for i in labels if i.get("trap")]
        if missing:
            r.add("FAIL", "labels.json 이 없는 파일을 가리킨다", ", ".join(missing[:3]))
        else:
            r.add(
                "OK",
                "등급 라벨",
                f"{len(labels)}건 · 함정 문서 {len(traps)}건",
            )
    else:
        r.add("WARN", "labels.json 없음", "", "분류 정확도를 측정할 수 없다 (Day 2 게이트)")

    sessions = list(cfg.sessions_root.glob("*.json")) if cfg.sessions_root.exists() else []
    if sessions:
        r.add("OK", "세션", f"{len(sessions)}개")
    else:
        r.add("WARN", "세션 없음", "", "data/sessions/*.json 이 필요하다")

    fixtures = cfg.fixtures_root
    api_fx = list((fixtures / "api").glob("*.json")) if (fixtures / "api").exists() else []
    llm_fx = list(fixtures.rglob("*.json"))
    r.add(
        "INFO",
        "픽스처",
        f"API {len(api_fx)}개 · 전체 {len(llm_fx)}개",
        "" if llm_fx else "make record-fixtures 로 목업 응답을 녹화하라",
    )


# ══════════════════════════════════════════════════════════════════════
# 3. 엔드포인트 (LLM 호출 최대 2회)
# ══════════════════════════════════════════════════════════════════════


def check_exaone(r: Report, cfg) -> None:
    section("EXAONE (신뢰 구역 LLM) — 호출 1회")

    if cfg.exaone_mode == "mock":
        r.add("INFO", "목업 모드", "네트워크 호출 생략")
        return
    if not cfg.friendli_token:
        r.add("FAIL", "FRIENDLI_TOKEN 없음", "", ".env 에 토큰을 기입하라")
        return

    import asyncio

    from mesh.exceptions import ExaoneUnavailable
    from mesh.llm.exaone import ExaoneClient

    async def probe() -> tuple[float, dict]:
        async with ExaoneClient(cfg) as c:
            t = time.perf_counter()
            out = await c.complete_json(
                'Reply with JSON only: {"ok": true}',
                "ping",
                name="preflight_ping",
                max_tokens=32,
            )
            return time.perf_counter() - t, out

    try:
        elapsed, out = asyncio.run(probe())
    except ExaoneUnavailable as e:
        r.add(
            "FAIL",
            "EXAONE 호출 실패",
            str(e),
            "엔드포인트·토큰을 확인하라. 또는 EXAONE_MODE=mock 으로 실행",
        )
        return

    r.add("OK", "EXAONE 왕복", f"{elapsed:.2f}s · {cfg.exaone_model_id}")
    if "reasoning" in json.dumps(out):
        r.add(
            "FAIL",
            "응답에 reasoning 이 남아 있다",
            "",
            "enable_thinking=False 와 strip_thinking() 을 확인하라 (FR-14).\n"
            "이 필드는 원문을 인용할 수 있다",
        )
    else:
        r.add("OK", "thinking 제거", "reasoning* 부재")


def check_bedrock(r: Report, cfg) -> None:
    section("Agent (Claude) — 호출 1회")

    from mesh.schemas import Transport

    if cfg.agent_transport is Transport.MOCK:
        r.add("INFO", "목업 모드", "네트워크 호출 생략")
        return

    if cfg.agent_transport is Transport.BROKER:
        if not (cfg.broker_api_url and cfg.broker_api_key):
            r.add("FAIL", "브로커 설정 없음", "", "make deploy 후 .env 에 URL·키를 기입")
            return
        r.add("OK", "브로커", cfg.broker_api_url)
        r.add("INFO", "브로커 왕복 확인 생략", "Bedrock 호출 비용을 아끼기 위해 direct 만 확인")
        return

    # direct 모드
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        r.add("FAIL", "boto3 없음")
        return

    try:
        ident = boto3.client("sts", region_name=cfg.aws_region).get_caller_identity()
    except (NoCredentialsError, ClientError, BotoCoreError) as e:
        r.add(
            "FAIL",
            "AWS 자격증명 무효",
            type(e).__name__,
            "source .kiro/.env  또는  AGENT_TRANSPORT=mock 으로 실행",
        )
        return

    r.add("OK", "AWS 계정", f"{ident['Account']} · {cfg.aws_region}")

    if ident.get("Arn", "").startswith("arn:aws:sts::"):
        r.add(
            "INFO",
            "임시 STS 자격증명",
            "만료 가능",
            "시연 중 만료되면 Agent 호출이 죽는다.\n"
            "AGENT_TRANSPORT=broker 는 Lambda 실행 역할을 쓰므로 만료가 없다",
        )

    if cfg.aws_region != "us-east-1":
        r.add(
            "WARN",
            "리전이 us-east-1 이 아니다",
            cfg.aws_region,
            "계정 정책이 다른 리전을 Deny 한다 (실측 확인)",
        )

    try:
        rt = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
        t = time.perf_counter()
        resp = rt.converse(
            modelId=cfg.agent_model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with exactly: OK"}]}],
            inferenceConfig={"maxTokens": 8, "temperature": 0},
        )
        elapsed = time.perf_counter() - t
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "?")
        r.add(
            "FAIL",
            "Bedrock 호출 실패",
            f"{code} · {cfg.agent_model_id}",
            "설계 문서의 claude-sonnet-5 는 이 계정에서 AccessDenied 다.\n"
            "AGENT_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0 를 쓰라.\n"
            "모든 Claude 는 추론 프로파일(us. 접두사)이 필요하다",
        )
        return

    usage = resp.get("usage", {})
    r.add("OK", "Bedrock 왕복", f"{elapsed:.2f}s · {cfg.agent_model_id}")
    r.add(
        "INFO",
        "지연 참고",
        f"{usage.get('outputTokens', '?')} 출력 토큰",
        "실제 답변은 500 토큰 규모라 9초대다 (실측). 4 토큰 응답으로 판단하지 말 것",
    )


def check_cdk(r: Report, cfg) -> None:
    section("CDK (선택)")

    from mesh.schemas import Transport

    if cfg.agent_transport is Transport.MOCK:
        r.add("INFO", "목업 모드", "CDK 확인 생략")
        return

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return

    try:
        boto3.client("cloudformation", region_name=cfg.aws_region).describe_stacks(
            StackName="CDKToolkit"
        )
        r.add("OK", "CDK 부트스트랩", "CDKToolkit 존재")
    except (ClientError, BotoCoreError):
        level: Level = "FAIL" if cfg.agent_transport is Transport.BROKER else "WARN"
        r.add(
            level,
            "CDK 부트스트랩 안 됨",
            "",
            "make bootstrap\ndirect 모드로도 데모가 돌기 때문에 필수는 아니다 (FR-49)",
        )


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════


def main() -> int:
    ap = argparse.ArgumentParser(description="환경 검증")
    ap.add_argument("--no-network", action="store_true", help="LLM·AWS 호출 생략")
    args = ap.parse_args()

    print("대리 에이전트 메시 — 환경 검증")

    r = Report()
    check_runtime(r)
    check_gitignore(r)

    cfg = load_config(r)
    if cfg is None:
        print()
        print("설정을 먼저 고치라. 이후 검사는 생략한다.")
        return 1

    check_trust_boundary(r, cfg)
    check_data(r, cfg)

    if args.no_network:
        section("네트워크 검사 생략 (--no-network)")
    else:
        check_exaone(r, cfg)
        check_bedrock(r, cfg)
        check_cdk(r, cfg)

    section("요약")
    total = len(r.checks)
    print(f"검사 {total}건 · 실패 {r.failed} · 경고 {r.warned}")

    if r.failed:
        print("\n실패 항목을 고친 뒤 다시 실행하라.")
        return 1

    if cfg.trust_boundary_simulated:
        print("\n신뢰 경계가 시뮬레이션이다. 데모에서 먼저 밝히라 (위 경고 참조).")

    print("\n준비됨.  make test  ->  make run")
    return 0


if __name__ == "__main__":
    sys.exit(main())

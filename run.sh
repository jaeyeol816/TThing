#!/usr/bin/env bash
# macOS / Linux 실행 스크립트 — run.ps1 의 대응물.
#
#   ./run.sh
#
# .env 로드 → uv sync → preflight → uvicorn 순서로 run.ps1 과 동일하다.
# 한 가지만 다르다: **이미 설정된 환경변수를 .env 가 덮어쓰지 않는다.**
# 유닉스에서는 `EXAONE_MODE=mock ./run.sh` 로 한 번만 다르게 띄우는 것이
# 흔한 사용법인데, .env 가 이기면 그 지시가 조용히 무시되기 때문이다.

set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1

# ── 출력 ─────────────────────────────────────────────────────
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    CYAN=$'\033[36m'; GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    CYAN=''; GREEN=''; RED=''; RESET=''
fi
info() { printf '%s%s%s\n' "$CYAN" "$1" "$RESET"; }
ok()   { printf '%s%s%s\n' "$GREEN" "$1" "$RESET"; }
die()  { printf '%sFAILED: %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

# ── .env 를 환경으로 ─────────────────────────────────────────
# CRLF(윈도우에서 편집한 .env), `export` 접두, 따옴표, 줄 끝 주석을 감당한다.
# .env.example 에 `MESH_BIND_HOST=127.0.0.1   # 0.0.0.0 금지` 같은 줄이 있어서
# 주석을 떼지 않으면 바인딩 주소가 통째로 망가진다.
if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
        key="${BASH_REMATCH[2]}"
        value="${BASH_REMATCH[3]}"

        value="${value#"${value%%[![:space:]]*}"}"   # 앞 공백
        value="${value%"${value##*[![:space:]]}"}"   # 뒤 공백

        if [[ "$value" == '"'*'"' && ${#value} -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        elif [[ "$value" == "'"*"'" && ${#value} -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        else
            value="${value%%[[:space:]]#*}"          # 줄 끝 주석 (공백 + #)
            value="${value%"${value##*[![:space:]]}"}"
        fi

        # 이미 환경에 있으면 그쪽이 이긴다 (명령줄 지정 > .env)
        [ -n "${!key+x}" ] || export "$key=$value"
    done < .env
fi

command -v uv >/dev/null 2>&1 || die "uv 를 찾을 수 없다 — curl -LsSf https://astral.sh/uv/install.sh | sh"

info "[1/3] Installing dependencies..."
uv sync || die "uv sync"

info "[2/3] Preflight check..."
uv run python scripts/preflight.py || die "preflight"

HOST="${MESH_BIND_HOST:-127.0.0.1}"
PORT="${MESH_BIND_PORT:-8080}"
ok "[3/3] Starting server at http://${HOST}:${PORT}"
# exec: Ctrl-C 가 래퍼가 아니라 uvicorn 에 바로 간다.
exec uv run uvicorn --factory mesh.main:create_app --app-dir src \
    --host "$HOST" --port "$PORT"

#!/usr/bin/env bash
set -uo pipefail

export PYTHONUTF8=1

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    CYAN=$'\033[36m'; GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    CYAN=''; GREEN=''; RED=''; RESET=''
fi
info() { printf '%s%s%s\n' "$CYAN" "$1" "$RESET"; }
ok()   { printf '%s%s%s\n' "$GREEN" "$1" "$RESET"; }
die()  { printf '%sFAILED: %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
        key="${BASH_REMATCH[2]}"
        value="${BASH_REMATCH[3]}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ "$value" == '"'*'"' && ${#value} -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        elif [[ "$value" == "'"*"'" && ${#value} -ge 2 ]]; then
            value="${value:1:${#value}-2}"
        else
            value="${value%%[[:space:]]#*}"
            value="${value%"${value##*[![:space:]]}"}"
        fi
        [ -n "${!key+x}" ] || export "$key=$value"
    done < .env
fi

command -v uv >/dev/null 2>&1 || die "uv not found -- curl -LsSf https://astral.sh/uv/install.sh | sh"

info "[1/3] Installing dependencies..."
uv sync || die "uv sync"

info "[2/3] Preflight check..."
uv run python scripts/preflight.py || die "preflight"

HOST="${MESH_BIND_HOST:-127.0.0.1}"
PORT="${MESH_BIND_PORT:-8080}"
ok "[3/3] Starting server at http://${HOST}:${PORT}"
exec uv run uvicorn --factory mesh.main:create_app --app-dir src \
    --host "$HOST" --port "$PORT"

#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
CORE_VENV="${CORE_VENV:-.venv-core}"
INGEST_VENV="${INGEST_VENV:-.venv-ingest}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() {
    printf '==> %s\n' "$*"
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

has_uv() {
    command -v uv >/dev/null 2>&1
}

venv_matches_python() {
    local venv="$1"
    [[ -x "$venv/bin/python" ]] || return 1
    "$venv/bin/python" - "$PYTHON_VERSION" <<'PY'
import sys

wanted = tuple(int(part) for part in sys.argv[1].split(".")[:2])
raise SystemExit(0 if sys.version_info[:2] == wanted else 1)
PY
}

find_system_python() {
    local candidate
    for candidate in "python${PYTHON_VERSION}" python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
            'import sys; raise SystemExit(0 if ".".join(map(str, sys.version_info[:2])) == sys.argv[1] else 1)' \
            "$PYTHON_VERSION"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

create_venv() {
    local venv="$1"

    if [[ -d "$venv" ]] && ! venv_matches_python "$venv"; then
        log "Removing $venv because it is not Python $PYTHON_VERSION"
        rm -rf "$venv"
    fi

    if venv_matches_python "$venv"; then
        return 0
    fi

    log "Creating $venv with Python $PYTHON_VERSION"
    if has_uv; then
        uv venv --python "$PYTHON_VERSION" "$venv"
    else
        local python
        python="$(find_system_python)" || die \
            "Python $PYTHON_VERSION is required. Install it, or install uv so setup can provision it automatically."
        "$python" -m venv "$venv"
        "$venv/bin/python" -m pip install --upgrade pip
    fi
}

install_requirements() {
    local venv="$1"
    local requirements="$2"

    log "Installing $requirements into $venv"
    if has_uv; then
        uv pip install --python "$venv/bin/python" -r "$requirements"
    else
        "$venv/bin/python" -m pip install -r "$requirements"
    fi
}

create_venv "$CORE_VENV"
install_requirements "$CORE_VENV" services/core/requirements.txt

if [[ "${WITH_QLIB:-0}" == "1" ]]; then
    install_requirements "$CORE_VENV" services/core/requirements-qlib.txt
else
    log "Skipping optional Qlib stack. Run 'make qlib' or 'WITH_QLIB=1 make setup' to enable it."
fi

create_venv "$INGEST_VENV"
install_requirements "$INGEST_VENV" services/ingest/requirements.txt

log "Setup complete. Run 'make all' to build the local dataset."

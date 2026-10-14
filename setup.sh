#!/bin/sh
# Instalador para Linux y macOS: instala uv si falta y ejecuta install.py.
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "Instalando uv..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    else
        wget -qO- https://astral.sh/uv/install.sh | sh
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

exec uv run --project whatsapp-mcp-server python install.py "$@"

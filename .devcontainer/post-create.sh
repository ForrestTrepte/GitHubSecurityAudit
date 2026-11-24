#!/usr/bin/env bash
# Runs once after the dev container is created.
set -euo pipefail

# Install uv (Python package and environment manager).
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Project environment in .venv (VS Code is pointed at .venv/bin/python).
if [ -f pyproject.toml ]; then
  uv sync
else
  uv venv
fi

echo
echo "Dev container ready."
echo "  uv: $(uv --version)"
echo "  Python: $(.venv/bin/python --version)"
echo "  Claude Code: $(claude --version 2>/dev/null || echo 'not found')"
echo "Run 'claude' in the terminal (or use the Claude Code panel) and sign in."

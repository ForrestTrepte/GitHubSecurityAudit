#!/usr/bin/env bash
# Runs once after the dev container is created.
set -euo pipefail

# Named volumes are created root-owned; hand the Claude config dir to the dev user.
sudo chown -R "$(id -u):$(id -g)" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Project virtual environment (VS Code is pointed at .venv/bin/python).
if [ ! -d .venv ]; then
  python -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip

if [ -f requirements.txt ]; then
  .venv/bin/pip install -r requirements.txt
fi
if [ -f requirements-dev.txt ]; then
  .venv/bin/pip install -r requirements-dev.txt
fi
if [ -f pyproject.toml ]; then
  .venv/bin/pip install -e .
fi

echo
echo "Dev container ready."
echo "  Python: $(.venv/bin/python --version)"
echo "  Claude Code: $(claude --version 2>/dev/null || echo 'not found')"
echo "Run 'claude' in the terminal (or use the Claude Code panel) and sign in on first use."

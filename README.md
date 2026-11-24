# GitHubSecurityAudit
Scans all my github repositories to verify that dependabot is enabled and working.

## Development environment

This repo includes a [dev container](.devcontainer/devcontainer.json) with Python 3.12, the GitHub CLI, and [Claude Code](https://docs.claude.com/en/docs/claude-code/overview).

- **Codespaces:** on GitHub, choose **Code → Codespaces → Create codespace on main**, then open it in VS Code.
- **Local VS Code:** install the Dev Containers extension and run **Dev Containers: Reopen in Container**.

On first start the container creates a `.venv` and installs `requirements.txt` / `pyproject.toml` if present. Run `claude` in the terminal (or open the Claude Code panel) and sign in; the login is stored in a named volume so it survives container rebuilds.

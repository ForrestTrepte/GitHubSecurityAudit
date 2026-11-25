# GitHubSecurityAudit
Scans all my github repositories to verify that dependabot is enabled and working.

## Usage

```bash
uv run audit.py                 # summary table of every non-archived repo you own (forks included)
uv run audit.py --csv audit.csv # also write the full per-repo detail to a CSV file
```

Each repo gets a status:

| Status | Meaning |
| --- | --- |
| ❌ FAIL | Dependabot alerts or security updates are off, the dependency graph is off or finds no manifests, the last Dependabot run failed, or there are open critical/high alerts. |
| ❓ ERROR | A check couldn't run, usually because the token is missing a permission (the Problems column says which). |
| ⚠️ WARN | Open medium/low alerts, security updates paused, or a Dependabot PR open for more than 30 days. |
| ✅ OK | None of the above. |

The script exits with code 1 if any repo is FAIL or ERROR.

### Token

The script reads a read-only token from `DEPENDABOT_AUDIT_TOKEN`. (The `GITHUB_TOKEN` that Codespaces provides only covers this repo, so it can't be used.)

1. Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new):
   - **Resource owner:** your account. **Repository access:** All repositories.
   - **Repository permissions, all Read-only:** Administration, Contents, Dependabot alerts, Actions, Pull requests. (Metadata is added automatically.)
2. Make it available as `DEPENDABOT_AUDIT_TOKEN`, either by running `export DEPENDABOT_AUDIT_TOKEN=...` in the terminal or by adding it as a [Codespaces secret](https://github.com/settings/codespaces) for this repo, then rebuilding the codespace.

"Administration: read" is needed because GitHub only shows whether Dependabot alerts and security updates are enabled to tokens that can read repository settings. Read-only access can't change those settings.

## Development environment

This repo includes a [dev container](.devcontainer/devcontainer.json) with Python 3.12, [uv](https://docs.astral.sh/uv/), the GitHub CLI, and [Claude Code](https://docs.claude.com/en/docs/claude-code/overview).

- **Codespaces:** on GitHub, choose **Code → Codespaces → Create codespace on main**, then open it in VS Code.
- **Local VS Code:** install the Dev Containers extension and run **Dev Containers: Reopen in Container**.

On first start the container installs uv and creates `.venv` (running `uv sync` once a `pyproject.toml` exists). Add dependencies with `uv add <package>` and run code with `uv run`. Run `claude` in the terminal (or open the Claude Code panel) and sign in.

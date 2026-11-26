# GitHubSecurityAudit
Scans all my github repositories to verify that dependabot is enabled and working.

## Usage

```bash
uv run audit.py                 # summary table of every non-archived repo you own (forks included)
uv run audit.py --csv audit.csv # also write the full per-repo detail to a CSV file
```

`audit.csv` in this repo holds the latest results and is committed on purpose. It includes private repo names and their alert counts.

### Scope

- **Repos:** only repos you own. Archived repos are skipped; forks are included.
- **Checked:** Dependabot alerts and security updates. Version updates (`dependabot.yml`) are out of scope for now.
- **Repos with nothing to scan:** a repo where the dependency graph finds no dependencies is N/A if the graph did find manifest files and they just list none (e.g. a `.csproj` with no NuGet packages; a `.sln` isn't a manifest). If it finds no manifests at all, the repo is FAIL, with two exceptions that make it N/A:
  - it contains a project type Dependabot can't scan (currently Unity), or
  - it has no dependencies by design (e.g. data files only) and you've given it the `no-dependencies` [topic](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics). The audit checks that this is still true: if GitHub's language stats show any code (they ignore data and prose such as JSON, CSV and Markdown), the repo is FAIL; if the dependency graph finds dependencies, it's audited normally and WARN until you remove the topic.
- **Not checked:** malware alerts. GitHub's API can't tell whether they're turned on, and there's no account-wide setting for them, so check each repo's Settings → Code security page.

### Statuses

Each repo gets a status:

| Status | Meaning |
| --- | --- |
| ❌ FAIL | Dependabot alerts or security updates are off, the dependency graph is off or finds no manifests (see Scope for exceptions), the last Dependabot run failed, or there are open critical/high alerts. |
| ❓ ERROR | A check couldn't run, usually because the token is missing a permission (the Problems column says which). |
| ⚠️ WARN | Open medium/low alerts, security updates paused, a Dependabot PR open for more than 30 days, a manifest the dependency graph can't parse or that's too large for it, or a `no-dependencies` repo whose dependency graph found dependencies. |
| ➖ N/A | Dependabot has nothing to scan in this repo: it has no ecosystems Dependabot supports, and either manifests that list no dependencies, only a project type it doesn't support (currently Unity), or the `no-dependencies` topic and no code. Settings problems still make it FAIL. |
| ✅ OK | None of the above. |

The script exits with code 1 if any repo is FAIL or ERROR.

### Columns

Summary table columns (`?` or `n/a` means the value couldn't be read; see the Problems column):

| Column | Meaning |
| --- | --- |
| Repository | Repo name; forks are marked `(fork)`. |
| Status | See above. |
| Alerts on | Whether Dependabot alerts are enabled. |
| Sec. updates | Whether Dependabot security updates are enabled (`paused` if GitHub has paused them, e.g. after many unmerged PRs). |
| Ecosystems | Package ecosystems the dependency graph found, which are what Dependabot alerts cover. `—` means none were found. Project types Dependabot can't scan are shown dimmed as `(unsupported)`, e.g. `nuget, unity (unsupported)` for a Unity project that also uses NuGet packages. |
| Open alerts | Open Dependabot alerts by severity: **C**ritical, **H**igh, **M**edium, **L**ow. |
| Last run | How long ago Dependabot last ran any job in the repo (from its built-in GitHub Actions runs), marked `✗` if that run failed. See below for `none`. |
| Problems | Every reason behind the status, plus notes, e.g. the Unity editor version for Unity projects. |

**About `Last run` and `none`:** GitHub doesn't publish when Dependabot last scanned a repo for alerts, so this column shows the closest available signal: the most recent Dependabot job. There are two kinds:

- **Graph updates** (`update-graph`): Dependabot re-resolves the repo's dependencies so the dependency graph, and therefore alerts, stay current. These run routinely, but only for ecosystems where GitHub can't read the full dependency list straight from the files (for example, pip projects without a lock file).
- **Security updates** (`dependabot-updates`): Dependabot opens a PR to fix an alert. These run only when there is an alert to fix and security updates are on.

So `none` is normal for a repo whose dependencies GitHub reads directly from its files and that has no fixable alerts, and on its own it isn't a problem. It's worth a look if the repo has open alerts and security updates are on but `Last run` is still `none`, or if a run failed (`✗`). The CSV (`--csv`) also has the exact run timestamp, job type and conclusion, plus the count and age of open Dependabot PRs.

The CSV's `manifests` column lists the manifest files the dependency graph found, including ones with no dependencies. They come from GitHub's GraphQL `dependencyGraphManifests` field, because the dependency graph's SBOM only lists packages.

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

Lint and format with `uvx ruff check audit.py` and `uvx ruff format audit.py`.

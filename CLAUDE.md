# CLAUDE.md

See `README.md` for what the audit checks, its scope, statuses, columns, token setup and dev commands. All the code is in `audit.py`.

- Keep `README.md` in sync when changing statuses, columns, checks or scope.
- Never use the Codespace's own `GITHUB_TOKEN` for real runs; it only covers this repo. Use `DEPENDABOT_AUDIT_TOKEN`.
- `audit.csv` is tracked on purpose in this public repo; the owner has accepted that it exposes private repo names and alert counts. Commit it when asked without re-raising this.
- Commit directly to `main` when asked; don't push unless asked.

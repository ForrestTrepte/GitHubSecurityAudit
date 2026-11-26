"""Audit Dependabot alerts and security updates across the repositories you own.

Usage:
    uv run audit.py              # compact summary table
    uv run audit.py --csv out.csv  # also write the full detail to a CSV file

Authentication: set DEPENDABOT_AUDIT_TOKEN to a fine-grained personal access token
with read-only access to all of your repositories (see README.md).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime

import httpx
from rich.console import Console
from rich.table import Table

API = "https://api.github.com"
TOKEN_ENV = "DEPENDABOT_AUDIT_TOKEN"
SEVERITIES = ("critical", "high", "medium", "low")
STALE_PR_DAYS = 30
RATE_LIMIT_RETRIES = 5
MAX_RATE_LIMIT_WAIT = 120  # seconds; give up rather than wait out a long reset

# purl types (from the dependency-graph SBOM) -> ecosystem names as GitHub shows them.
ECOSYSTEM_NAMES = {
    "pypi": "pip",
    "npm": "npm",
    "maven": "maven",
    "nuget": "nuget",
    "gem": "rubygems",
    "golang": "go",
    "cargo": "cargo",
    "composer": "composer",
    "githubactions": "actions",
    "pub": "pub",
    "swift": "swift",
    "hex": "hex",
}

# Project types Dependabot can't scan, shown alongside the supported ecosystems.
# A repo with only these (no supported ecosystems) is N/A rather than FAIL.
# Map: marker-file suffix -> name shown in the Ecosystems column.
UNSUPPORTED_MARKERS = {
    "ProjectSettings/ProjectVersion.txt": "unity",
}

# Repo topic that marks a repo as having no dependencies by design (e.g. data only).
# Such a repo is N/A instead of FAIL, as long as GitHub finds no code in it either.
NO_DEPENDENCIES_TOPIC = "no-dependencies"


class PermissionDenied(Exception):
    """The token lacks a permission needed for one check."""


class RateLimited(Exception):
    """GitHub kept rate-limiting a request after all retries."""


def is_rate_limited(r: httpx.Response) -> bool:
    if r.status_code == 429:
        return True
    if r.status_code == 403:
        return (
            r.headers.get("x-ratelimit-remaining") == "0"
            or "rate limit" in r.text.lower()
        )
    if r.status_code == 200 and r.request.url.path == "/graphql":
        errors = r.json().get("errors") or []
        return any(e.get("type") == "RATE_LIMITED" for e in errors)
    return False


def rate_limit_wait(r: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying, from GitHub's headers if it sent any."""
    if retry_after := r.headers.get("retry-after"):
        return float(retry_after)
    if r.headers.get("x-ratelimit-remaining") == "0" and (
        reset := r.headers.get("x-ratelimit-reset")
    ):
        return max(0.0, float(reset) - time.time()) + 1
    return 2.0 ** (attempt + 2)  # 4, 8, 16, ... seconds


def failure_detail(r: httpx.Response, needs: str | None = None) -> str:
    """Why a request failed, for the Problems column."""
    if is_rate_limited(r):
        return f"HTTP {r.status_code}; rate limited by GitHub, try again later"
    return f"HTTP {r.status_code}; needs {needs}" if needs else f"HTTP {r.status_code}"


@dataclass
class RepoAudit:
    repo: str
    url: str
    fork: bool
    private: bool
    marked_no_dependencies: bool = False  # has the NO_DEPENDENCIES_TOPIC topic
    languages: list[str] = field(default_factory=list)  # only read when marked
    alerts_enabled: bool | None = None
    security_updates_enabled: bool | None = None
    security_updates_paused: bool | None = None
    dependency_graph_enabled: bool | None = None
    ecosystems: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)  # files the graph parsed
    unsupported: list[str] = field(default_factory=list)  # e.g. unity
    open_alerts: dict[str, int] | None = None  # None = could not be read
    last_dependabot_run_at: str | None = None
    last_dependabot_run_conclusion: str | None = None
    last_dependabot_run_job: str | None = None  # e.g. update-graph, dependabot-updates
    open_dependabot_prs: int | None = None
    oldest_dependabot_pr_days: int | None = None
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # checks that couldn't run
    notes: list[str] = field(default_factory=list)  # informational only

    @property
    def status(self) -> str:
        if self.failures:
            return "FAIL"
        if self.errors:
            return "ERROR"
        if self.warnings:
            return "WARN"
        if (
            self.unsupported or self.manifests or self.marked_no_dependencies
        ) and not self.ecosystems:
            return "N/A"
        return "OK"


class GitHub:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url=API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Send a request, waiting and retrying while GitHub rate-limits it."""
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            r = self.client.request(method, url, **kwargs)
            if not is_rate_limited(r) or attempt == RATE_LIMIT_RETRIES:
                return r
            wait = rate_limit_wait(r, attempt)
            if wait > MAX_RATE_LIMIT_WAIT:
                return r
            time.sleep(wait)
        return r

    def get(self, path: str, **params) -> httpx.Response:
        return self.request("GET", path, params=params or None)

    def graphql(self, query: str, **variables) -> httpx.Response:
        return self.request(
            "POST", "/graphql", json={"query": query, "variables": variables}
        )

    def paginate(self, path: str, **params) -> list[dict]:
        params.setdefault("per_page", 100)
        items: list[dict] = []
        response = self.request("GET", path, params=params)
        while True:
            if is_rate_limited(response):
                raise RateLimited(path)
            if response.status_code == 403:
                raise PermissionDenied(path)
            response.raise_for_status()
            items.extend(response.json())
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return items
            response = self.request("GET", next_url)


def days_since(timestamp: str) -> int:
    return (datetime.now(UTC) - datetime.fromisoformat(timestamp)).days


def check_alerts_enabled(gh: GitHub, a: RepoAudit) -> None:
    r = gh.get(f"/repos/{a.repo}/vulnerability-alerts")
    if r.status_code == 204:
        a.alerts_enabled = True
    elif r.status_code == 404:
        a.alerts_enabled = False
        a.failures.append("Dependabot alerts disabled")
    else:
        a.errors.append(
            f"can't read alert setting ({failure_detail(r, 'Administration: read')})"
        )


def check_security_updates(gh: GitHub, a: RepoAudit) -> None:
    r = gh.get(f"/repos/{a.repo}/automated-security-fixes")
    if r.status_code == 200:
        body = r.json()
        a.security_updates_enabled = body.get("enabled", False)
        a.security_updates_paused = body.get("paused", False)
    elif r.status_code == 404:
        a.security_updates_enabled = False
    else:
        a.errors.append(
            "can't read security-update setting "
            f"({failure_detail(r, 'Administration: read')})"
        )
        return
    if not a.security_updates_enabled:
        a.failures.append("security updates disabled")
    elif a.security_updates_paused:
        a.warnings.append("security updates paused")


def check_dependency_graph(gh: GitHub, a: RepoAudit) -> None:
    r = gh.get(f"/repos/{a.repo}/dependency-graph/sbom")
    if r.status_code == 200:
        a.dependency_graph_enabled = True
        ecosystems = set()
        for package in r.json().get("sbom", {}).get("packages", []):
            for ref in package.get("externalRefs", []):
                locator = ref.get("referenceLocator", "")
                if ref.get("referenceType") == "purl" and locator.startswith("pkg:"):
                    purl_type = locator[4:].split("/", 1)[0]
                    if purl_type != "github":  # the repo itself
                        ecosystems.add(ECOSYSTEM_NAMES.get(purl_type, purl_type))
        a.ecosystems = sorted(ecosystems)
        if not check_manifests(gh, a):
            return
        detect_unsupported(gh, a)
        if a.marked_no_dependencies:
            check_no_dependencies(gh, a)
        elif a.ecosystems:
            pass
        elif a.manifests:
            a.notes.append(f"no dependencies listed in {', '.join(a.manifests)}")
        elif not a.unsupported:
            a.failures.append("no dependency manifests detected")
    elif r.status_code in (403, 404) and "not enabled" in r.text.lower():
        a.dependency_graph_enabled = False
        a.failures.append("dependency graph disabled")
    else:
        a.errors.append(
            f"can't read dependency graph ({failure_detail(r, 'Contents: read')})"
        )


MANIFESTS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    dependencyGraphManifests(first: 100) {
      nodes { filename parseable exceedsMaxSize dependenciesCount }
    }
  }
}
"""


def check_manifests(gh: GitHub, a: RepoAudit) -> bool:
    """List the manifest files the dependency graph found, even ones with no
    dependencies (the SBOM only lists packages). Returns False if unreadable."""
    owner, name = a.repo.split("/", 1)
    r = gh.graphql(MANIFESTS_QUERY, owner=owner, name=name)
    body = r.json() if r.status_code == 200 else {}
    repo = (body.get("data") or {}).get("repository") or {}
    if "dependencyGraphManifests" not in repo or body.get("errors"):
        a.errors.append(f"can't read dependency manifests ({failure_detail(r)})")
        return False
    for m in repo["dependencyGraphManifests"]["nodes"]:
        a.manifests.append(m["filename"])
        if m.get("exceedsMaxSize"):
            a.warnings.append(f"{m['filename']} too large for the dependency graph")
        elif not m.get("parseable"):
            a.warnings.append(f"dependency graph can't parse {m['filename']}")
    return True


def check_no_dependencies(gh: GitHub, a: RepoAudit) -> None:
    """Verify that a repo marked as having no dependencies really has none."""
    marked = f"marked {NO_DEPENDENCIES_TOPIC}"
    if a.ecosystems:
        a.warnings.append(
            f"{marked} but dependency graph found {', '.join(a.ecosystems)}"
        )
        return
    if a.manifests:  # the graph read them and they list no dependencies
        a.notes.append("no dependencies (by design)")
        return
    # GitHub's language stats ignore data and prose (JSON, CSV, Markdown, ...),
    # so any language here means there is code that may need dependencies.
    r = gh.get(f"/repos/{a.repo}/languages")
    if r.status_code != 200:
        a.errors.append(f"can't read languages ({failure_detail(r)})")
        return
    a.languages = sorted(r.json())
    if a.languages:
        a.failures.append(
            f"{marked} but has code ({', '.join(a.languages)}) "
            "and no dependency manifests"
        )
    else:
        a.notes.append("no dependencies (by design)")


def detect_unsupported(gh: GitHub, a: RepoAudit) -> None:
    """Look for project types Dependabot can't scan (see UNSUPPORTED_MARKERS)."""
    r = gh.get(f"/repos/{a.repo}/git/trees/HEAD", recursive=1)
    if r.status_code != 200:
        return  # e.g. 409 for an empty repo: genuinely nothing to scan
    paths = [t["path"] for t in r.json().get("tree", []) if t["type"] == "blob"]
    found = {}
    for marker, name in UNSUPPORTED_MARKERS.items():
        hits = [p for p in paths if p == marker or p.endswith("/" + marker)]
        if hits:
            found[name] = hits
    a.unsupported = sorted(found)
    if "unity" in found:
        versions = sorted(
            {unity_editor_version(gh, a.repo, p) or "unknown" for p in found["unity"]}
        )
        a.notes.append(
            f"Unity {', '.join(versions)}: Dependabot can't scan Unity packages"
        )


def unity_editor_version(gh: GitHub, repo: str, path: str) -> str | None:
    r = gh.request(
        "GET",
        f"/repos/{repo}/contents/{path}",
        headers={"Accept": "application/vnd.github.raw"},
    )
    if r.status_code != 200:
        return None
    for line in r.text.splitlines():
        if line.startswith("m_EditorVersion:"):
            return line.split(":", 1)[1].strip()
    return None


def check_open_alerts(gh: GitHub, a: RepoAudit) -> None:
    if a.alerts_enabled is False:
        return  # the endpoint just returns 403 in this case
    try:
        alerts = gh.paginate(f"/repos/{a.repo}/dependabot/alerts", state="open")
    except PermissionDenied:
        a.errors.append("can't read Dependabot alerts (needs Dependabot alerts: read)")
        return
    counts = dict.fromkeys(SEVERITIES, 0)
    for alert in alerts:
        severity = alert.get("security_advisory", {}).get("severity", "low")
        counts[severity] = counts.get(severity, 0) + 1
    a.open_alerts = counts
    serious = counts["critical"] + counts["high"]
    if serious:
        a.failures.append(f"{serious} open critical/high alert(s)")
    elif sum(counts.values()):
        a.warnings.append(f"{sum(counts.values())} open medium/low alert(s)")


def check_dependabot_runs(gh: GitHub, a: RepoAudit) -> None:
    """Dependabot runs as a built-in ("dynamic") GitHub Actions workflow."""
    r = gh.get(f"/repos/{a.repo}/actions/runs", event="dynamic", per_page=50)
    if r.status_code != 200:
        a.errors.append(
            f"can't read Actions runs ({failure_detail(r, 'Actions: read')})"
        )
        return
    for run in r.json().get("workflow_runs", []):
        if run.get("path", "").startswith("dynamic/dependabot/"):
            a.last_dependabot_run_at = run["created_at"]
            a.last_dependabot_run_job = run["path"].removeprefix("dynamic/dependabot/")
            a.last_dependabot_run_conclusion = run.get("conclusion") or run.get(
                "status"
            )
            if run.get("conclusion") == "failure":
                a.failures.append("last Dependabot run failed")
            return


def check_dependabot_prs(gh: GitHub, a: RepoAudit) -> None:
    try:
        prs = gh.paginate(f"/repos/{a.repo}/pulls", state="open")
    except PermissionDenied:
        a.errors.append("can't read pull requests (needs Pull requests: read)")
        return
    mine = [p for p in prs if p.get("user", {}).get("login") == "dependabot[bot]"]
    a.open_dependabot_prs = len(mine)
    if mine:
        a.oldest_dependabot_pr_days = max(days_since(p["created_at"]) for p in mine)
        if a.oldest_dependabot_pr_days > STALE_PR_DAYS:
            a.warnings.append(f"Dependabot PR open {a.oldest_dependabot_pr_days} days")


CHECKS = (
    check_alerts_enabled,
    check_security_updates,
    check_dependency_graph,
    check_open_alerts,
    check_dependabot_runs,
    check_dependabot_prs,
)


def audit_repo(gh: GitHub, repo: dict) -> RepoAudit:
    a = RepoAudit(
        repo=repo["full_name"],
        url=repo["html_url"],
        fork=repo["fork"],
        private=repo["private"],
        marked_no_dependencies=NO_DEPENDENCIES_TOPIC in repo.get("topics", []),
    )
    for check in CHECKS:
        try:
            check(gh, a)
        except httpx.HTTPError as e:
            a.errors.append(f"{check.__name__}: {e}")
        except RateLimited:
            a.errors.append(
                f"{check.__name__}: rate limited by GitHub, try again later"
            )
    return a


# ---------- output ----------

STATUS_STYLE = {
    "OK": "[green]✅ OK[/]",
    "WARN": "[yellow]⚠️  WARN[/]",
    "FAIL": "[red]❌ FAIL[/]",
    "ERROR": "[magenta]❓ ERROR[/]",
    "N/A": "[dim]➖ N/A[/]",
}


def yes_no(value: bool | None) -> str:
    return {True: "[green]yes[/]", False: "[red]no[/]", None: "[dim]?[/]"}[value]


def alerts_cell(a: RepoAudit) -> str:
    if a.open_alerts is None:
        return "[dim]n/a[/]"
    parts = [f"{n} {sev[0].upper()}" for sev, n in a.open_alerts.items() if n]
    return f"[red]{', '.join(parts)}[/]" if parts else "[green]0[/]"


def last_run_cell(a: RepoAudit) -> str:
    if not a.last_dependabot_run_at:
        return "[dim]none[/]"
    text = f"{days_since(a.last_dependabot_run_at)}d ago"
    return (
        f"[red]{text} ✗[/]" if a.last_dependabot_run_conclusion == "failure" else text
    )


def print_summary(audits: list[RepoAudit], console: Console) -> None:
    table = Table(title="Dependabot audit", show_lines=False)
    table.add_column("Repository", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Alerts on")
    table.add_column("Sec. updates")
    table.add_column("Ecosystems")
    table.add_column("Open alerts")
    table.add_column("Last run")
    table.add_column("Problems")
    for a in audits:
        name = a.repo.split("/", 1)[1] + (" [dim](fork)[/]" if a.fork else "")
        sec = yes_no(a.security_updates_enabled)
        if a.security_updates_paused:
            sec = "[yellow]paused[/]"
        table.add_row(
            name,
            STATUS_STYLE[a.status],
            yes_no(a.alerts_enabled),
            sec,
            ", ".join(
                a.ecosystems + [f"[dim]{u} (unsupported)[/]" for u in a.unsupported]
            )
            or "[dim]—[/]",
            alerts_cell(a),
            last_run_cell(a),
            "; ".join(a.failures + a.warnings + a.errors + a.notes),
        )
    console.print(table)
    counts = {s: sum(a.status == s for a in audits) for s in STATUS_STYLE}
    console.print(
        f"{len(audits)} repos: " + ", ".join(f"{n} {s}" for s, n in counts.items())
    )


def write_csv(audits: list[RepoAudit], path: str) -> None:
    columns = [f.name for f in fields(RepoAudit) if f.name != "open_alerts"]
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["status", *columns, *(f"open_{s}" for s in SEVERITIES)])
        for a in audits:
            row = [getattr(a, c) for c in columns]
            row = ["; ".join(v) if isinstance(v, list) else v for v in row]
            sev = [
                a.open_alerts.get(s, 0) if a.open_alerts is not None else ""
                for s in SEVERITIES
            ]
            writer.writerow([a.status, *row, *sev])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--csv", metavar="PATH", help="also write full per-repo detail to this CSV file"
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="repos to audit in parallel (default 8)"
    )
    args = parser.parse_args()

    console = Console()
    token = os.environ.get(TOKEN_ENV)
    if not token:
        console.print(
            f"[red]Set {TOKEN_ENV} to a read-only fine-grained token. See README.md.[/]"
        )
        return 2

    gh = GitHub(token)
    try:
        repos = gh.paginate("/user/repos", affiliation="owner")
    except (PermissionDenied, RateLimited, httpx.HTTPStatusError) as e:
        console.print(f"[red]Could not list repositories: {e}[/]")
        return 2
    repos = sorted(
        (r for r in repos if not r["archived"]), key=lambda r: r["full_name"].lower()
    )

    with (
        console.status(f"Auditing {len(repos)} repositories…"),
        ThreadPoolExecutor(args.workers) as pool,
    ):
        audits = list(pool.map(lambda r: audit_repo(gh, r), repos))

    print_summary(audits, console)
    if args.csv:
        write_csv(audits, args.csv)
        console.print(f"Full detail written to {args.csv}")
    return 1 if any(a.status in ("FAIL", "ERROR") for a in audits) else 0


if __name__ == "__main__":
    sys.exit(main())

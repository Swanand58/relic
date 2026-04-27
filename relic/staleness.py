"""Staleness detection via gitpython — checks if graph.md is older than latest commit in subproject path."""

from pathlib import Path
from datetime import datetime, timezone

import git


def get_repo(base_path: Path) -> git.Repo:
    """Walk up from base_path to find the git repo root."""
    return git.Repo(base_path, search_parent_directories=True)


def latest_commit_time(repo: git.Repo, subproject_path: Path) -> datetime | None:
    """Return the datetime of the most recent commit touching subproject_path.

    Returns None if no commits exist for that path.
    """
    try:
        commits = list(repo.iter_commits(paths=str(subproject_path), max_count=1))
    except git.GitCommandError:
        return None

    if not commits:
        return None

    return datetime.fromtimestamp(commits[0].committed_date, tz=timezone.utc)


def graph_mtime(graph_path: Path) -> datetime | None:
    """Return the last-modified datetime of graph.md, or None if it doesn't exist."""
    if not graph_path.exists():
        return None
    return datetime.fromtimestamp(graph_path.stat().st_mtime, tz=timezone.utc)


def is_stale(subproject_name: str, subproject_path: Path, knowledge_dir: Path) -> dict:
    """Check if the graph.md for a subproject is stale.

    Returns a dict with keys:
        name        — subproject name
        stale       — bool
        reason      — human-readable explanation
        last_commit — datetime or None
        graph_mtime — datetime or None
    """
    graph_path = knowledge_dir / subproject_name / "graph.md"
    abs_subproject = subproject_path.resolve()

    try:
        repo = get_repo(abs_subproject)
    except git.InvalidGitRepositoryError:
        return {
            "name": subproject_name,
            "stale": True,
            "reason": "Not inside a git repository.",
            "last_commit": None,
            "graph_mtime": None,
        }

    last_commit = latest_commit_time(repo, abs_subproject)
    g_mtime = graph_mtime(graph_path)

    if g_mtime is None:
        return {
            "name": subproject_name,
            "stale": True,
            "reason": "graph.md does not exist.",
            "last_commit": last_commit,
            "graph_mtime": None,
        }

    if last_commit is None:
        return {
            "name": subproject_name,
            "stale": False,
            "reason": "No commits found for this path — graph considered fresh.",
            "last_commit": None,
            "graph_mtime": g_mtime,
        }

    stale = last_commit > g_mtime
    reason = (
        f"Commit {last_commit.isoformat()} is newer than graph {g_mtime.isoformat()}."
        if stale
        else f"Graph is up to date (last commit {last_commit.isoformat()})."
    )

    return {
        "name": subproject_name,
        "stale": stale,
        "reason": reason,
        "last_commit": last_commit,
        "graph_mtime": g_mtime,
    }


def check_all_staleness(subprojects: dict, knowledge_dir: Path) -> list[dict]:
    """Run is_stale for every subproject entry in the config dict.

    subprojects — parsed relic.yaml['subprojects'] dict
    """
    results = []
    for name, cfg in subprojects.items():
        path = Path(cfg["path"]).resolve()
        results.append(is_stale(name, path, knowledge_dir))
    return results

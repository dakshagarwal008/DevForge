"""Transparent, rule-based relevance scoring.

Every point added comes with a human-readable reason, so the UI and the AI
module can show *why* something was recommended. This is a ranking signal, not
an AI judgement: the AI module decides what to build, reuse or extend.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

from .models import IssueResult, LibraryResult, RepositoryResult
from .normalize import parse_dt

_TOKEN = re.compile(r"[a-z0-9+#]+")


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def matched_terms(text: str | None, terms: list[str]) -> list[str]:
    """Terms whose every word appears as a whole word in text (plural-insensitive)."""
    words = {_stem(w) for w in _TOKEN.findall((text or "").lower())}
    hits = []
    for term in terms:
        parts = [_stem(w) for w in _TOKEN.findall(term.lower())]
        if parts and all(p in words for p in parts):
            hits.append(term)
    return hits


def _compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


class _Scorer:
    def __init__(self) -> None:
        self.points = 0
        self.reasons: list[str] = []

    def add(self, points: int, reason: str) -> None:
        self.points += points
        self.reasons.append(reason)

    def result(self, fallback: str) -> tuple[int, list[str]]:
        return min(100, self.points), self.reasons or [fallback]


def score_repository(repo: RepositoryResult, terms: list[str], languages: list[str]) -> None:
    s = _Scorer()
    if hits := matched_terms(repo.full_name.split("/")[-1], terms):
        s.add(20, f"Name matches '{hits[0]}'")
    if hits := matched_terms(" ".join(repo.topics), terms):
        s.add(15, f"Topics include '{hits[0]}'")
    if hits := matched_terms(repo.description, terms):
        s.add(15, f"Description mentions '{hits[0]}'")
    if repo.language and repo.language.lower() in languages:
        s.add(10, f"Written in {repo.language}, one of your languages")
    if repo.maintenance and repo.maintenance.status == "active":
        s.add(20, repo.maintenance.detail)
    elif repo.maintenance and repo.maintenance.status == "slowing":
        s.add(8, repo.maintenance.detail)
    if (repo.stars or 0) >= 100:
        s.add(min(15, round(math.log10(repo.stars) * 3.5)), f"{_compact(repo.stars)} stars")
    if repo.license and repo.license.declared:
        s.add(5, f"Licensed under {repo.license.name}")
    else:
        s.reasons.append("Caution: no license declared, so reuse rights are unclear")
    repo.relevance_score, repo.relevance_reasons = s.result("Matched your language and search filters")


def score_issue(issue: IssueResult, terms: list[str], languages: list[str], level_label: str,
                top_repo_names: set[str], now: datetime) -> None:
    s = _Scorer()
    s.add(25, f"Labelled '{level_label}' by maintainers")
    if hits := matched_terms(issue.title, terms):
        s.add(20, f"Title mentions '{hits[0]}'")
    elif hits := matched_terms(issue.summary, terms):
        s.add(10, f"Description mentions '{hits[0]}'")
    repo = issue.repository
    if repo.language and repo.language.lower() in languages:
        s.add(10, f"Repository is written in {repo.language}")
    updated = parse_dt(issue.updated_at)
    if updated:
        days = max(0, (now - updated).days)
        if days <= 14:
            s.add(15, f"Updated {days} day{'s' if days != 1 else ''} ago")
        elif days <= 60:
            s.add(8, f"Updated {days} days ago")
    if issue.comments == 0:
        s.add(10, "No comments yet, so likely unclaimed")
    elif issue.comments <= 3:
        s.add(5, f"Only {issue.comments} comment{'s' if issue.comments != 1 else ''}")
    if repo.maintenance and repo.maintenance.status == "active":
        s.add(10, "Repository is actively maintained")
    if repo.full_name.lower() in top_repo_names:
        s.add(5, "Repository also appears in your top repository matches")
    issue.relevance_score, issue.relevance_reasons = s.result("Matched your search filters")


def score_library(lib: LibraryResult, terms: list[str], languages: list[str],
                  repo_names: set[str], *, suggested: bool = False,
                  npm_maintenance: float | None = None) -> None:
    s = _Scorer()
    registry = "PyPI" if lib.ecosystem == "pypi" else "npm"
    if suggested:
        s.add(20, f"Suggested for this project and confirmed to exist on {registry}")
    if hits := matched_terms(lib.name, terms):
        s.add(25, f"Package name matches '{hits[0]}'")
    elif hits := matched_terms(f"{lib.description or ''} {' '.join(lib.keywords)}", terms):
        s.add(15, f"Description/keywords mention '{hits[0]}'")
    wanted = "python" if lib.ecosystem == "pypi" else ("javascript", "typescript")
    if any(l in languages for l in ([wanted] if isinstance(wanted, str) else wanted)):
        s.add(10, f"Fits your {'Python' if lib.ecosystem == 'pypi' else 'JavaScript/TypeScript'} stack")
    if lib.maintenance and lib.maintenance.status == "active":
        s.add(25, lib.maintenance.detail)
    elif lib.maintenance and lib.maintenance.status == "slowing":
        s.add(12, lib.maintenance.detail)
    if npm_maintenance is not None and npm_maintenance > 0:
        s.add(round(15 * min(1.0, npm_maintenance)), f"npm maintenance score {npm_maintenance:.2f} (0-1)")
    if lib.license and lib.license.declared:
        s.add(5, f"Licensed under {lib.license.name}")
    elif lib.license is not None:
        s.reasons.append("Caution: no license declared, so reuse rights are unclear")
    if lib.linked_repository and lib.linked_repository.lower() in repo_names:
        s.add(10, f"Source repository {lib.linked_repository} is also in the GitHub results")
    lib.relevance_score, lib.relevance_reasons = s.result("Found in the package registry")

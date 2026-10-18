"""GitHub search query builders. Inputs must already be cleaned (see models.clean_term)."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config


def _lang(language: str) -> str:
    return f'language:"{language}"' if " " in language else f"language:{language}"


def _since(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).date().isoformat()


def build_repo_query(term: str | None, language: str | None, now: datetime) -> str:
    parts = []
    if term:
        parts.append(f"{term} in:name,description,topics")
    if language:
        parts.append(_lang(language))
    parts += ["archived:false", "fork:false", f"stars:>={config.MIN_REPO_STARS}",
              f"pushed:>{_since(now, config.REPO_PUSHED_WITHIN_DAYS)}"]
    return " ".join(parts)


def build_issue_query(term: str | None, language: str | None, label: str, now: datetime,
                      strict: bool = True) -> str:
    parts = []
    if term:
        parts.append(f"{term} in:title,body")
    parts += [f'label:"{label}"', "is:issue", "is:open", "no:assignee"]
    if strict:  # optional qualifiers; dropped if GitHub rejects the query
        parts += ["archived:false", "-linked:pr"]
    if language:
        parts.append(_lang(language))
    parts.append(f"updated:>{_since(now, config.ISSUE_UPDATED_WITHIN_DAYS)}")
    return " ".join(parts)

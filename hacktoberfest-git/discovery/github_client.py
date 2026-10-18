"""Thin wrapper around the official GitHub REST API (search + repository lookup)."""
from __future__ import annotations

import re

from . import config
from .errors import NotFoundError
from .http import JsonClient

_FULL_NAME = re.compile(r"^[\w.-]+/[\w.-]+$")


def _items(body) -> list[dict]:
    items = body.get("items") if isinstance(body, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


class GitHubClient:
    def __init__(self, token: str | None = None, http: JsonClient | None = None, **client_kwargs):
        token = token if token is not None else config.github_token()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.authenticated = bool(token)
        self.http = http or JsonClient(headers=headers, **client_kwargs)

    def search_repositories(self, query: str, per_page: int = 15) -> list[dict]:
        body = self.http.get_json(
            f"{config.GITHUB_API}/search/repositories",
            {"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
            ttl=config.SEARCH_TTL, resource="search")
        return _items(body)

    def search_issues(self, query: str, per_page: int = 20) -> list[dict]:
        body = self.http.get_json(
            f"{config.GITHUB_API}/search/issues",
            {"q": query, "sort": "updated", "order": "desc", "per_page": per_page},
            ttl=config.SEARCH_TTL, resource="search")
        return _items(body)

    def get_repository(self, full_name: str) -> dict:
        if not _FULL_NAME.match(full_name):
            raise NotFoundError(f"Invalid repository name: {full_name!r}")
        body = self.http.get_json(f"{config.GITHUB_API}/repos/{full_name}",
                                  ttl=config.DETAIL_TTL, resource="core")
        if not isinstance(body, dict):
            raise NotFoundError(f"Unexpected response for {full_name}")
        return body

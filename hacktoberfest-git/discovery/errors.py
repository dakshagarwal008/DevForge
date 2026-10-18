"""Typed errors so callers can tell "no results" from "API limit" from "broken"."""
from __future__ import annotations

from datetime import datetime


class DiscoveryError(Exception):
    """Base class for every error this module raises on purpose."""


class UpstreamError(DiscoveryError):
    """The remote API returned an unexpected response."""


class NetworkError(UpstreamError):
    """The remote API could not be reached (timeout, DNS, connection reset)."""


class NotFoundError(DiscoveryError):
    """The requested repository or package does not exist."""


class AuthError(DiscoveryError):
    """Credentials were rejected (for example an invalid GITHUB_TOKEN)."""


class QueryRejectedError(DiscoveryError):
    """The API refused the search query (HTTP 422)."""


class RateLimitError(DiscoveryError):
    """An API limit was hit. reset_at says when it is safe to retry, if known."""

    def __init__(self, message: str, reset_at: datetime | None = None):
        super().__init__(message)
        self.reset_at = reset_at

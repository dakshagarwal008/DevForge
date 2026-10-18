"""Small JSON-over-HTTP client with caching, retries and rate-limit handling.

Standard library only. One JsonClient is created per remote service so that the
GitHub token is never sent to PyPI or npm.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import config
from .errors import (AuthError, NetworkError, NotFoundError, QueryRejectedError,
                     RateLimitError, UpstreamError)

_MISS = object()
_NOT_FOUND = object()


@dataclass
class HttpResponse:
    status: int
    headers: dict  # lower-cased keys
    body: Any


Transport = Callable[[str, dict, float], HttpResponse]


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {"message": raw[:200].decode("utf-8", "replace")}


def urllib_transport(url: str, headers: dict, timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()},
                                _parse_body(resp.read()))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, {k.lower(): v for k, v in exc.headers.items()},
                            _parse_body(exc.read()))
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise NetworkError(f"Network error: {exc}") from exc


class TTLCache:
    """Tiny thread-safe in-memory cache."""

    def __init__(self, max_entries: int = 512, clock: Callable[[], float] = time.monotonic):
        self._items: dict[str, tuple[float, Any]] = {}
        self._max = max_entries
        self._clock = clock
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return _MISS
            expires, value = entry
            if expires < self._clock():
                del self._items[key]
                return _MISS
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            if key not in self._items and len(self._items) >= self._max:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (self._clock() + ttl, value)


class JsonClient:
    def __init__(self, headers: dict | None = None, transport: Transport = urllib_transport,
                 cache: TTLCache | None = None, timeout: float = 10.0, retries: int = 2,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] | None = None):
        self.headers = {"Accept": "application/json", "User-Agent": config.USER_AGENT,
                        **(headers or {})}
        self.transport = transport
        self.cache = cache or TTLCache()
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))
        # Last seen X-RateLimit-* values per resource ("search", "core").
        self.rate_limits: dict[str, dict] = {}

    # ------------------------------------------------------------ public ---
    def get_json(self, url: str, params: dict | None = None, *, ttl: float = 600,
                 resource: str | None = None) -> Any:
        full_url = url
        if params:
            full_url += "?" + urllib.parse.urlencode(sorted(params.items()))

        cached = self.cache.get(full_url)
        if cached is _NOT_FOUND:
            raise NotFoundError(f"Not found: {url}")
        if cached is not _MISS:
            return cached

        self._check_budget(resource)
        attempt = 0
        while True:
            try:
                response = self.transport(full_url, self.headers, self.timeout)
            except NetworkError:
                if attempt >= self.retries:
                    raise
                attempt += 1
                self.sleep(0.5 * attempt)
                continue

            self._record_rate_limit(response, resource)
            if response.status == 200:
                self.cache.set(full_url, response.body, ttl)
                return response.body
            if response.status >= 500 and attempt < self.retries:
                attempt += 1
                self.sleep(0.5 * attempt)
                continue
            if response.status == 404:
                self.cache.set(full_url, _NOT_FOUND, min(ttl, 300))
            self._raise_for_status(response, url)

    # ----------------------------------------------------------- helpers ---
    def _check_budget(self, resource: str | None) -> None:
        """Skip the network call if we already know the limit is exhausted."""
        snap = self.rate_limits.get(resource) if resource else None
        if snap and snap["remaining"] == 0 and snap["reset_at"] and snap["reset_at"] > self.now():
            raise RateLimitError(_limit_message(resource, snap["reset_at"]), snap["reset_at"])

    def _record_rate_limit(self, response: HttpResponse, resource: str | None) -> None:
        h = response.headers
        if "x-ratelimit-remaining" not in h:
            return
        try:
            snap = {
                "limit": int(h.get("x-ratelimit-limit", 0)),
                "remaining": int(h["x-ratelimit-remaining"]),
                "reset_at": datetime.fromtimestamp(int(h["x-ratelimit-reset"]), timezone.utc)
                if "x-ratelimit-reset" in h else None,
            }
        except ValueError:
            return
        self.rate_limits[resource or h.get("x-ratelimit-resource") or "default"] = snap

    def _reset_time(self, response: HttpResponse) -> datetime | None:
        h = response.headers
        try:
            if "retry-after" in h:
                return self.now() + timedelta(seconds=int(h["retry-after"]))
            if "x-ratelimit-reset" in h:
                return datetime.fromtimestamp(int(h["x-ratelimit-reset"]), timezone.utc)
        except ValueError:
            pass
        return None

    def _raise_for_status(self, response: HttpResponse, url: str) -> None:
        status, h = response.status, response.headers
        body = response.body
        message = body.get("message") if isinstance(body, dict) else None
        message = message if isinstance(message, str) else ""

        if status in (403, 429) and (
            status == 429 or h.get("x-ratelimit-remaining") == "0"
            or "retry-after" in h or "rate limit" in message.lower()
        ):
            reset_at = self._reset_time(response)
            raise RateLimitError(_limit_message(urllib.parse.urlparse(url).netloc, reset_at), reset_at)
        if status == 404:
            raise NotFoundError(f"Not found: {url}")
        if status == 401:
            raise AuthError("Credentials rejected (check GITHUB_TOKEN).")
        if status == 422:
            raise QueryRejectedError(message or "Query rejected by API.")
        raise UpstreamError(f"HTTP {status} from {urllib.parse.urlparse(url).netloc}: {message[:120]}")


def _limit_message(what: str | None, reset_at: datetime | None) -> str:
    when = f"retry after {reset_at.strftime('%H:%M:%S')} UTC" if reset_at else "retry later"
    return f"Rate limit reached for {what}; {when}."

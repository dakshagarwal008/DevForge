"""Package registry clients: PyPI (JSON API) and npm (registry + search API).

PyPI has no official keyword-search API, so PyPI packages are found by looking
up specific names (suggested names, or names of GitHub repositories) and then
checking that the package's declared source link really points at that repo.
"""
from __future__ import annotations

import re
import urllib.parse

from . import config
from .errors import NotFoundError
from .http import JsonClient

_PYPI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_NPM_NAME = re.compile(r"^(@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")


def normalize_pypi_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class PyPIClient:
    def __init__(self, http: JsonClient | None = None, **client_kwargs):
        self.http = http or JsonClient(**client_kwargs)

    def get_project(self, name: str) -> dict:
        if not _PYPI_NAME.match(name):
            raise NotFoundError(f"Invalid PyPI name: {name!r}")
        slug = urllib.parse.quote(normalize_pypi_name(name))
        body = self.http.get_json(f"{config.PYPI_API}/{slug}/json", ttl=config.DETAIL_TTL)
        if not isinstance(body, dict):
            raise NotFoundError(f"Unexpected PyPI response for {name}")
        return body


class NpmClient:
    def __init__(self, http: JsonClient | None = None, **client_kwargs):
        self.http = http or JsonClient(**client_kwargs)

    def search(self, text: str, size: int = 8) -> list[dict]:
        body = self.http.get_json(f"{config.NPM_REGISTRY}/-/v1/search",
                                  {"text": text, "size": size}, ttl=config.SEARCH_TTL)
        objects = body.get("objects") if isinstance(body, dict) else None
        return [o for o in objects if isinstance(o, dict)] if isinstance(objects, list) else []

    def get_latest(self, name: str) -> dict:
        if not _NPM_NAME.match(name):
            raise NotFoundError(f"Invalid npm name: {name!r}")
        body = self.http.get_json(
            f"{config.NPM_REGISTRY}/{urllib.parse.quote(name, safe='@')}/latest",
            ttl=config.DETAIL_TTL)
        if not isinstance(body, dict):
            raise NotFoundError(f"Unexpected npm response for {name}")
        return body

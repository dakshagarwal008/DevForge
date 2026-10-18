"""Orchestrates GitHub + registry lookups into one structured response."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable

from . import config
from .errors import DiscoveryError, NotFoundError, QueryRejectedError, RateLimitError
from .github_client import GitHubClient
from .models import (DiscoveryQuery, DiscoveryResponse, IssueResult, LibraryResult,
                     RepositoryRef, RepositoryResult, SourceStatus)
from .normalize import (build_maintenance, clean_summary, iso, normalize_github_url,
                        parse_dt, parse_npm, parse_pypi, parse_repository,
                        repo_full_name_from_api_url, to_ref)
from .queries import build_issue_query, build_repo_query
from .registries import NpmClient, PyPIClient, normalize_pypi_name
from .scoring import score_issue, score_library, score_repository

logger = logging.getLogger(__name__)

LEVEL_LABELS = {"beginner": "good first issue", "intermediate": "help wanted",
                "advanced": "help wanted"}
JS_LANGUAGES = {"javascript", "typescript"}
NO_RESULTS = ("No existing repositories or libraries matched. Try broader keywords. If nothing "
              "relevant exists, this may be a project worth building from scratch.")
_GONE = object()  # repository no longer exists


class _Budget:
    def __init__(self, n: int) -> None:
        self.left = n

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


class DiscoveryService:
    def __init__(self, github: GitHubClient | None = None, pypi: PyPIClient | None = None,
                 npm: NpmClient | None = None, now: Callable[[], datetime] | None = None):
        self.github = github or GitHubClient()
        self.pypi = pypi or PyPIClient()
        self.npm = npm or NpmClient()
        self.now = now or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------ public ---
    def discover(self, query: DiscoveryQuery) -> DiscoveryResponse:
        q = query.normalized()
        now = self.now()
        resp = DiscoveryResponse(query=q.to_dict(), generated_at=iso(now))
        if not q.languages and not q.terms:
            resp.warnings.append("Provide at least one language, interest or keyword.")
            return resp

        repos = self._guard(resp, "github_repositories", lambda: self._repositories(q, now, resp), [])
        issues = self._guard(resp, "github_issues", lambda: self._issues(q, repos, now, resp), [])
        libs = self._guard(resp, "libraries", lambda: self._libraries(q, repos, now, resp), [])

        self._annotate_repositories(repos, issues, libs, LEVEL_LABELS[q.experience_level])
        resp.repositories, resp.issues, resp.libraries = repos, issues, libs
        resp.coverage = "good" if (repos and libs) else "partial" if (repos or libs) else "none"
        if not (repos or issues or libs):
            resp.warnings.append(NO_RESULTS)
        resp.rate_limits = self._rate_limit_snapshot()
        return resp

    # ------------------------------------------------------ repositories ---
    def _repositories(self, q: DiscoveryQuery, now: datetime, resp: DiscoveryResponse):
        found: dict[str, RepositoryResult] = {}
        limited: RateLimitError | None = None
        errors: list[str] = []
        for lang in q.languages[:config.MAX_LANGUAGES] or [None]:
            for term in q.terms[:config.MAX_TERMS] or [None]:
                if limited:
                    break
                try:
                    items = self.github.search_repositories(build_repo_query(term, lang, now))
                except RateLimitError as exc:
                    limited = exc
                    break
                except DiscoveryError as exc:
                    errors.append(str(exc))
                    continue
                for item in items:
                    repo = parse_repository(item, now)
                    if repo and not repo.maintenance.archived:
                        found.setdefault(repo.full_name.lower(), repo)
        repos = list(found.values())
        for repo in repos:
            score_repository(repo, q.terms, q.languages)
        repos.sort(key=lambda r: (-r.relevance_score, -(r.stars or 0)))
        repos = repos[:q.max_repositories]
        self._record(resp, "github_repositories", len(repos), limited, errors,
                     "No repositories matched; try broader keywords.")
        return repos

    # ------------------------------------------------------------ issues ---
    def _search_issues(self, label: str, term: str | None, lang: str | None, now: datetime):
        try:
            return self.github.search_issues(build_issue_query(term, lang, label, now))
        except QueryRejectedError:
            return self.github.search_issues(build_issue_query(term, lang, label, now, strict=False))

    def _issues(self, q: DiscoveryQuery, repos: list[RepositoryResult], now: datetime,
                resp: DiscoveryResponse):
        label = LEVEL_LABELS[q.experience_level]
        term = q.terms[0] if q.terms else None
        found: dict[str, dict] = {}
        limited: RateLimitError | None = None
        errors: list[str] = []
        for lang in q.languages[:config.MAX_LANGUAGES] or [None]:
            try:
                items = self._search_issues(label, term, lang, now) if term else []
                if not items:  # nothing mentions the term: fall back to language-wide issues
                    items = self._search_issues(label, None, lang, now)
            except RateLimitError as exc:
                limited = exc
                break
            except DiscoveryError as exc:
                errors.append(str(exc))
                continue
            for item in items:
                found.setdefault(item.get("html_url"), item)

        candidates: list[tuple[dict, str]] = []
        for url, item in found.items():
            if not url or "pull_request" in item or item.get("locked"):
                continue
            if (item.get("comments") or 0) > config.MAX_ISSUE_COMMENTS:
                continue
            full = repo_full_name_from_api_url(item.get("repository_url") or "")
            if full:
                candidates.append((item, full))
        candidates = candidates[:config.MAX_ISSUE_CANDIDATES]

        known = {r.full_name.lower(): r for r in repos}
        refs: dict[str, object] = {}
        lookups, core_limited = 0, None
        for _, full in candidates:
            key = full.lower()
            if key in refs:
                continue
            if key in known:
                refs[key] = known[key]
            elif lookups >= config.MAX_REPO_LOOKUPS or core_limited:
                refs[key] = None
            else:
                lookups += 1
                try:
                    refs[key] = parse_repository(self.github.get_repository(full), now)
                except NotFoundError:
                    refs[key] = _GONE
                except RateLimitError as exc:
                    core_limited, refs[key] = exc, None
                except DiscoveryError:
                    refs[key] = None

        top_names, label_text = set(known), label
        issues: list[IssueResult] = []
        for item, full in candidates:
            ref = refs.get(full.lower())
            if ref is _GONE:
                continue
            if ref is not None and ref.maintenance.status in {"archived", "stale"}:
                continue
            repo_ref = to_ref(ref) if ref else RepositoryRef(
                full_name=full, url=f"https://github.com/{full}")
            issue = IssueResult(
                title=item.get("title") or "(untitled)", url=item["html_url"],
                number=item.get("number"), repository=repo_ref,
                labels=[l["name"] for l in item.get("labels") or []
                        if isinstance(l, dict) and l.get("name")],
                comments=item.get("comments") or 0,
                created_at=item.get("created_at"), updated_at=item.get("updated_at"),
                summary=clean_summary(item.get("body")))
            score_issue(issue, q.terms, q.languages, label_text, top_names, now)
            issues.append(issue)
        issues.sort(key=lambda i: -i.relevance_score)
        issues = issues[:q.max_issues]

        self._record(resp, "github_issues", len(issues), limited, errors,
                     f"No open '{label}' issues matched; try a broader keyword.")
        if core_limited:
            resp.warnings.append("Some repository details were unavailable because the GitHub "
                                 "core rate limit was reached; those issues show limited info."
                                 + self._token_hint())
        return issues

    # ---------------------------------------------------------- libraries ---
    def _libraries(self, q: DiscoveryQuery, repos: list[RepositoryResult], now: datetime,
                   resp: DiscoveryResponse):
        libs: dict[tuple[str, str], LibraryResult] = {}
        suggested: set[tuple[str, str]] = set()
        npm_scores: dict[str, float] = {}
        repo_names = {r.full_name.lower() for r in repos}
        budget = _Budget(config.MAX_REGISTRY_LOOKUPS)
        wants_py = not q.languages or "python" in q.languages
        wants_js = not q.languages or any(l in JS_LANGUAGES for l in q.languages)
        py_errors: list[str] = []
        npm_errors: list[str] = []
        py_limited = npm_limited = None

        def lookup_pypi(name: str, source_repo: str | None = None) -> LibraryResult | None:
            nonlocal py_limited
            if py_limited or not budget.take():
                return None
            try:
                lib = parse_pypi(self.pypi.get_project(name), now)
            except NotFoundError:
                return None
            except RateLimitError as exc:
                py_limited = exc
                return None
            except DiscoveryError as exc:
                py_errors.append(str(exc))
                return None
            if lib and source_repo and (lib.linked_repository or "").lower() != source_repo.lower():
                return None  # same name, different project: do not trust it
            return lib

        # 1) names suggested by the caller (e.g. the AI module): verify they exist
        for entry in q.candidate_libraries:
            prefix, _, bare = entry.partition(":")
            if prefix.lower() in {"pypi", "npm"} and bare:
                targets, name = [prefix.lower()], bare.strip()
            elif not q.languages:
                resp.warnings.append(f"Suggested library '{entry}' skipped: prefix it with "
                                     "'pypi:' or 'npm:' when no language is selected.")
                continue
            else:
                targets = (["pypi"] if wants_py else []) + (["npm"] if wants_js else [])
                name = entry.strip()
            found_any = False
            for eco in targets:
                lib = lookup_pypi(name) if eco == "pypi" else self._lookup_npm(
                    name, now, budget, npm_errors)
                if lib and lib.ecosystem == "npm" and lib.maintenance.status == "deprecated":
                    lib = None
                if lib:
                    found_any = True
                    libs.setdefault((lib.ecosystem, lib.name.lower()), lib)
                    suggested.add((lib.ecosystem, lib.name.lower()))
            if not found_any:
                resp.warnings.append(f"Suggested library '{entry}' was not found in the package "
                                     "registry and was left out.")

        # 2) npm keyword search (npm offers an official search endpoint)
        npm_count_before = len([k for k in libs if k[0] == "npm"])
        if wants_js and q.terms:
            hits: dict[str, dict] = {}
            for term in q.terms[:config.MAX_TERMS]:
                try:
                    for obj in self.npm.search(term, size=8):
                        pkg = obj.get("package") or {}
                        if pkg.get("name"):
                            score = obj.get("score") if isinstance(obj.get("score"), dict) else {}
                            prev = hits.get(pkg["name"])
                            if not prev or (score.get("final") or 0) > prev["final"]:
                                hits[pkg["name"]] = {"pkg": pkg, "final": score.get("final") or 0,
                                                     "maintenance": (score.get("detail") or {})
                                                     .get("maintenance")}
                except RateLimitError as exc:
                    npm_limited = exc
                    break
                except DiscoveryError as exc:
                    npm_errors.append(str(exc))
            ranked = sorted(hits.values(), key=lambda h: -h["final"])[:q.max_libraries + 2]
            for hit in ranked:
                name = hit["pkg"]["name"]
                if ("npm", name.lower()) in libs:
                    continue
                latest = None
                if budget.take():
                    try:
                        latest = self.npm.get_latest(name)
                    except DiscoveryError:
                        latest = None  # keep the result, license shown as unknown
                lib = parse_npm(hit["pkg"], latest, now)
                if lib and lib.maintenance.status != "deprecated":
                    libs.setdefault(("npm", name.lower()), lib)
                    if isinstance(hit["maintenance"], (int, float)):
                        npm_scores[name.lower()] = float(hit["maintenance"])

        # 3) PyPI packages that belong to Python repositories already found
        if wants_py:
            for repo in repos:
                if (repo.language or "").lower() != "python":
                    continue
                name = repo.full_name.split("/")[1]
                if ("pypi", normalize_pypi_name(name)) in {(e, normalize_pypi_name(n)) for e, n in libs}:
                    continue
                lib = lookup_pypi(name, source_repo=repo.full_name)
                if lib:
                    libs.setdefault(("pypi", lib.name.lower()), lib)

        for key, lib in libs.items():
            score_library(lib, q.terms, q.languages, repo_names, suggested=key in suggested,
                          npm_maintenance=npm_scores.get(lib.name.lower()))
        result = sorted(libs.values(), key=lambda l: -l.relevance_score)[:q.max_libraries]

        py_count = sum(1 for l in result if l.ecosystem == "pypi")
        npm_count = sum(1 for l in result if l.ecosystem == "npm")
        if wants_py:
            self._record(resp, "pypi", py_count, py_limited, py_errors,
                         "PyPI has no keyword search API; matches come from suggested names and "
                         "GitHub repositories only.")
        else:
            resp.sources.append(SourceStatus(source="pypi", status="skipped",
                                             message="Python not selected."))
        if wants_js and q.terms:
            self._record(resp, "npm", npm_count, npm_limited, npm_errors,
                         "No npm packages matched; try broader keywords.")
        else:
            resp.sources.append(SourceStatus(
                source="npm", status="skipped",
                message="JavaScript/TypeScript not selected." if not wants_js
                else "No keywords to search npm with."))
        return result

    def _lookup_npm(self, name: str, now: datetime, budget: _Budget, errors: list[str]):
        if not budget.take():
            return None
        try:
            latest = self.npm.get_latest(name)
        except NotFoundError:
            return None
        except DiscoveryError as exc:
            errors.append(str(exc))
            return None
        return parse_npm({"name": latest.get("name") or name, "version": latest.get("version"),
                          "description": latest.get("description"),
                          "keywords": latest.get("keywords") or [],
                          "date": None, "links": {}}, latest, now) if latest.get("name") else None

    # ----------------------------------------------------------- helpers ---
    def _annotate_repositories(self, repos, issues, libs, label: str) -> None:
        issue_counts: dict[str, int] = {}
        for issue in issues:
            key = issue.repository.full_name.lower()
            issue_counts[key] = issue_counts.get(key, 0) + 1
        lib_by_repo = {l.linked_repository.lower(): l for l in libs if l.linked_repository}
        for repo in repos:
            key = repo.full_name.lower()
            lib = lib_by_repo.get(key)
            if lib:
                reg = "PyPI" if lib.ecosystem == "pypi" else "npm"
                repo.relevance_reasons.append(
                    f"Published on {reg} as '{lib.name}', so you can reuse it instead of rebuilding")
                repo.suggested_action = "reuse" if lib.license and lib.license.declared else "study"
            elif issue_counts.get(key):
                n = issue_counts[key]
                repo.relevance_reasons.append(
                    f"{n} open '{label}' issue{'s' if n != 1 else ''} you could pick up")
                repo.suggested_action = "extend"

    def _record(self, resp: DiscoveryResponse, source: str, count: int,
                limited: RateLimitError | None, errors: list[str], empty_message: str) -> None:
        if limited:
            status, message = ("partial" if count else "rate_limited"), str(limited)
            if source.startswith("github"):
                message += self._token_hint()
        elif errors:
            status, message = ("partial" if count else "error"), "; ".join(errors[:2])
        elif count == 0:
            status, message = "empty", empty_message
        else:
            status, message = "ok", None
        retry = iso(limited.reset_at) if limited and limited.reset_at else None
        resp.sources.append(SourceStatus(source=source, status=status, count=count,
                                         message=message, retry_after=retry))
        if status in {"partial", "rate_limited", "error"}:
            resp.warnings.append(f"{source}: {message}")

    def _token_hint(self) -> str:
        return "" if self.github.authenticated else " Set GITHUB_TOKEN to raise the limit."

    def _guard(self, resp: DiscoveryResponse, source: str, fn, default):
        try:
            return fn()
        except Exception:  # keep the platform alive if one section has a bug
            logger.exception("Unexpected failure in %s", source)
            resp.sources.append(SourceStatus(source=source, status="error",
                                             message="Unexpected internal error; see server logs."))
            resp.warnings.append(f"{source}: unexpected internal error.")
            return default

    def _rate_limit_snapshot(self) -> dict:
        resources = {name: {"limit": s["limit"], "remaining": s["remaining"],
                            "reset_at": iso(s["reset_at"])}
                     for name, s in self.github.http.rate_limits.items()}
        return {"github": {"authenticated": self.github.authenticated, "resources": resources}}


# ------------------------------------------------------- module entry point ---
_service: DiscoveryService | None = None
_lock = threading.Lock()


def discover(query: DiscoveryQuery | dict) -> dict:
    """Main entry point for the backend. Returns a JSON-serialisable dict.

    A shared service instance is reused so the cache and rate-limit tracking
    persist across requests. Raises ValueError only for invalid input
    (e.g. unknown experience_level); upstream problems become warnings.
    """
    global _service
    if isinstance(query, dict):
        query = DiscoveryQuery.from_dict(query)
    with _lock:
        if _service is None:
            _service = DiscoveryService()
    return _service.discover(query).to_dict()

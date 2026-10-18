"""Turn raw API payloads into the module's result models."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import LibraryResult, LicenseInfo, Maintenance, RepositoryRef, RepositoryResult

_GITHUB_URL = re.compile(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", re.I)
_API_REPO_URL = re.compile(r"/repos/([^/]+/[^/]+)$")
_STATUS_LABEL = {"active": "Active", "slowing": "Slowing", "stale": "Stale",
                 "archived": "Archived", "deprecated": "Deprecated", "unknown": "Unknown"}


# ------------------------------------------------------------------ dates ---

def parse_dt(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def build_maintenance(last: datetime | None, now: datetime, *, archived: bool = False,
                      deprecated: bool = False, open_issues: int | None = None,
                      active_days: int = 90, slowing_days: int = 365,
                      subject: str = "push") -> Maintenance:
    days = max(0, (now - last).days) if last else None
    if deprecated:
        status = "deprecated"
    elif archived:
        status = "archived"
    elif days is None:
        status = "unknown"
    elif days <= active_days:
        status = "active"
    elif days <= slowing_days:
        status = "slowing"
    else:
        status = "stale"
    if days is None:
        detail = f"{_STATUS_LABEL[status]}: last {subject} date unavailable"
    else:
        ago = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"
        detail = f"{_STATUS_LABEL[status]}: last {subject} {ago}"
    return Maintenance(status=status, detail=detail, last_activity=iso(last),
                       days_since_activity=days, archived=archived, open_issues=open_issues)


# --------------------------------------------------------------- licenses ---

_RANK = {"permissive": 0, "weak-copyleft": 1, "other": 2, "strong-copyleft": 3}
_PERMISSIVE = {"mit", "mit-0", "isc", "unlicense", "zlib", "0bsd", "bsl-1.0", "cc0-1.0", "cc0"}


def _single_category(text: str) -> str:
    tokens = re.findall(r"[a-z0-9.\-]+", text.lower())
    joined = " ".join(tokens)
    if "affero" in joined or any("agpl" in t for t in tokens):
        return "strong-copyleft"
    if "lesser" in joined or "mozilla" in joined or any(
            t.startswith(("lgpl", "mpl", "epl", "eclipse")) for t in tokens):
        return "weak-copyleft"
    if "general public" in joined or any("gpl" in t for t in tokens):
        return "strong-copyleft"
    if any(t in _PERMISSIVE or t.startswith(("apache", "bsd")) for t in tokens):
        return "permissive"
    return "other"


def classify_license(text: str | None) -> str:
    """Coarse category from an SPDX id/expression or a license name.

    Informational only (not legal advice). For "A OR B" expressions the most
    permissive option is reported, since the user may choose it.
    """
    if not text or not text.strip():
        return "none"
    t = text.strip().lower()
    parts = re.split(r"\s+or\s+", t)
    if len(parts) > 1 and all(" " not in p.strip("() ") for p in parts):
        return min((_single_category(p) for p in parts), key=_RANK.get)
    return _single_category(t)


def undeclared_license() -> LicenseInfo:
    return LicenseInfo(name="No license declared", spdx_id=None, declared=False, category="none")


def license_from_text(text: str | None) -> LicenseInfo:
    text = (text or "").strip()
    if not text or text.upper() in {"UNLICENSED", "NONE", "NOASSERTION"}:
        return undeclared_license()
    looks_spdx = " " not in text and len(text) <= 40
    return LicenseInfo(name=text, spdx_id=text if looks_spdx else None, declared=True,
                       category=classify_license(text))


def license_from_github(obj) -> LicenseInfo:
    if not isinstance(obj, dict):
        return undeclared_license()
    spdx = obj.get("spdx_id")
    if spdx == "NOASSERTION":
        spdx = None
    name = obj.get("name") or spdx or "Unknown"
    category = classify_license(spdx or name)
    return LicenseInfo(name=name, spdx_id=spdx, declared=True,
                       category="other" if category == "none" else category)


# ------------------------------------------------------------- GitHub URLs ---

def normalize_github_url(url) -> str | None:
    """Return "owner/name" for any GitHub repo URL form, else None."""
    if not url or not isinstance(url, str):
        return None
    match = _GITHUB_URL.search(url)
    if not match:
        return None
    owner, name = match.group(1), match.group(2)
    if name.endswith(".git"):
        name = name[:-4]
    return f"{owner}/{name}" if owner and name else None


def repo_full_name_from_api_url(url: str) -> str | None:
    match = _API_REPO_URL.search(url or "")
    return match.group(1) if match else None


# ------------------------------------------------------------ GitHub items ---

def parse_repository(item: dict, now: datetime) -> RepositoryResult | None:
    full_name, url = item.get("full_name"), item.get("html_url")
    if not full_name or not url:
        return None
    return RepositoryResult(
        full_name=full_name, url=url,
        description=item.get("description"), language=item.get("language"),
        stars=item.get("stargazers_count"), forks=item.get("forks_count"),
        topics=[t for t in (item.get("topics") or []) if isinstance(t, str)],
        license=license_from_github(item.get("license")),
        maintenance=build_maintenance(parse_dt(item.get("pushed_at")), now,
                                      archived=bool(item.get("archived")),
                                      open_issues=item.get("open_issues_count")),
    )


def to_ref(repo: RepositoryRef) -> RepositoryRef:
    """Slim copy used inside issue results (drops scores, topics, forks)."""
    return RepositoryRef(full_name=repo.full_name, url=repo.url, description=repo.description,
                         language=repo.language, stars=repo.stars, license=repo.license,
                         maintenance=repo.maintenance)


def clean_summary(body, limit: int = 280) -> str:
    text = re.sub(r"<!--.*?-->", " ", body or "", flags=re.S)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ------------------------------------------------------------------- PyPI ---

def _pypi_license(info: dict) -> LicenseInfo:
    if info.get("license_expression"):
        return license_from_text(info["license_expression"])
    for classifier in info.get("classifiers") or []:
        if classifier.startswith("License ::"):
            name = classifier.split("::")[-1].strip()
            if name and name != "OSI Approved":
                return license_from_text(name)
    text = (info.get("license") or "").strip()
    if text and len(text) <= 60 and "\n" not in text:
        return license_from_text(text)
    if text:  # a full license text was pasted into the field
        return LicenseInfo(name="Custom license (see project page)", declared=True, category="other")
    return undeclared_license()


def parse_pypi(data: dict, now: datetime) -> LibraryResult | None:
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    name = info.get("name")
    if not name:
        return None
    uploads = [parse_dt(f.get("upload_time_iso_8601")) for f in data.get("urls") or []
               if isinstance(f, dict)]
    uploads = [u for u in uploads if u]
    project_urls = info.get("project_urls") if isinstance(info.get("project_urls"), dict) else {}
    candidates = [info.get("home_page"), *project_urls.values()]
    repo = next((r for r in map(normalize_github_url, candidates) if r), None)
    page = info.get("package_url") or ""
    if not page.startswith("https://pypi.org/project/"):
        page = f"https://pypi.org/project/{name}/"
    keywords = re.split(r"[,\s]+", info.get("keywords") or "")
    return LibraryResult(
        name=name, ecosystem="pypi", version=info.get("version"), url=page,
        description=info.get("summary"), keywords=[k for k in keywords if k][:10],
        license=_pypi_license(info),
        maintenance=build_maintenance(max(uploads) if uploads else None, now,
                                      deprecated=bool(info.get("yanked")),
                                      active_days=180, slowing_days=730, subject="release"),
        install_command=f"pip install {name}",
        homepage_url=info.get("home_page") or project_urls.get("Homepage"),
        source_repository_url=f"https://github.com/{repo}" if repo else None,
        linked_repository=repo, verified_via="pypi_json_api")


# -------------------------------------------------------------------- npm ---

def _npm_license(manifest: dict) -> LicenseInfo:
    lic = manifest.get("license")
    if isinstance(lic, dict):
        lic = lic.get("type")
    if not lic and isinstance(manifest.get("licenses"), list):
        types = [l.get("type") for l in manifest["licenses"] if isinstance(l, dict) and l.get("type")]
        lic = " OR ".join(types)
    return license_from_text(lic if isinstance(lic, str) else None)


def parse_npm(search_pkg: dict, latest: dict | None, now: datetime) -> LibraryResult | None:
    name = search_pkg.get("name")
    if not name:
        return None
    manifest = latest or {}
    links = search_pkg.get("links") if isinstance(search_pkg.get("links"), dict) else {}
    repo_field = manifest.get("repository")
    repo_url = repo_field.get("url") if isinstance(repo_field, dict) else repo_field
    repo = normalize_github_url(repo_url) or normalize_github_url(links.get("repository"))
    return LibraryResult(
        name=name, ecosystem="npm", version=manifest.get("version") or search_pkg.get("version"),
        url=links.get("npm") or f"https://www.npmjs.com/package/{name}",
        description=search_pkg.get("description") or manifest.get("description"),
        keywords=[k for k in (search_pkg.get("keywords") or []) if isinstance(k, str)][:10],
        license=_npm_license(manifest) if latest else None,
        maintenance=build_maintenance(parse_dt(search_pkg.get("date")), now,
                                      deprecated=bool(manifest.get("deprecated")),
                                      active_days=180, slowing_days=730, subject="release"),
        install_command=f"npm install {name}",
        homepage_url=links.get("homepage") or manifest.get("homepage"),
        source_repository_url=f"https://github.com/{repo}" if repo else None,
        linked_repository=repo, verified_via="npm_registry")

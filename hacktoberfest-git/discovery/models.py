"""Input and output contracts for the discovery module."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"
LEVELS = ("beginner", "intermediate", "advanced")
LANGUAGE_ALIASES = {
    "py": "python", "python3": "python",
    "js": "javascript", "node": "javascript", "nodejs": "javascript", "node.js": "javascript",
    "ts": "typescript", "golang": "go", "cpp": "c++", "csharp": "c#",
}
_OPERATORS = {"AND", "OR", "NOT"}


def clean_term(value: Any, limit: int = 60) -> str:
    """Make free text safe to embed in a GitHub search query.

    Strips punctuation GitHub treats as qualifiers or negation (user:, org:,
    quotes, a leading minus) so form input cannot change the query's meaning.
    """
    text = re.sub(r"[^\w\s.+#-]", " ", str(value))
    words = []
    for word in text.split():
        word = word.lstrip("-")
        if word:
            words.append(word.lower() if word in _OPERATORS else word)
    return " ".join(words)[:limit].strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[,\n]", value) if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _clamp(value: Any, default: int) -> int:
    try:
        return max(1, min(20, int(value)))
    except (TypeError, ValueError):
        return default


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


# ----------------------------------------------------------------- input ---

@dataclass(kw_only=True)
class DiscoveryQuery:
    experience_level: str = "beginner"
    languages: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    # Specific terms for the project idea (supplied by the AI module, if any).
    keywords: list[str] = field(default_factory=list)
    # Library names to verify against registries, e.g. "requests" or "npm:express".
    candidate_libraries: list[str] = field(default_factory=list)
    max_repositories: int = 8
    max_issues: int = 8
    max_libraries: int = 8

    @classmethod
    def from_dict(cls, data: dict) -> "DiscoveryQuery":
        """Build from a request body. Unknown keys (e.g. available_time) are ignored."""
        level = str(data.get("experience_level") or data.get("level") or "beginner")
        return cls(
            experience_level=level,
            languages=_as_list(data.get("languages")),
            interests=_as_list(data.get("interests")),
            keywords=_as_list(data.get("keywords")),
            candidate_libraries=_as_list(data.get("candidate_libraries")),
            max_repositories=_clamp(data.get("max_repositories"), 8),
            max_issues=_clamp(data.get("max_issues"), 8),
            max_libraries=_clamp(data.get("max_libraries"), 8),
        )

    def normalized(self) -> "DiscoveryQuery":
        level = self.experience_level.strip().lower()
        if level not in LEVELS:
            raise ValueError(f"experience_level must be one of {LEVELS}, got {self.experience_level!r}")
        languages = []
        for lang in self.languages:
            lang = clean_term(lang, 30).lower()
            languages.append(LANGUAGE_ALIASES.get(lang, lang))
        return DiscoveryQuery(
            experience_level=level,
            languages=_dedupe(languages),
            interests=_dedupe([clean_term(i) for i in self.interests]),
            keywords=_dedupe([clean_term(k) for k in self.keywords]),
            candidate_libraries=_dedupe([c.strip() for c in self.candidate_libraries])[:10],
            max_repositories=_clamp(self.max_repositories, 8),
            max_issues=_clamp(self.max_issues, 8),
            max_libraries=_clamp(self.max_libraries, 8),
        )

    @property
    def terms(self) -> list[str]:
        """Search terms, most specific first."""
        return _dedupe(self.keywords + self.interests)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- output ---

@dataclass(kw_only=True)
class LicenseInfo:
    name: str
    spdx_id: str | None = None
    declared: bool = True
    # permissive | weak-copyleft | strong-copyleft | other | none
    category: str = "other"


@dataclass(kw_only=True)
class Maintenance:
    # active | slowing | stale | archived | deprecated | unknown
    status: str
    detail: str
    last_activity: str | None = None
    days_since_activity: int | None = None
    archived: bool = False
    open_issues: int | None = None  # GitHub's count includes open pull requests


@dataclass(kw_only=True)
class RepositoryRef:
    full_name: str
    url: str
    description: str | None = None
    language: str | None = None
    stars: int | None = None
    license: LicenseInfo | None = None
    maintenance: Maintenance | None = None


@dataclass(kw_only=True)
class RepositoryResult(RepositoryRef):
    topics: list[str] = field(default_factory=list)
    forks: int | None = None
    relevance_score: int = 0
    relevance_reasons: list[str] = field(default_factory=list)
    # reuse | extend | study
    suggested_action: str = "study"
    verified_via: str = "github_api"


@dataclass(kw_only=True)
class IssueResult:
    title: str
    url: str
    number: int | None
    repository: RepositoryRef
    labels: list[str] = field(default_factory=list)
    comments: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    summary: str = ""
    relevance_score: int = 0
    relevance_reasons: list[str] = field(default_factory=list)
    suggested_action: str = "extend"
    verified_via: str = "github_api"


@dataclass(kw_only=True)
class LibraryResult:
    name: str
    ecosystem: str  # pypi | npm
    version: str | None
    url: str  # registry page
    description: str | None = None
    keywords: list[str] = field(default_factory=list)
    license: LicenseInfo | None = None  # None = could not be retrieved
    maintenance: Maintenance | None = None
    install_command: str = ""
    # Declared by the package author; not independently verified.
    homepage_url: str | None = None
    source_repository_url: str | None = None
    # "owner/name" when the package's source link matches a GitHub result.
    linked_repository: str | None = None
    relevance_score: int = 0
    relevance_reasons: list[str] = field(default_factory=list)
    suggested_action: str = "reuse"
    verified_via: str = ""


@dataclass(kw_only=True)
class SourceStatus:
    source: str
    # ok | partial | empty | rate_limited | error | skipped
    status: str
    count: int = 0
    message: str | None = None
    retry_after: str | None = None


@dataclass(kw_only=True)
class DiscoveryResponse:
    query: dict
    generated_at: str
    schema_version: str = SCHEMA_VERSION
    is_mock: bool = False
    # none | partial | good: whether existing repos AND libraries were found
    coverage: str = "none"
    repositories: list[RepositoryResult] = field(default_factory=list)
    issues: list[IssueResult] = field(default_factory=list)
    libraries: list[LibraryResult] = field(default_factory=list)
    sources: list[SourceStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rate_limits: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

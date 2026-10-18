"""Sample response so other modules can build against this one before it is wired up.

Clearly flagged with is_mock=true. The repository and library are real projects
(figures are approximate); the issue is a placeholder and does NOT exist.
"""
from __future__ import annotations

from .models import (DiscoveryResponse, IssueResult, LibraryResult, LicenseInfo, Maintenance,
                     RepositoryRef, RepositoryResult, SourceStatus)

_ACTIVE = Maintenance(status="active", detail="Active: last push 3 days ago",
                      last_activity="2026-10-15T09:00:00Z", days_since_activity=3, open_issues=400)
_BSD = LicenseInfo(name="BSD 3-Clause License", spdx_id="BSD-3-Clause", category="permissive")


def sample_response() -> dict:
    repo = RepositoryResult(
        full_name="scrapy/scrapy", url="https://github.com/scrapy/scrapy",
        description="Web crawling and web scraping framework", language="Python",
        stars=55000, forks=11000, topics=["crawler", "scraping", "web-scraping"],
        license=_BSD, maintenance=_ACTIVE, relevance_score=92,
        relevance_reasons=["Topics include 'web scraping'", "Active: last push 3 days ago",
                           "Published on PyPI as 'Scrapy', so you can reuse it instead of rebuilding"],
        suggested_action="reuse")
    issue = IssueResult(
        title="[MOCK] Improve documentation for a scraping example",
        url="https://github.com/example-org/example-repo/issues/1", number=1,
        repository=RepositoryRef(full_name="example-org/example-repo",
                                 url="https://github.com/example-org/example-repo",
                                 language="Python", stars=120, license=_BSD, maintenance=_ACTIVE),
        labels=["good first issue", "documentation"], comments=0,
        created_at="2026-10-10T08:00:00Z", updated_at="2026-10-14T08:00:00Z",
        summary="Placeholder issue used for UI development only.", relevance_score=80,
        relevance_reasons=["Labelled 'good first issue' by maintainers",
                           "No comments yet, so likely unclaimed"],
        verified_via="mock")
    lib = LibraryResult(
        name="Scrapy", ecosystem="pypi", version="2.13.0", url="https://pypi.org/project/Scrapy/",
        description="A high-level Web Crawling and Web Scraping framework",
        keywords=["crawler", "scraping"], license=_BSD,
        maintenance=Maintenance(status="active", detail="Active: last release 40 days ago",
                                last_activity="2026-09-08T00:00:00Z", days_since_activity=40),
        install_command="pip install Scrapy", source_repository_url="https://github.com/scrapy/scrapy",
        linked_repository="scrapy/scrapy", relevance_score=88,
        relevance_reasons=["Description/keywords mention 'web scraping'",
                           "Source repository scrapy/scrapy is also in the GitHub results"],
        verified_via="pypi_json_api")
    resp = DiscoveryResponse(
        query={"experience_level": "beginner", "languages": ["python"],
               "interests": ["web scraping"]},
        generated_at="2026-10-18T00:00:00Z", is_mock=True, coverage="good",
        repositories=[repo], issues=[issue], libraries=[lib],
        sources=[SourceStatus(source="github_repositories", status="ok", count=1),
                 SourceStatus(source="github_issues", status="ok", count=1),
                 SourceStatus(source="pypi", status="ok", count=1),
                 SourceStatus(source="npm", status="skipped", message="JavaScript/TypeScript not selected.")],
        warnings=["Mock data: do not display as real results."])
    return resp.to_dict()

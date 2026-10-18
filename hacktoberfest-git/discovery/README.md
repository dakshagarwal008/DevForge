# discovery: GitHub repository, issue and library discovery

Owner: Person 3. Finds **real** repositories, beginner-friendly issues and
installable libraries from official APIs (GitHub REST, PyPI JSON, npm registry).
Every result carries a URL taken from an API response, a plain-English relevance
reason, and maintenance and license info. Standard library only; Python 3.10+.

This module does not touch the frontend or the AI recommendation module. The only
integration points are `discover()` and the JSON shape below.

## Use it

```python
from discovery import discover

result = discover({
    "experience_level": "beginner",          # beginner | intermediate | advanced
    "languages": ["python"],                 # drives GitHub + registry choice
    "interests": ["web scraping"],           # from the user form
    "keywords": ["scraper"],                 # optional: specific terms from the AI module
    "candidate_libraries": ["requests"],     # optional: names to verify ("npm:express" also works)
    "max_repositories": 8, "max_issues": 8, "max_libraries": 8,
})                                           # -> JSON-serialisable dict
```

`discover()` raises `ValueError` only for invalid input (e.g. unknown level, so
return HTTP 400). API failures never raise; they appear in `sources` and `warnings`.
Unknown keys such as `available_time` are ignored.

Smoke-test with live APIs: `python -m discovery --language python --interest "web scraping"`
Sample data with no network: `python -m discovery --mock` (or `discovery.mock.sample_response()`).

## Setup

- No packages to install. Copy the `discovery/` folder into the backend.
- Optional but recommended: set `GITHUB_TOKEN` (a fine-grained token with no extra
  scopes is enough). Without it GitHub allows only 10 search requests/min and 60
  core requests/hour. With it: 30/min and 5000/hour. Never commit the token.
- Tests: `python -m unittest discover -s discovery/tests -t .` (offline).

## Response shape (`schema_version` "1.0")

```
query, generated_at, is_mock, coverage ("none"|"partial"|"good")
repositories[]: full_name, url, description, language, stars, forks, topics,
                license{name, spdx_id, declared, category}, maintenance{status, detail,
                last_activity, days_since_activity, archived, open_issues},
                relevance_score (0-100), relevance_reasons[], suggested_action
                ("reuse"|"extend"|"study"), verified_via
issues[]:       title, url, number, repository{full_name, url, language, stars, license,
                maintenance, ...}, labels[], comments, created_at, updated_at, summary,
                relevance_score, relevance_reasons[], suggested_action ("extend"), verified_via
libraries[]:    name, ecosystem ("pypi"|"npm"), version, url, description, keywords[],
                license, maintenance, install_command, homepage_url,
                source_repository_url, linked_repository, relevance_score,
                relevance_reasons[], suggested_action ("reuse"), verified_via
sources[]:      source, status ("ok"|"partial"|"empty"|"rate_limited"|"error"|"skipped"),
                count, message, retry_after
warnings[]:     human-readable strings, safe to show
rate_limits:    last-seen GitHub limits
```

`maintenance.status`: `active` (push within 90 days), `slowing` (within a year),
`stale`, `archived`, `deprecated`, `unknown`. Libraries use 180 days / 2 years since
the latest release. `license.category` is `permissive`, `weak-copyleft`,
`strong-copyleft`, `other` or `none`. It is informational, not legal advice.

## How it behaves

- **Verified links.** `url` fields come straight from API responses. A PyPI package
  found via a GitHub repo is accepted only if the package's declared source link points
  back at that repo (a same-name package with a different source is discarded). Names
  suggested by the AI module are checked against the registry, and names that don't
  exist are dropped with a warning, which guards against invented libraries.
  `homepage_url` and `source_repository_url` are declared by package authors and are
  not independently verified.
- **No results.** Empty lists plus a `sources[].status` of `empty` and a warning. If
  nothing at all is found, `coverage` is `none`: a useful signal for the AI module's
  "build it yourself" suggestion.
- **API limits.** Responses are cached in memory (10 min searches, 1 h details). Once
  GitHub reports the limit is exhausted, no more calls are made until the reset time;
  the other sources still return, `sources[].status` becomes `rate_limited`/`partial`,
  and `retry_after` holds the reset time. Timeouts and 5xx errors are retried twice.
- **Level.** Beginner issues need the `good first issue` label; intermediate and
  advanced use `help wanted`. Issues with 10+ comments, assignees, locked threads, or in
  archived/stale (no push in a year) repositories are dropped.
- **Scoring is rules, not AI.** Each point maps to a reason string. The AI module
  should decide what to build, reuse or extend; `suggested_action` is only a hint.

## Known limits

- PyPI has no keyword-search API, so Python libraries come from suggested names and
  from Python repos found on GitHub. npm has a real search, so JS/TS discovery is broader.
  Other ecosystems (crates.io, Maven, Go) are not covered yet. Add a client in
  `registries.py` and a branch in `service._libraries`.
- `maintenance.open_issues` is GitHub's `open_issues_count`, which includes open PRs.
- Calls run sequentially (up to ~8 GitHub searches and ~12 registry lookups per
  request in the worst case). Repeats are served from cache. A thread pool is the
  obvious next step if latency matters.
- Written and unit-tested against API response shapes with a fake transport and a local
  HTTP server. Run the CLI once with real network access before the demo.

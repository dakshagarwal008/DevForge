"""Endpoints and tunables for the discovery module."""
import os

GITHUB_API = "https://api.github.com"
PYPI_API = "https://pypi.org/pypi"
NPM_REGISTRY = "https://registry.npmjs.org"
USER_AGENT = "ai-project-discovery/0.1 (hackathon project)"

# Request budget per discover() call. GitHub's search API allows 10 requests/min
# unauthenticated and 30/min with a token, so keep these small.
MAX_LANGUAGES = 2          # languages used for GitHub searches
MAX_TERMS = 2              # keywords/interests used for GitHub searches
MAX_REPO_LOOKUPS = 10      # GET /repos/{owner}/{name} calls to enrich issues
MAX_REGISTRY_LOOKUPS = 12  # PyPI / npm detail lookups

# Filters
MIN_REPO_STARS = 20
REPO_PUSHED_WITHIN_DAYS = 365
ISSUE_UPDATED_WITHIN_DAYS = 90
MAX_ISSUE_COMMENTS = 10
MAX_ISSUE_CANDIDATES = 30

# Cache lifetimes (seconds)
SEARCH_TTL = 600
DETAIL_TTL = 3600


def github_token():
    """Read the token from the environment. Never hard-code or commit it."""
    return os.environ.get("GITHUB_TOKEN") or None

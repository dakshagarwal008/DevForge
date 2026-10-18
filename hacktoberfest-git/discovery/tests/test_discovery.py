"""Offline tests. A fake transport replays payloads shaped like the real APIs."""
import unittest
from datetime import datetime, timedelta, timezone

from discovery.errors import (AuthError, NotFoundError, RateLimitError, UpstreamError)
from discovery.github_client import GitHubClient
from discovery.http import HttpResponse, JsonClient
from discovery.models import DiscoveryQuery, clean_term
from discovery.normalize import (build_maintenance, classify_license, normalize_github_url,
                                 parse_pypi)
from discovery.queries import build_issue_query, build_repo_query
from discovery.registries import NpmClient, PyPIClient
from discovery.service import DiscoveryService

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
nosleep = lambda s: None


def ago(days):
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


class FakeTransport:
    def __init__(self):
        self.routes, self.calls = [], []

    def add(self, needle, *responses):
        self.routes.append((needle, list(responses)))

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        for needle, responses in self.routes:
            if needle in url:
                item = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(item, Exception):
                    raise item
                return item
        return HttpResponse(404, {}, {"message": "Not Found"})

    def count(self, needle):
        return sum(1 for url, _ in self.calls if needle in url)


def ok(body, headers=None):
    return HttpResponse(200, headers or {}, body)


def make_service(fake, token="secret-token"):
    kw = dict(transport=fake, sleep=nosleep, now=lambda: NOW)
    return DiscoveryService(github=GitHubClient(token=token, **kw), pypi=PyPIClient(**kw),
                            npm=NpmClient(**kw), now=lambda: NOW)


def gh_repo(full, **over):
    base = {"full_name": full, "name": full.split("/")[1], "html_url": f"https://github.com/{full}",
            "description": "", "language": "Python", "topics": [], "stargazers_count": 500,
            "forks_count": 10, "open_issues_count": 5, "archived": False, "pushed_at": ago(10),
            "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"}}
    base.update(over)
    return base


def gh_issue(full, number, **over):
    base = {"title": f"Issue {number}", "html_url": f"https://github.com/{full}/issues/{number}",
            "number": number, "repository_url": f"https://api.github.com/repos/{full}",
            "labels": [{"name": "good first issue"}], "comments": 1, "locked": False,
            "created_at": ago(20), "updated_at": ago(3), "body": "Some <!-- hidden --> text"}
    base.update(over)
    return base


class PureFunctionTests(unittest.TestCase):
    def test_license_classification(self):
        cases = {"MIT": "permissive", "Apache-2.0": "permissive", "BSD-3-Clause": "permissive",
                 "MIT OR Apache-2.0": "permissive", "GPL-3.0": "strong-copyleft",
                 "AGPL-3.0-only": "strong-copyleft", "LGPL-2.1": "weak-copyleft",
                 "MPL-2.0": "weak-copyleft", "GNU General Public License v3 or later (GPLv3+)": "strong-copyleft",
                 "Limited use license": "other", "": "none", None: "none"}
        for text, expected in cases.items():
            self.assertEqual(classify_license(text), expected, text)

    def test_github_url_forms(self):
        for url in ["https://github.com/psf/requests", "git+https://github.com/psf/requests.git",
                    "git@github.com:psf/requests.git", "https://github.com/psf/requests/tree/main"]:
            self.assertEqual(normalize_github_url(url), "psf/requests", url)
        self.assertIsNone(normalize_github_url("https://gitlab.com/a/b"))
        self.assertIsNone(normalize_github_url(None))

    def test_maintenance_status(self):
        self.assertEqual(build_maintenance(NOW - timedelta(days=5), NOW).status, "active")
        self.assertEqual(build_maintenance(NOW - timedelta(days=200), NOW).status, "slowing")
        self.assertEqual(build_maintenance(NOW - timedelta(days=900), NOW).status, "stale")
        self.assertEqual(build_maintenance(None, NOW).status, "unknown")
        self.assertEqual(build_maintenance(NOW, NOW, archived=True).status, "archived")

    def test_query_input_is_sanitised(self):
        self.assertEqual(clean_term('user:octocat -secret "AI OR ML"'), "user octocat secret AI or ML")
        q = DiscoveryQuery(experience_level="Beginner", languages=["JS", "py", "js"],
                           interests=["org:evil", "web"]).normalized()
        self.assertEqual(q.languages, ["javascript", "python"])
        self.assertNotIn(":", " ".join(q.interests))
        with self.assertRaises(ValueError):
            DiscoveryQuery(experience_level="expert").normalized()

    def test_from_dict_ignores_unknown_keys_and_splits_strings(self):
        q = DiscoveryQuery.from_dict({"level": "advanced", "languages": "python, go",
                                      "available_time": "5h", "max_issues": 99})
        self.assertEqual((q.experience_level, q.languages, q.max_issues), ("advanced", ["python", "go"], 20))

    def test_query_builders(self):
        r = build_repo_query("web scraping", "python", NOW)
        self.assertIn("web scraping in:name,description,topics", r)
        self.assertIn("pushed:>2025-10-18", r)
        i = build_issue_query(None, "c++", "good first issue", NOW)
        self.assertIn('label:"good first issue"', i)
        self.assertNotIn("-linked:pr", build_issue_query(None, None, "x", NOW, strict=False))

    def test_pypi_license_and_link(self):
        data = {"info": {"name": "Foo", "version": "1.0", "summary": "s", "license": "",
                         "classifiers": ["License :: OSI Approved :: MIT License"],
                         "project_urls": {"Source": "https://github.com/a/foo"}},
                "urls": [{"upload_time_iso_8601": ago(10)}]}
        lib = parse_pypi(data, NOW)
        self.assertEqual(lib.license.category, "permissive")
        self.assertEqual(lib.linked_repository, "a/foo")
        self.assertEqual(lib.url, "https://pypi.org/project/Foo/")
        self.assertEqual(lib.maintenance.status, "active")


class HttpClientTests(unittest.TestCase):
    def client(self, fake, **kw):
        return JsonClient(transport=fake, sleep=nosleep, now=lambda: NOW, **kw)

    def test_retries_5xx_then_succeeds(self):
        fake = FakeTransport()
        fake.add("x.test", HttpResponse(503, {}, None), ok({"a": 1}))
        self.assertEqual(self.client(fake).get_json("https://x.test/a"), {"a": 1})
        self.assertEqual(fake.count("x.test"), 2)

    def test_gives_up_after_retries(self):
        fake = FakeTransport()
        fake.add("x.test", HttpResponse(500, {}, {"message": "boom"}))
        with self.assertRaises(UpstreamError):
            self.client(fake).get_json("https://x.test/a")
        self.assertEqual(fake.count("x.test"), 3)

    def test_rate_limit_detected_and_blocks_further_calls(self):
        reset = int((NOW + timedelta(minutes=5)).timestamp())
        fake = FakeTransport()
        fake.add("api.test", HttpResponse(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
                                          {"message": "API rate limit exceeded"}))
        c = self.client(fake)
        with self.assertRaises(RateLimitError) as ctx:
            c.get_json("https://api.test/search", {"q": "a"}, resource="search")
        self.assertEqual(int(ctx.exception.reset_at.timestamp()), reset)
        with self.assertRaises(RateLimitError):  # second call must not hit the network
            c.get_json("https://api.test/search", {"q": "b"}, resource="search")
        self.assertEqual(fake.count("api.test"), 1)

    def test_secondary_limit_via_retry_after(self):
        fake = FakeTransport()
        fake.add("api.test", HttpResponse(403, {"retry-after": "30"}, {"message": "secondary"}))
        with self.assertRaises(RateLimitError) as ctx:
            self.client(fake).get_json("https://api.test/x")
        self.assertEqual(ctx.exception.reset_at, NOW + timedelta(seconds=30))

    def test_404_is_cached_and_401_maps_to_auth_error(self):
        fake = FakeTransport()
        fake.add("api.test/missing", HttpResponse(404, {}, {"message": "Not Found"}))
        fake.add("api.test/auth", HttpResponse(401, {}, {"message": "Bad credentials"}))
        c = self.client(fake)
        for _ in range(2):
            with self.assertRaises(NotFoundError):
                c.get_json("https://api.test/missing")
        self.assertEqual(fake.count("api.test/missing"), 1)
        with self.assertRaises(AuthError):
            c.get_json("https://api.test/auth")

    def test_success_is_cached(self):
        fake = FakeTransport()
        fake.add("x.test", ok({"a": 1}))
        c = self.client(fake)
        c.get_json("https://x.test/a"); c.get_json("https://x.test/a")
        self.assertEqual(fake.count("x.test"), 1)


class ServiceTests(unittest.TestCase):
    def build_fake(self):
        fake = FakeTransport()
        fake.add("api.github.com/search/repositories", ok({"items": [
            gh_repo("scrapy/scrapy", description="A fast web crawling and web scraping framework",
                    topics=["crawler", "web-scraping"], stargazers_count=53000),
            gh_repo("someone/old-scraper", description="scraper", stargazers_count=300,
                    pushed_at=ago(200), license=None),
            gh_repo("acme/dead", archived=True),
        ]}, {"x-ratelimit-remaining": "28", "x-ratelimit-limit": "30",
             "x-ratelimit-reset": str(int((NOW + timedelta(minutes=1)).timestamp()))}))
        fake.add("api.github.com/search/issues", ok({"items": [
            gh_issue("scrapy/scrapy", 1, title="Docs: clarify scraper settings"),
            gh_issue("new/repo", 2),
            gh_issue("scrapy/scrapy", 3, pull_request={"url": "x"}),
            gh_issue("scrapy/scrapy", 4, comments=15),
            gh_issue("gone/repo", 5),
            gh_issue("stale/repo", 6),
        ]}))
        fake.add("api.github.com/repos/new/repo", ok(gh_repo("new/repo", stargazers_count=80)))
        fake.add("api.github.com/repos/stale/repo", ok(gh_repo("stale/repo", pushed_at=ago(800))))
        fake.add("pypi.org/pypi/scrapy/json", ok({
            "info": {"name": "Scrapy", "version": "2.11.0", "summary": "Crawling framework",
                     "license_expression": "BSD-3-Clause", "keywords": "crawler scraping",
                     "project_urls": {"Source": "https://github.com/scrapy/scrapy"}},
            "urls": [{"upload_time_iso_8601": ago(20)}]}))
        fake.add("pypi.org/pypi/old-scraper/json", ok({  # same name, different project
            "info": {"name": "old-scraper", "version": "0.1", "project_urls": {"Source": "https://github.com/other/thing"}},
            "urls": []}))
        fake.add("pypi.org/pypi/requests/json", ok({
            "info": {"name": "requests", "version": "2.32.0", "summary": "HTTP for humans",
                     "license_expression": "Apache-2.0",
                     "project_urls": {"Source": "https://github.com/psf/requests"}},
            "urls": [{"upload_time_iso_8601": ago(60)}]}))
        return fake

    def test_end_to_end_python(self):
        fake = self.build_fake()
        q = DiscoveryQuery(experience_level="beginner", languages=["python"],
                           interests=["web scraping"], keywords=["scraper"],
                           candidate_libraries=["requests", "totally-made-up-lib"])
        resp = make_service(fake).discover(q)

        names = [r.full_name for r in resp.repositories]
        self.assertEqual(names[0], "scrapy/scrapy")
        self.assertNotIn("acme/dead", names)
        top = resp.repositories[0]
        self.assertEqual(top.suggested_action, "reuse")
        self.assertTrue(any(r.startswith("Active") for r in top.relevance_reasons))
        self.assertTrue(all(r.url.startswith("https://github.com/") for r in resp.repositories))

        issue_urls = [i.url for i in resp.issues]
        self.assertIn("https://github.com/scrapy/scrapy/issues/1", issue_urls)
        self.assertIn("https://github.com/new/repo/issues/2", issue_urls)
        for excluded in (3, 4, 5, 6):  # PR, too many comments, missing repo, stale repo
            self.assertFalse(any(u.endswith(f"/issues/{excluded}") for u in issue_urls), excluded)
        new_repo_issue = next(i for i in resp.issues if i.number == 2)
        self.assertEqual(new_repo_issue.repository.stars, 80)
        self.assertNotIn("hidden", new_repo_issue.summary)
        self.assertEqual(resp.issues[0].number, 1)  # title match outranks

        libs = {l.name: l for l in resp.libraries}
        self.assertEqual(set(libs), {"Scrapy", "requests"})
        self.assertEqual(libs["Scrapy"].linked_repository, "scrapy/scrapy")
        self.assertEqual(libs["Scrapy"].install_command, "pip install Scrapy")
        self.assertTrue(any("Suggested" in r for r in libs["requests"].relevance_reasons))
        self.assertTrue(any("totally-made-up-lib" in w and "not found" in w for w in resp.warnings))

        status = {s.source: s.status for s in resp.sources}
        self.assertEqual(status, {"github_repositories": "ok", "github_issues": "ok",
                                  "pypi": "ok", "npm": "skipped"})
        self.assertEqual(resp.coverage, "good")
        self.assertEqual(resp.rate_limits["github"]["resources"]["search"]["remaining"], 28)

    def test_github_token_never_sent_to_registries(self):
        fake = self.build_fake()
        make_service(fake).discover(DiscoveryQuery(languages=["python"], keywords=["scraper"],
                                                   candidate_libraries=["requests"]))
        self.assertTrue(any("api.github.com" in u and h.get("Authorization") == "Bearer secret-token"
                            for u, h in fake.calls))
        for url, headers in fake.calls:
            if "api.github.com" not in url:
                self.assertNotIn("Authorization", headers, url)

    def test_github_rate_limit_gives_partial_results_from_other_sources(self):
        reset = int((NOW + timedelta(minutes=10)).timestamp())
        fake = FakeTransport()
        fake.add("api.github.com/search", HttpResponse(
            403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
            {"message": "API rate limit exceeded"}))
        fake.add("registry.npmjs.org/-/v1/search", ok({"objects": [
            {"package": {"name": "x-ray", "version": "1.0.0", "description": "web scraper",
                         "keywords": ["scraper"], "date": ago(30),
                         "links": {"npm": "https://www.npmjs.com/package/x-ray"}},
             "score": {"final": 0.8, "detail": {"maintenance": 0.9}}},
            {"package": {"name": "old-dep", "version": "1.0.0", "description": "scraper", "date": ago(30)},
             "score": {"final": 0.5, "detail": {}}}]}))
        fake.add("registry.npmjs.org/x-ray/latest", ok({
            "name": "x-ray", "version": "1.0.0", "license": "MIT",
            "repository": {"type": "git", "url": "git+https://github.com/matthewmueller/x-ray.git"}}))
        fake.add("registry.npmjs.org/old-dep/latest", ok({"name": "old-dep", "version": "1.0.0",
                                                          "license": "MIT", "deprecated": "use another"}))
        resp = make_service(fake, token=None).discover(
            DiscoveryQuery(languages=["javascript"], keywords=["scraper"]))

        self.assertEqual(resp.repositories, [])
        self.assertEqual([l.name for l in resp.libraries], ["x-ray"])  # deprecated one dropped
        lib = resp.libraries[0]
        self.assertEqual(lib.license.spdx_id, "MIT")
        self.assertEqual(lib.linked_repository, "matthewmueller/x-ray")
        status = {s.source: s for s in resp.sources}
        self.assertEqual(status["github_repositories"].status, "rate_limited")
        self.assertEqual(status["github_issues"].status, "rate_limited")
        self.assertIsNotNone(status["github_repositories"].retry_after)
        self.assertIn("GITHUB_TOKEN", status["github_repositories"].message)
        self.assertEqual(fake.count("api.github.com/search"), 1)  # stopped hammering after the limit
        self.assertEqual(resp.coverage, "partial")

    def test_nothing_found_is_reported_not_raised(self):
        fake = FakeTransport()
        fake.add("api.github.com/search", ok({"items": []}))
        fake.add("registry.npmjs.org/-/v1/search", ok({"objects": []}))
        resp = make_service(fake).discover(DiscoveryQuery(languages=["javascript"], keywords=["zzzz"]))
        self.assertEqual((resp.repositories, resp.issues, resp.libraries), ([], [], []))
        self.assertEqual(resp.coverage, "none")
        self.assertTrue(any("worth building" in w for w in resp.warnings))
        self.assertEqual({s.source: s.status for s in resp.sources}["github_repositories"], "empty")

    def test_issue_query_falls_back_when_rejected_and_when_term_has_no_hits(self):
        fake = FakeTransport()
        fake.add("api.github.com/search/repositories", ok({"items": []}))
        fake.add("api.github.com/search/issues",
                 HttpResponse(422, {}, {"message": "Validation Failed"}),  # strict query rejected
                 ok({"items": []}),                                         # relaxed, with term: no hits
                 HttpResponse(422, {}, {"message": "Validation Failed"}),
                 ok({"items": [gh_issue("a/b", 9)]}))                       # relaxed, language-wide
        fake.add("api.github.com/repos/a/b", ok(gh_repo("a/b")))
        resp = make_service(fake).discover(DiscoveryQuery(languages=["python"], keywords=["niche"]))
        self.assertEqual([i.number for i in resp.issues], [9])

    def test_empty_query_returns_warning(self):
        resp = make_service(FakeTransport()).discover(DiscoveryQuery())
        self.assertTrue(resp.warnings)
        self.assertEqual(resp.coverage, "none")

    def test_unprefixed_candidate_needs_language(self):
        fake = FakeTransport()
        fake.add("api.github.com/search", ok({"items": []}))
        resp = make_service(fake).discover(DiscoveryQuery(keywords=["x"], candidate_libraries=["requests"]))
        self.assertTrue(any("prefix" in w for w in resp.warnings))
        self.assertEqual(fake.count("pypi.org"), 0)

    def test_unexpected_bug_in_one_section_does_not_crash(self):
        fake = FakeTransport()
        fake.add("api.github.com/search/repositories", ok({"items": [gh_repo("a/b")]}))
        svc = make_service(fake)
        svc._issues = lambda *a, **k: 1 / 0
        with self.assertLogs("discovery.service", level="ERROR"):
            resp = svc.discover(DiscoveryQuery(languages=["python"], keywords=["b"]))
        self.assertEqual(len(resp.repositories), 1)
        self.assertEqual({s.source: s.status for s in resp.sources}["github_issues"], "error")


if __name__ == "__main__":
    unittest.main()


class RealTransportTests(unittest.TestCase):
    """Exercises urllib_transport against a throwaway local HTTP server."""

    @classmethod
    def setUpClass(cls):
        import json, threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/ok"):
                    body, status, headers = {"hello": "world", "ua": self.headers["User-Agent"]}, 200, {}
                elif self.path.startswith("/limited"):
                    body, status = {"message": "API rate limit exceeded"}, 403
                    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4102444800"}
                elif self.path.startswith("/html"):
                    self.send_response(200); self.end_headers(); self.wfile.write(b"<html>oops</html>"); return
                else:
                    body, status, headers = {"message": "Not Found"}, 404, {}
                raw = json.dumps(body).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_success_not_found_rate_limit_and_non_json(self):
        c = JsonClient(sleep=nosleep)
        body = c.get_json(f"{self.base}/ok")
        self.assertEqual(body["hello"], "world")
        self.assertIn("ai-project-discovery", body["ua"])
        with self.assertRaises(NotFoundError):
            c.get_json(f"{self.base}/nope")
        with self.assertRaises(RateLimitError) as ctx:
            c.get_json(f"{self.base}/limited", resource="search")
        self.assertEqual(ctx.exception.reset_at.year, 2100)
        self.assertEqual(c.get_json(f"{self.base}/html"), {"message": "<html>oops</html>"})

    def test_connection_failure_becomes_network_error_after_retries(self):
        from discovery.errors import NetworkError
        c = JsonClient(sleep=nosleep, retries=1, timeout=1)
        with self.assertRaises(NetworkError):
            c.get_json("http://127.0.0.1:9/unreachable")

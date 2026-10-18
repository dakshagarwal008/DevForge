from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "hactoberfest"
sys.path.insert(0, str(ROOT / "hacktoberfest-git"))
from discovery import discover  # noqa: E402

MODEL = "gemma-4-26b-a4b-it"
GEMMA_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
CATEGORIES = {"AI & data", "Web apps", "E-commerce", "Developer tools", "Games", "Social good", "Productivity",
              "iOS apps", "Android apps", "Linux apps"}
LANGUAGES = {"JavaScript", "Python", "TypeScript", "Java", "Go", "C", "HTML", "C++"}
LEVELS = {"beginner", "intermediate", "advanced"}


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def generate_category_guide(category: str, languages: list[str], level: str, hours: int,
                            idea_count: int = 3, avoid_ideas: list[str] | None = None) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemma is not configured yet. Add GEMINI_API_KEY to the project .env file.")

    prompt = (
        "Create several distinct buildable project ideas and GitHub search terms for a project discovery page. "
        "Return only one valid JSON object with exactly these keys: heading (a short category heading), "
        "summary (string, one sentence), keywords (array of 3-5 short GitHub repository search terms), "
        f"project_ideas (array of exactly {idea_count} distinct project ideas; each has name (2-6 words), description (one sentence), "
        "and structure (array of 6-10 short folder or file paths forming a realistic starter tree)). "
        "Make the ideas meaningfully different and feasible for the selected experience and available time. "
        "Keep every idea specific to the selected category. For iOS apps, suggest iPhone or iPad apps; "
        "for Android apps, suggest Android mobile apps; for Linux apps, suggest Linux desktop or command-line apps. "
        "Use the selected language when one is provided. Keep paths concise and do not use markdown fences. "
        "Do not invent repository names or URLs. The keywords should help find real repositories.\n"
        f"Category: {category}\nLanguages: {', '.join(languages) or 'any'}\n"
        f"Experience: {level}\nTime available: {hours} hours per week"
    )
    excluded_names = [str(name).strip()[:90] for name in (avoid_ideas or []) if str(name).strip()][:18]
    if excluded_names:
        prompt += "\nDo not repeat or closely rephrase these previously suggested project names: " + "; ".join(excluded_names)
    payload = {
        "systemInstruction": {
            "parts": [{"text": "You are an expert project discovery assistant. Keep the heading specific to the selected category and experience level."}]
        },
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2100 if idea_count > 3 else 1100,
                             "thinkingConfig": {"thinkingLevel": "minimal"}},
    }
    request = urllib.request.Request(
        GEMMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=55) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError("Gemma rejected the API key. Check GEMINI_API_KEY in .env.") from exc
        if exc.code == 429:
            raise RuntimeError("Gemma free-tier rate limit reached. Wait a little and try again.") from exc
        raise RuntimeError(f"Gemma request failed (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Could not reach the Gemma API. Check your internet connection.") from exc

    parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts if part.get("text"))
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.IGNORECASE)
    try:
        generated = json.loads(text)
    except json.JSONDecodeError:
        # Some Gemma responses add a short preface around the requested JSON.
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise RuntimeError("Gemma returned an unexpected response. Please try again.")
        try:
            generated = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemma returned an unexpected response. Please try again.") from exc

    heading = str(generated.get("heading", "")).strip()[:90]
    summary = str(generated.get("summary", "")).strip()[:240]
    keywords = [str(word).strip()[:60] for word in generated.get("keywords", []) if str(word).strip()]
    project_ideas = []
    raw_ideas = generated.get("project_ideas", [])
    if isinstance(raw_ideas, list):
        for idea in raw_ideas[:idea_count]:
            if not isinstance(idea, dict):
                continue
            name = str(idea.get("name", "")).strip()[:90]
            description = str(idea.get("description", "")).strip()[:240]
            raw_structure = idea.get("structure", [])
            structure = [path.strip()[:100] for path in raw_structure
                         if isinstance(path, str) and path.strip()][:12] if isinstance(raw_structure, list) else []
            if name and description and structure:
                project_ideas.append({"name": name, "description": description, "structure": structure})
    if not heading or not summary or not keywords or len(project_ideas) < idea_count:
        raise RuntimeError("Gemma did not return a complete project guide. Please try again.")
    return {"heading": heading, "summary": summary, "keywords": keywords[:5],
            "project_ideas": project_ideas}


def normalize_preferences(payload: dict) -> tuple[str, str, list[str], int]:
    category = str(payload.get("category", ""))
    if category not in CATEGORIES:
        raise ValueError("Choose one of the available project categories.")
    level = str(payload.get("experience", "beginner"))
    if level not in LEVELS:
        raise ValueError("Choose a valid experience level.")
    raw_languages = payload.get("languages", [])
    if not isinstance(raw_languages, list):
        raw_languages = []
    languages = [str(language) for language in raw_languages if str(language) in LANGUAGES][:2]
    try:
        hours = max(1, min(20, int(payload.get("hours", 5))))
    except (TypeError, ValueError):
        hours = 5
    return category, level, languages, hours


def find_project_ideas(payload: dict) -> dict:
    category, level, languages, hours = normalize_preferences(payload)
    raw_avoid = payload.get("avoid_ideas", [])
    avoid_ideas = raw_avoid if isinstance(raw_avoid, list) else []
    guide = generate_category_guide(category, languages, level, hours, idea_count=6, avoid_ideas=avoid_ideas)
    return {
        "heading": guide["heading"],
        "summary": guide["summary"],
        "category": category,
        "experience": level,
        "project_ideas": guide["project_ideas"],
    }


def find_projects(payload: dict) -> dict:
    category, level, languages, hours = normalize_preferences(payload)

    guide = generate_category_guide(category, languages, level, hours)
    result = discover({
        "experience_level": level,
        "languages": [language.lower() for language in languages],
        "interests": [category],
        "keywords": guide["keywords"],
        "max_repositories": 12,
        "max_issues": 1,
        "max_libraries": 1,
    })
    projects = []
    for repo in result.get("repositories", []):
        projects.append({
            "full_name": repo.get("full_name", ""),
            "url": repo.get("url", ""),
            "description": repo.get("description") or "Open-source project related to your selections.",
            "language": repo.get("language"),
            "stars": repo.get("stars") or 0,
            "forks": repo.get("forks") or 0,
            "relevance_score": repo.get("relevance_score", 0),
            "relevance_reasons": repo.get("relevance_reasons", []),
            "suggested_action": repo.get("suggested_action", "study"),
            "maintenance": (repo.get("maintenance") or {}).get("status", "unknown"),
        })
    return {
        **guide,
        "category": category,
        "experience": level,
        "projects": projects,
        "warnings": result.get("warnings", []),
        "sources": result.get("sources", []),
    }


class AppHandler(BaseHTTPRequestHandler):
    server_version = "ProjectDiscovery/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:
        request_path = urlparse(self.path).path
        if request_path not in ("/api/projects", "/api/ideas"):
            self.send_json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                self.send_json(400, {"error": "Invalid request size"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object.")
            response = find_project_ideas(payload) if request_path == "/api/ideas" else find_projects(payload)
            self.send_json(200, response)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send_json(503, {"error": str(exc)})
        except Exception:
            self.send_json(502, {"error": "Project discovery failed. Please try again."})

    def do_GET(self) -> None:
        request_path = unquote(urlparse(self.path).path)
        if request_path == "/api/health":
            self.send_json(200, {"ok": True, "gemma_configured": bool(os.environ.get("GEMINI_API_KEY"))})
            return
        if request_path in ("/", "/home"):
            request_path = "/forntend.html"
        elif request_path == "/search":
            request_path = "/search page.html"
        elif request_path == "/ai-agent":
            request_path = "/ai-agent.html"
        try:
            candidate = (WEB_ROOT / request_path.lstrip("/")).resolve()
            if not candidate.is_relative_to(WEB_ROOT.resolve()) or not candidate.is_file():
                self.send_error(404)
                return
            data = candidate.read_bytes()
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_error(404)


if __name__ == "__main__":
    load_dotenv()
    port = int(os.environ.get("PORT", "8000"))
    print(f"Project discovery running at http://127.0.0.1:{port}")
    print("Gemma API key: configured" if os.environ.get("GEMINI_API_KEY") else "Gemma API key: missing (add it to .env to enable AI headings)")
    ThreadingHTTPServer(("127.0.0.1", port), AppHandler).serve_forever()

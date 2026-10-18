# DevForge

DevForge helps developers turn an interest into a project they can build. Explore two ways to get started: ask the AI Project Agent for ideas, or use the Project Finder to discover real open-source repositories related to your goals.

## What you can do

- **Get project ideas:** Choose a category, programming language, experience level, and weekly time commitment. The AI Agent suggests six projects, each with a short description and an expandable starter folder structure. Generate another set whenever you want.
- **Find real projects:** Use the Project Finder to look for public GitHub repositories that match your category and preferences. Repository links come from GitHub's API.
- **Explore different platforms:** Categories include AI and data, web apps, e-commerce, developer tools, games, social good, productivity, iOS apps, Android apps, and Linux apps.
- **Choose a language:** JavaScript, Python, TypeScript, Java, Go, C, HTML, or C++.

## Get started

### Requirements

- Python 3.10 or newer
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey)
- An internet connection for AI suggestions and live repository results

The project uses Python's standard library, so there are no packages to install.

### 1. Get the project files

Clone the repository and enter the project directory:

```bash
git clone https://github.com/dakshagarwal008/hacktoberfest.git
cd hacktoberfest
```

### 2. Add your API key

Create a `.env` in the project root and copy `.env.example` to it. 
On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

On macOS or Linux:

```bash
cp .env.example .env
```

Open `.env` and add your Gemini API key:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GITHUB_TOKEN=
```

`GITHUB_TOKEN` is optional. It can increase GitHub API rate limits. Never commit `.env` or share its keys; the file is ignored by Git.

### 3. Run DevForge

From the project root, start the local server:

```bash
python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. Stop the server with `Ctrl+C`.

To use another port, set the `PORT` environment variable before starting the server. For example, in PowerShell:

```powershell
$env:PORT = "8001"
python run.py
```

## Using the app

### AI Project Agent

1. Open **AI Project Agent** from the home page or visit `/ai-agent`.
2. Pick a project category and optionally select a language.
3. Set your experience level and available hours per week.
4. Choose **Ask the AI agent** to see six project suggestions.
5. Expand **View suggested folder structure** on an idea to see its starter layout.
6. Choose **Generate different ideas** to request another set.

### Project Finder

1. Open **Project Finder** from the home page or visit `/search`.
2. Choose your category, language, experience level, and time commitment.
3. Search to see matching public repositories and their details.
4. Follow a repository link to explore the project on GitHub.

The AI Agent generates project concepts and structures. The Project Finder uses the discovery module to retrieve repository information from public APIs; repository availability depends on the selected filters and API limits.

## How it works

```text
Browser pages (HTML, CSS, JavaScript)
                |
                v
         Python web server
           /           \
          v             v
   Gemini API       discovery module
   project ideas    GitHub and registries
```

- `hactoberfest/` contains the home page, Project Finder page, and AI Agent page.
- `run.py` serves the pages and handles the `/api/ideas` and `/api/projects` requests.
- `hacktoberfest-git/discovery/` finds and ranks repository, issue, and library results using public APIs.

## Configuration and troubleshooting

- **AI requests say the key is missing:** Confirm `.env` is in the project root, contains `GEMINI_API_KEY=...`, and restart `python run.py` after editing it.
- **GitHub results are limited or empty:** GitHub applies API rate limits, and results depend on the selected category and language. Add a `GITHUB_TOKEN` to `.env` if you need a higher limit.
- **Port 8000 is already in use:** Start the app on another port using the `PORT` environment variable.
- **Check whether the server is running:** Visit `http://127.0.0.1:8000/api/health`. The response reports whether the Gemini key was loaded; it does not reveal the key.

## Project structure

```text
.
|-- hactoberfest/
|   |-- ai-agent.html
|   |-- forntend.html
|   `-- search page.html
|-- hacktoberfest-git/
|   `-- discovery/
|-- .env.example
|-- README.md
`-- run.py
```
## Security

Keep API keys in your local `.env` file. Do not put secrets in frontend files, commit them, or include them in screenshots or issue reports. The `.env.example` file is safe to share because it contains blank values.

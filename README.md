# Run the project discovery app

This static frontend is served by a small Python backend. The backend asks Gemma 4 for a category-specific headline and GitHub search keywords, then uses the `hacktoberfest-git/discovery` module to find verified public repositories.

## Setup

1. Install Python 3.10 or newer.
2. Copy `.env.example` to `.env`.
3. Create a free Gemini API key in [Google AI Studio](https://aistudio.google.com/app/apikey) and set it as `GEMINI_API_KEY` in `.env`.
4. Start the app from the project root:

   ```powershell
   python run.py
   ```

5. Open `http://127.0.0.1:8000` and choose **Find a project**.

No Python packages are required. The app uses Gemma 4 (`gemma-4-26b-a4b-it`) for the category header and keywords, and the discovery module for real repository data. GitHub links shown in results come from the discovery API response.

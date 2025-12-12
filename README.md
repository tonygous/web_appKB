# web_appKB

A FastAPI application that crawls web pages and exports a knowledge base-friendly Markdown bundle.

## Running locally

1. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   python -m pip install -r requirements-dev.txt  # for linting/tests
   ```
3. Launch the API server:
   ```bash
   uvicorn main:app --reload
   ```
4. Open http://127.0.0.1:8000/ in your browser to access the UI.

When using the FastAPI crawler with Playwright-powered rendering, install the browser drivers first:
```bash
python -m playwright install
```

## Self-check

Run the quick project health checks locally:
```bash
python -m pytest
ruff check .
ruff format --check .
```

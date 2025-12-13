import re
from collections.abc import Callable

# mypy: ignore-errors
import httpx
from fastapi.testclient import TestClient
from httpx import Response
from pytest import MonkeyPatch

from crawler import AsyncCrawler
from main import app

client = TestClient(app)

PAGE1_HTML = """
<!doctype html>
<html>
<head><title>Welcome to Example</title></head>
<body>
<nav>
  <ul>
    <li><a href="#">Navigation Noise</a></li>
    <li><a href="#">Another Link</a></li>
  </ul>
</nav>
<main>
  <h1>Getting Started</h1>
  <p>This demo page introduces the documentation site.</p>
  <p>It provides a concise overview of the features and workflows for new users.</p>
  <p>Repeated explanatory text helps the crawler treat the page as substantive.</p>
  <a href="/page2">Continue to deeper guide</a>
</main>
<footer>Footer links should be ignored</footer>
</body>
</html>
"""

PAGE2_HTML = """
<!doctype html>
<html>
<head><title>Deep Dive</title></head>
<body>
<header>
  <h1>Header Banner</h1>
</header>
<aside>
  <p>Table of contents placeholder</p>
</aside>
<article>
  <h2>Usage Guide</h2>
  <p>This section contains detailed guidance on daily configuration tasks.</p>
  <p>Include enough text so the crawler deems the page substantial.</p>
  <p>Another sentence describing features and operational expectations for teams.</p>
  <p>Final paragraph to push the total character count above the threshold.</p>
  <p>Supplementary material with configuration notes for offline environments.</p>
  <p>Additional content mentioning pagination and contextual link following.</p>
</article>
</body>
</html>
"""


def build_mock_transport(host: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> Response:
        if request.url == httpx.URL(f"{host}/page1"):
            return Response(200, text=PAGE1_HTML, headers={"content-type": "text/html"})
        if request.url == httpx.URL(f"{host}/page2"):
            return Response(200, text=PAGE2_HTML, headers={"content-type": "text/html"})
        return Response(404)

    return httpx.MockTransport(handler)


def override_client_factory(host: str) -> Callable[[AsyncCrawler], httpx.AsyncClient]:
    transport = build_mock_transport(host)

    def _factory(self: AsyncCrawler) -> httpx.AsyncClient:  # noqa: ANN001
        return httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
            timeout=self.request_timeout,
        )

    return _factory


def test_generate_uses_mocked_http_and_returns_markdown(monkeypatch: MonkeyPatch):
    host = "https://example.test"
    monkeypatch.setattr(
        AsyncCrawler,
        "_create_client",
        override_client_factory(host),
        raising=True,
    )

    response = client.post(
        "/generate",
        data={
            "url": f"{host}/page1",
            "max_pages": 2,
            "respect_robots": "false",
            "use_sitemap": "false",
            "strip_links": "true",
            "strip_images": "true",
            "min_text_chars": 200,
        },
    )

    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/markdown" in content_type or "application/zip" in content_type

    body_bytes = response.content
    assert len(body_bytes) > 500

    content_text = response.text
    assert "# example.test" in content_text
    assert re.search(r"Getting Started", content_text)
    assert re.search(r"Usage Guide", content_text)

    assert "Navigation Noise" not in content_text
    assert "Footer links should be ignored" not in content_text

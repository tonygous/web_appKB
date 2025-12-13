import asyncio

import httpx
import pytest

from crawler import AsyncCrawler, PageRecord
from main import app


def test_health_and_version_endpoints():
    transport = httpx.ASGITransport(app=app)
    async def _run():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            version = await client.get("/version")
            assert version.status_code == 200
            assert "git_sha" in version.json()

    asyncio.run(_run())


def test_generate_rejects_excessive_pages():
    transport = httpx.ASGITransport(app=app)
    async def _run():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/generate",
                data={"url": "http://example.com", "max_pages": 600},
            )

        return response

    response = asyncio.run(_run())

    assert response.status_code == 400
    assert "max_pages" in response.text


def test_generate_uses_stubbed_crawler(monkeypatch: pytest.MonkeyPatch):
    page = PageRecord(
        url="http://example.com",
        host="example.com",
        path="/",
        title="Example",
        markdown="# Example\n" + ("content " * 120),
    )

    async def fake_crawl(self: AsyncCrawler):
        self.skipped_links = 0
        return [page]

    def fake_combine(self: AsyncCrawler, pages):
        return "<!-- Crawl summary: -->\n# Example\n" + pages[0].markdown

    monkeypatch.setattr(AsyncCrawler, "crawl_with_pages", fake_crawl, raising=True)
    monkeypatch.setattr(AsyncCrawler, "_combine_pages", fake_combine, raising=True)

    transport = httpx.ASGITransport(app=app)

    async def _run():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/generate",
                data={"url": "http://example.com", "max_pages": 1},
            )

    response = asyncio.run(_run())

    assert response.status_code == 200
    assert "text/markdown" in response.headers.get("content-type", "")
    assert "Crawl summary" in response.text

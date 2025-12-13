import asyncio

import httpx
import pytest

from crawler import AsyncCrawler, PageRecord


def make_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            html = """
            <html><head><title>Home</title></head>
            <body>
                <main>
                    <p>Welcome text with enough content.</p>
                    <a href="/page-2">Next</a>
                    <a href="http://127.0.0.1/blocked">Blocked</a>
                </main>
            </body></html>
            """
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        if request.url.path == "/page-2":
            html = "<html><body><main><p>Second page content body.</p></main></body></html>"
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        return httpx.Response(404, text="missing", headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def test_crawl_respects_page_and_depth_limits(monkeypatch: pytest.MonkeyPatch):
    transport = make_transport()
    crawler = AsyncCrawler(
        start_url="http://example.com/",
        max_pages=1,
        max_depth=2,
        respect_robots=False,
        use_sitemap=False,
        transport=transport,
        crawl_timeout=30,
    )

    async def _no_sitemap(_: httpx.AsyncClient):
        return None

    monkeypatch.setattr(crawler, "_discover_sitemap_urls", _no_sitemap)

    async def _run():
        return await crawler.crawl_with_pages()

    pages = asyncio.run(_run())

    assert len(pages) == 1
    assert pages[0].title == "Home"
    assert crawler.skipped_links >= 0


def test_blocked_private_hosts_are_rejected():
    crawler = AsyncCrawler(
        start_url="http://example.com",
        respect_robots=False,
        use_sitemap=False,
    )

    async def _run():
        async with httpx.AsyncClient(transport=make_transport()) as client:
            return await crawler._can_visit_url(client, "http://127.0.0.1/secret")

    allowed = asyncio.run(_run())

    assert allowed is False
    assert any(err.get("reason") == "blocked-host" for err in crawler.errors)


def test_combine_pages_includes_summary():
    crawler = AsyncCrawler(
        start_url="http://example.com",
        respect_robots=False,
        use_sitemap=False,
    )
    crawler.skipped_links = 2
    crawler.errors.append({"url": "http://bad", "reason": "timeout", "status": ""})

    markdown = crawler._combine_pages(
        [
            PageRecord(
                url="http://example.com",
                host="example.com",
                path="/",
                title="Title",
                markdown="# Title\nBody",
            )
        ]
    )

    assert "Skipped links: 2" in markdown
    assert "Errors: 1" in markdown
    assert "# example.com" in markdown

import io
import ipaddress
import re
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from connectors.importer import ImportConnector
from crawler import AsyncCrawler

BUILD_TIME = datetime.utcnow().isoformat() + "Z"
TEMPLATES_DIR = Path("templates")
CRAWL_TIMEOUT_SECONDS = 90.0
MAX_CONCURRENCY = 8


def _detect_git_sha() -> str:
    try:
        output = subprocess.check_output(
            [
                "git",
                "rev-parse",
                "--short",
                "HEAD",
            ]
        )
        return output.decode().strip() or "unknown"
    except Exception:
        return "unknown"


GIT_SHA = _detect_git_sha()

app = FastAPI(title="Web-to-KnowledgeBase")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory="static"), name="static")


last_run_info = {
    "errors": [],
    "diagnostics": [],
    "pages_count": 0,
    "thin_pages_count": 0,
    "total_chars": 0,
    "skipped_links": 0,
    "timed_out": False,
}


@app.get("/")
async def read_root(request: Request):
    template_path = TEMPLATES_DIR / "index.html"
    if not template_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Template not found: {template_path}",
        )

    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request, "build_tag": BUILD_TIME},
    )


@app.get("/healthz")
async def healthcheck():
    return {"status": "ok"}


@app.get("/version")
async def version():
    return {
        "app": "Web-to-KnowledgeBase",
        "git_sha": GIT_SHA,
        "build_time": BUILD_TIME,
    }


@app.get("/debug/last-run")
async def debug_last_run():
    return {
        "errors": last_run_info.get("errors", []),
        "diagnostics": last_run_info.get("diagnostics", []),
        "pages_count": last_run_info.get("pages_count", 0),
        "thin_pages_count": last_run_info.get("thin_pages_count", 0),
        "skipped_links": last_run_info.get("skipped_links", 0),
        "timed_out": last_run_info.get("timed_out", False),
    }


@app.post("/import")
async def import_documents(
    *,
    mode: str = Query("combined", pattern="^(combined|zip)$"),
    files: List[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    connector = ImportConnector()
    documents = await connector.parse_uploads(files)

    if not documents:
        raise HTTPException(
            status_code=400, detail="Uploaded files could not be parsed into documents."
        )

    if mode == "combined":
        combined = connector.export_combined(documents)
        if not combined.strip():
            raise HTTPException(
                status_code=400,
                detail="Uploaded files did not contain readable content.",
            )
        headers = {"Content-Disposition": 'attachment; filename="combined.md"'}
        return Response(
            content=combined,
            media_type="text/markdown; charset=utf-8",
            headers=headers,
        )

    archive = connector.export_zip(documents)
    headers = {"Content-Disposition": 'attachment; filename="documents.zip"'}
    return StreamingResponse(archive, media_type="application/zip", headers=headers)


def _parse_list_field(raw_value: str | None) -> List[str]:
    if not raw_value:
        return []
    parts = re.split(r"[,\s]+", raw_value)
    return [part.strip() for part in parts if part.strip()]


def _parse_list(payload_value) -> List[str]:
    if not payload_value:
        return []
    if isinstance(payload_value, list):
        return [str(item).strip() for item in payload_value if str(item).strip()]
    return _parse_list_field(str(payload_value))


def _slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "page"


def _parse_bool_field(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _clamp_min_text_chars(raw_value: int | None) -> int:
    if raw_value is None:
        return 600
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 600
    return max(200, min(value, 5000))


def _clamp_max_depth(raw_value: int | None) -> int:
    if raw_value is None:
        return 3
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(value, 10))


def _update_last_run(crawler: AsyncCrawler, pages: List, total_chars: int) -> None:
    thin_pages = sum(1 for page in pages if len(page.markdown.strip()) < 200)
    last_run_info.update(
        {
            "errors": list(crawler.errors),
            "diagnostics": list(crawler.diagnostics),
            "pages_count": len(pages),
            "thin_pages_count": thin_pages,
            "total_chars": total_chars,
            "skipped_links": crawler.skipped_links,
            "timed_out": crawler.timed_out,
        }
    )


def _validate_max_pages(raw_value: int | None) -> int:
    try:
        pages = int(raw_value) if raw_value is not None else 10
    except (TypeError, ValueError):
        pages = 1
    if pages > 500:
        raise HTTPException(status_code=400, detail="max_pages cannot exceed 500")
    return max(1, pages)


def _ensure_public_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        parsed = urlparse(f"https://{url}")
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Only http(s) URLs are allowed.")

    host = parsed.hostname or ""
    try:
        ip_value = ipaddress.ip_address(host)
        if ip_value.is_private or ip_value.is_loopback or ip_value.is_link_local:
            raise HTTPException(status_code=400, detail="Target host is not allowed.") from None
    except ValueError:
        if host.lower() == "localhost":
            raise HTTPException(status_code=400, detail="Target host is not allowed.") from None

    return parsed.geturl()


def _parse_render_mode(value: str | None) -> str:
    if not value:
        return "http"
    normalized = str(value).strip().lower()
    if normalized in {"http", "auto", "browser"}:
        return normalized
    return "http"


@app.post("/generate")
async def generate_knowledgebase(
    url: str = Form(...),
    max_pages: int | None = Form(10),
    allowed_hosts: str = Form(""),
    path_prefixes: str = Form(""),
    include_subdomains: str | None = Form("false"),
    respect_robots: str | None = Form("true"),
    use_sitemap: str | None = Form("true"),
    max_depth: int | None = Form(3),
    strip_links: str | None = Form("true"),
    strip_images: str | None = Form("true"),
    readability_fallback: str | None = Form("true"),
    min_text_chars: int | None = Form(600),
    render_mode: str | None = Form("http"),
):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    safe_url = _ensure_public_url(url)

    pages_to_crawl = _validate_max_pages(max_pages)
    allowed = _parse_list_field(allowed_hosts)
    prefixes = _parse_list_field(path_prefixes)
    include_subdomains_bool = _parse_bool_field(include_subdomains, False)
    respect_robots_bool = _parse_bool_field(respect_robots, True)
    use_sitemap_bool = _parse_bool_field(use_sitemap, True)
    depth_value = _clamp_max_depth(max_depth)
    strip_links_bool = _parse_bool_field(strip_links, True)
    strip_images_bool = _parse_bool_field(strip_images, True)
    readability_bool = _parse_bool_field(readability_fallback, True)
    min_text_value = _clamp_min_text_chars(min_text_chars)
    render_mode_value = _parse_render_mode(render_mode)

    crawler = AsyncCrawler(
        start_url=safe_url,
        max_pages=pages_to_crawl,
        include_subdomains=include_subdomains_bool,
        allowed_hosts=allowed,
        path_prefixes=prefixes,
        respect_robots=respect_robots_bool,
        use_sitemap=use_sitemap_bool,
        max_depth=depth_value,
        strip_links=strip_links_bool,
        strip_images=strip_images_bool,
        readability_fallback=readability_bool,
        min_text_chars=min_text_value,
        render_mode=render_mode_value,
        crawl_timeout=CRAWL_TIMEOUT_SECONDS,
        max_concurrent_requests=MAX_CONCURRENCY,
    )

    pages = await crawler.crawl_with_pages()
    total_chars = sum(len(page.markdown) for page in pages)
    markdown_content = crawler._combine_pages(pages)

    _update_last_run(crawler, pages, total_chars)

    if len(pages) == 0 or total_chars < 500:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Crawl produced too little content. Please review diagnostics.",
                "diagnostics": crawler.diagnostics[:10],
                "errors": crawler.errors[:10],
            },
        )

    parsed = urlparse(url)
    hostname = _slugify(parsed.hostname or "output")
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    outputs_dir = Path("outputs")
    outputs_dir.mkdir(parents=True, exist_ok=True)
    output_filename = outputs_dir / f"{hostname}__{timestamp}.md"
    output_filename.write_text(markdown_content, encoding="utf-8")

    headers = {"Content-Disposition": "attachment; filename=knowledgebase.md"}
    return Response(
        content=markdown_content,
        media_type="text/markdown; charset=utf-8",
        headers=headers,
    )


@app.post("/crawl-preview")
async def crawl_preview(
    url: str = Form(...),
    max_pages: int | None = Form(10),
    allowed_hosts: str | None = Form(None),
    path_prefixes: str | None = Form(None),
    include_subdomains: str | None = Form("false"),
    respect_robots: str | None = Form("true"),
    use_sitemap: str | None = Form("true"),
    max_depth: int | None = Form(3),
    strip_links: str | None = Form("true"),
    strip_images: str | None = Form("true"),
    readability_fallback: str | None = Form("true"),
    min_text_chars: int | None = Form(600),
    render_mode: str | None = Form("http"),
):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    safe_url = _ensure_public_url(url)

    pages_to_crawl = _validate_max_pages(max_pages)
    allowed = _parse_list_field(allowed_hosts)
    prefixes = _parse_list_field(path_prefixes)
    include_subdomains_bool = _parse_bool_field(include_subdomains, False)
    respect_robots_bool = _parse_bool_field(respect_robots, True)
    use_sitemap_bool = _parse_bool_field(use_sitemap, True)
    depth_value = _clamp_max_depth(max_depth)
    strip_links_bool = _parse_bool_field(strip_links, True)
    strip_images_bool = _parse_bool_field(strip_images, True)
    readability_bool = _parse_bool_field(readability_fallback, True)
    min_text_value = _clamp_min_text_chars(min_text_chars)
    render_mode_value = _parse_render_mode(render_mode)

    crawler = AsyncCrawler(
        start_url=safe_url,
        max_pages=pages_to_crawl,
        include_subdomains=include_subdomains_bool,
        allowed_hosts=allowed,
        path_prefixes=prefixes,
        respect_robots=respect_robots_bool,
        use_sitemap=use_sitemap_bool,
        max_depth=depth_value,
        strip_links=strip_links_bool,
        strip_images=strip_images_bool,
        readability_fallback=readability_bool,
        min_text_chars=min_text_value,
        render_mode=render_mode_value,
        crawl_timeout=CRAWL_TIMEOUT_SECONDS,
        max_concurrent_requests=MAX_CONCURRENCY,
    )

    pages = await crawler.crawl_with_pages()
    total_chars = sum(len(page.markdown) for page in pages)
    _update_last_run(crawler, pages, total_chars)

    if not pages:
        raise HTTPException(
            status_code=400,
            detail="No pages found for this configuration.",
        )

    preview = []
    for idx, page in enumerate(pages):
        slug_source = page.title or page.path or page.url
        filename = f"{page.host}__{_slugify(slug_source)}.md"
        preview.append(
            {
                "id": idx,
                "url": page.url,
                "host": page.host,
                "path": page.path,
                "title": page.title,
                "suggested_filename": filename,
            }
        )

    return preview


@app.post("/download-selected")
async def download_selected(payload=Body(...)):
    url = payload.get("url")
    max_pages = payload.get("max_pages", 10)
    allowed_hosts = payload.get("allowed_hosts") or []
    path_prefixes = payload.get("path_prefixes") or []
    include_subdomains = _parse_bool_field(payload.get("include_subdomains"), False)
    respect_robots = _parse_bool_field(payload.get("respect_robots"), True)
    use_sitemap = _parse_bool_field(payload.get("use_sitemap"), True)
    max_depth = _clamp_max_depth(payload.get("max_depth"))
    pages = payload.get("pages", [])
    strip_links = _parse_bool_field(payload.get("strip_links"), True)
    strip_images = _parse_bool_field(payload.get("strip_images"), True)
    readability_fallback = _parse_bool_field(payload.get("readability_fallback"), True)
    min_text_chars = _clamp_min_text_chars(payload.get("min_text_chars"))
    render_mode = _parse_render_mode(payload.get("render_mode"))

    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")
    if not pages:
        raise HTTPException(status_code=400, detail="No pages selected.")

    safe_url = _ensure_public_url(url)
    pages_to_crawl = _validate_max_pages(max_pages)

    crawler = AsyncCrawler(
        start_url=safe_url,
        max_pages=pages_to_crawl,
        include_subdomains=include_subdomains,
        allowed_hosts=allowed_hosts,
        path_prefixes=path_prefixes,
        respect_robots=respect_robots,
        use_sitemap=use_sitemap,
        max_depth=max_depth,
        strip_links=strip_links,
        strip_images=strip_images,
        readability_fallback=readability_fallback,
        min_text_chars=min_text_chars,
        render_mode=render_mode,
        crawl_timeout=CRAWL_TIMEOUT_SECONDS,
        max_concurrent_requests=MAX_CONCURRENCY,
    )

    buffer = io.BytesIO()
    added_files = 0

    async with crawler._create_client() as client:
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for page in pages:
                page_url = page.get("url")
                filename = page.get("filename") or f"page-{added_files}.md"
                if not page_url:
                    continue

                if not await crawler._can_visit_url(client, page_url):
                    continue

                rendered = await crawler._render_page(client, page_url)
                if not rendered:
                    continue

                _, title, markdown, _, _, final_url = rendered
                host = page.get("host") or (urlparse(final_url).hostname or "")
                heading = page.get("title") or page.get("path") or title

                body = f"# {host}\n## {heading}\n\n{markdown}"
                normalized_body = crawler._postprocess_markdown(body)
                zip_file.writestr(filename, normalized_body)
                added_files += 1

    if added_files == 0:
        raise HTTPException(
            status_code=400,
            detail="No pages could be downloaded with the provided selection.",
        )

    buffer.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="knowledgebase_pages.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


def _build_index_markdown(pages: List) -> str:
    grouped = _group_pages_by_host(pages)
    lines = ["# Index", ""]
    for host in sorted(grouped.keys()):
        lines.append(f"## {host}")
        for page in grouped[host]:
            filename = page.get("filename", "")
            title = page.get("title") or page.get("path") or page.get("url") or filename
            if filename:
                lines.append(f"- [{title}]({filename})")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _group_pages_by_host(pages: List) -> dict:
    grouped: dict = {}
    for page in pages:
        host = page.get("host") if isinstance(page, dict) else getattr(page, "host", "")
        grouped.setdefault(host or "", []).append(page)
    return grouped


def _build_grouped_markdown(pages: List) -> str:
    grouped = _group_pages_by_host(pages)
    sections = []
    for host, host_pages in grouped.items():
        entries = [f"# {host}"]
        for page in host_pages:
            title = page.get("title") if isinstance(page, dict) else getattr(page, "title", "")
            path = page.get("path") if isinstance(page, dict) else getattr(page, "path", "")
            url = page.get("url") if isinstance(page, dict) else getattr(page, "url", "")
            markdown = (
                page.get("markdown") if isinstance(page, dict) else getattr(page, "markdown", "")
            )
            heading = title or path or url or "Page"
            entries.append(f"## {heading}\n\n{markdown}\n")
        sections.append("\n\n".join(entries))
    return "\n\n---\n\n".join(sections).strip() + "\n"


def _prepare_crawler_options(payload):
    options = payload.get("options") or {}
    strip_links = _parse_bool_field(options.get("strip_links"), True)
    strip_images = _parse_bool_field(options.get("strip_images"), True)
    readability_fallback = _parse_bool_field(
        options.get("use_readability", options.get("readability_fallback")), True
    )
    min_text_chars = _clamp_min_text_chars(options.get("min_text_chars"))
    include_subdomains = _parse_bool_field(options.get("include_subdomains"), True)
    render_mode = options.get("render_mode")
    return {
        "strip_links": strip_links,
        "strip_images": strip_images,
        "readability_fallback": readability_fallback,
        "min_text_chars": min_text_chars,
        "include_subdomains": include_subdomains,
        "render_mode": render_mode,
    }


def _validate_urls(urls: List[str]) -> List[str]:
    if not urls:
        raise HTTPException(status_code=400, detail="At least one URL is required.")
    if len(urls) > 200:
        raise HTTPException(status_code=400, detail="Provide 200 URLs or fewer.")
    seen = set()
    unique_urls = []
    for url in urls:
        normalized = str(url).strip()
        if not normalized:
            continue
        normalized = _ensure_public_url(normalized)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_urls.append(normalized)
    return unique_urls


@app.post("/bulk/combined")
async def bulk_combined(payload=Body(...)):
    raw_urls = payload.get("urls") or []
    urls = _validate_urls(raw_urls)
    allowed_hosts = _parse_list(payload.get("allowed_hosts"))
    path_prefixes = _parse_list(payload.get("path_prefixes"))
    options = _prepare_crawler_options(payload)

    crawler = AsyncCrawler(
        start_url=urls[0],
        max_pages=len(urls),
        include_subdomains=options["include_subdomains"],
        allowed_hosts=allowed_hosts,
        path_prefixes=path_prefixes,
        strip_links=options["strip_links"],
        strip_images=options["strip_images"],
        readability_fallback=options["readability_fallback"],
        min_text_chars=options["min_text_chars"],
        crawl_timeout=CRAWL_TIMEOUT_SECONDS,
        max_concurrent_requests=MAX_CONCURRENCY,
    )

    pages = []
    async with crawler._create_client() as client:
        for url in urls:
            page = await crawler.fetch_and_clean_page(client, url)
            if page:
                pages.append(page)

    if not pages:
        raise HTTPException(
            status_code=400,
            detail="No pages could be processed for the provided URLs.",
        )

    markdown_content = _build_grouped_markdown(pages)

    headers = {
        "Content-Disposition": 'attachment; filename="bulk_combined.md"',
        "X-Page-Count": str(len(pages)),
    }
    return Response(
        content=markdown_content,
        media_type="text/markdown; charset=utf-8",
        headers=headers,
    )


@app.post("/bulk/zip")
async def bulk_zip(payload=Body(...)):
    raw_urls = payload.get("urls") or []
    urls = _validate_urls(raw_urls)
    allowed_hosts = _parse_list(payload.get("allowed_hosts"))
    path_prefixes = _parse_list(payload.get("path_prefixes"))
    options = _prepare_crawler_options(payload)

    crawler = AsyncCrawler(
        start_url=urls[0],
        max_pages=len(urls),
        include_subdomains=options["include_subdomains"],
        allowed_hosts=allowed_hosts,
        path_prefixes=path_prefixes,
        strip_links=options["strip_links"],
        strip_images=options["strip_images"],
        readability_fallback=options["readability_fallback"],
        min_text_chars=options["min_text_chars"],
        crawl_timeout=CRAWL_TIMEOUT_SECONDS,
        max_concurrent_requests=MAX_CONCURRENCY,
    )

    buffer = io.BytesIO()
    rendered_pages = []

    async with crawler._create_client() as client:
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for url in urls:
                page = await crawler.fetch_and_clean_page(client, url)
                if not page:
                    continue
                title_source = page.title or page.path or page.url
                filename = f"{page.host}__{_slugify(title_source)}.md"
                rendered_pages.append(
                    {
                        "filename": filename,
                        "title": page.title,
                        "path": page.path,
                        "url": page.url,
                        "host": page.host,
                    }
                )
                zip_file.writestr(filename, page.markdown)

            if rendered_pages:
                index_markdown = _build_index_markdown(rendered_pages)
                zip_file.writestr("index.md", index_markdown)

    if not rendered_pages:
        raise HTTPException(
            status_code=400,
            detail="No pages could be processed for the provided URLs.",
        )

    buffer.seek(0)
    headers = {
        "Content-Disposition": 'attachment; filename="bulk_pages.zip"',
        "X-Page-Count": str(len(rendered_pages)),
    }
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

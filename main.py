import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from crawler import AsyncCrawler

app = FastAPI(title="Web-to-KnowledgeBase")
templates = Jinja2Templates(directory="templates")


last_run_info = {
    "errors": [],
    "diagnostics": [],
    "pages_count": 0,
    "thin_pages_count": 0,
    "total_chars": 0,
}


@app.get("/")
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/debug/last-run")
async def debug_last_run():
    return {
        "errors": last_run_info.get("errors", []),
        "diagnostics": last_run_info.get("diagnostics", []),
        "pages_count": last_run_info.get("pages_count", 0),
        "thin_pages_count": last_run_info.get("thin_pages_count", 0),
    }


def _parse_list_field(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    parts = re.split(r"[,\s]+", raw_value)
    return [part.strip() for part in parts if part.strip()]


def _slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "page"


def _parse_bool_field(value: Optional[str], default: bool) -> bool:
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


def _clamp_min_text_chars(raw_value: Optional[int]) -> int:
    if raw_value is None:
        return 600
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 600
    return max(200, min(value, 5000))


def _update_last_run(crawler: AsyncCrawler, pages: List, total_chars: int) -> None:
    thin_pages = sum(1 for page in pages if len(page.markdown.strip()) < 200)
    last_run_info.update(
        {
            "errors": list(crawler.errors),
            "diagnostics": list(crawler.diagnostics),
            "pages_count": len(pages),
            "thin_pages_count": thin_pages,
            "total_chars": total_chars,
        }
    )


def _clamp_max_pages(raw_value: Optional[int]) -> int:
    pages = raw_value or 1
    return max(1, min(pages, 500))


@app.post("/generate")
async def generate_knowledgebase(
    url: str = Form(...),
    max_pages: Optional[int] = Form(10),
    allowed_hosts: Optional[str] = Form(None),
    path_prefixes: Optional[str] = Form(None),
    strip_links: Optional[str] = Form("true"),
    strip_images: Optional[str] = Form("true"),
    readability_fallback: Optional[str] = Form("true"),
    min_text_chars: Optional[int] = Form(600),
):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    pages_to_crawl = _clamp_max_pages(max_pages)
    allowed = _parse_list_field(allowed_hosts)
    prefixes = _parse_list_field(path_prefixes)
    strip_links_bool = _parse_bool_field(strip_links, True)
    strip_images_bool = _parse_bool_field(strip_images, True)
    readability_bool = _parse_bool_field(readability_fallback, True)
    min_text_value = _clamp_min_text_chars(min_text_chars)

    crawler = AsyncCrawler(
        start_url=url,
        max_pages=pages_to_crawl,
        include_subdomains=True,
        allowed_hosts=allowed,
        path_prefixes=prefixes,
        strip_links=strip_links_bool,
        strip_images=strip_images_bool,
        readability_fallback=readability_bool,
        min_text_chars=min_text_value,
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
    max_pages: Optional[int] = Form(10),
    allowed_hosts: Optional[str] = Form(None),
    path_prefixes: Optional[str] = Form(None),
    strip_links: Optional[str] = Form("true"),
    strip_images: Optional[str] = Form("true"),
    readability_fallback: Optional[str] = Form("true"),
    min_text_chars: Optional[int] = Form(600),
):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    pages_to_crawl = _clamp_max_pages(max_pages)
    allowed = _parse_list_field(allowed_hosts)
    prefixes = _parse_list_field(path_prefixes)
    strip_links_bool = _parse_bool_field(strip_links, True)
    strip_images_bool = _parse_bool_field(strip_images, True)
    readability_bool = _parse_bool_field(readability_fallback, True)
    min_text_value = _clamp_min_text_chars(min_text_chars)

    crawler = AsyncCrawler(
        start_url=url,
        max_pages=pages_to_crawl,
        include_subdomains=True,
        allowed_hosts=allowed,
        path_prefixes=prefixes,
        strip_links=strip_links_bool,
        strip_images=strip_images_bool,
        readability_fallback=readability_bool,
        min_text_chars=min_text_value,
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
    pages = payload.get("pages", [])
    strip_links = _parse_bool_field(payload.get("strip_links"), True)
    strip_images = _parse_bool_field(payload.get("strip_images"), True)
    readability_fallback = _parse_bool_field(payload.get("readability_fallback"), True)
    min_text_chars = _clamp_min_text_chars(payload.get("min_text_chars"))

    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")
    if not pages:
        raise HTTPException(status_code=400, detail="No pages selected.")

    crawler = AsyncCrawler(
        start_url=url,
        max_pages=_clamp_max_pages(max_pages),
        include_subdomains=True,
        allowed_hosts=allowed_hosts,
        path_prefixes=path_prefixes,
        strip_links=strip_links,
        strip_images=strip_images,
        readability_fallback=readability_fallback,
        min_text_chars=min_text_chars,
    )

    buffer = io.BytesIO()
    added_files = 0

    async with crawler._create_client() as client:
        with zipfile.ZipFile(
            buffer, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zip_file:
            for page in pages:
                page_url = page.get("url")
                filename = page.get("filename") or f"page-{added_files}.md"
                if not page_url or not crawler._is_allowed_url(page_url):
                    continue

                content = await crawler._fetch_content(client, page_url)
                if not content:
                    continue

                title, markdown, _ = crawler._clean_html(content, page_url)
                host = page.get("host") or (urlparse(page_url).hostname or "")
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

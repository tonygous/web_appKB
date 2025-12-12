import asyncio
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
import html2text
from readability import Document


IGNORED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".mp4",
    ".mp3",
    ".wav",
}


@dataclass
class PageRecord:
    url: str
    host: str
    path: str
    title: str
    markdown: str
    clean_text_chars: int = 0
    used_readability: bool = False


class AsyncCrawler:
    def __init__(
        self,
        start_url: str,
        max_pages: int = 50,
        timeout: float = 10.0,
        crawl_timeout: float = 120.0,
        include_subdomains: bool = False,
        allowed_hosts: Optional[Sequence[str]] = None,
        path_prefixes: Optional[Sequence[str]] = None,
        max_depth: int = 3,
        max_concurrent_requests: int = 8,
        respect_robots: bool = True,
        use_sitemap: bool = True,
        *,
        strip_links: bool = True,
        strip_images: bool = True,
        min_text_chars: int = 600,
        readability_fallback: bool = True,
        remove_additional_noise: bool = True,
        main_selectors: Optional[Sequence[str]] = None,
    ) -> None:
        self.start_url = self._normalize_url(start_url)
        self.max_pages = max(1, min(max_pages, 500))
        self.timeout = timeout
        self.crawl_timeout = crawl_timeout
        self.include_subdomains = include_subdomains
        self.max_depth = max(0, max_depth)
        self.max_concurrent_requests = max(1, max_concurrent_requests)
        self.respect_robots = respect_robots
        self.use_sitemap = use_sitemap

        self.allowed_hosts = self._normalize_hosts(allowed_hosts)
        self.path_prefixes = self._normalize_prefixes(path_prefixes)

        self.request_timeout = httpx.Timeout(
            timeout, connect=timeout, read=timeout, write=timeout, pool=timeout
        )

        self.strip_links = strip_links
        self.strip_images = strip_images
        self.min_text_chars = max(0, min_text_chars)
        self.readability_fallback = readability_fallback
        self.remove_additional_noise = remove_additional_noise
        self.main_selectors = list(main_selectors) if main_selectors else [
            "main",
            "article",
            '[role="main"]',
            "#content",
            ".content",
            ".main",
            ".main-content",
            ".article",
            ".post",
            ".entry-content",
        ]

        parsed_start = urlparse(self.start_url)
        self.start_hostname = parsed_start.hostname or ""
        self.root_domain = self._extract_root_domain(parsed_start.hostname)

        self.visited: Set[str] = set()
        self.enqueued: Set[str] = {self.start_url}
        self.queue: Deque[Tuple[str, int]] = deque([(self.start_url, 0)])

        self._robots_fetched: Set[str] = set()
        self._robots_rules: Dict[str, List[str]] = {}

        self.errors: List[Dict[str, str]] = []
        self.diagnostics: List[Dict[str, object]] = []
        self.timed_out: bool = False
        self.line_frequencies: Dict[str, int] = {}
        self.logger = logging.getLogger(__name__)

        self._retry_statuses = {408, 425, 429, 500, 502, 503, 504}

    def _normalize_hosts(self, hosts: Optional[Sequence[str]]) -> List[str]:
        if not hosts:
            return []
        normalized: List[str] = []
        for host in hosts:
            if not host:
                continue
            normalized.append(host.strip().lower())
        return normalized

    def _normalize_prefixes(self, prefixes: Optional[Sequence[str]]) -> List[str]:
        if not prefixes:
            return []
        normalized: List[str] = []
        for prefix in prefixes:
            if not prefix:
                continue
            normalized.append(prefix.strip())
        return normalized

    def _extract_canonical(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        canonical_link = soup.find("link", rel=lambda value: value and "canonical" in value.lower())
        if canonical_link and canonical_link.get("href"):
            candidate = self._normalize_url(urljoin(base_url, canonical_link.get("href")))
            if candidate:
                return candidate
        return None

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        if not parsed.scheme:
            parsed = urlparse(f"https://{url}")

        query_parts: List[Tuple[str, str]] = []
        tracking_prefixes = {"utm_"}
        tracking_keys = {"gclid", "fbclid", "mc_cid", "mc_eid"}

        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            lowered = key.lower()
            if any(lowered.startswith(prefix) for prefix in tracking_prefixes):
                continue
            if lowered in tracking_keys:
                continue
            query_parts.append((key, value))

        query_parts.sort()
        normalized_query = urlencode(query_parts, doseq=True)

        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")

        parsed = parsed._replace(path=path, query=normalized_query, fragment="")
        normalized = urlunparse(parsed)
        return normalized or url

    @staticmethod
    def _normalize_markdown(content: str) -> str:
        if not content:
            return ""
        collapsed = re.sub(r"\n{3,}", "\n\n", content)
        trimmed_lines = [line.rstrip() for line in collapsed.splitlines()]
        return "\n".join(trimmed_lines).strip()

    def _extract_root_domain(self, hostname: Optional[str]) -> str:
        if not hostname:
            return ""
        parts = hostname.lower().split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return hostname.lower()

    def _record_error(self, url: str, reason: str, status: str = "") -> None:
        self.errors.append({"url": url, "reason": reason, "status": status})
        self.logger.warning("Error fetching %s: %s %s", url, reason, status)

    def _record_diagnostic(
        self,
        *,
        original_url: str,
        final_url: str,
        status: str,
        content_type: str,
        content_bytes: int,
        elapsed_ms: int,
        reason: str,
    ) -> None:
        self.diagnostics.append(
            {
                "url": original_url,
                "final_url": final_url,
                "status": status,
                "content_type": content_type,
                "bytes": content_bytes,
                "elapsed_ms": elapsed_ms,
                "reason": reason,
            }
        )

    def _is_internal_link(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"", "http", "https"}:
            return False

        hostname = (parsed.hostname or "").lower()
        if hostname == "":
            return True

        if hostname == self.start_hostname.lower():
            return True

        if self.allowed_hosts:
            for allowed in self.allowed_hosts:
                if hostname == allowed or hostname.endswith(f".{allowed}"):
                    return True
            return False

        if hostname == self.root_domain:
            return True

        if self.include_subdomains and hostname.endswith(f".{self.root_domain}"):
            return True

        return False

    def _matches_path_prefix(self, url: str) -> bool:
        if not self.path_prefixes:
            return True
        parsed = urlparse(url)
        path_with_query = parsed.path or "/"
        if parsed.query:
            path_with_query += f"?{parsed.query}"
        return any(path_with_query.startswith(prefix) for prefix in self.path_prefixes)

    def _is_allowed_url(self, url: str) -> bool:
        if self._has_ignored_extension(url):
            return False
        if not self._is_internal_link(url):
            return False
        if not self._matches_path_prefix(url):
            return False
        if self.respect_robots and self._is_disallowed_by_robots(url):
            return False
        return True

    def _has_ignored_extension(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(path.endswith(ext) for ext in IGNORED_EXTENSIONS)

    def _backoff_delay(self, attempt: int) -> float:
        base = 0.5 * (2 ** attempt)
        jitter = random.uniform(-0.05, 0.05)
        return max(0.0, base + jitter)

    def _is_html_content(self, content_type: str) -> bool:
        lowered = content_type.lower()
        return "text/html" in lowered or "application/xhtml+xml" in lowered

    async def _ensure_robots_rules(self, client: httpx.AsyncClient, url: str) -> None:
        if not self.respect_robots:
            return
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host or host in self._robots_fetched:
            return

        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        disallow_rules: List[str] = []

        try:
            response = await client.get(robots_url)
            if response.status_code == 200:
                disallow_rules = self._parse_robots(response.text)
        except httpx.HTTPError:
            disallow_rules = []

        self._robots_fetched.add(host)
        self._robots_rules[host] = disallow_rules

    def _parse_robots(self, content: str) -> List[str]:
        disallow: List[str] = []
        lines = content.splitlines()
        relevant = False
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip().lower()
                relevant = agent == "*"
                continue
            if not relevant:
                continue
            if line.lower().startswith("disallow:"):
                rule = line.split(":", 1)[1].strip() or "/"
                disallow.append(rule)
            if line.lower().startswith("allow:"):
                rule = line.split(":", 1)[1].strip()
                if rule in disallow:
                    disallow.remove(rule)
        return disallow

    def _is_disallowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return False
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path or "/"
        rules = self._robots_rules.get(host)
        if not rules:
            return False
        for rule in rules:
            if not rule:
                continue
            if rule == "/":
                return True
            if path.startswith(rule):
                return True
        return False

    async def _can_visit_url(self, client: httpx.AsyncClient, url: str) -> bool:
        if self.respect_robots:
            await self._ensure_robots_rules(client, url)
            if self._is_disallowed_by_robots(url):
                self._record_error(url, "robots-disallowed")
                self._record_diagnostic(
                    original_url=url,
                    final_url=url,
                    status="",
                    content_type="",
                    content_bytes=0,
                    elapsed_ms=0,
                    reason="robots-disallowed",
                )
                return False
        return self._is_allowed_url(url)

    async def _can_enqueue_url(self, client: httpx.AsyncClient, url: str) -> bool:
        if not self._has_ignored_extension(url) and self._is_internal_link(url) and self._matches_path_prefix(url):
            if self.respect_robots:
                await self._ensure_robots_rules(client, url)
                if self._is_disallowed_by_robots(url):
                    return False
            return True
        return False

    def _create_client(self) -> httpx.AsyncClient:
        default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        }
        return httpx.AsyncClient(
            headers=default_headers,
            follow_redirects=True,
            timeout=self.request_timeout,
        )

    async def _fetch_content(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        max_retries = 2
        attempt = 0
        while attempt <= max_retries:
            start_time = time.monotonic()
            try:
                response = await client.get(url)
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                status_code = response.status_code
                content_type = response.headers.get("content-type", "")
                content_length = len(response.content or b"")
                final_url = str(response.url)

                if status_code in self._retry_statuses and attempt < max_retries:
                    await asyncio.sleep(self._backoff_delay(attempt))
                    attempt += 1
                    continue

                if status_code >= 400:
                    reason = "blocked" if status_code in {401, 403, 429} else "http-error"
                    self._record_error(url, reason, str(status_code))
                    self._record_diagnostic(
                        original_url=url,
                        final_url=final_url,
                        status=str(status_code),
                        content_type=content_type,
                        content_bytes=content_length,
                        elapsed_ms=elapsed_ms,
                        reason=reason,
                    )
                    return None

                if not self._is_html_content(content_type):
                    self._record_error(url, "non-html", str(status_code))
                    self._record_diagnostic(
                        original_url=url,
                        final_url=final_url,
                        status=str(status_code),
                        content_type=content_type,
                        content_bytes=content_length,
                        elapsed_ms=elapsed_ms,
                        reason="non-html",
                    )
                    return None

                self._record_diagnostic(
                    original_url=url,
                    final_url=final_url,
                    status=str(status_code),
                    content_type=content_type,
                    content_bytes=content_length,
                    elapsed_ms=elapsed_ms,
                    reason="ok",
                )
                return response.text
            except httpx.TimeoutException:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                if attempt < max_retries:
                    await asyncio.sleep(self._backoff_delay(attempt))
                    attempt += 1
                    continue
                self._record_error(url, "timeout")
                self._record_diagnostic(
                    original_url=url,
                    final_url=url,
                    status="",
                    content_type="",
                    content_bytes=0,
                    elapsed_ms=elapsed_ms,
                    reason="timeout",
                )
                return None
            except httpx.HTTPError as exc:  # pragma: no cover - safety net
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                status_code = str(getattr(exc.response, "status_code", ""))
                if attempt < max_retries:
                    await asyncio.sleep(self._backoff_delay(attempt))
                    attempt += 1
                    continue
                self._record_error(url, "http-error", status_code)
                self._record_diagnostic(
                    original_url=url,
                    final_url=url,
                    status=status_code,
                    content_type="",
                    content_bytes=0,
                    elapsed_ms=elapsed_ms,
                    reason="http-error",
                )
                return None

    def _remove_noise_tags(self, soup: BeautifulSoup) -> None:
        removable_tags = ["script", "style", "nav", "footer", "header", "aside"]
        if self.remove_additional_noise:
            removable_tags.extend(["form", "noscript", "svg", "iframe"])
        for tag_name in removable_tags:
            for tag in soup.find_all(tag_name):
                tag.decompose()

    def _select_main_area(self, soup: BeautifulSoup) -> BeautifulSoup:
        for selector in self.main_selectors:
            found = soup.select_one(selector)
            if found:
                return found
        if soup.body:
            return soup.body
        return soup

    def _create_markdown_converter(self) -> html2text.HTML2Text:
        converter = html2text.HTML2Text()
        converter.body_width = 0
        converter.ignore_links = self.strip_links
        converter.ignore_images = self.strip_images
        converter.inline_links = False
        converter.wrap_links = False
        return converter

    def _postprocess_markdown(self, text: str) -> str:
        lines = [line.rstrip() for line in text.splitlines()]

        while lines and lines[0] == "":
            lines.pop(0)
        while lines and lines[-1] == "":
            lines.pop()

        normalized_lines: List[str] = []
        previous_blank = False
        for line in lines:
            is_blank = line == ""
            if is_blank and previous_blank:
                continue
            normalized_lines.append(line)
            previous_blank = is_blank

        normalized_text = "\n".join(normalized_lines)
        normalized_text = re.sub(r"\n{3,}", "\n\n", normalized_text)
        if not normalized_text.endswith("\n"):
            normalized_text += "\n"
        return normalized_text

    def _clean_html(self, html: str, url: str) -> Tuple[str, str, bool]:
        soup = BeautifulSoup(html, "html.parser")
        self._remove_noise_tags(soup)

        title_text = ""
        if soup.title and soup.title.string:
            title_text = soup.title.string.strip()
        title = title_text or url

        content_area = self._select_main_area(soup)
        visible_length = len(content_area.get_text(" ", strip=True))
        used_readability = False

        if self.readability_fallback and visible_length < self.min_text_chars:
            try:
                doc = Document(html)
                readability_html = doc.summary(html_partial=True)
                readability_title = doc.short_title() or title
                readability_soup = BeautifulSoup(readability_html, "html.parser")
                self._remove_noise_tags(readability_soup)
                content_area = self._select_main_area(readability_soup)
                title = readability_title
                used_readability = True
            except Exception:  # pragma: no cover - safety net
                used_readability = False

        markdown_converter = self._create_markdown_converter()
        markdown_content = markdown_converter.handle(str(content_area))
        cleaned_markdown = self._postprocess_markdown(markdown_content)
        return title, cleaned_markdown, used_readability

    def _extract_links(self, url: str, soup: BeautifulSoup) -> List[str]:
        links: List[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            absolute_url = self._normalize_url(urljoin(url, href))
            if not absolute_url:
                continue
            if not self._is_allowed_url(absolute_url):
                continue
            if absolute_url not in self.visited and absolute_url not in self.enqueued:
                links.append(absolute_url)
        return links

    def _parse_sitemap_content(self, content: str) -> Tuple[List[str], List[str]]:
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(content)
        except ET.ParseError:
            return [], []

        ns_clean = lambda tag: tag.split("}")[-1]
        sitemaps: List[str] = []
        urls: List[str] = []

        tag = ns_clean(root.tag)
        if tag == "sitemapindex":
            for child in root:
                if ns_clean(child.tag) == "sitemap":
                    for loc in child:
                        if ns_clean(loc.tag) == "loc" and loc.text:
                            sitemaps.append(loc.text.strip())
        elif tag == "urlset":
            for child in root:
                if ns_clean(child.tag) == "url":
                    for loc in child:
                        if ns_clean(loc.tag) == "loc" and loc.text:
                            urls.append(loc.text.strip())
        return sitemaps, urls

    async def _discover_sitemap_urls(self, client: httpx.AsyncClient) -> None:
        if not self.use_sitemap:
            return

        parsed = urlparse(self.start_url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

        to_process = [sitemap_url]
        processed: Set[str] = set()
        max_sitemaps = 20
        url_limit = self.max_pages * 3

        while to_process and len(processed) < max_sitemaps and len(self.enqueued) < url_limit:
            current = to_process.pop(0)
            normalized_current = self._normalize_url(current)
            if normalized_current in processed:
                continue
            processed.add(normalized_current)

            try:
                response = await client.get(normalized_current)
            except httpx.HTTPError:
                continue

            if response.status_code != 200:
                continue

            sitemaps, urls = self._parse_sitemap_content(response.text)
            for sitemap in sitemaps:
                if len(processed) + len(to_process) >= max_sitemaps:
                    break
                normalized = self._normalize_url(urljoin(normalized_current, sitemap))
                if normalized:
                    to_process.append(normalized)

            for url in urls:
                if len(self.enqueued) >= url_limit:
                    break
                normalized_url = self._normalize_url(urljoin(normalized_current, url))
                if not normalized_url:
                    continue
                if normalized_url in self.enqueued or normalized_url in self.visited:
                    continue
                if not await self._can_enqueue_url(client, normalized_url):
                    continue
                self.enqueued.add(normalized_url)
                self.queue.append((normalized_url, 0))

    def _should_keep_line(self, line: str, frequency: int) -> bool:
        if frequency <= 3:
            return True
        stripped = line.strip()
        if not stripped:
            return True
        if len(stripped) <= 60 and (
            stripped.lower()
            in {
                "overview",
                "introduction",
                "summary",
                "contents",
                "table of contents",
            }
            or stripped.startswith("# ")
            or stripped.startswith("## ")
        ):
            return True
        return False

    def _apply_boilerplate_filter(self, pages: List[PageRecord]) -> List[PageRecord]:
        line_counts: Dict[str, int] = {}
        for page in pages:
            for raw_line in page.markdown.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                line_counts[line] = line_counts.get(line, 0) + 1

        filtered_pages: List[PageRecord] = []
        for page in pages:
            lines: List[str] = []
            for raw_line in page.markdown.splitlines():
                line = raw_line.rstrip()
                frequency = line_counts.get(line.strip(), 0)
                if not self._should_keep_line(line, frequency):
                    continue
                lines.append(line)
            page.markdown = self._postprocess_markdown("\n".join(lines))
            filtered_pages.append(page)

        self.line_frequencies = line_counts
        return filtered_pages

    async def _process_url(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[Tuple[PageRecord, List[str]]]:
        content = await self._fetch_content(client, url)
        if not content:
            return None

        soup = BeautifulSoup(content, "html.parser")
        canonical_url = self._extract_canonical(soup, url)
        page_url = canonical_url or url
        links = self._extract_links(url, soup)
        title, markdown, used_readability = self._clean_html(content, page_url)
        parsed_url = urlparse(page_url)
        host = parsed_url.hostname or self.root_domain or ""
        path = parsed_url.path or "/"
        if parsed_url.query:
            path = f"{path}?{parsed_url.query}"
        page_record = PageRecord(
            url=page_url,
            host=host,
            path=path or "/",
            title=title or "",
            markdown=markdown,
            clean_text_chars=len(markdown.strip()),
            used_readability=used_readability,
        )
        return page_record, links

    async def fetch_and_clean_page(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[PageRecord]:
        normalized_url = self._normalize_url(url)
        if not normalized_url:
            return None
        if self._has_ignored_extension(normalized_url):
            return None
        if not self._is_allowed_url(normalized_url):
            return None

        content = await self._fetch_content(client, normalized_url)
        if not content:
            return None

        title, markdown, used_readability = self._clean_html(
            content, normalized_url
        )
        parsed_url = urlparse(normalized_url)
        host = parsed_url.hostname or self.root_domain or ""
        path = parsed_url.path or "/"
        if parsed_url.query:
            path = f"{path}?{parsed_url.query}"

        return PageRecord(
            url=normalized_url,
            host=host,
            path=path or "/",
            title=title or "",
            markdown=markdown,
            clean_text_chars=len(markdown.strip()),
            used_readability=used_readability,
        )

    async def crawl_with_pages(self) -> List[PageRecord]:
        pages: List[PageRecord] = []
        semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        start_time = time.monotonic()

        async with self._create_client() as client:
            await self._discover_sitemap_urls(client)
            while self.queue and len(pages) < self.max_pages:
                if time.monotonic() - start_time > self.crawl_timeout:
                    self.timed_out = True
                    self.logger.warning("Crawl timed out after %.2f seconds", self.crawl_timeout)
                    break

                tasks = []
                task_urls: List[Tuple[str, int]] = []
                while (
                    self.queue
                    and len(tasks) < self.max_concurrent_requests
                    and len(pages) + len(tasks) < self.max_pages
                ):
                    current_url, depth = self.queue.popleft()
                    if current_url in self.visited:
                        continue
                    if not await self._can_visit_url(client, current_url):
                        continue
                    self.visited.add(current_url)
                    task_urls.append((current_url, depth))

                    async def _task(url=current_url):
                        async with semaphore:
                            return await self._process_url(client, url)

                    tasks.append(asyncio.create_task(_task()))

                if not tasks:
                    break

                results = await asyncio.gather(*tasks)
                for (current_url, depth), result in zip(task_urls, results):
                    if not result:
                        continue
                    page_record, links = result
                    self.visited.add(page_record.url)
                    self.enqueued.add(page_record.url)
                    pages.append(page_record)
                    for link in links:
                        next_depth = depth + 1
                        if next_depth > self.max_depth:
                            continue
                        if len(self.visited) + len(self.queue) >= self.max_pages:
                            break
                        if link in self.visited or link in self.enqueued:
                            continue
                        if not await self._can_enqueue_url(client, link):
                            continue
                        self.enqueued.add(link)
                        self.queue.append((link, next_depth))

        if pages:
            pages = self._apply_boilerplate_filter(pages)

        return pages

    async def crawl(self) -> str:
        pages = await self.crawl_with_pages()
        return self._combine_pages(pages)

    def _combine_pages(self, pages: List[PageRecord]) -> str:
        """Собираем финальный markdown: summary + контент по хостам."""
        # summary (в HTML-комментарии, чтобы не мешать рендеру)
        summary_lines: List[str] = ["<!-- Crawl summary:"]
        summary_lines.append(f"Total pages: {len(pages)}")
        summary_lines.append(f"Errors: {len(self.errors)}")
        if self.errors:
            for err in self.errors[:20]:
                url = err.get("url", "")
                reason = err.get("reason", "")
                status = err.get("status", "")
                if status:
                    summary_lines.append(f"  - {url} ({reason} {status})")
                else:
                    summary_lines.append(f"  - {url} ({reason})")
        if self.timed_out:
            summary_lines.append(
                f"Crawl timed out after {self.crawl_timeout:.0f} seconds."
            )
        summary_lines.append("-->")

        # группируем страницы по host
        pages_by_host: Dict[str, List[PageRecord]] = {}
        for page in pages:
            host = page.host or self.root_domain or "unknown host"
            pages_by_host.setdefault(host, []).append(page)

        markdown_parts: List[str] = []
        for host in sorted(pages_by_host.keys()):
            host_section: List[str] = [f"# {host}"]
            for page in pages_by_host[host]:
                title = page.title or page.path or page.url
                body = page.markdown.rstrip()
                host_section.append(f"## {title}\n\n{body}\n")
            markdown_parts.append("\n\n".join(host_section))

        body = "\n\n---\n\n".join(markdown_parts)
        combined = "\n".join(summary_lines)
        if body:
            combined += "\n\n" + body
        if not combined.endswith("\n"):
            combined += "\n"
        return combined


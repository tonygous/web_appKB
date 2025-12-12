import io
import mailbox
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, List

from bs4 import BeautifulSoup
import html2text
from email import policy
from email.parser import Parser
from fastapi import UploadFile

from connectors.base import BaseConnector, Document


class ImportConnector(BaseConnector):
    def __init__(self) -> None:
        self.main_selectors = [
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

    async def parse_uploads(self, files: Iterable[UploadFile]) -> List[Document]:
        documents: List[Document] = []
        for upload in files:
            raw_bytes = await upload.read()
            content = raw_bytes.decode("utf-8", errors="replace")
            extension = Path(upload.filename or "").suffix.lower()

            if extension in {".txt", ".md"}:
                documents.append(
                    Document(
                        source=upload.filename or "text",
                        url=None,
                        title=upload.filename or "Text document",
                        body_markdown=self._normalize_markdown(content),
                        meta={"type": "text"},
                    )
                )
            elif extension in {".html", ".htm"}:
                documents.append(self._parse_html(content, upload.filename or "HTML page"))
            elif extension == ".eml":
                documents.extend(self._parse_email(content, upload.filename or "email"))
            elif extension == ".mbox":
                documents.extend(self._parse_mbox(raw_bytes, upload.filename or "mbox"))
            else:
                documents.append(
                    Document(
                        source=upload.filename or "file",
                        url=None,
                        title=upload.filename or "Uploaded file",
                        body_markdown=self._normalize_markdown(content),
                        meta={"type": "text", "note": "Unknown extension treated as text"},
                    )
                )
        return documents

    def export_combined(self, documents: List[Document]) -> str:
        if not documents:
            return ""

        parts: List[str] = []
        for doc in documents:
            heading = doc.title or doc.source
            parts.append(f"# {heading}")
            if doc.url:
                parts.append(f"_Source_: {doc.url}")
            parts.append("")
            parts.append(doc.body_markdown.strip())
            parts.append("")
        return self._normalize_markdown("\n".join(parts))

    def export_zip(self, documents: List[Document]) -> io.BytesIO:
        buffer = io.BytesIO()
        index_lines = ["# Imported documents", ""]

        import zipfile

        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for idx, doc in enumerate(documents, start=1):
                filename = f"{self._slugify(doc.title or doc.source or f'doc-{idx}')}.md"
                index_lines.append(f"- [{doc.title or filename}]({filename})")
                archive.writestr(filename, self._normalize_markdown(doc.body_markdown))
            archive.writestr("index.md", self._normalize_markdown("\n".join(index_lines)))

        buffer.seek(0)
        return buffer

    def _parse_html(self, content: str, source: str) -> Document:
        soup = BeautifulSoup(content, "html.parser")
        for tag_name in ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg", "iframe"]:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        main_area = self._select_main_area(soup)
        converter = self._create_markdown_converter()
        markdown = converter.handle(str(main_area))
        normalized = self._normalize_markdown(markdown)

        title = source
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        return Document(
            source=source,
            url=None,
            title=title or source,
            body_markdown=normalized,
            meta={"type": "html"},
        )

    def _parse_email(self, content: str, source: str) -> List[Document]:
        message = Parser(policy=policy.default).parsestr(content)
        title = message.get("Subject", source) or source
        body_markdown = self._extract_email_body(message)

        header_lines = [
            f"**From:** {message.get('From', 'Unknown')}",
            f"**To:** {message.get('To', 'Unknown')}",
        ]
        if message.get("Date"):
            header_lines.append(f"**Date:** {message.get('Date')}")
        header_lines.append("")

        combined_body = "\n".join(header_lines + [body_markdown])
        document = Document(
            source=source,
            url=None,
            title=title,
            body_markdown=self._normalize_markdown(combined_body),
            meta={"type": "email"},
        )
        return [document]

    def _parse_mbox(self, raw_bytes: bytes, source: str) -> List[Document]:
        documents: List[Document] = []
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mbox") as tmp:
            tmp.write(raw_bytes)
            temp_path = tmp.name

        try:
            box = mailbox.mbox(temp_path)
            for idx, message in enumerate(box, start=1):
                title = message.get("Subject", f"Message {idx}") or f"Message {idx}"
                body_markdown = self._extract_email_body(message)
                header_lines = [
                    f"**From:** {message.get('From', 'Unknown')}",
                    f"**To:** {message.get('To', 'Unknown')}",
                ]
                if message.get("Date"):
                    header_lines.append(f"**Date:** {message.get('Date')}")
                header_lines.append("")
                combined_body = "\n".join(header_lines + [body_markdown])
                documents.append(
                    Document(
                        source=f"{source}#{idx}",
                        url=None,
                        title=title,
                        body_markdown=self._normalize_markdown(combined_body),
                        meta={"type": "email", "mbox_source": source, "index": idx},
                    )
                )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return documents

    def _extract_email_body(self, message) -> str:
        if message.is_multipart():
            for part in message.walk():
                content_disposition = part.get_content_disposition()
                if content_disposition == "attachment":
                    continue
                if part.get_content_type() == "text/plain":
                    return self._normalize_markdown(part.get_content())
                if part.get_content_type() == "text/html":
                    converter = self._create_markdown_converter(strip_links=False, strip_images=True)
                    return self._normalize_markdown(converter.handle(part.get_content()))
        else:
            if message.get_content_type() == "text/html":
                converter = self._create_markdown_converter(strip_links=False, strip_images=True)
                return self._normalize_markdown(converter.handle(message.get_content()))
            return self._normalize_markdown(message.get_content())
        return ""

    def _select_main_area(self, soup: BeautifulSoup) -> BeautifulSoup:
        for selector in self.main_selectors:
            found = soup.select_one(selector)
            if found:
                return found
        if soup.body:
            return soup.body
        return soup

    def _create_markdown_converter(self, *, strip_links: bool = True, strip_images: bool = True) -> html2text.HTML2Text:
        converter = html2text.HTML2Text()
        converter.body_width = 0
        converter.ignore_links = strip_links
        converter.ignore_images = strip_images
        converter.inline_links = False
        converter.wrap_links = False
        return converter

    def _normalize_markdown(self, content: str) -> str:
        if not content:
            return ""
        collapsed = re.sub(r"\n{3,}", "\n\n", content)
        trimmed_lines = [line.rstrip() for line in collapsed.splitlines()]
        normalized = "\n".join(trimmed_lines).strip()
        if not normalized.endswith("\n"):
            normalized += "\n"
        return normalized

    def _slugify(self, value: str) -> str:
        value = value.lower()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        value = re.sub(r"-+", "-", value).strip("-")
        return value or "document"

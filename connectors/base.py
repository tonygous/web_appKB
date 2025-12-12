from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Document:
    """Normalized document produced by a connector."""

    source: str
    url: Optional[str]
    title: str
    body_markdown: str
    meta: Dict[str, Any] = field(default_factory=dict)


class BaseConnector:
    """Interface for connectors that turn inputs into documents."""

    async def parse_uploads(self, *args, **kwargs):  # pragma: no cover - interface placeholder
        raise NotImplementedError

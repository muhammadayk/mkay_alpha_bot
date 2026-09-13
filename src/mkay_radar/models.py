from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


@dataclass(frozen=True)
class Opportunity:
    title: str
    project: str
    category: str
    url: str
    source: str
    description: str | None = None
    requirements: str | None = None
    chain: str | None = None
    reward: str | None = None
    source_status: str | None = None
    risk_level: str = "REVIEW_REQUIRED"
    score: float = 0.0
    status: str = "REVIEW"
    discovered_at: str = ""

    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.url)

    @property
    def content_hash(self) -> str:
        material = "|".join(filter(None, [self.title.lower(), self.description, self.requirements, self.source_status]))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def record(self) -> dict:
        data = asdict(self)
        data["canonical_url"] = self.canonical_url
        data["content_hash"] = self.content_hash
        data["discovered_at"] = self.discovered_at or datetime.now(timezone.utc).isoformat()
        return data


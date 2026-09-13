from __future__ import annotations

import os
import re
import time
import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mkay_radar.models import Opportunity, clean_text


BASE_URL = "https://airdrops.io/"
SOURCE = "airdrops.io"
EXCLUDED_SLUGS = {"", "latest", "hot", "potential", "confirmed", "claims", "blog", "contact", "faq", "calendar", "feed", "wp-json"}
logger = logging.getLogger(__name__)


@dataclass
class AirdropsIoCollector:
    max_items: int = int(os.getenv("RADAR_MAX_ITEMS_PER_RUN", "10"))
    delay_seconds: float = float(os.getenv("RADAR_REQUEST_DELAY_SECONDS", "1"))
    user_agent: str = os.getenv("RADAR_USER_AGENT", "MKAY-Opportunity-Radar/0.1")

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"})
        retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def collect(self) -> list[Opportunity]:
        """Collect a small, polite sample of public airdrop guides for human review."""
        listing = self._get(BASE_URL)
        urls = self._guide_urls(listing)[: self.max_items]
        results: list[Opportunity] = []
        for index, url in enumerate(urls):
            if index:
                time.sleep(self.delay_seconds)
            try:
                opportunity = self._parse_guide(url, self._get(url))
            except requests.RequestException as error:
                logger.warning("Skipping unavailable guide %s: %s", url, error)
                continue
            if opportunity:
                results.append(opportunity)
        return results

    def _get(self, url: str) -> str:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    @staticmethod
    def _guide_urls(html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            url = urljoin(BASE_URL, anchor["href"])
            parsed = urlsplit(url)
            if parsed.netloc != "airdrops.io":
                continue
            slug_parts = [part for part in parsed.path.split("/") if part]
            # Individual guides are one public path segment, e.g. /flop-labs/.
            if len(slug_parts) != 1 or slug_parts[0] in EXCLUDED_SLUGS or parsed.query:
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    @staticmethod
    def _parse_guide(url: str, html: str) -> Opportunity | None:
        soup = BeautifulSoup(html, "html.parser")
        heading = clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None)
        if not heading:
            return None
        title = re.sub(r"^(Potential|Confirmed)\s+", "", heading, flags=re.I)
        title = re.sub(r"\s+Airdrop.*$", "", title, flags=re.I) or heading
        text = clean_text(soup.get_text(" ", strip=True)) or ""
        details = AirdropsIoCollector._section_text(soup, "Airdrop Details")
        description = clean_text(details) or clean_text(text[:1200])
        return Opportunity(
            title=title,
            project=title,
            category="AIRDROP",
            url=url,
            source=SOURCE,
            description=description,
            requirements=AirdropsIoCollector._labeled_list_value(soup, "Actions:"),
            chain=AirdropsIoCollector._labeled_list_value(soup, "Chain:"),
            reward=AirdropsIoCollector._labeled_list_value(soup, "Airdrop Allocation:"),
            source_status="Confirmed" if re.search(r"Airdrop confirmed", text, re.I) else "Potential",
        )

    @staticmethod
    def _section_text(soup: BeautifulSoup, heading_fragment: str) -> str | None:
        heading = next((tag for tag in soup.find_all(["h2", "h3"]) if heading_fragment.lower() in tag.get_text(" ", strip=True).lower()), None)
        if not heading:
            return None
        chunks: list[str] = []
        for sibling in heading.find_all_next():
            if sibling.name in {"h2", "h3"} and sibling is not heading:
                break
            if sibling.name in {"p", "li"}:
                value = clean_text(sibling.get_text(" ", strip=True))
                if value:
                    chunks.append(value)
            if len(" ".join(chunks)) >= 1200:
                break
        return clean_text(" ".join(chunks)[:1200])

    @staticmethod
    def _labeled_list_value(soup: BeautifulSoup, label: str) -> str | None:
        """Read a guide's structured list item, never a page-wide text substring."""
        for item in soup.find_all("li"):
            value = clean_text(item.get_text(" ", strip=True))
            if value and value.lower().startswith(label.lower()):
                return clean_text(value[len(label):])
        return None

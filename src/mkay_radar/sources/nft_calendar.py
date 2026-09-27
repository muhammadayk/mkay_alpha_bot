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

# Public listing pages only. This mirrors the Airdrops.io collector's boundaries:
# no login, no following the site's own "/goto/" sponsored redirect links, and a
# polite delay between requests. Re-check https://nftcalendar.io/terms/ and
# robots.txt before scheduling, the same as the Airdrops.io source.
BASE_URL = "https://nftcalendar.io/events/"
SOURCE = "nftcalendar.io"
EXCLUDED_SLUGS = {"", "community", "ongoing", "newest", "past", "archive"}
DATE_RANGE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*[\u2013-]\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
logger = logging.getLogger(__name__)


@dataclass
class NftCalendarCollector:
    """Collects public upcoming NFT drop/whitelist listings from NFTCalendar.io."""

    max_items: int = int(os.getenv("RADAR_MAX_ITEMS_PER_RUN", "10"))
    delay_seconds: float = float(os.getenv("RADAR_REQUEST_DELAY_SECONDS", "1"))
    user_agent: str = os.getenv("RADAR_USER_AGENT") or "MKAY-Opportunity-Radar/0.1"

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"})
        retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def collect(self) -> list[Opportunity]:
        """Collect a small, polite sample of public upcoming-drop pages for human review."""
        listing = self._get(BASE_URL)
        urls = self._event_urls(listing)[: self.max_items]
        results: list[Opportunity] = []
        for index, url in enumerate(urls):
            if index:
                time.sleep(self.delay_seconds)
            try:
                opportunity = self._parse_event(url, self._get(url))
            except requests.RequestException as error:
                logger.warning("Skipping unavailable event %s: %s", url, error)
                continue
            if opportunity:
                results.append(opportunity)
        return results

    def _get(self, url: str) -> str:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    @staticmethod
    def _event_urls(html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            url = urljoin(BASE_URL, anchor["href"])
            parsed = urlsplit(url)
            if parsed.netloc != "nftcalendar.io":
                continue
            slug_parts = [part for part in parsed.path.split("/") if part]
            # Individual drops are two public path segments, e.g. /event/some-drop/.
            if len(slug_parts) != 2 or slug_parts[0] != "event" or slug_parts[1] in EXCLUDED_SLUGS or parsed.query:
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    @staticmethod
    def _parse_event(url: str, html: str) -> Opportunity | None:
        soup = BeautifulSoup(html, "html.parser")
        title = clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None)
        if not title:
            return None
        meta_description = soup.select_one('meta[name="description"]')
        description = clean_text(meta_description["content"]) if meta_description and meta_description.get("content") else None
        text = clean_text(soup.get_text(" ", strip=True)) or ""
        date_match = DATE_RANGE_RE.search(text)
        window = f"{date_match.group(1)} – {date_match.group(2)}" if date_match else None
        return Opportunity(
            title=title,
            project=title,
            category="NFT_WHITELIST",
            url=url,
            source=SOURCE,
            description=description or clean_text(text[:1200]),
            requirements="Follow the project's official links on the page above and confirm whitelist/mint requirements yourself.",
            chain=NftCalendarCollector._labeled_link_text(soup, "Blockchain:"),
            reward=NftCalendarCollector._labeled_link_text(soup, "Marketplace:"),
            source_status=f"Drop window: {window}" if window else "Listed on NFTCalendar.io",
        )

    @staticmethod
    def _labeled_link_text(soup: BeautifulSoup, label: str) -> str | None:
        """Read the link right after a bolded/heading label, never a page-wide guess."""
        heading = next((tag for tag in soup.find_all(["h2", "h3", "strong", "b"]) if tag.get_text(" ", strip=True).lower() == label.lower()), None)
        if not heading:
            return None
        link = heading.find_next("a")
        return clean_text(link.get_text(" ", strip=True)) if link else None

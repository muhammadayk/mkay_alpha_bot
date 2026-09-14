from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from mkay_radar.models import Opportunity, clean_text


RSS_URL = "https://airdropalert.com/feed/rssfeed"
SOURCE = "airdropalert.com"


@dataclass
class AirdropAlertCollector:
    max_items: int = int(os.getenv("RADAR_MAX_ITEMS_PER_RUN", "10"))
    user_agent: str = os.getenv("RADAR_USER_AGENT") or "MKAY-Opportunity-Radar/0.1"

    def collect(self) -> list[Opportunity]:
        response = requests.get(RSS_URL, headers={"User-Agent": self.user_agent, "Accept": "application/rss+xml,application/xml,text/xml"}, timeout=30)
        response.raise_for_status()
        return self.parse(response.content)[: self.max_items]

    @staticmethod
    def parse(payload: bytes) -> list[Opportunity]:
        root = ET.fromstring(payload)
        results: list[Opportunity] = []
        for item in root.findall("./channel/item"):
            title = clean_text(item.findtext("title"))
            url = clean_text(item.findtext("link"))
            html_description = item.findtext("description") or ""
            description = clean_text(BeautifulSoup(html_description, "html.parser").get_text(" ", strip=True))
            # The official feed can contain broader blog posts. Keep actual airdrop listings only.
            if not title or not url or "airdrop" not in f"{title} {description or ''}".lower():
                continue
            project = title.replace("Airdrop", "").replace("airdrop", "").strip(" -:|") or title
            results.append(Opportunity(
                title=title,
                project=project,
                category="AIRDROP",
                url=url,
                source=SOURCE,
                description=description[:1200] if description else None,
                source_status="New listing (official RSS)",
            ))
        return results

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from mkay_radar.models import Opportunity, clean_text


API_URL = "https://api.cryptojobslist.com/public/jobs"
SOURCE = "cryptojobslist.com"


@dataclass
class CryptoJobsListCollector:
    """Collect remote Web3 roles through CryptoJobsList's official API."""

    api_key: str | None = None
    max_items: int = int(os.getenv("RADAR_MAX_ITEMS_PER_RUN", "10"))

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("CRYPTOJOBSLIST_API_KEY")

    def configured(self) -> bool:
        return bool(self.api_key)

    def collect(self) -> list[Opportunity]:
        if not self.configured():
            raise RuntimeError("CryptoJobsList is not configured. Set CRYPTOJOBSLIST_API_KEY.")
        response = requests.get(
            API_URL,
            headers={"x-api-key": self.api_key, "Accept": "application/json"},
            params={"remote": "true", "page": 1},
            timeout=30,
        )
        response.raise_for_status()
        return self.parse(response.json())[: self.max_items]

    @staticmethod
    def parse(payload: dict) -> list[Opportunity]:
        results: list[Opportunity] = []
        for job in payload.get("jobs", []):
            title = clean_text(job.get("jobTitle"))
            company = clean_text(job.get("companyName")) or "Unknown company"
            url = clean_text(job.get("canonicalURL"))
            if not title or not url:
                continue
            tags = [clean_text(str(tag)) for tag in job.get("tags", [])]
            employment = [clean_text(str(kind).replace("_", " ").title()) for kind in job.get("employmentType", [])]
            requirements = ", ".join(part for part in ["Remote", *employment, *tags] if part)
            results.append(Opportunity(
                title=f"{title} — {company}",
                project=company,
                category="JOB",
                url=url,
                source=SOURCE,
                description=clean_text(job.get("description")),
                requirements=requirements or "Review the role and apply through the original listing.",
                source_status="Remote role",
            ))
        return results

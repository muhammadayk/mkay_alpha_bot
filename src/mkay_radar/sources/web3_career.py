from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from mkay_radar.models import Opportunity, clean_text


API_URL = "https://web3.career/api/v1"
SOURCE = "web3.career"


@dataclass
class Web3CareerCollector:
    """Collect remote Web3 jobs through web3.career's official API."""

    token: str | None = None
    max_items: int = int(os.getenv("RADAR_MAX_ITEMS_PER_RUN", "10"))
    user_agent: str = os.getenv("RADAR_USER_AGENT") or "MKAY-Opportunity-Radar/0.1"
    remote_only: bool = os.getenv("RADAR_WEB3_CAREER_REMOTE_ONLY", "true").lower() == "true"

    def __post_init__(self) -> None:
        self.token = self.token or os.getenv("WEB3_CAREER_API_TOKEN")

    def configured(self) -> bool:
        return bool(self.token)

    def collect(self) -> list[Opportunity]:
        if not self.configured():
            raise RuntimeError("web3.career is not configured. Set WEB3_CAREER_API_TOKEN.")
        params: dict[str, str | int] = {"token": self.token, "limit": min(self.max_items, 100)}
        if self.remote_only:
            params["remote"] = "true"
        response = requests.get(API_URL, params=params, headers={"User-Agent": self.user_agent}, timeout=30)
        response.raise_for_status()
        return self.parse(response.json())[: self.max_items]

    @staticmethod
    def parse(payload: object) -> list[Opportunity]:
        # The documented API response contains metadata followed by the job list.
        if not isinstance(payload, list) or not payload or not isinstance(payload[-1], list):
            return []
        results: list[Opportunity] = []
        for job in payload[-1]:
            if not isinstance(job, dict):
                continue
            title = clean_text(job.get("title"))
            company = clean_text(job.get("company")) or "Unknown company"
            # This is the source-provided application URL. Do not alter it.
            apply_url = clean_text(job.get("apply_url"))
            if not title or not apply_url:
                continue
            tags = job.get("tags")
            tag_text = ", ".join(str(tag) for tag in tags) if isinstance(tags, list) else None
            location = clean_text(job.get("location")) or ("Remote" if job.get("is_remote") else None)
            posted_at = None
            try:
                if job.get("date_epoch"):
                    posted_at = datetime.fromtimestamp(int(job["date_epoch"]), tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                pass
            status = " · ".join(bit for bit in [location, f"Posted {posted_at}" if posted_at else None] if bit)
            results.append(Opportunity(
                title=f"{title} — {company}",
                project=company,
                category="JOB",
                url=apply_url,
                source=SOURCE,
                description=clean_text(job.get("description")),
                requirements=tag_text or "Review the role and apply through the original listing.",
                reward=clean_text(job.get("salary_text")),
                source_status=status or "Job listing",
            ))
        return results

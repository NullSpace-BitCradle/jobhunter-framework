"""Lever public postings API scraper. Endpoint: api.lever.co/v0/postings/<slug>?mode=json"""
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from .base import Scraper, Job, strip_html

logger = logging.getLogger(__name__)


class LeverScraper(Scraper):
    ats_name = "lever"
    BASE_URL = "https://api.lever.co/v0/postings/{slug}"

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str) -> list[Job]:
        url = self.BASE_URL.format(slug=company_slug)
        try:
            resp = self._throttled_get(url, params={"mode": "json"}, timeout=self.timeout)
        except requests.RequestException as e:
            logger.warning("%s: request failed: %s", company_name, e)
            return []

        if resp.status_code == 404:
            logger.warning("%s: Lever board not found at slug '%s' - verify the slug", company_name, company_slug)
            return []
        if not resp.ok:
            logger.warning("%s: Lever API returned %s", company_name, resp.status_code)
            return []

        try:
            data = resp.json()
        except ValueError:
            logger.warning("%s: Lever returned non-JSON", company_name)
            return []

        if not isinstance(data, list):
            logger.warning("%s: unexpected Lever response shape", company_name)
            return []

        jobs: list[Job] = []
        for j in data:
            job_id = j.get("id", "")
            if not job_id:
                continue
            title = (j.get("text") or "").strip()
            categories = j.get("categories") or {}
            location = (categories.get("location") or "").strip()
            commitment = (categories.get("commitment") or "").strip()
            workplace_type = (j.get("workplaceType") or "").lower()
            url_apply = j.get("hostedUrl") or j.get("applyUrl") or ""
            created_at_ms = j.get("createdAt")
            posted_at: Optional[datetime] = None
            if isinstance(created_at_ms, (int, float)):
                try:
                    posted_at = datetime.fromtimestamp(created_at_ms / 1000.0, tz=timezone.utc)
                except (OverflowError, OSError, ValueError):
                    posted_at = None
            description = j.get("descriptionPlain") or strip_html(j.get("description") or "")

            remote: Optional[bool] = None
            if workplace_type == "remote":
                remote = True
            elif workplace_type in ("onsite", "on-site"):
                remote = False
            elif "remote" in location.lower():
                remote = True

            jobs.append(Job(
                id=f"lever:{company_slug}:{job_id}",
                company=company_name,
                company_slug=company_slug,
                company_tier=company_tier,
                title=title,
                location=location or commitment,
                remote=remote,
                url=url_apply,
                posted_at=posted_at,
                description_text=description,
                raw=j,
            ))
        return jobs

"""Greenhouse public jobs API scraper. Endpoint: boards-api.greenhouse.io/v1/boards/<slug>/jobs"""
import logging
from typing import Optional

import requests

from .base import Scraper, Job, strip_html, parse_iso

logger = logging.getLogger(__name__)


class GreenhouseScraper(Scraper):
    ats_name = "greenhouse"
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str, timeout: int | None = None) -> list[Job]:
        effective_timeout = timeout if timeout is not None else self.timeout
        url = self.BASE_URL.format(slug=company_slug)
        try:
            resp = self._throttled_get(url, params={"content": "true"}, timeout=effective_timeout)
        except requests.RequestException as e:
            logger.warning("%s: request failed: %s", company_name, e)
            return []

        if resp.status_code == 404:
            logger.warning("%s: Greenhouse board not found at slug '%s' - verify the slug", company_name, company_slug)
            return []
        if not resp.ok:
            logger.warning("%s: Greenhouse API returned %s", company_name, resp.status_code)
            return []

        try:
            data = resp.json()
        except ValueError:
            logger.warning("%s: Greenhouse returned non-JSON", company_name)
            return []

        jobs_raw = data.get("jobs", []) or []
        jobs: list[Job] = []
        for j in jobs_raw:
            job_id = str(j.get("id", ""))
            if not job_id:
                continue
            title = (j.get("title") or "").strip()
            location = ((j.get("location") or {}).get("name") or "").strip()
            url_apply = j.get("absolute_url", "")
            posted_at = parse_iso(j.get("updated_at") or j.get("first_published_at") or j.get("created_at"))
            description = strip_html(j.get("content", ""))

            remote: Optional[bool] = None
            loc_lower = location.lower()
            if "remote" in loc_lower:
                remote = True
            elif any(s in loc_lower for s in ["onsite", "on-site", "on site"]):
                remote = False

            jobs.append(Job(
                id=f"greenhouse:{company_slug}:{job_id}",
                company=company_name,
                company_slug=company_slug,
                company_tier=company_tier,
                title=title,
                location=location,
                remote=remote,
                url=url_apply,
                posted_at=posted_at,
                description_text=description,
                raw=j,
            ))
        return jobs

"""Ashby public job-board API scraper. Endpoint: api.ashbyhq.com/posting-api/job-board/<slug>"""
import logging
from typing import Optional

import requests

from .base import Scraper, Job, parse_iso

logger = logging.getLogger(__name__)


class AshbyScraper(Scraper):
    ats_name = "ashby"
    BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str, timeout: int | None = None) -> list[Job]:
        effective_timeout = timeout if timeout is not None else self.timeout
        url = self.BASE_URL.format(slug=company_slug)
        try:
            resp = self._throttled_get(url, params={"includeCompensation": "false"}, timeout=effective_timeout)
        except requests.RequestException as e:
            logger.warning("%s: request failed: %s", company_name, e)
            return []

        if resp.status_code == 404:
            logger.warning("%s: Ashby board not found at slug '%s' - verify the slug", company_name, company_slug)
            return []
        if not resp.ok:
            logger.warning("%s: Ashby API returned %s", company_name, resp.status_code)
            return []

        try:
            data = resp.json()
        except ValueError:
            logger.warning("%s: Ashby returned non-JSON", company_name)
            return []

        jobs_raw = data.get("jobs") or []
        jobs: list[Job] = []
        for j in jobs_raw:
            job_id = j.get("id", "")
            if not job_id:
                continue
            title = (j.get("title") or "").strip()
            location = (j.get("locationName") or "").strip()
            url_apply = j.get("jobUrl") or j.get("applyUrl") or ""
            posted_at = parse_iso(j.get("publishedDate") or j.get("updatedAt"))
            description = (j.get("descriptionPlain") or "").strip()

            remote: Optional[bool] = None
            is_remote_flag = j.get("isRemote")
            if isinstance(is_remote_flag, bool):
                remote = is_remote_flag
            elif "remote" in location.lower():
                remote = True

            jobs.append(Job(
                id=f"ashby:{company_slug}:{job_id}",
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

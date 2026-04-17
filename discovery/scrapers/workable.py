"""Workable public widget API scraper.

Endpoint: https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true

Note: Workable's widget endpoint returns HTTP 200 with `jobs: []` for unknown
company slugs, so 404 detection relies on checking the jobs array. Verify via
dry-run that any company added here actually has listings. Description data
from the widget endpoint is limited - title matching is the primary signal.
"""
import logging
from typing import Optional

import requests

from .base import Scraper, Job, strip_html, parse_iso

logger = logging.getLogger(__name__)


class WorkableScraper(Scraper):
    ats_name = "workable"
    BASE_URL = "https://apply.workable.com/api/v1/widget/accounts/{slug}"

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str) -> list[Job]:
        url = self.BASE_URL.format(slug=company_slug)
        try:
            resp = self._throttled_get(url, params={"details": "true"}, timeout=self.timeout)
        except requests.RequestException as e:
            logger.warning("%s: request failed: %s", company_name, e)
            return []

        if resp.status_code == 404:
            logger.warning("%s: Workable account not found at slug '%s' - verify the slug", company_name, company_slug)
            return []
        if not resp.ok:
            logger.warning("%s: Workable API returned %s", company_name, resp.status_code)
            return []

        try:
            data = resp.json()
        except ValueError:
            logger.warning("%s: Workable returned non-JSON", company_name)
            return []

        jobs_raw = data.get("jobs") or []
        if not jobs_raw:
            logger.warning("%s: Workable returned 0 jobs - verify slug '%s'", company_name, company_slug)
            return []

        jobs: list[Job] = []
        for j in jobs_raw:
            job_id = j.get("id") or j.get("shortcode", "")
            if not job_id:
                continue
            title = (j.get("title") or "").strip()

            location_parts = []
            if j.get("city"):
                location_parts.append(j["city"])
            if j.get("region"):
                location_parts.append(j["region"])
            if j.get("country"):
                location_parts.append(j["country"])
            location = ", ".join(location_parts)

            apply_url = j.get("url") or j.get("application_url") or ""
            if not apply_url and j.get("shortcode"):
                apply_url = f"https://apply.workable.com/{company_slug}/j/{j['shortcode']}/"

            posted_at = parse_iso(j.get("created_at") or j.get("published_on"))
            description = strip_html(j.get("description") or "")

            remote: Optional[bool] = None
            if isinstance(j.get("telecommuting"), bool):
                remote = j["telecommuting"]
            elif j.get("remote") is True:
                remote = True
            elif "remote" in location.lower():
                remote = True

            jobs.append(Job(
                id=f"workable:{company_slug}:{job_id}",
                company=company_name,
                company_slug=company_slug,
                company_tier=company_tier,
                title=title,
                location=location,
                remote=remote,
                url=apply_url,
                posted_at=posted_at,
                description_text=description,
                raw=j,
            ))
        return jobs

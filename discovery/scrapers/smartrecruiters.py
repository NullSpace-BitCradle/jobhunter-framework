"""SmartRecruiters public postings API scraper.

Endpoint: https://api.smartrecruiters.com/v1/companies/<company>/postings

Note: SmartRecruiters returns HTTP 200 with an empty `content` array for unknown
company slugs, so 404 detection relies on `totalFound == 0`. Verify via dry-run
that any company added here actually has listings.
"""
import logging
from typing import Optional

import requests

from .base import Scraper, Job, strip_html, parse_iso

logger = logging.getLogger(__name__)


class SmartRecruitersScraper(Scraper):
    ats_name = "smartrecruiters"
    BASE_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    PAGE_SIZE = 100
    MAX_PAGES = 50

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str, timeout: int | None = None) -> list[Job]:
        effective_timeout = timeout if timeout is not None else self.timeout
        url = self.BASE_URL.format(slug=company_slug)
        all_raw: list[dict] = []
        offset = 0

        while True:
            try:
                resp = self._throttled_get(
                    url,
                    params={"limit": self.PAGE_SIZE, "offset": offset},
                    timeout=effective_timeout,
                )
            except requests.RequestException as e:
                logger.warning("%s: request failed (offset %d): %s", company_name, offset, e)
                break  # preserve partial results

            if not resp.ok:
                logger.warning("%s: SmartRecruiters API returned %s (offset %d)", company_name, resp.status_code, offset)
                break  # preserve partial results

            try:
                data = resp.json()
            except ValueError:
                logger.warning("%s: SmartRecruiters returned non-JSON (offset %d)", company_name, offset)
                break  # preserve partial results

            content = data.get("content") or []
            total_found = data.get("totalFound", 0)

            if offset == 0 and total_found == 0:
                logger.warning(
                    "%s: SmartRecruiters returned totalFound=0 for slug '%s' - verify the slug",
                    company_name, company_slug,
                )
                return []

            all_raw.extend(content)
            offset += self.PAGE_SIZE
            if offset >= total_found or not content or offset >= self.MAX_PAGES * self.PAGE_SIZE:
                break

        jobs: list[Job] = []
        for j in all_raw:
            job_id = j.get("id") or j.get("uuid", "")
            if not job_id:
                continue
            title = (j.get("name") or "").strip()
            location_obj = j.get("location") or {}
            location_parts = [
                (location_obj.get("city") or "").strip(),
                (location_obj.get("region") or "").strip(),
                (location_obj.get("country") or "").strip(),
            ]
            location = ", ".join(p for p in location_parts if p)
            if location_obj.get("remote") is True:
                location = (location + " (Remote)").strip() if location else "Remote"

            apply_url = f"https://jobs.smartrecruiters.com/{company_slug}/{job_id}"
            posted_at = parse_iso(j.get("releasedDate") or j.get("createdOn"))
            description = strip_html(j.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "")) if isinstance(j.get("jobAd"), dict) else ""

            remote: Optional[bool] = location_obj.get("remote") if isinstance(location_obj.get("remote"), bool) else None
            if remote is None and "remote" in location.lower():
                remote = True

            jobs.append(Job(
                id=f"smartrecruiters:{company_slug}:{job_id}",
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

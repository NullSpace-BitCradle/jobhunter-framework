"""Ashby public job-board API scraper. Endpoint: api.ashbyhq.com/posting-api/job-board/<slug>

Documented job fields (developers.ashbyhq.com, "Public Job Posting API"):
`title`, `location`, `secondaryLocations`, `department`, `team`, `isListed`,
`isRemote`, `workplaceType` ("OnSite" | "Remote" | "Hybrid"), `descriptionPlain`,
`publishedAt`, `address.postalAddress`, `jobUrl`, `applyUrl`. Note `isRemote`
is true for Hybrid as well as Remote, so `workplaceType` is the field to trust.
The documented shape has no `id`; older responses (and this scraper's first
version) used `id`, `locationName` and `publishedDate`, which are still read as
fallbacks.
"""
import logging
from typing import Optional
from urllib.parse import urlparse

import requests

from .base import Scraper, Job, parse_iso

logger = logging.getLogger(__name__)


def _job_id(j: dict, company_slug: str) -> str:
    """`id` when present, else the last path segment of `jobUrl`/`applyUrl`.

    Posting URLs look like https://jobs.ashbyhq.com/<slug>/<uuid>[/application],
    so the uuid is the stable per-posting identifier either way.
    """
    explicit = str(j.get("id") or "").strip()
    if explicit:
        return explicit
    for key in ("jobUrl", "applyUrl"):
        url = (j.get(key) or "").strip()
        if not url:
            continue
        segments = [s for s in urlparse(url).path.split("/") if s]
        if segments and segments[-1].lower() == "application":
            segments = segments[:-1]
        if segments and segments[-1] != company_slug:
            return segments[-1]
    return ""


def _location(j: dict) -> str:
    location = (j.get("location") or j.get("locationName") or "").strip()
    if location:
        return location
    postal = (j.get("address") or {}).get("postalAddress") or {}
    parts = [str(postal.get(k) or "").strip()
             for k in ("addressLocality", "addressRegion", "addressCountry")]
    return ", ".join(p for p in parts if p)


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
            job_id = _job_id(j, company_slug)
            if not job_id:
                logger.warning("%s: skipping Ashby posting with no id or usable URL: %r",
                               company_name, (j.get("title") or "")[:60])
                continue
            title = (j.get("title") or "").strip()
            location = _location(j)
            url_apply = j.get("jobUrl") or j.get("applyUrl") or ""
            posted_at = parse_iso(j.get("publishedAt") or j.get("publishedDate") or j.get("updatedAt"))
            description = (j.get("descriptionPlain") or "").strip()

            # workplaceType is authoritative: isRemote is also true for Hybrid.
            # classify_work_mode() reads "hybrid" from the location string, so a
            # hybrid posting gets an explicit "(Hybrid)" marker, mirroring the
            # "(Remote)" marker the SmartRecruiters scraper adds.
            workplace = str(j.get("workplaceType") or "").strip().lower().replace("-", "").replace(" ", "")
            remote: Optional[bool] = None
            if workplace == "remote":
                remote = True
            elif workplace == "hybrid":
                remote = False
                if "hybrid" not in location.lower():
                    location = f"{location} (Hybrid)".strip()
            elif workplace == "onsite":
                remote = False
            else:
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

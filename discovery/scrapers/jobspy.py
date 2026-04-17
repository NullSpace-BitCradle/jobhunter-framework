"""JobSpy-based scraper for job board aggregators (LinkedIn, Indeed, Glassdoor, etc.)."""
import hashlib
import logging
from datetime import datetime, timezone

from .base import Job, Scraper
# url_utils lives at discovery/ root (one level up from scrapers/); scrapers are
# run with discovery/ on sys.path so the import works without path gymnastics.
from url_utils import canonical_url, normalize_url

logger = logging.getLogger(__name__)


class JobspyScraper(Scraper):
    """Scrapes job boards via python-jobspy. Unlike ATS scrapers, this searches
    by keyword/location rather than company slug."""

    ats_name = "jobspy"

    def __init__(self, timeout: int = 30):
        super().__init__(timeout=timeout)

    def fetch_jobs_by_search(self, config: dict, match_rules: dict) -> list[Job]:
        """Search job boards using jobspy.

        Args:
            config: The 'jobspy' section from keywords.yaml with search params.
            match_rules: The 'match_rules' section for building search terms.

        Returns:
            List of Job objects from board results.
        """
        jobs = self._run_search(config, match_rules)

        for extra in config.get("local_searches") or []:
            merged = {**config, **extra}
            merged.pop("local_searches", None)
            logger.info("JobSpy: local_searches pass -> %s (remote=%s)",
                        merged.get("location"), merged.get("is_remote"))
            jobs.extend(self._run_search(merged, match_rules))

        return jobs

    def _run_search(self, config: dict, match_rules: dict) -> list[Job]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            logger.error("python-jobspy not installed. Run: pip install python-jobspy")
            return []

        sites = config.get("sites", ["linkedin"])
        search_term = config.get("search_term")
        if not search_term:
            # Build search term from domain keywords if not explicitly set
            domain_kw = match_rules.get("domain_keywords_in_title", [])
            tier_kw = match_rules.get("tier_keywords_in_title", [])
            # Combine a few top domain keywords for a broad search
            terms = domain_kw[:4] if domain_kw else ["security"]
            search_term = " OR ".join(f'"{t}"' for t in terms)

        location = config.get("location", "USA")
        results_wanted = config.get("results_wanted", 50)
        hours_old = config.get("hours_old", 72)
        country_indeed = config.get("country_indeed", "USA")
        fetch_description = config.get("linkedin_fetch_description", False)
        is_remote = config.get("is_remote", False)

        logger.info("JobSpy: searching %s for '%s' in %s (last %dh, max %d results, remote=%s)",
                    sites, search_term, location, hours_old, results_wanted, is_remote)

        try:
            df = scrape_jobs(
                site_name=sites,
                search_term=search_term,
                location=location,
                results_wanted=results_wanted,
                hours_old=hours_old,
                country_indeed=country_indeed,
                linkedin_fetch_description=fetch_description,
                is_remote=is_remote,
            )
        except Exception as e:
            logger.warning("JobSpy scrape failed: %s", e)
            return []

        if df is None or df.empty:
            logger.info("JobSpy: no results returned")
            return []

        import pandas as pd

        jobs = []
        for row in df.to_dict("records"):
            job = self._row_to_job(row, pd)
            if job:
                if is_remote and not job.location:
                    job.location = f"Remote, {location}"
                    if job.remote is None:
                        job.remote = True
                jobs.append(job)

        logger.info("JobSpy: fetched %d jobs from %s", len(jobs), sites)
        return jobs

    def _row_to_job(self, row, pd) -> Job | None:
        """Map a jobspy DataFrame row to a Job dataclass."""
        site = str(row.get("site", "unknown")).lower()
        aggregator_url = str(row.get("job_url", ""))
        direct_url = str(row.get("job_url_direct") or "")

        # Canonicalize: prefer the ATS URL over the LinkedIn/Indeed URL when
        # job_url_direct resolves to a supported ATS. The ATS URL is more
        # stable (LinkedIn URLs expire), carries the real slug for candidate
        # tracking, and matches the URL the company uses in ack mail.
        job_url = canonical_url(aggregator_url, [direct_url])

        # Deterministic ID based on the canonicalized URL so re-scans of the
        # same posting produce the same ID even if the tracking params differ.
        url_hash = hashlib.sha256(job_url.encode()).hexdigest()[:16] if job_url else "unknown"
        job_id = f"jobspy:{site}:{url_hash}"

        company = str(row.get("company", "")).strip()
        title = str(row.get("title", "")).strip()
        if not title:
            return None

        # Location assembly
        loc_parts = []
        for field in ("city", "state", "country"):
            val = row.get(field)
            if val and not (isinstance(val, float) and pd.isna(val)):
                loc_parts.append(str(val).strip())
        location = ", ".join(loc_parts) if loc_parts else ""

        is_remote = row.get("is_remote")
        if isinstance(is_remote, float) and pd.isna(is_remote):
            is_remote = None
        elif is_remote is not None:
            is_remote = bool(is_remote)

        description = str(row.get("description", "")) if row.get("description") is not None else ""
        if isinstance(row.get("description"), float):
            description = ""

        # Parse date_posted
        posted_at = None
        raw_date = row.get("date_posted")
        if raw_date is not None and not (isinstance(raw_date, float) and pd.isna(raw_date)):
            if isinstance(raw_date, datetime):
                posted_at = raw_date if raw_date.tzinfo else raw_date.replace(tzinfo=timezone.utc)
            elif isinstance(raw_date, str):
                try:
                    posted_at = datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

        # Determine tier - board jobs don't have a tier, use a generic one
        company_tier = "board_match"

        # Preserve supplementary URLs that often contain the company's real ATS
        # slug. LinkedIn's job_url_direct is the external-apply URL; company_url
        # variants are careers-page links. Candidate tracking uses these to
        # auto-detect ATS platform and slug for promotion suggestions. We also
        # preserve the original aggregator URL here so the LinkedIn/Indeed
        # provenance stays discoverable even though Job.url now resolves to the
        # ATS URL when one is available.
        raw = {
            "aggregator_url": aggregator_url,
            "job_url_direct": direct_url,
            "company_url": str(row.get("company_url") or ""),
            "company_url_direct": str(row.get("company_url_direct") or ""),
        }

        return Job(
            id=job_id,
            company=company,
            company_slug=company.lower().replace(" ", "-"),
            company_tier=company_tier,
            title=title,
            location=location,
            remote=is_remote,
            url=job_url,
            posted_at=posted_at,
            description_text=description,
            source="board",
            raw=raw,
        )

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str) -> list[Job]:
        """Not used for JobSpy - exists to satisfy the Scraper interface.
        Use fetch_jobs_by_search() instead."""
        raise NotImplementedError("JobspyScraper uses fetch_jobs_by_search(), not fetch_jobs()")

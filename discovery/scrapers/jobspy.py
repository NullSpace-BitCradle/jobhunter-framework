"""JobSpy-based scraper for job board aggregators (LinkedIn, Indeed, Glassdoor, etc.)."""
import hashlib
import logging
from datetime import date, datetime, timezone

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

    @staticmethod
    def _clean(value, pd) -> str:
        """str(value) with every missing-data form mapped to "".

        pandas represents missing cells as NaN (float), pd.NA, pd.NaT or None
        depending on dtype and version; `str(x or "")` turned NaN into the
        literal "nan" (NaN is truthy). Non-scalars (lists) are stringified as-is.
        """
        if value is None:
            return ""
        if not isinstance(value, (list, tuple, set, dict)):
            try:
                if pd.isna(value):
                    return ""
            except (TypeError, ValueError):
                pass
        return str(value).strip()

    @staticmethod
    def _parse_posted(raw_date, pd) -> datetime | None:
        """date_posted -> aware datetime. JobSpy 1.4 returns datetime.date (no
        time part); older versions and tests may give datetime, pd.Timestamp or
        an ISO string. NaT/NaN/None give None."""
        if raw_date is None:
            return None
        try:
            if pd.isna(raw_date):  # NaN, pd.NA, pd.NaT
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(raw_date, datetime):  # includes pd.Timestamp
            return raw_date if raw_date.tzinfo else raw_date.replace(tzinfo=timezone.utc)
        if isinstance(raw_date, date):
            return datetime(raw_date.year, raw_date.month, raw_date.day, tzinfo=timezone.utc)
        if isinstance(raw_date, str) and raw_date.strip():
            try:
                parsed = datetime.fromisoformat(raw_date.strip())
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return None

    def _row_to_job(self, row, pd) -> Job | None:
        """Map a jobspy DataFrame row to a Job dataclass.

        Column shape (python-jobspy 1.4.x, `JobPost` in jobspy/model.py): a single
        `location` string column ("City, ST, USA"), `company`, `job_url`,
        `job_url_direct`, `date_posted` (datetime.date), `is_remote`,
        `description`. Older fixtures used separate `city`/`state`/`country`
        columns; those are still honoured when `location` is absent.
        """
        clean = lambda v: self._clean(v, pd)  # noqa: E731

        site = clean(row.get("site")).lower() or "unknown"
        aggregator_url = clean(row.get("job_url"))
        direct_url = clean(row.get("job_url_direct"))

        # Canonicalize: prefer the ATS URL over the LinkedIn/Indeed URL when
        # job_url_direct resolves to a supported ATS. The ATS URL is more
        # stable (LinkedIn URLs expire), carries the real slug for candidate
        # tracking, and matches the URL the company uses in ack mail.
        job_url = canonical_url(aggregator_url, [direct_url])

        # Deterministic ID based on the canonicalized URL so re-scans of the
        # same posting produce the same ID even if the tracking params differ.
        url_hash = hashlib.sha256(job_url.encode()).hexdigest()[:16] if job_url else "unknown"
        job_id = f"jobspy:{site}:{url_hash}"

        company = clean(row.get("company"))
        title = clean(row.get("title"))
        if not title:
            return None

        # Location: the single `location` column, else assemble city/state/country.
        location = clean(row.get("location"))
        if not location:
            loc_parts = [clean(row.get(f)) for f in ("city", "state", "country")]
            location = ", ".join(p for p in loc_parts if p)

        is_remote = row.get("is_remote")
        try:
            is_remote = None if is_remote is None or pd.isna(is_remote) else bool(is_remote)
        except (TypeError, ValueError):
            is_remote = None

        description = clean(row.get("description"))
        posted_at = self._parse_posted(row.get("date_posted"), pd)

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
            "company_url": clean(row.get("company_url")),
            "company_url_direct": clean(row.get("company_url_direct")),
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

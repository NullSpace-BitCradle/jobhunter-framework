"""Unit tests for JobSpy scraper field mapping and search configuration."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from scrapers.jobspy import JobspyScraper


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame mimicking jobspy output."""
    return pd.DataFrame(rows)


SAMPLE_ROWS = [
    {
        "site": "linkedin",
        "title": "Principal Security Engineer",
        "company": "Datadog",
        "job_url": "https://linkedin.com/jobs/view/123456",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "is_remote": True,
        "description": "Lead our security engineering team...",
        "date_posted": datetime(2026, 4, 10, tzinfo=timezone.utc),
    },
    {
        "site": "indeed",
        "title": "Staff Vulnerability Manager",
        "company": "Wiz, Inc.",
        "job_url": "https://indeed.com/jobs/view/789",
        "city": "",
        "state": "",
        "country": "US",
        "is_remote": True,
        "description": "Manage vulnerability programs...",
        "date_posted": "2026-04-11",
    },
]


class TestJobspyScraper:
    def setup_method(self):
        self.scraper = JobspyScraper()

    @patch("jobspy.scrape_jobs")
    def test_happy_path(self, mock_scrape):
        mock_scrape.return_value = _make_df(SAMPLE_ROWS)
        config = {"enabled": True, "sites": ["linkedin"], "location": "USA",
                  "results_wanted": 20, "hours_old": 72}
        match_rules = {"domain_keywords_in_title": ["security", "vulnerability"]}

        jobs = self.scraper.fetch_jobs_by_search(config, match_rules)

        assert len(jobs) == 2

        j = jobs[0]
        assert j.id.startswith("jobspy:linkedin:")
        assert j.title == "Principal Security Engineer"
        assert j.company == "Datadog"
        assert j.remote is True
        assert j.source == "board"
        assert j.company_tier == "board_match"
        assert "San Francisco" in j.location

    @patch("jobspy.scrape_jobs")
    def test_indeed_row_mapping(self, mock_scrape):
        mock_scrape.return_value = _make_df([SAMPLE_ROWS[1]])
        config = {"enabled": True, "sites": ["indeed"], "location": "USA",
                  "results_wanted": 10, "hours_old": 72}
        match_rules = {"domain_keywords_in_title": ["vulnerability"]}

        jobs = self.scraper.fetch_jobs_by_search(config, match_rules)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id.startswith("jobspy:indeed:")
        assert j.company == "Wiz, Inc."
        assert j.posted_at is not None

    @patch("jobspy.scrape_jobs")
    def test_empty_results(self, mock_scrape):
        mock_scrape.return_value = pd.DataFrame()
        config = {"enabled": True, "sites": ["linkedin"], "location": "USA",
                  "results_wanted": 10, "hours_old": 72}
        jobs = self.scraper.fetch_jobs_by_search(config, {})
        assert jobs == []

    @patch("jobspy.scrape_jobs")
    def test_scrape_failure(self, mock_scrape):
        mock_scrape.side_effect = Exception("Rate limited")
        config = {"enabled": True, "sites": ["linkedin"], "location": "USA",
                  "results_wanted": 10, "hours_old": 72}
        jobs = self.scraper.fetch_jobs_by_search(config, {})
        assert jobs == []

    @patch("jobspy.scrape_jobs")
    def test_missing_title_skipped(self, mock_scrape):
        row = {"site": "linkedin", "title": "", "company": "X",
               "job_url": "https://x.com/jobs/1", "description": ""}
        mock_scrape.return_value = _make_df([row])
        config = {"enabled": True, "sites": ["linkedin"], "location": "USA",
                  "results_wanted": 10, "hours_old": 72}
        jobs = self.scraper.fetch_jobs_by_search(config, {})
        assert jobs == []

    @patch("jobspy.scrape_jobs")
    def test_auto_search_term_from_domain_keywords(self, mock_scrape):
        """When no search_term in config, builds from domain keywords."""
        mock_scrape.return_value = pd.DataFrame()
        config = {"enabled": True, "sites": ["linkedin"], "location": "USA",
                  "results_wanted": 10, "hours_old": 72}
        match_rules = {"domain_keywords_in_title": ["security", "vulnerability", "threat"]}

        self.scraper.fetch_jobs_by_search(config, match_rules)

        call_kwargs = mock_scrape.call_args[1]
        assert "security" in call_kwargs["search_term"]
        assert "vulnerability" in call_kwargs["search_term"]

    def test_fetch_jobs_raises(self):
        """The per-company fetch_jobs() interface is not supported."""
        with pytest.raises(NotImplementedError):
            self.scraper.fetch_jobs("slug", "name", "tier")


# ---------------------------------------------------------------------------
# URL canonicalization on ingest
# ---------------------------------------------------------------------------

class TestUrlCanonicalization:
    """When JobSpy returns a row with job_url_direct pointing to an ATS, the
    resulting Job.url should be the ATS URL (normalized), not the LinkedIn
    URL. The LinkedIn URL is preserved in raw for provenance.
    """

    def setup_method(self):
        self.scraper = JobspyScraper()

    @patch("jobspy.scrape_jobs")
    def test_prefers_ats_direct_url(self, mock_scrape):
        row = {
            "site": "linkedin",
            "title": "Staff Security Engineer",
            "company": "Foo Corp",
            "job_url": "https://www.linkedin.com/jobs/view/12345?trk=public",
            "job_url_direct": "https://boards.greenhouse.io/foocorp/jobs/5555",
            "city": "SF", "state": "CA", "country": "US",
            "description": "...",
        }
        mock_scrape.return_value = _make_df([row])
        jobs = self.scraper.fetch_jobs_by_search({"sites": ["linkedin"]}, {})
        assert len(jobs) == 1
        j = jobs[0]
        assert j.url == "https://boards.greenhouse.io/foocorp/jobs/5555"
        # Aggregator URL still available in raw for provenance
        assert j.raw.get("aggregator_url") == "https://www.linkedin.com/jobs/view/12345?trk=public"

    @patch("jobspy.scrape_jobs")
    def test_keeps_linkedin_when_no_ats_direct(self, mock_scrape):
        """When job_url_direct is empty or not an ATS, the LinkedIn URL is used (normalized)."""
        row = {
            "site": "linkedin",
            "title": "Engineer",
            "company": "X",
            "job_url": "https://www.linkedin.com/jobs/view/999?trk=abc&utm_source=x",
            "job_url_direct": "",
            "description": "",
        }
        mock_scrape.return_value = _make_df([row])
        jobs = self.scraper.fetch_jobs_by_search({"sites": ["linkedin"]}, {})
        # Tracking stripped; LinkedIn kept since nothing else was ATS
        assert jobs[0].url == "https://www.linkedin.com/jobs/view/999"

    @patch("jobspy.scrape_jobs")
    def test_id_stable_across_tracking_variations(self, mock_scrape):
        """Same posting with different tracking params should produce the same job_id."""
        row_a = {
            "site": "linkedin", "title": "T", "company": "C",
            "job_url": "https://www.linkedin.com/jobs/view/42?trk=a",
            "description": "",
        }
        row_b = {
            "site": "linkedin", "title": "T", "company": "C",
            "job_url": "https://www.linkedin.com/jobs/view/42?trk=b&utm_source=x",
            "description": "",
        }
        mock_scrape.return_value = _make_df([row_a])
        jobs_a = self.scraper.fetch_jobs_by_search({"sites": ["linkedin"]}, {})
        mock_scrape.return_value = _make_df([row_b])
        jobs_b = self.scraper.fetch_jobs_by_search({"sites": ["linkedin"]}, {})
        assert jobs_a[0].id == jobs_b[0].id


# ---------------------------------------------------------------------------
# Row shape of python-jobspy >= 1.2 (verified against 1.4.1's JobPost model):
# a single `location` string column, `date_posted` as datetime.date, and
# missing cells as NaN / None / pd.NA / pd.NaT.
# ---------------------------------------------------------------------------

import datetime as _dt


def _fetch(rows):
    scraper = JobspyScraper()
    with patch("jobspy.scrape_jobs", return_value=_make_df(rows)):
        return scraper.fetch_jobs_by_search({"sites": ["linkedin"]}, {})


class TestRealJobspyRowShape:
    def test_single_location_column_is_used(self):
        jobs = _fetch([{
            "site": "linkedin", "title": "Staff Security Engineer", "company": "Acme",
            "job_url": "https://www.linkedin.com/jobs/view/1",
            "location": "San Francisco, CA, USA", "is_remote": False,
            "description": "x", "date_posted": _dt.date(2026, 4, 10),
        }])
        assert jobs[0].location == "San Francisco, CA, USA"

    def test_location_column_wins_over_legacy_city_state_country(self):
        jobs = _fetch([{
            "site": "linkedin", "title": "T", "company": "C",
            "job_url": "https://www.linkedin.com/jobs/view/2",
            "location": "Remote, USA", "city": "Ignored", "state": "ZZ", "country": "Nowhere",
        }])
        assert jobs[0].location == "Remote, USA"

    def test_date_posted_as_plain_date(self):
        """JobSpy returns datetime.date, which is NOT a datetime: it used to give None."""
        jobs = _fetch([{
            "site": "linkedin", "title": "T", "company": "C",
            "job_url": "https://www.linkedin.com/jobs/view/3",
            "date_posted": _dt.date(2026, 4, 10),
        }])
        assert jobs[0].posted_at == datetime(2026, 4, 10, tzinfo=timezone.utc)

    @pytest.mark.parametrize("missing", [float("nan"), None, pd.NA, pd.NaT])
    def test_missing_values_never_become_the_string_nan(self, missing):
        jobs = _fetch([{
            "site": "linkedin", "title": "Real Title", "company": missing,
            "job_url": "https://www.linkedin.com/jobs/view/4",
            "job_url_direct": missing, "location": missing, "description": missing,
            "company_url": missing, "company_url_direct": missing,
            "date_posted": missing, "is_remote": missing,
        }])
        j = jobs[0]
        assert j.company == ""
        assert j.location == ""
        assert j.description_text == ""
        assert j.posted_at is None
        assert j.remote is None
        assert j.raw["job_url_direct"] == ""
        assert j.raw["company_url"] == ""
        assert j.raw["company_url_direct"] == ""
        assert "nan" not in (j.company + j.location + j.description_text + j.url).lower().replace("linkedin", "")

    def test_nan_in_dataframe_column_from_missing_keys(self):
        """A column that is only present in some rows becomes NaN in the others."""
        jobs = _fetch([
            {"site": "linkedin", "title": "A", "company": "Acme",
             "job_url": "https://www.linkedin.com/jobs/view/5", "description": "d",
             "date_posted": _dt.date(2026, 4, 1)},
            {"site": "linkedin", "title": "B", "company": "Beta",
             "job_url": "https://www.linkedin.com/jobs/view/6"},
        ])
        b = [j for j in jobs if j.title == "B"][0]
        assert b.description_text == ""
        assert b.posted_at is None

    def test_pandas_timestamp_date(self):
        jobs = _fetch([{
            "site": "linkedin", "title": "T", "company": "C",
            "job_url": "https://www.linkedin.com/jobs/view/7",
            "date_posted": pd.Timestamp("2026-04-10"),
        }])
        assert jobs[0].posted_at == datetime(2026, 4, 10, tzinfo=timezone.utc)

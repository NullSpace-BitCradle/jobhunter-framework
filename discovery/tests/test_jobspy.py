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

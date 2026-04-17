"""Unit tests for ATS scrapers. Mocks HTTP responses to test field mapping and error handling."""
import json
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.ashby import AshbyScraper
from scrapers.smartrecruiters import SmartRecruitersScraper
from scrapers.workable import WorkableScraper
from scrapers.base import Job

import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_data=None, text="", raise_exc=None):
    """Build a mock requests.Response."""
    if raise_exc:
        m = MagicMock()
        m.side_effect = raise_exc
        return m
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.json.return_value = json_data
    resp.text = text
    return resp


def _patch_session(scraper, return_value=None, side_effect=None):
    """Patch a scraper's _throttled_get method."""
    return patch.object(scraper, "_throttled_get",
                        return_value=return_value,
                        side_effect=side_effect)


COMPANY = ("testco", "Test Company", "tier_1_saas")


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

GREENHOUSE_RESPONSE = {
    "jobs": [
        {
            "id": 12345,
            "title": "Principal Security Engineer",
            "location": {"name": "Remote - US"},
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/12345",
            "updated_at": "2026-04-01T12:00:00Z",
            "content": "<p>Build secure systems.</p>",
        },
        {
            "id": 67890,
            "title": "Staff Engineer",
            "location": {"name": "New York, NY (On-site)"},
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/67890",
            "updated_at": "2026-03-28T08:00:00Z",
            "content": "<b>Design</b> stuff.",
        },
    ]
}


class TestGreenhouseScraper:
    def test_happy_path(self):
        scraper = GreenhouseScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data=GREENHOUSE_RESPONSE)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 2
        j = jobs[0]
        assert j.id == "greenhouse:testco:12345"
        assert j.title == "Principal Security Engineer"
        assert j.location == "Remote - US"
        assert j.remote is True
        assert j.url == "https://boards.greenhouse.io/testco/jobs/12345"
        assert j.posted_at == datetime.fromisoformat("2026-04-01T12:00:00+00:00")
        assert "Build secure systems" in j.description_text
        # Second job: on-site
        assert jobs[1].remote is False

    def test_404(self):
        scraper = GreenhouseScraper()
        with _patch_session(scraper, return_value=_mock_response(404)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert jobs == []

    def test_timeout(self):
        scraper = GreenhouseScraper()
        with _patch_session(scraper, side_effect=requests.Timeout("timed out")):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert jobs == []

    def test_empty_board(self):
        scraper = GreenhouseScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data={"jobs": []})):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert jobs == []


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

LEVER_RESPONSE = [
    {
        "id": "aaa-bbb-ccc",
        "text": "Senior Security Architect",
        "categories": {"location": "Remote, US", "commitment": "Full-time"},
        "workplaceType": "remote",
        "hostedUrl": "https://jobs.lever.co/testco/aaa-bbb-ccc",
        "createdAt": 1743465600000,  # 2025-04-01 in ms
        "descriptionPlain": "Design architecture for cloud security.",
    }
]


class TestLeverScraper:
    def test_happy_path(self):
        scraper = LeverScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data=LEVER_RESPONSE)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "lever:testco:aaa-bbb-ccc"
        assert j.title == "Senior Security Architect"
        assert j.remote is True
        assert "cloud security" in j.description_text

    def test_404(self):
        scraper = LeverScraper()
        with _patch_session(scraper, return_value=_mock_response(404)):
            assert scraper.fetch_jobs(*COMPANY) == []

    def test_unexpected_shape(self):
        scraper = LeverScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data={"error": "bad"})):
            assert scraper.fetch_jobs(*COMPANY) == []


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

ASHBY_RESPONSE = {
    "jobs": [
        {
            "id": "ash-001",
            "title": "Staff Threat Researcher",
            "locationName": "United States",
            "isRemote": True,
            "jobUrl": "https://jobs.ashbyhq.com/testco/ash-001",
            "publishedDate": "2026-04-05T00:00:00Z",
            "descriptionPlain": "Hunt threats across the ecosystem.",
        }
    ]
}


class TestAshbyScraper:
    def test_happy_path(self):
        scraper = AshbyScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data=ASHBY_RESPONSE)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "ashby:testco:ash-001"
        assert j.title == "Staff Threat Researcher"
        assert j.remote is True
        assert j.location == "United States"

    def test_timeout(self):
        scraper = AshbyScraper()
        with _patch_session(scraper, side_effect=requests.ReadTimeout("read timed out")):
            assert scraper.fetch_jobs(*COMPANY) == []


# ---------------------------------------------------------------------------
# SmartRecruiters
# ---------------------------------------------------------------------------

SMARTRECRUITERS_RESPONSE = {
    "totalFound": 1,
    "content": [
        {
            "id": "sr-001",
            "name": "Principal VM Engineer",
            "location": {"city": "Denver", "region": "CO", "country": "US", "remote": True},
            "releasedDate": "2026-04-02T10:00:00Z",
            "jobAd": {"sections": {"jobDescription": {"text": "<p>Manage vulnerability programs.</p>"}}},
        }
    ],
}


class TestSmartRecruitersScraper:
    def test_happy_path(self):
        scraper = SmartRecruitersScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data=SMARTRECRUITERS_RESPONSE)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "smartrecruiters:testco:sr-001"
        assert j.title == "Principal VM Engineer"
        assert j.remote is True
        assert "Denver" in j.location
        assert "Remote" in j.location
        assert "vulnerability programs" in j.description_text

    def test_empty_slug(self):
        scraper = SmartRecruitersScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data={"totalFound": 0, "content": []})):
            assert scraper.fetch_jobs(*COMPANY) == []


# ---------------------------------------------------------------------------
# Workable
# ---------------------------------------------------------------------------

WORKABLE_RESPONSE = {
    "jobs": [
        {
            "id": "wk-001",
            "shortcode": "ABC123",
            "title": "Lead Security Engineer",
            "city": "Austin",
            "region": "TX",
            "country": "US",
            "telecommuting": True,
            "url": "https://apply.workable.com/testco/j/ABC123/",
            "created_at": "2026-04-03T14:00:00Z",
            "description": "<div>Defend the perimeter.</div>",
        }
    ]
}


class TestWorkableScraper:
    def test_happy_path(self):
        scraper = WorkableScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data=WORKABLE_RESPONSE)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "workable:testco:wk-001"
        assert j.title == "Lead Security Engineer"
        assert j.remote is True
        assert "Austin" in j.location
        assert "Defend the perimeter" in j.description_text

    def test_empty_jobs(self):
        scraper = WorkableScraper()
        with _patch_session(scraper, return_value=_mock_response(json_data={"jobs": []})):
            assert scraper.fetch_jobs(*COMPANY) == []

    def test_connection_error(self):
        scraper = WorkableScraper()
        with _patch_session(scraper, side_effect=requests.ConnectionError("refused")):
            assert scraper.fetch_jobs(*COMPANY) == []


# ---------------------------------------------------------------------------
# Retry behavior (base Scraper class)
# ---------------------------------------------------------------------------

class TestRetryBehavior:
    """Verify _throttled_get retries on transient errors with exponential backoff.

    All time.sleep calls are patched so tests run fast. We use GreenhouseScraper
    as the concrete test vehicle since all ATS scrapers share the same base
    _throttled_get implementation.
    """

    @patch("scrapers.base.time.sleep")
    def test_retries_on_503_then_succeeds(self, mock_sleep):
        scraper = GreenhouseScraper()
        responses = [_mock_response(503), _mock_response(json_data=GREENHOUSE_RESPONSE)]
        with patch.object(scraper.session, "get", side_effect=responses):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 2  # succeeded on second try
        # Verify we slept once between attempts
        assert mock_sleep.called

    @patch("scrapers.base.time.sleep")
    def test_retries_on_5xx_up_to_max(self, mock_sleep):
        scraper = GreenhouseScraper()
        # 3 consecutive 503s - exhausts retries, returns last response
        responses = [_mock_response(503), _mock_response(503), _mock_response(503)]
        with patch.object(scraper.session, "get", side_effect=responses):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert jobs == []  # scraper sees last 503 as not-ok and returns empty

    @patch("scrapers.base.time.sleep")
    def test_retries_on_timeout_then_succeeds(self, mock_sleep):
        scraper = GreenhouseScraper()
        # First call times out, second succeeds
        with patch.object(
            scraper.session, "get",
            side_effect=[requests.Timeout("t"), _mock_response(json_data=GREENHOUSE_RESPONSE)],
        ):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 2

    @patch("scrapers.base.time.sleep")
    def test_timeout_exhausted_raises(self, mock_sleep):
        """After MAX_RETRIES timeouts, the exception propagates and the scraper catches it."""
        scraper = GreenhouseScraper()
        with patch.object(
            scraper.session, "get",
            side_effect=[requests.Timeout("t")] * 3,
        ):
            jobs = scraper.fetch_jobs(*COMPANY)
        # scraper's try/except RequestException catches the final raise
        assert jobs == []

    @patch("scrapers.base.time.sleep")
    def test_honors_retry_after_header(self, mock_sleep):
        """429 with Retry-After should drive the sleep duration."""
        scraper = GreenhouseScraper()
        rate_limited = _mock_response(429)
        rate_limited.headers = {"Retry-After": "3"}
        ok_resp = _mock_response(json_data=GREENHOUSE_RESPONSE)
        with patch.object(scraper.session, "get", side_effect=[rate_limited, ok_resp]):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 2
        # First sleep arg should include the 3s Retry-After (vs. default 1s backoff)
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        assert any(s >= 3 for s in sleep_calls)

    @patch("scrapers.base.time.sleep")
    def test_retry_after_cap(self, mock_sleep):
        """Retry-After cap prevents absurdly long waits."""
        scraper = GreenhouseScraper()
        rate_limited = _mock_response(429)
        rate_limited.headers = {"Retry-After": "9999"}
        ok_resp = _mock_response(json_data=GREENHOUSE_RESPONSE)
        with patch.object(scraper.session, "get", side_effect=[rate_limited, ok_resp]):
            scraper.fetch_jobs(*COMPANY)
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        # cap is 10.0 - no sleep should exceed that
        assert max(sleep_calls) <= 10.0


# ---------------------------------------------------------------------------
# Per-company timeout override
# ---------------------------------------------------------------------------

class TestTimeoutOverride:
    def test_timeout_override_used_when_provided(self):
        scraper = GreenhouseScraper()  # default self.timeout=10
        captured = {}

        def capture(url, **kwargs):
            captured.update(kwargs)
            return _mock_response(json_data={"jobs": []})

        with patch.object(scraper, "_throttled_get", side_effect=capture):
            scraper.fetch_jobs(*COMPANY, timeout=45)
        assert captured.get("timeout") == 45

    def test_falls_back_to_self_timeout_when_none(self):
        scraper = GreenhouseScraper()
        captured = {}

        def capture(url, **kwargs):
            captured.update(kwargs)
            return _mock_response(json_data={"jobs": []})

        with patch.object(scraper, "_throttled_get", side_effect=capture):
            scraper.fetch_jobs(*COMPANY)  # no timeout override
        assert captured.get("timeout") == scraper.timeout  # 10

    def test_ashby_default_timeout(self):
        """Ashby scraper in main.py's SCRAPER_REGISTRY is instantiated with timeout=20."""
        scraper = AshbyScraper(timeout=20)
        captured = {}

        def capture(url, **kwargs):
            captured.update(kwargs)
            return _mock_response(json_data={"jobs": []})

        with patch.object(scraper, "_throttled_get", side_effect=capture):
            scraper.fetch_jobs(*COMPANY)
        assert captured.get("timeout") == 20

"""Unit tests for ATS scrapers. Mocks HTTP responses to test field mapping and error handling."""
import json
from datetime import datetime
from unittest.mock import patch, MagicMock

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
        with patch("scrapers.greenhouse.requests.get", return_value=_mock_response(json_data=GREENHOUSE_RESPONSE)):
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
        with patch("scrapers.greenhouse.requests.get", return_value=_mock_response(404)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert jobs == []

    def test_timeout(self):
        scraper = GreenhouseScraper()
        with patch("scrapers.greenhouse.requests.get", side_effect=requests.Timeout("timed out")):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert jobs == []

    def test_empty_board(self):
        scraper = GreenhouseScraper()
        with patch("scrapers.greenhouse.requests.get", return_value=_mock_response(json_data={"jobs": []})):
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
        with patch("scrapers.lever.requests.get", return_value=_mock_response(json_data=LEVER_RESPONSE)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "lever:testco:aaa-bbb-ccc"
        assert j.title == "Senior Security Architect"
        assert j.remote is True
        assert "cloud security" in j.description_text

    def test_404(self):
        scraper = LeverScraper()
        with patch("scrapers.lever.requests.get", return_value=_mock_response(404)):
            assert scraper.fetch_jobs(*COMPANY) == []

    def test_unexpected_shape(self):
        scraper = LeverScraper()
        with patch("scrapers.lever.requests.get", return_value=_mock_response(json_data={"error": "bad"})):
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
        with patch("scrapers.ashby.requests.get", return_value=_mock_response(json_data=ASHBY_RESPONSE)):
            jobs = scraper.fetch_jobs(*COMPANY)
        assert len(jobs) == 1
        j = jobs[0]
        assert j.id == "ashby:testco:ash-001"
        assert j.title == "Staff Threat Researcher"
        assert j.remote is True
        assert j.location == "United States"

    def test_timeout(self):
        scraper = AshbyScraper()
        with patch("scrapers.ashby.requests.get", side_effect=requests.ReadTimeout("read timed out")):
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
        with patch("scrapers.smartrecruiters.requests.get", return_value=_mock_response(json_data=SMARTRECRUITERS_RESPONSE)):
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
        with patch("scrapers.smartrecruiters.requests.get", return_value=_mock_response(json_data={"totalFound": 0, "content": []})):
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
        with patch("scrapers.workable.requests.get", return_value=_mock_response(json_data=WORKABLE_RESPONSE)):
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
        with patch("scrapers.workable.requests.get", return_value=_mock_response(json_data={"jobs": []})):
            assert scraper.fetch_jobs(*COMPANY) == []

    def test_connection_error(self):
        scraper = WorkableScraper()
        with patch("scrapers.workable.requests.get", side_effect=requests.ConnectionError("refused")):
            assert scraper.fetch_jobs(*COMPANY) == []

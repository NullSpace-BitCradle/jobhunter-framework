"""Unit tests for cross-source deduplication."""
from datetime import datetime, timezone

import pytest

from scrapers.base import Job
from dedup import normalize_company, normalize_title, deduplicate_cross_source


def _job(id: str, company: str, title: str, source: str = "ats") -> Job:
    return Job(
        id=id,
        company=company,
        company_slug=company.lower().replace(" ", "-"),
        company_tier="tier_1_saas",
        title=title,
        location="Remote, US",
        remote=True,
        url=f"https://example.com/jobs/{id}",
        posted_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        description_text="Job description here.",
        source=source,
    )


class TestNormalization:
    def test_company_strips_inc(self):
        assert normalize_company("Datadog, Inc.") == "datadog"

    def test_company_strips_llc(self):
        assert normalize_company("Acme LLC") == "acme"

    def test_company_strips_technologies(self):
        assert normalize_company("Wiz Technologies") == "wiz"

    def test_company_case_insensitive(self):
        assert normalize_company("CLOUDFLARE") == normalize_company("cloudflare")

    def test_title_strips_parens(self):
        assert normalize_title("Security Engineer (Remote)") == "security engineer"

    def test_title_normalizes_whitespace(self):
        assert normalize_title("  Staff Security Architect  ") == "staff security architect"

    def test_title_strips_punctuation(self):
        assert normalize_title("Sr. Security Engineer - Cloud") == "sr security engineer cloud"


class TestDeduplicateCrossSource:
    def test_no_duplicates(self):
        """Different jobs at different companies pass through."""
        jobs = [
            _job("greenhouse:dd:1", "Datadog", "Principal Security Engineer", source="ats"),
            _job("jobspy:linkedin:2", "Wiz", "Staff Vulnerability Manager", source="board"),
        ]
        result = deduplicate_cross_source(jobs)
        assert len(result) == 2

    def test_exact_duplicate_prefers_ats(self):
        """Same job from ATS and board - ATS version kept."""
        jobs = [
            _job("greenhouse:dd:1", "Datadog", "Principal Security Engineer", source="ats"),
            _job("jobspy:linkedin:2", "Datadog", "Principal Security Engineer", source="board"),
        ]
        result = deduplicate_cross_source(jobs)
        assert len(result) == 1
        assert result[0].id == "greenhouse:dd:1"
        assert result[0].source == "ats"

    def test_board_first_then_ats_replaces(self):
        """If board version is seen first, ATS version replaces it."""
        jobs = [
            _job("jobspy:linkedin:2", "Datadog", "Principal Security Engineer", source="board"),
            _job("greenhouse:dd:1", "Datadog", "Principal Security Engineer", source="ats"),
        ]
        result = deduplicate_cross_source(jobs)
        assert len(result) == 1
        assert result[0].id == "greenhouse:dd:1"

    def test_fuzzy_company_name_match(self):
        """'Wiz, Inc.' on LinkedIn matches 'Wiz' on ATS."""
        jobs = [
            _job("greenhouse:wiz:1", "Wiz", "Staff Cloud Security Architect", source="ats"),
            _job("jobspy:linkedin:2", "Wiz, Inc.", "Staff Cloud Security Architect", source="board"),
        ]
        result = deduplicate_cross_source(jobs)
        assert len(result) == 1

    def test_fuzzy_title_match(self):
        """Title with parens on board matches clean ATS title."""
        jobs = [
            _job("greenhouse:dd:1", "Datadog", "Principal Security Engineer", source="ats"),
            _job("jobspy:linkedin:2", "Datadog", "Principal Security Engineer (Remote)", source="board"),
        ]
        result = deduplicate_cross_source(jobs)
        assert len(result) == 1

    def test_different_titles_not_deduped(self):
        """Same company, different roles are NOT duplicates."""
        jobs = [
            _job("greenhouse:dd:1", "Datadog", "Principal Security Engineer", source="ats"),
            _job("jobspy:linkedin:2", "Datadog", "Staff Product Manager", source="board"),
        ]
        result = deduplicate_cross_source(jobs)
        assert len(result) == 2

    def test_same_source_same_key_keeps_first(self):
        """Two ATS jobs with same normalized key - first one wins (ID dedup is separate)."""
        jobs = [
            _job("greenhouse:dd:1", "Datadog", "Principal Security Engineer", source="ats"),
            _job("greenhouse:dd:2", "Datadog", "Principal Security Engineer", source="ats"),
        ]
        result = deduplicate_cross_source(jobs)
        # Same source - cross-source dedup doesn't remove these (ID dedup handles it)
        assert len(result) == 1  # Same normalized key, first one wins

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

    def test_same_source_distinct_postings_are_both_kept(self):
        """Two ATS postings with the same title but different ids are different
        postings (e.g. London vs Remote-US); cross-source dedup must not drop one.
        (It used to keep only the first, which could discard the only posting that
        passes the location filter.)"""
        jobs = [
            _job("greenhouse:dd:1", "Datadog", "Principal Security Engineer", source="ats"),
            _job("greenhouse:dd:2", "Datadog", "Principal Security Engineer", source="ats"),
        ]
        result = deduplicate_cross_source(jobs)
        assert [j.id for j in result] == ["greenhouse:dd:1", "greenhouse:dd:2"]

    def test_same_id_repeated_is_dropped(self):
        """The same board posting returned by the main search and a local_searches pass."""
        jobs = [
            _job("jobspy:linkedin:9", "Wiz", "Staff Security Engineer", source="board"),
            _job("jobspy:linkedin:9", "Wiz", "Staff Security Engineer", source="board"),
        ]
        assert len(deduplicate_cross_source(jobs)) == 1

    def test_board_dropped_when_ats_copy_has_location_suffix(self):
        jobs = [
            _job("greenhouse:wiz:1", "Wiz", "Staff Security Engineer", source="ats"),
            _job("jobspy:linkedin:2", "Wiz", "Staff Security Engineer - Remote", source="board"),
        ]
        assert [j.id for j in deduplicate_cross_source(jobs)] == ["greenhouse:wiz:1"]

    def test_distinct_role_suffix_is_not_collapsed(self):
        jobs = [
            _job("greenhouse:x:1", "X", "Senior Security Engineer - Detection", source="ats"),
            _job("jobspy:linkedin:2", "X", "Senior Security Engineer - Cloud", source="board"),
        ]
        assert len(deduplicate_cross_source(jobs)) == 2

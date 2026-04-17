"""Unit tests for candidate company tracking."""
import json
from datetime import datetime, timezone, timedelta

import pytest

from scrapers.base import Job
from candidates import (
    detect_ats_from_url,
    is_known_company,
    load_candidates,
    save_candidates,
    update_candidate,
    promotable_candidates,
    CANDIDATE_SCORE_THRESHOLD,
    CANDIDATE_MATCH_THRESHOLD,
    CANDIDATE_MAX_AGE_DAYS,
    MAX_SAMPLE_TITLES,
)


def _board_job(company="Foo Labs", title="Senior Security Engineer",
               url="https://linkedin.com/jobs/view/123", raw=None) -> Job:
    return Job(
        id=f"jobspy:linkedin:{hash(company + title)}",
        company=company,
        company_slug=company.lower().replace(" ", "-"),
        company_tier="board_match",
        title=title,
        location="Remote, US",
        remote=True,
        url=url,
        posted_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        description_text="Build secure systems.",
        source="board",
        raw=raw or {},
    )


# ---------------------------------------------------------------------------
# detect_ats_from_url
# ---------------------------------------------------------------------------

class TestDetectAts:
    def test_greenhouse_boards(self):
        assert detect_ats_from_url("https://boards.greenhouse.io/anthropic/jobs/12345") == ("greenhouse", "anthropic")

    def test_greenhouse_job_boards(self):
        assert detect_ats_from_url("https://job-boards.greenhouse.io/figma/jobs/5555") == ("greenhouse", "figma")

    def test_greenhouse_api(self):
        assert detect_ats_from_url("https://boards-api.greenhouse.io/v1/boards/wizinc/jobs") == ("greenhouse", "wizinc")

    def test_lever_jobs(self):
        assert detect_ats_from_url("https://jobs.lever.co/vercel/abc-def-ghi") == ("lever", "vercel")

    def test_lever_api(self):
        assert detect_ats_from_url("https://api.lever.co/v0/postings/netlify") == ("lever", "netlify")

    def test_ashby_jobs(self):
        assert detect_ats_from_url("https://jobs.ashbyhq.com/openai/abc123") == ("ashby", "openai")

    def test_ashby_api(self):
        assert detect_ats_from_url("https://api.ashbyhq.com/posting-api/job-board/ramp") == ("ashby", "ramp")

    def test_smartrecruiters_jobs(self):
        assert detect_ats_from_url("https://jobs.smartrecruiters.com/OracleCorporation/744000") == ("smartrecruiters", "OracleCorporation")

    def test_smartrecruiters_api(self):
        assert detect_ats_from_url("https://api.smartrecruiters.com/v1/companies/bosch/postings") == ("smartrecruiters", "bosch")

    def test_workable(self):
        assert detect_ats_from_url("https://apply.workable.com/toptal/j/ABC123/") == ("workable", "toptal")

    def test_linkedin_easy_apply_returns_none(self):
        assert detect_ats_from_url("https://linkedin.com/jobs/view/123456") is None

    def test_indeed_returns_none(self):
        assert detect_ats_from_url("https://indeed.com/viewjob?jk=abc") is None

    def test_empty_string_returns_none(self):
        assert detect_ats_from_url("") is None

    def test_none_returns_none(self):
        assert detect_ats_from_url(None) is None

    def test_custom_portal_returns_none(self):
        assert detect_ats_from_url("https://careers.megacorp.com/apply/12345") is None


# ---------------------------------------------------------------------------
# is_known_company
# ---------------------------------------------------------------------------

class TestIsKnownCompany:
    def test_matches_tier_1_saas(self):
        companies = {
            "tier_1_saas": [{"name": "Datadog", "ats": "greenhouse", "slug": "datadog"}]
        }
        assert is_known_company("Datadog", companies) is True

    def test_matches_fuzzy_via_normalize(self):
        companies = {
            "tier_1_saas": [{"name": "Datadog", "ats": "greenhouse", "slug": "datadog"}]
        }
        # normalize_company strips ", Inc." etc.
        assert is_known_company("Datadog, Inc.", companies) is True

    def test_not_in_any_tier(self):
        companies = {
            "tier_1_saas": [{"name": "Datadog", "ats": "greenhouse", "slug": "datadog"}]
        }
        assert is_known_company("Unknown Corp", companies) is False

    def test_manual_check_counts_as_known(self):
        companies = {
            "manual_check": [{"name": "CrowdStrike", "careers_url": "https://..."}]
        }
        assert is_known_company("CrowdStrike", companies) is True

    def test_empty_company_name(self):
        companies = {"tier_1_saas": [{"name": "Datadog"}]}
        assert is_known_company("", companies) is False
        assert is_known_company(None, companies) is False

    def test_empty_companies_config(self):
        assert is_known_company("Foo", {}) is False


# ---------------------------------------------------------------------------
# load / save roundtrip + pruning
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_missing_file_returns_default(self, tmp_path):
        state = load_candidates(tmp_path / "nonexistent.json")
        assert state == {"candidates": {}, "last_update": None}

    def test_corrupt_json_returns_default(self, tmp_path):
        bad = tmp_path / "c.json"
        bad.write_text("not json at all")
        state = load_candidates(bad)
        assert state == {"candidates": {}, "last_update": None}

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "c.json"
        state = {"candidates": {"foo": {"display_name": "Foo", "last_seen": datetime.now(timezone.utc).isoformat()}}}
        save_candidates(state, path)
        loaded = load_candidates(path)
        assert "foo" in loaded["candidates"]
        assert loaded["last_update"] is not None

    def test_prune_stale_entries(self, tmp_path):
        path = tmp_path / "c.json"
        old_date = (datetime.now(timezone.utc) - timedelta(days=CANDIDATE_MAX_AGE_DAYS + 5)).isoformat()
        fresh_date = datetime.now(timezone.utc).isoformat()
        state = {
            "candidates": {
                "stale": {"display_name": "Stale", "last_seen": old_date, "total_matches": 1},
                "fresh": {"display_name": "Fresh", "last_seen": fresh_date, "total_matches": 1},
            }
        }
        save_candidates(state, path)
        loaded = load_candidates(path)
        assert "fresh" in loaded["candidates"]
        assert "stale" not in loaded["candidates"]


# ---------------------------------------------------------------------------
# update_candidate
# ---------------------------------------------------------------------------

class TestUpdateCandidate:
    def test_new_candidate_created(self):
        state = {"candidates": {}}
        now = "2026-04-17T12:00:00+00:00"
        update_candidate(state, _board_job(), score=12, now_iso=now)
        assert "foo labs" in state["candidates"]
        entry = state["candidates"]["foo labs"]
        assert entry["display_name"] == "Foo Labs"
        assert entry["total_matches"] == 1
        assert entry["total_score"] == 12
        assert entry["first_seen"] == now
        assert entry["last_seen"] == now

    def test_existing_candidate_accumulates(self):
        state = {"candidates": {}}
        first_time = "2026-04-01T12:00:00+00:00"
        second_time = "2026-04-17T12:00:00+00:00"
        update_candidate(state, _board_job(title="Role A"), 10, first_time)
        update_candidate(state, _board_job(title="Role B"), 15, second_time)
        entry = state["candidates"]["foo labs"]
        assert entry["total_matches"] == 2
        assert entry["total_score"] == 25
        assert entry["first_seen"] == first_time  # preserved
        assert entry["last_seen"] == second_time  # updated
        assert "Role A" in entry["sample_titles"]
        assert "Role B" in entry["sample_titles"]

    def test_negative_scores_clamped(self):
        """Negative scores should not pull cumulative score down."""
        state = {"candidates": {}}
        update_candidate(state, _board_job(), -5, "2026-04-17T00:00:00+00:00")
        assert state["candidates"]["foo labs"]["total_score"] == 0

    def test_detects_ats_from_url(self):
        state = {"candidates": {}}
        job = _board_job(url="https://boards.greenhouse.io/anthropic/jobs/12345")
        update_candidate(state, job, 10, "2026-04-17T00:00:00+00:00")
        entry = state["candidates"]["foo labs"]
        assert entry["discovered_ats"] == "greenhouse"
        assert entry["discovered_slug"] == "anthropic"

    def test_detects_ats_from_raw_job_url_direct(self):
        state = {"candidates": {}}
        job = _board_job(
            url="https://linkedin.com/jobs/view/123",  # LinkedIn URL, no ATS
            raw={"job_url_direct": "https://jobs.lever.co/figma/abc"},
        )
        update_candidate(state, job, 10, "2026-04-17T00:00:00+00:00")
        entry = state["candidates"]["foo labs"]
        assert entry["discovered_ats"] == "lever"
        assert entry["discovered_slug"] == "figma"

    def test_ats_not_detected_for_easy_apply(self):
        state = {"candidates": {}}
        job = _board_job(url="https://linkedin.com/jobs/view/123", raw={})
        update_candidate(state, job, 10, "2026-04-17T00:00:00+00:00")
        entry = state["candidates"]["foo labs"]
        assert entry["discovered_ats"] is None

    def test_ats_detection_preserved_on_later_update(self):
        """Once an ATS is detected, a later update without the URL signal shouldn't unset it."""
        state = {"candidates": {}}
        job1 = _board_job(url="https://jobs.lever.co/figma/abc")
        update_candidate(state, job1, 10, "2026-04-01T00:00:00+00:00")
        job2 = _board_job(url="https://linkedin.com/jobs/view/999")
        update_candidate(state, job2, 10, "2026-04-17T00:00:00+00:00")
        entry = state["candidates"]["foo labs"]
        assert entry["discovered_ats"] == "lever"

    def test_sample_titles_capped(self):
        state = {"candidates": {}}
        for i in range(MAX_SAMPLE_TITLES + 3):
            update_candidate(
                state,
                _board_job(title=f"Role {i}"),
                5,
                "2026-04-17T00:00:00+00:00",
            )
        entry = state["candidates"]["foo labs"]
        assert len(entry["sample_titles"]) == MAX_SAMPLE_TITLES

    def test_empty_company_ignored(self):
        state = {"candidates": {}}
        update_candidate(state, _board_job(company=""), 10, "2026-04-17T00:00:00+00:00")
        assert state["candidates"] == {}


# ---------------------------------------------------------------------------
# promotable_candidates
# ---------------------------------------------------------------------------

class TestPromotable:
    def test_score_threshold(self):
        state = {"candidates": {
            "hit": {"display_name": "Hit", "total_matches": 1, "total_score": CANDIDATE_SCORE_THRESHOLD},
            "miss": {"display_name": "Miss", "total_matches": 1, "total_score": CANDIDATE_SCORE_THRESHOLD - 1},
        }}
        names = [c["display_name"] for c in promotable_candidates(state)]
        assert "Hit" in names
        assert "Miss" not in names

    def test_match_threshold(self):
        state = {"candidates": {
            "hit": {"display_name": "Hit", "total_matches": CANDIDATE_MATCH_THRESHOLD, "total_score": 0},
            "miss": {"display_name": "Miss", "total_matches": CANDIDATE_MATCH_THRESHOLD - 1, "total_score": 0},
        }}
        names = [c["display_name"] for c in promotable_candidates(state)]
        assert "Hit" in names
        assert "Miss" not in names

    def test_sorted_by_score_desc(self):
        state = {"candidates": {
            "low": {"display_name": "Low", "total_matches": 5, "total_score": 15},
            "high": {"display_name": "High", "total_matches": 5, "total_score": 99},
            "mid": {"display_name": "Mid", "total_matches": 5, "total_score": 40},
        }}
        names = [c["display_name"] for c in promotable_candidates(state)]
        assert names == ["High", "Mid", "Low"]

    def test_empty_state(self):
        assert promotable_candidates({"candidates": {}}) == []

"""Unit tests for filter and scoring functions in main.py."""
from datetime import datetime, timezone, timedelta

import pytest

from main import match_title, match_location, is_anti_target, score_job, classify_work_mode
from scrapers.base import Job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job(**overrides) -> Job:
    """Build a Job with sensible defaults, overridden by kwargs."""
    defaults = dict(
        id="test:co:1",
        company="TestCo",
        company_slug="testco",
        company_tier="tier_1_saas",
        title="Senior Security Engineer",
        location="Remote - US",
        remote=True,
        url="https://example.com/jobs/1",
        posted_at=datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc),
        description_text="We need someone to build secure systems.",
        source="ats",
    )
    defaults.update(overrides)
    return Job(**defaults)


# ---------------------------------------------------------------------------
# match_title
# ---------------------------------------------------------------------------

class TestMatchTitle:
    def test_tier_and_domain_keywords_match(self):
        rules = {
            "tier_keywords_in_title": ["senior", "staff", "principal"],
            "domain_keywords_in_title": ["security", "threat"],
        }
        assert match_title(_job(title="Senior Security Engineer"), rules) is True

    def test_exclusion_blocks_match(self):
        rules = {
            "tier_keywords_in_title": ["senior"],
            "domain_keywords_in_title": ["security"],
            "title_exclusions": ["intern"],
        }
        assert match_title(_job(title="Senior Security Intern"), rules) is False

    def test_empty_keywords_pass_through(self):
        """When no tier/domain keywords are configured, any title passes."""
        rules = {"tier_keywords_in_title": [], "domain_keywords_in_title": []}
        assert match_title(_job(title="Janitor"), rules) is True

    def test_empty_title_returns_false(self):
        rules = {"tier_keywords_in_title": [], "domain_keywords_in_title": []}
        assert match_title(_job(title=""), rules) is False

    def test_whitespace_only_title(self):
        rules = {"tier_keywords_in_title": [], "domain_keywords_in_title": []}
        # " ".lower() is " " which is truthy, but has no keywords to match
        # With empty keyword lists it should pass through (truthy after lower())
        assert match_title(_job(title=" "), rules) is True

    def test_tier_keyword_miss(self):
        rules = {
            "tier_keywords_in_title": ["director", "vp"],
            "domain_keywords_in_title": ["security"],
        }
        assert match_title(_job(title="Senior Security Engineer"), rules) is False

    def test_domain_keyword_miss(self):
        rules = {
            "tier_keywords_in_title": ["senior"],
            "domain_keywords_in_title": ["marketing"],
        }
        assert match_title(_job(title="Senior Security Engineer"), rules) is False


# ---------------------------------------------------------------------------
# match_location
# ---------------------------------------------------------------------------

class TestMatchLocation:
    def test_require_remote_false_passes_everything(self):
        rules = {"require_remote": False}
        assert match_location(_job(remote=False, location="On-site NYC"), rules) is True

    def test_remote_true_passes(self):
        rules = {"require_remote": True}
        assert match_location(_job(remote=True, location="San Francisco"), rules) is True

    def test_remote_in_location_passes(self):
        rules = {"require_remote": True}
        assert match_location(_job(remote=False, location="Remote - US"), rules) is True

    def test_non_remote_fails(self):
        rules = {"require_remote": True}
        assert match_location(_job(remote=False, location="On-site NYC"), rules) is False

    def test_regex_pattern_match(self):
        rules = {
            "require_remote": True,
            "required_location_regex": r"\bus\b|united states",
        }
        assert match_location(_job(remote=True, location="Remote - US"), rules) is True

    def test_regex_pattern_no_match(self):
        rules = {
            "require_remote": True,
            "required_location_regex": r"\bus\b|united states",
        }
        assert match_location(_job(remote=True, location="Remote - Germany"), rules) is False

    def test_invalid_regex_doesnt_crash(self):
        rules = {
            "require_remote": True,
            "required_location_regex": r"[invalid",
        }
        # Invalid regex should not crash; falls through to True
        assert match_location(_job(remote=True, location="Remote - US"), rules) is True

    def test_remote_none_location_none(self):
        rules = {"require_remote": True}
        assert match_location(_job(remote=None, location=None), rules) is False


# ---------------------------------------------------------------------------
# is_anti_target
# ---------------------------------------------------------------------------

class TestIsAntiTarget:
    def test_title_contains_any_match(self):
        filters = {"anti_target_patterns": {
            "govt": {
                "description": "Government roles",
                "title_contains_any": ["clearance", "government"],
            }
        }}
        matched, reason = is_anti_target(_job(title="Senior Engineer (Clearance Required)"), filters)
        assert matched is True
        assert reason == "Government roles"

    def test_description_contains_any(self):
        filters = {"anti_target_patterns": {
            "defense": {
                "description": "Defense contractor",
                "description_contains_any": ["top secret", "dod"],
            }
        }}
        matched, _ = is_anti_target(
            _job(description_text="Must hold Top Secret clearance"), filters
        )
        assert matched is True

    def test_description_contains_all(self):
        filters = {"anti_target_patterns": {
            "combo": {
                "description": "Requires both",
                "description_contains_all": ["java", "cobol"],
            }
        }}
        # Only one keyword present: should NOT match
        matched, _ = is_anti_target(_job(description_text="Expert in Java and Python"), filters)
        assert matched is False

        # Both present: should match
        matched, reason = is_anti_target(
            _job(description_text="Migrate from COBOL to Java"), filters
        )
        assert matched is True
        assert reason == "Requires both"

    def test_negates_if_location_also_contains(self):
        filters = {"anti_target_patterns": {
            "onsite_only": {
                "description": "On-site in bad location",
                "location_contains_any": ["india"],
                "negates_if_location_also_contains": ["remote"],
            }
        }}
        # Location has "india" but also "remote" => negated, not anti-target
        matched, _ = is_anti_target(_job(location="India - Remote"), filters)
        assert matched is False

        # Location has "india" without "remote" => anti-target
        matched, reason = is_anti_target(_job(location="India - On-site"), filters)
        assert matched is True
        assert reason == "On-site in bad location"

    def test_empty_pattern_dict_returns_false(self):
        filters = {"anti_target_patterns": {}}
        matched, reason = is_anti_target(_job(), filters)
        assert matched is False
        assert reason == ""

    def test_none_description(self):
        filters = {"anti_target_patterns": {
            "lang": {
                "description": "Bad stack",
                "description_contains_any": ["cobol"],
            }
        }}
        matched, _ = is_anti_target(_job(description_text=None), filters)
        assert matched is False

    def test_pattern_with_no_conditions_specified(self):
        """A pattern entry with no condition keys should not fire."""
        filters = {"anti_target_patterns": {
            "empty": {"description": "Ghost pattern"}
        }}
        matched, _ = is_anti_target(_job(), filters)
        assert matched is False


# ---------------------------------------------------------------------------
# score_job
# ---------------------------------------------------------------------------

class TestScoreJob:
    def test_title_bonus(self):
        scoring = {"title_bonus": {"security": 10, "staff": 5}}
        job = _job(title="Staff Security Engineer")
        assert score_job(job, scoring) >= 15

    def test_tier_bonus(self):
        scoring = {"tier_bonus": {"tier_1_saas": 8}}
        assert score_job(_job(company_tier="tier_1_saas", posted_at=None), scoring) == 8

    def test_freshness_bonus_under_3_days(self):
        now = datetime.now(timezone.utc)
        scoring: dict = {}
        job = _job(posted_at=now - timedelta(hours=12))
        # score_job adds +3 for posts under 3 days old, no other bonuses with empty scoring
        assert score_job(job, scoring) == 3

    def test_freshness_bonus_under_7_days(self):
        now = datetime.now(timezone.utc)
        scoring: dict = {}
        job = _job(posted_at=now - timedelta(days=5))
        # score_job adds +1 for posts between 3 and 7 days old
        assert score_job(job, scoring) == 1

    def test_freshness_bonus_older(self):
        scoring: dict = {}
        job = _job(posted_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        assert score_job(job, scoring) == 0

    def test_naive_datetime_handling(self):
        """Naive datetimes should be treated as UTC and not crash."""
        now = datetime.now(timezone.utc)
        naive = now.replace(tzinfo=None) - timedelta(hours=1)
        scoring: dict = {}
        job = _job(posted_at=naive)
        assert score_job(job, scoring) == 3

    def test_posted_at_none(self):
        scoring = {"title_bonus": {"engineer": 3}}
        job = _job(posted_at=None, title="Engineer")
        assert score_job(job, scoring) == 3

    def test_empty_scoring_dict(self):
        assert score_job(_job(), {}) >= 0

    def test_location_mode_bonus_remote(self):
        scoring = {"location_mode_bonus": {"remote": 5, "hybrid": 2, "on_site": 0}}
        job = _job(remote=True, location="Remote, USA", posted_at=None)
        assert score_job(job, scoring) == 5

    def test_location_mode_bonus_hybrid(self):
        scoring = {"location_mode_bonus": {"remote": 5, "hybrid": 2, "on_site": 0}}
        job = _job(remote=False, location="Springfield, IL (Hybrid)", posted_at=None)
        assert score_job(job, scoring) == 2

    def test_location_mode_bonus_on_site(self):
        scoring = {"location_mode_bonus": {"remote": 5, "hybrid": 2, "on_site": 0}}
        job = _job(remote=False, location="Springfield, IL", posted_at=None)
        assert score_job(job, scoring) == 0

    def test_location_mode_bonus_section_absent_is_neutral(self):
        """Scoring configs without location_mode_bonus should behave as before."""
        scoring = {"title_bonus": {"engineer": 3}}
        job = _job(remote=True, title="Senior Engineer", posted_at=None)
        # 3 (title bonus for 'engineer'), no location bonus since section absent
        assert score_job(job, scoring) == 3


class TestClassifyWorkMode:
    def _j(self, remote, location):
        return Job(
            id="x:1", company="C", company_slug="c", company_tier="tier_1_saas",
            title="Engineer", location=location, remote=remote, url="u",
            posted_at=None, description_text="", source="ats",
        )

    def test_explicit_remote_flag(self):
        assert classify_work_mode(self._j(True, "")) == "remote"

    def test_remote_in_location(self):
        assert classify_work_mode(self._j(None, "Remote, USA")) == "remote"

    def test_hybrid_beats_remote_when_both_present(self):
        """Cautious classification: 'Remote - Hybrid' counts as hybrid."""
        assert classify_work_mode(self._j(True, "Remote - Hybrid")) == "hybrid"

    def test_hybrid_in_location(self):
        assert classify_work_mode(self._j(False, "Springfield, IL (Hybrid)")) == "hybrid"

    def test_on_site(self):
        assert classify_work_mode(self._j(False, "Springfield, IL")) == "on_site"

    def test_empty_location_not_remote_flag(self):
        assert classify_work_mode(self._j(False, "")) == "on_site"

    def test_empty_location_none_remote(self):
        assert classify_work_mode(self._j(None, None)) == "on_site"

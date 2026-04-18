"""Unit tests for state management and digest writing in main.py."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from main import load_state, save_state, write_digest, load_declined_urls
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
        description_text="Build secure systems.",
        source="ats",
    )
    defaults.update(overrides)
    return Job(**defaults)


# ---------------------------------------------------------------------------
# load_state
# ---------------------------------------------------------------------------

class TestLoadState:
    def test_missing_file_returns_empty(self, tmp_path):
        state = load_state(tmp_path / "nonexistent.json")
        assert state == {"seen_ids": {}, "last_run": None}

    def test_corrupt_json_returns_empty(self, tmp_path):
        bad_file = tmp_path / "state.json"
        bad_file.write_text("{this is not valid json!!!")
        state = load_state(bad_file)
        assert state == {"seen_ids": {}, "last_run": None}

    def test_valid_roundtrip(self, tmp_path):
        state_file = tmp_path / "state.json"
        original = {
            "seen_ids": {"gh:acme:123": "2026-04-10T00:00:00+00:00"},
            "last_run": "2026-04-10T12:00:00+00:00",
        }
        state_file.write_text(json.dumps(original))
        loaded = load_state(state_file)
        assert loaded["seen_ids"] == original["seen_ids"]
        assert loaded["last_run"] == original["last_run"]


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------

class TestSaveState:
    def test_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "state.json"
        state = {"seen_ids": {"id1": "2026-04-10T00:00:00+00:00"}}
        save_state(state, nested)
        assert nested.exists()
        data = json.loads(nested.read_text())
        assert "id1" in data["seen_ids"]

    def test_writes_valid_json_with_last_run(self, tmp_path):
        state_file = tmp_path / "state.json"
        state = {"seen_ids": {"x:y:1": "2026-04-10T00:00:00+00:00"}}
        save_state(state, state_file)
        data = json.loads(state_file.read_text())
        assert "last_run" in data
        # last_run should be a valid ISO timestamp
        parsed = datetime.fromisoformat(data["last_run"])
        assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# write_digest
# ---------------------------------------------------------------------------

class TestWriteDigest:
    def test_basic_output_with_matches_and_anti_targets(self, tmp_path):
        job_match = _job(id="m1", title="Staff Security Engineer", company="Acme")
        job_anti = _job(id="a1", title="Junior COBOL Dev", company="LegacyCorp")
        results = [
            (15, job_match, True, ""),
            (0, job_anti, False, "Bad stack"),
        ]
        stats = {
            "companies_scanned": 5,
            "total_fetched": 100,
            "board_fetched": 0,
            "new_jobs": 10,
        }
        out = write_digest(results, stats, tmp_path)
        assert out.exists()
        content = out.read_text()
        assert "Staff Security Engineer" in content
        assert "Acme" in content
        assert "Bad stack" in content
        assert "Matches (post-filter):** 1" in content
        assert "Skipped (anti-target):** 1" in content

    def test_empty_results(self, tmp_path):
        stats = {
            "companies_scanned": 3,
            "total_fetched": 50,
            "new_jobs": 0,
        }
        out = write_digest([], stats, tmp_path)
        content = out.read_text()
        assert "No new matching jobs today" in content
        assert "Matches (post-filter):** 0" in content

    def test_board_source_tag_appears(self, tmp_path):
        job = _job(source="board", title="Cloud Security Analyst", company="BoardCo")
        results = [(10, job, True, "")]
        stats = {
            "companies_scanned": 2,
            "total_fetched": 20,
            "board_fetched": 5,
            "new_jobs": 5,
        }
        out = write_digest(results, stats, tmp_path)
        content = out.read_text()
        assert "_(via board)_" in content
        assert "Board jobs (JobSpy):** 5" in content

    def test_declined_filtered_appears_when_nonzero(self, tmp_path):
        """When discovery skipped previously-declined URLs, the digest header reports it."""
        stats = {
            "companies_scanned": 5,
            "total_fetched": 100,
            "new_jobs": 10,
            "declined_filtered": 7,
        }
        out = write_digest([], stats, tmp_path)
        content = out.read_text()
        assert "Skipped (previously declined in tracker):** 7" in content

    def test_declined_filtered_omitted_when_zero(self, tmp_path):
        """When no declined URLs were filtered, the header line is omitted (not '0')."""
        stats = {
            "companies_scanned": 5,
            "total_fetched": 100,
            "new_jobs": 10,
            "declined_filtered": 0,
        }
        out = write_digest([], stats, tmp_path)
        content = out.read_text()
        assert "previously declined" not in content


# ---------------------------------------------------------------------------
# load_declined_urls - URL normalization
# ---------------------------------------------------------------------------

class TestLoadDeclinedUrlsNormalization:
    """URLs in applications.md often have tracking params from the session the
    user was browsing in. A later scan picks up the same job with different
    tracking - if we compared raw URLs the skip list would miss. These tests
    confirm the stored set is normalized so same-posting comparisons match.
    """

    def _tracker(self, tmp_path, rows: list[tuple[str, str]]):
        """Write a minimal tracker with (status, url) rows."""
        lines = [
            "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for status, url in rows:
            lines.append(f"| 2026-04-10 | Foo | Sr Sec Eng | {status} | 2026-04-12 | 10 |  | {url} | |")
        path = tmp_path / "applications.md"
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_strips_tracking_from_stored_urls(self, tmp_path):
        tracker = self._tracker(tmp_path, [
            ("rejected", "<https://www.linkedin.com/jobs/view/4401234567?trk=abc&refId=xyz>"),
        ])
        declined = load_declined_urls(tracker)
        # The stored entry should have no tracking params
        assert "https://www.linkedin.com/jobs/view/4401234567" in declined
        assert not any("trk=" in u for u in declined)

    def test_markdown_link_syntax_handled(self, tmp_path):
        tracker = self._tracker(tmp_path, [
            ("rejected", "[LinkedIn](https://www.linkedin.com/jobs/view/42?utm_source=email)"),
        ])
        declined = load_declined_urls(tracker)
        assert "https://www.linkedin.com/jobs/view/42" in declined

    def test_non_terminal_status_not_included(self, tmp_path):
        tracker = self._tracker(tmp_path, [
            ("queued", "<https://www.linkedin.com/jobs/view/1>"),
            ("applied", "<https://www.linkedin.com/jobs/view/2>"),
            ("rejected", "<https://www.linkedin.com/jobs/view/3>"),
        ])
        declined = load_declined_urls(tracker)
        assert "https://www.linkedin.com/jobs/view/3" in declined
        assert "https://www.linkedin.com/jobs/view/1" not in declined
        assert "https://www.linkedin.com/jobs/view/2" not in declined

    def test_different_sessions_same_posting_match(self, tmp_path):
        """A URL stored with session-A tracking should match a scan that sees session-B tracking."""
        from url_utils import normalize_url
        tracker = self._tracker(tmp_path, [
            ("rejected", "<https://www.linkedin.com/jobs/view/1001?trk=session_a&refId=x>"),
        ])
        declined = load_declined_urls(tracker)
        # Simulate the scan seeing the same job with different tracking
        scan_url = "https://www.linkedin.com/jobs/view/1001?trk=session_b&utm_source=newsletter"
        assert normalize_url(scan_url) in declined


# ---------------------------------------------------------------------------
# load_repeat_decline_pairs - same Company+Role declined N+ times
# ---------------------------------------------------------------------------

class TestLoadRepeatDeclinePairs:
    """Auto-derive (company, title) skip-pairs from tracker rows where the same
    posting pattern has been declined REPEAT_DECLINE_THRESHOLD or more times.
    Motivating case: Fivetran Senior Sales Engineer declined 4 times in 5 days
    with identical SE-function-mismatch reasoning, each time with a fresh
    gh_jid URL that defeats URL-based skipping.
    """

    def _tracker(self, tmp_path, rows: list[tuple[str, str, str]]):
        """Write a tracker with (status, company, role) rows."""
        lines = [
            "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for status, company, role in rows:
            lines.append(
                f"| 2026-04-10 | {company} | {role} | {status} | 2026-04-10 | 5 |  | "
                f"<https://x.com/{abs(hash((company, role, status))) % 100000}> | |"
            )
        path = tmp_path / "applications.md"
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_returns_empty_when_no_tracker(self, tmp_path):
        from main import load_repeat_decline_pairs
        assert load_repeat_decline_pairs(None) == set()
        assert load_repeat_decline_pairs(tmp_path / "missing.md") == set()

    def test_below_threshold_not_returned(self, tmp_path):
        from main import load_repeat_decline_pairs
        tracker = self._tracker(tmp_path, [
            ("declined_anti_target", "Fivetran", "Senior Sales Engineer, Enterprise"),
            ("declined_anti_target", "Fivetran", "Senior Sales Engineer, Enterprise"),
        ])
        assert load_repeat_decline_pairs(tracker, threshold=3) == set()

    def test_at_or_above_threshold_returned(self, tmp_path):
        from main import load_repeat_decline_pairs
        from dedup import normalize_company
        from main import normalize_title
        tracker = self._tracker(tmp_path, [
            ("declined_anti_target", "Fivetran", "Senior Sales Engineer, Enterprise"),
            ("declined_anti_target", "Fivetran", "Senior Sales Engineer, Enterprise"),
            ("declined_anti_target", "Fivetran", "Senior Sales Engineer, Enterprise"),
        ])
        result = load_repeat_decline_pairs(tracker, threshold=3)
        assert (normalize_company("Fivetran"), normalize_title("Senior Sales Engineer, Enterprise")) in result

    def test_only_declined_anti_target_status_counted(self, tmp_path):
        """Other terminal statuses (rejected, withdrew) should NOT contribute to
        the repeat-decline counter - they reflect distinct workflows (post-submit
        rejection vs pre-submit anti-target refusal). Only the latter is a
        signal that the JD pattern is dead weight at discovery time."""
        from main import load_repeat_decline_pairs
        tracker = self._tracker(tmp_path, [
            ("declined_anti_target", "Fivetran", "Senior Sales Engineer, Enterprise"),
            ("rejected", "Fivetran", "Senior Sales Engineer, Enterprise"),
            ("rejected", "Fivetran", "Senior Sales Engineer, Enterprise"),
            ("rejected", "Fivetran", "Senior Sales Engineer, Enterprise"),
        ])
        # Only 1 declined_anti_target row, threshold is 3 - should be empty
        assert load_repeat_decline_pairs(tracker, threshold=3) == set()

    def test_company_normalized_for_match(self, tmp_path):
        """Tracker may have 'Fivetran' but discovery sees 'Fivetran, Inc.' - the
        normalize_company() helper should fold these into the same key."""
        from main import load_repeat_decline_pairs, normalize_title
        from dedup import normalize_company
        tracker = self._tracker(tmp_path, [
            ("declined_anti_target", "Fivetran", "Senior Sales Engineer, Enterprise"),
            ("declined_anti_target", "Fivetran, Inc.", "Senior Sales Engineer, Enterprise"),
            ("declined_anti_target", "fivetran", "Senior Sales Engineer, Enterprise"),
        ])
        result = load_repeat_decline_pairs(tracker, threshold=3)
        # All three should normalize to the same company key
        expected_key = (normalize_company("Fivetran"), normalize_title("Senior Sales Engineer, Enterprise"))
        assert expected_key in result

    def test_different_roles_at_same_company_independent(self, tmp_path):
        """Three different role declines at the same company should NOT trigger
        the threshold for any individual role."""
        from main import load_repeat_decline_pairs
        tracker = self._tracker(tmp_path, [
            ("declined_anti_target", "Tenable", "Sales Security Engineer"),
            ("declined_anti_target", "Tenable", "Sales Engineer Federal"),
            ("declined_anti_target", "Tenable", "Director of Detection Engineering"),
        ])
        assert load_repeat_decline_pairs(tracker, threshold=3) == set()


# ---------------------------------------------------------------------------
# persist_board_descriptions - JD body cache for /apply
# ---------------------------------------------------------------------------

class TestPersistBoardDescriptions:
    """JobSpy fetches descriptions during /discover but discards them. /apply
    has to re-WebFetch each LinkedIn URL, hitting 403s frequently. Persisting
    the JD body keyed by normalized URL gives /apply a local cache to read
    from, eliminating the redundant fetch round-trip.
    """

    def test_writes_normalized_url_keyed_descriptions(self, tmp_path):
        from main import persist_board_descriptions
        cache = tmp_path / "board-descriptions.json"
        jobs = [
            _job(
                id="jobspy:linkedin:abc",
                company="TestSec",
                title="Senior Offensive Security Consultant",
                url="https://www.linkedin.com/jobs/view/4402721640?trk=foo",
                description_text="Manual web app pentest, 6-8 yrs experience.",
                source="board",
            ),
        ]
        persist_board_descriptions(jobs, cache)
        data = json.loads(cache.read_text())
        # URL key should be normalized (no trk= tracking param)
        assert "https://www.linkedin.com/jobs/view/4402721640" in data
        entry = data["https://www.linkedin.com/jobs/view/4402721640"]
        assert entry["company"] == "TestSec"
        assert entry["description"] == "Manual web app pentest, 6-8 yrs experience."
        assert "saved_at" in entry

    def test_skips_jobs_without_description(self, tmp_path):
        from main import persist_board_descriptions
        cache = tmp_path / "board-descriptions.json"
        jobs = [
            _job(url="https://example.com/1", description_text="", source="board"),
            _job(url="https://example.com/2", description_text="real content", source="board"),
        ]
        persist_board_descriptions(jobs, cache)
        data = json.loads(cache.read_text())
        assert len(data) == 1
        assert "https://example.com/2" in data

    def test_merges_with_existing_cache(self, tmp_path):
        """A second discovery run should add new entries without clobbering
        prior ones (subject to TTL pruning)."""
        from main import persist_board_descriptions
        cache = tmp_path / "board-descriptions.json"
        first_run = [_job(url="https://x.com/1", description_text="first", source="board")]
        persist_board_descriptions(first_run, cache)
        second_run = [_job(url="https://x.com/2", description_text="second", source="board")]
        persist_board_descriptions(second_run, cache)
        data = json.loads(cache.read_text())
        assert "https://x.com/1" in data
        assert "https://x.com/2" in data

    def test_prunes_stale_entries(self, tmp_path):
        """Entries older than SEEN_ID_MAX_AGE_DAYS get dropped on next write."""
        from main import persist_board_descriptions, SEEN_ID_MAX_AGE_DAYS
        cache = tmp_path / "board-descriptions.json"
        # Pre-seed with a stale entry
        stale_iso = (datetime.now(timezone.utc) - timedelta(days=SEEN_ID_MAX_AGE_DAYS + 5)).isoformat()
        cache.write_text(json.dumps({
            "https://stale.example.com/old": {
                "company": "OldCo", "title": "T", "location": "",
                "description": "stale", "posted_at": None, "saved_at": stale_iso,
            }
        }))
        # Trigger a write with a fresh entry
        persist_board_descriptions(
            [_job(url="https://fresh.example.com/new", description_text="fresh", source="board")],
            cache,
        )
        data = json.loads(cache.read_text())
        assert "https://stale.example.com/old" not in data
        assert "https://fresh.example.com/new" in data

    def test_corrupt_cache_starts_fresh(self, tmp_path):
        """A corrupt JSON file shouldn't crash discovery; just start fresh."""
        from main import persist_board_descriptions
        cache = tmp_path / "board-descriptions.json"
        cache.write_text("not valid json {{{")
        persist_board_descriptions(
            [_job(url="https://x.com/1", description_text="ok", source="board")],
            cache,
        )
        data = json.loads(cache.read_text())
        assert "https://x.com/1" in data

"""Unit tests for state management and digest writing in main.py."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from main import load_state, save_state, write_digest
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

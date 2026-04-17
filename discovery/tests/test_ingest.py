"""Unit tests for manual ingest mode."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from main import (
    _build_manual_jobs,
    explain_match_rejection,
    run_ingest,
    write_ingest_digest,
)
from scrapers.base import Job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _posting(**overrides) -> dict:
    """Sample valid posting dict."""
    d = {
        "url": "https://www.linkedin.com/jobs/view/12345",
        "title": "Senior Security Engineer",
        "company": "Foo Labs",
        "location": "Remote, USA",
        "description": "Build secure systems. 5+ years required.",
        "posted_at": "2026-04-15",
    }
    d.update(overrides)
    return d


def _write_postings(tmp_path: Path, postings: list) -> Path:
    path = tmp_path / "postings.json"
    path.write_text(json.dumps(postings))
    return path


def _write_yaml_configs(config_dir: Path,
                        tier_kw=None, domain_kw=None, exclusions=None,
                        require_remote=False, anti_targets=None, scoring=None):
    """Materialize the three required yaml configs in config_dir."""
    import yaml
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "companies.yaml").write_text(yaml.safe_dump({
        "tier_1_saas": [{"name": "Datadog", "ats": "greenhouse", "slug": "datadog"}]
    }))
    (config_dir / "keywords.yaml").write_text(yaml.safe_dump({
        "match_rules": {
            "tier_keywords_in_title": tier_kw or ["senior", "staff", "principal"],
            "domain_keywords_in_title": domain_kw or ["security", "vulnerability"],
            "title_exclusions": exclusions or ["intern"],
            "require_remote": require_remote,
        },
        "scoring": scoring or {"title_bonus": {"security": 5}},
    }))
    (config_dir / "filters.yaml").write_text(yaml.safe_dump({
        "anti_target_patterns": anti_targets or {},
    }))


# ---------------------------------------------------------------------------
# _build_manual_jobs
# ---------------------------------------------------------------------------

class TestBuildManualJobs:
    def test_valid_posting_builds_job(self):
        jobs, skipped = _build_manual_jobs([_posting()])
        assert len(jobs) == 1
        assert skipped == []
        j = jobs[0]
        assert j.source == "manual"
        assert j.company == "Foo Labs"
        assert j.title == "Senior Security Engineer"
        assert j.url == "https://www.linkedin.com/jobs/view/12345"
        assert j.id.startswith("manual:")
        assert j.company_tier == "manual_ingest"
        assert j.posted_at is not None
        assert j.posted_at.tzinfo is not None

    def test_missing_url_skipped(self):
        jobs, skipped = _build_manual_jobs([_posting(url="")])
        assert jobs == []
        assert len(skipped) == 1
        assert "url" in skipped[0]["reason"]

    def test_missing_title_skipped(self):
        jobs, skipped = _build_manual_jobs([_posting(title="")])
        assert jobs == []
        assert len(skipped) == 1
        assert "title" in skipped[0]["reason"]

    def test_missing_company_skipped(self):
        jobs, skipped = _build_manual_jobs([_posting(company="")])
        assert jobs == []
        assert len(skipped) == 1
        assert "company" in skipped[0]["reason"]

    def test_non_dict_entry_skipped(self):
        jobs, skipped = _build_manual_jobs(["not a dict", 42, None])
        assert jobs == []
        assert len(skipped) == 3

    def test_location_remote_detected_from_string(self):
        jobs, _ = _build_manual_jobs([_posting(location="Remote, USA")])
        assert jobs[0].remote is True

    def test_explicit_remote_flag_respected(self):
        jobs, _ = _build_manual_jobs([_posting(location="NYC", remote=False)])
        assert jobs[0].remote is False

    def test_bad_date_ignored_not_skipped(self):
        """Malformed posted_at shouldn't kill the whole entry."""
        jobs, skipped = _build_manual_jobs([_posting(posted_at="not a date")])
        assert len(jobs) == 1
        assert skipped == []
        assert jobs[0].posted_at is None


# ---------------------------------------------------------------------------
# explain_match_rejection
# ---------------------------------------------------------------------------

class TestExplainRejection:
    def _j(self, **over):
        defaults = dict(
            id="m:1", company="C", company_slug="c", company_tier="manual_ingest",
            title="Senior Security Engineer", location="Remote, USA",
            remote=True, url="u", posted_at=None, description_text="",
            source="manual",
        )
        defaults.update(over)
        return Job(**defaults)

    def test_none_when_all_pass(self):
        rules = {"tier_keywords_in_title": ["senior"], "domain_keywords_in_title": ["security"]}
        assert explain_match_rejection(self._j(), rules) is None

    def test_empty_title(self):
        rules = {}
        assert explain_match_rejection(self._j(title=""), rules) == "empty title"

    def test_missing_tier_keyword(self):
        rules = {"tier_keywords_in_title": ["director"], "domain_keywords_in_title": ["security"]}
        reason = explain_match_rejection(self._j(title="Senior Security Engineer"), rules)
        assert reason is not None
        assert "tier" in reason

    def test_missing_domain_keyword(self):
        rules = {"tier_keywords_in_title": ["senior"], "domain_keywords_in_title": ["marketing"]}
        reason = explain_match_rejection(self._j(title="Senior Security Engineer"), rules)
        assert reason is not None
        assert "domain" in reason

    def test_title_exclusion(self):
        rules = {
            "tier_keywords_in_title": ["senior"],
            "domain_keywords_in_title": ["security"],
            "title_exclusions": ["intern"],
        }
        reason = explain_match_rejection(self._j(title="Senior Security Intern"), rules)
        assert reason is not None
        assert "intern" in reason

    def test_not_remote_when_required(self):
        rules = {
            "tier_keywords_in_title": ["senior"],
            "domain_keywords_in_title": ["security"],
            "require_remote": True,
        }
        reason = explain_match_rejection(
            self._j(remote=False, location="NYC"), rules
        )
        assert reason is not None
        assert "remote" in reason

    def test_location_regex_miss(self):
        rules = {
            "tier_keywords_in_title": ["senior"],
            "domain_keywords_in_title": ["security"],
            "require_remote": True,
            "required_location_regex": r"united states|usa|\bus\b",
        }
        reason = explain_match_rejection(
            self._j(remote=True, location="Remote, Germany"), rules
        )
        assert reason is not None
        assert "location" in reason.lower()


# ---------------------------------------------------------------------------
# run_ingest end-to-end
# ---------------------------------------------------------------------------

class TestRunIngest:
    def test_matched_posting_lands_in_digest(self, tmp_path):
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_file = tmp_path / "state" / "seen-jobs.json"
        _write_yaml_configs(config_dir)

        postings_path = _write_postings(tmp_path, [_posting()])
        rc = run_ingest(postings_path, config_dir, state_file, digest_dir,
                        framework_config={}, dry_run=False)
        assert rc == 0
        files = list(digest_dir.glob("ingest-*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "Senior Security Engineer" in content
        assert "## Matches" in content
        assert "Foo Labs" in content

    def test_filtered_posting_shows_reason(self, tmp_path):
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_file = tmp_path / "state" / "seen-jobs.json"
        _write_yaml_configs(
            config_dir,
            tier_kw=["director"],  # we don't have one
            domain_kw=["security"],
        )
        postings_path = _write_postings(tmp_path, [_posting(title="Senior Security Engineer")])
        run_ingest(postings_path, config_dir, state_file, digest_dir, {}, False)
        content = list(digest_dir.glob("ingest-*.md"))[0].read_text()
        assert "## Filtered (no match)" in content
        assert "tier" in content.lower()

    def test_anti_target_flagged(self, tmp_path):
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_file = tmp_path / "state" / "seen-jobs.json"
        _write_yaml_configs(
            config_dir,
            anti_targets={
                "clearance_required": {
                    "description": "Active clearance required",
                    "description_contains_any": ["top secret"],
                }
            },
        )
        postings_path = _write_postings(tmp_path, [_posting(
            description="Must hold active Top Secret clearance."
        )])
        run_ingest(postings_path, config_dir, state_file, digest_dir, {}, False)
        content = list(digest_dir.glob("ingest-*.md"))[0].read_text()
        assert "## Anti-target hits" in content
        assert "Active clearance required" in content

    def test_previously_declined_warning(self, tmp_path):
        """URLs in the applications tracker with terminal status get flagged but still process."""
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_file = tmp_path / "state" / "seen-jobs.json"
        _write_yaml_configs(config_dir)
        # Write a minimal tracker with one rejected URL matching our posting
        tracker = tmp_path / "applications.md"
        tracker.write_text(
            "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 2026-04-01 | Foo Labs | Sr Sec Eng | rejected | 2026-04-02 | 10 |  | <https://www.linkedin.com/jobs/view/12345> | declined |\n"
        )
        postings_path = _write_postings(tmp_path, [_posting()])
        rc = run_ingest(postings_path, config_dir, state_file, digest_dir,
                        {"applications_file": str(tracker)}, False)
        assert rc == 0
        content = list(digest_dir.glob("ingest-*.md"))[0].read_text()
        assert "## Previously declined in tracker" in content
        assert "## Matches" in content  # still processed

    def test_candidate_tracking_for_unknown_company(self, tmp_path):
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_dir = tmp_path / "state"
        state_file = state_dir / "seen-jobs.json"
        _write_yaml_configs(config_dir)
        # URL that resolves to a known ATS (Greenhouse)
        postings_path = _write_postings(tmp_path, [_posting(
            url="https://boards.greenhouse.io/foolabs/jobs/12345"
        )])
        run_ingest(postings_path, config_dir, state_file, digest_dir, {}, False)
        candidate_file = state_dir / "candidate-companies.json"
        assert candidate_file.exists()
        state = json.loads(candidate_file.read_text())
        assert "foo labs" in state["candidates"]
        entry = state["candidates"]["foo labs"]
        assert entry["discovered_ats"] == "greenhouse"
        assert entry["discovered_slug"] == "foolabs"

    def test_does_not_write_seen_jobs(self, tmp_path):
        """Manual ingest must not populate seen-jobs.json - re-ingest should work."""
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_dir = tmp_path / "state"
        state_file = state_dir / "seen-jobs.json"
        _write_yaml_configs(config_dir)
        postings_path = _write_postings(tmp_path, [_posting()])
        run_ingest(postings_path, config_dir, state_file, digest_dir, {}, False)
        assert not state_file.exists()

    def test_dry_run_skips_candidate_update(self, tmp_path):
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_dir = tmp_path / "state"
        state_file = state_dir / "seen-jobs.json"
        _write_yaml_configs(config_dir)
        postings_path = _write_postings(tmp_path, [_posting(
            url="https://boards.greenhouse.io/foolabs/jobs/12345"
        )])
        run_ingest(postings_path, config_dir, state_file, digest_dir, {}, dry_run=True)
        candidate_file = state_dir / "candidate-companies.json"
        assert not candidate_file.exists()

    def test_malformed_postings_file(self, tmp_path):
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_file = tmp_path / "state" / "seen-jobs.json"
        _write_yaml_configs(config_dir)
        bad = tmp_path / "postings.json"
        bad.write_text("{not json at all")
        rc = run_ingest(bad, config_dir, state_file, digest_dir, {}, False)
        assert rc == 1

    def test_postings_not_an_array(self, tmp_path):
        config_dir = tmp_path / "config"
        digest_dir = tmp_path / "digests"
        state_file = tmp_path / "state" / "seen-jobs.json"
        _write_yaml_configs(config_dir)
        bad = tmp_path / "postings.json"
        bad.write_text(json.dumps({"not": "an array"}))
        rc = run_ingest(bad, config_dir, state_file, digest_dir, {}, False)
        assert rc == 1


# ---------------------------------------------------------------------------
# write_ingest_digest - additional coverage
# ---------------------------------------------------------------------------

class TestIngestDigestSections:
    def _match_job(self, **over):
        defaults = dict(
            id="m:1", company="Acme", company_slug="acme", company_tier="manual_ingest",
            title="Staff Security Engineer", location="Remote",
            remote=True, url="u", posted_at=None, description_text="",
            source="manual",
        )
        defaults.update(over)
        return Job(**defaults)

    def test_header_counts(self, tmp_path):
        job = self._match_job()
        results = [(15, job, True, "", "")]
        out = write_ingest_digest(results, [], [], [], tmp_path)
        content = out.read_text()
        assert "Postings submitted:** 1" in content
        assert "Matches (post-filter):** 1" in content

    def test_empty_sections_omitted(self, tmp_path):
        out = write_ingest_digest([], [], [], [], tmp_path)
        content = out.read_text()
        assert "## Matches" not in content
        assert "## Anti-target hits" not in content
        assert "## Filtered (no match)" not in content

    def test_timestamped_filename(self, tmp_path):
        out = write_ingest_digest([], [], [], [], tmp_path)
        assert out.name.startswith("ingest-")
        assert out.name.endswith(".md")
        # Format: ingest-YYYY-MM-DD-HHMMSS.md
        parts = out.stem.split("-")
        assert len(parts) == 5  # ingest, YYYY, MM, DD, HHMMSS

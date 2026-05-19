"""Unit tests for --backfill mode and its helpers."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from main import (
    parse_digest_matches,
    load_tracker_urls,
    load_tracker_identity_keys,
    run_backfill,
    write_backfill_digest,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_DIGEST = """# Job Discovery Digest - 2026-04-14

- **Companies scanned:** 50
- **Matches (post-filter):** 3

---

## tier_1_security_vendor

### Wiz - Staff Vulnerability Engineer
- **Location:** Remote, USA
- **Posted:** 2026-04-12
- **Score:** 25
- **Apply:** https://boards.greenhouse.io/wizinc/jobs/55555

### Tenable - Principal Solutions Engineer
- **Location:** Remote
- **Score:** 18
- **Apply:** https://boards.greenhouse.io/tenableinc/jobs/99999

## tier_1_saas

### Datadog - Staff Security Engineer _(via board)_
- **Location:** Remote, USA
- **Posted:** 2026-04-10
- **Score:** 12
- **Apply:** https://www.linkedin.com/jobs/view/4401234567

---

## Skipped (Anti-Target Lanes)

- 2x Hands-on AppSec role

---

## Candidate companies (not yet in companies.yaml)

### Foo Labs
- **Matches:** 2 | **Cumulative score:** 30
"""


# Historical digest format (em-dash separator in job headers, pre dash cleanup)
HISTORICAL_DIGEST = """# Job Discovery Digest — 2026-04-10

## tier_1_saas

### Legacy Corp — Senior Security Engineer
- **Location:** Remote, USA
- **Score:** 15
- **Apply:** https://boards.greenhouse.io/legacycorp/jobs/1
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def _tracker(tmp_path: Path, rows: list[dict]) -> Path:
    lines = [
        "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('date', '2026-04-10')} | {r['company']} | {r['role']} | "
            f"{r['status']} | 2026-04-12 | 10 |  | {r['url']} | |"
        )
    path = tmp_path / "applications.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# parse_digest_matches
# ---------------------------------------------------------------------------

class TestParseDigestMatches:
    def test_captures_all_tier_jobs(self, tmp_path):
        path = _write(tmp_path / "digest-2026-04-14.md", SAMPLE_DIGEST)
        entries = parse_digest_matches(path, date(2026, 4, 14))
        assert len(entries) == 3
        companies = [e["company"] for e in entries]
        assert "Wiz" in companies
        assert "Tenable" in companies
        assert "Datadog" in companies

    def test_extracts_all_fields(self, tmp_path):
        path = _write(tmp_path / "d.md", SAMPLE_DIGEST)
        entries = parse_digest_matches(path, date(2026, 4, 14))
        wiz = next(e for e in entries if e["company"] == "Wiz")
        assert wiz["title"] == "Staff Vulnerability Engineer"
        assert wiz["score"] == 25
        assert wiz["location"] == "Remote, USA"
        assert wiz["posted_at"] == date(2026, 4, 12)
        assert wiz["tier"] == "tier_1_security_vendor"
        assert wiz["url"] == "https://boards.greenhouse.io/wizinc/jobs/55555"
        assert wiz["source"] == "ats"
        assert wiz["digest_date"] == date(2026, 4, 14)

    def test_detects_board_source(self, tmp_path):
        path = _write(tmp_path / "d.md", SAMPLE_DIGEST)
        entries = parse_digest_matches(path, date(2026, 4, 14))
        datadog = next(e for e in entries if e["company"] == "Datadog")
        assert datadog["source"] == "board"

    def test_excludes_anti_target_section(self, tmp_path):
        """Jobs listed under '## Skipped' must not be picked up."""
        path = _write(tmp_path / "d.md", SAMPLE_DIGEST)
        entries = parse_digest_matches(path, date(2026, 4, 14))
        for e in entries:
            assert "AppSec" not in e.get("title", "")

    def test_excludes_candidate_section(self, tmp_path):
        """'## Candidate companies' entries must not be confused with matches."""
        path = _write(tmp_path / "d.md", SAMPLE_DIGEST)
        entries = parse_digest_matches(path, date(2026, 4, 14))
        for e in entries:
            assert e["company"] != "Foo Labs"

    def test_historical_em_dash_separator(self, tmp_path):
        """Pre-dash-cleanup digests used em-dashes - parser should still extract."""
        path = _write(tmp_path / "historical.md", HISTORICAL_DIGEST)
        entries = parse_digest_matches(path, date(2026, 4, 10))
        assert len(entries) == 1
        assert entries[0]["company"] == "Legacy Corp"
        assert entries[0]["title"] == "Senior Security Engineer"

    def test_missing_file_returns_empty(self, tmp_path):
        assert parse_digest_matches(tmp_path / "nope.md", date(2026, 4, 14)) == []

    def test_title_with_embedded_hyphen(self, tmp_path):
        digest = """# Digest

## tier_1_saas

### Foo Corp - Staff Engineer - Infrastructure
- **Score:** 10
- **Apply:** https://boards.greenhouse.io/foo/jobs/1
"""
        path = _write(tmp_path / "d.md", digest)
        entries = parse_digest_matches(path, date(2026, 4, 14))
        assert len(entries) == 1
        assert entries[0]["company"] == "Foo Corp"
        assert entries[0]["title"] == "Staff Engineer - Infrastructure"

    def test_job_without_score_skipped(self, tmp_path):
        digest = """# D

## tier_1_saas

### No Score Co - Role
- **Apply:** https://foo.com/1

### Has Score - Real Role
- **Score:** 5
- **Apply:** https://boards.greenhouse.io/x/jobs/2
"""
        path = _write(tmp_path / "d.md", digest)
        entries = parse_digest_matches(path, date(2026, 4, 14))
        assert len(entries) == 1
        assert entries[0]["company"] == "Has Score"


# ---------------------------------------------------------------------------
# load_tracker_urls + load_tracker_identity_keys
# ---------------------------------------------------------------------------

class TestLoadTrackerIdentityKeys:
    def test_returns_urls_and_pairs(self, tmp_path):
        tracker = _tracker(tmp_path, [
            {"company": "Acme Lending", "role": "Director of Security Operations",
             "status": "ack", "url": "<https://jobs.lever.co/AcmeLending/abc>"},
            {"company": "Globex Streaming", "role": "Security Engineer (L4)",
             "status": "ack", "url": "<https://globexstreaming.com/jobs/123>"},
        ])
        urls, pairs = load_tracker_identity_keys(tracker)
        assert "https://jobs.lever.co/AcmeLending/abc" in urls
        assert ("acme lending", "director of security operations") in pairs
        assert ("globex streaming", "security engineer (l4)") in pairs

    def test_status_filter(self, tmp_path):
        tracker = _tracker(tmp_path, [
            {"company": "A", "role": "R1", "status": "ack", "url": "<https://a.com/1>"},
            {"company": "B", "role": "R2", "status": "rejected", "url": "<https://b.com/2>"},
        ])
        urls, pairs = load_tracker_identity_keys(tracker, statuses={"rejected"})
        assert "https://b.com/2" in urls
        assert "https://a.com/1" not in urls
        assert ("b", "r2") in pairs
        assert ("a", "r1") not in pairs

    def test_missing_file(self, tmp_path):
        urls, pairs = load_tracker_identity_keys(tmp_path / "nope.md")
        assert urls == set()
        assert pairs == set()


class TestLoadTrackerUrlsAllStatuses:
    def test_returns_all_when_no_filter(self, tmp_path):
        tracker = _tracker(tmp_path, [
            {"company": "A", "role": "R1", "status": "queued", "url": "<https://a.com/1>"},
            {"company": "B", "role": "R2", "status": "applied", "url": "<https://b.com/2>"},
            {"company": "C", "role": "R3", "status": "rejected", "url": "<https://c.com/3>"},
        ])
        urls = load_tracker_urls(tracker)
        assert len(urls) == 3


# ---------------------------------------------------------------------------
# run_backfill end-to-end
# ---------------------------------------------------------------------------

class TestRunBackfill:
    def test_surfaces_matched_jobs_not_in_tracker(self, tmp_path):
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        _write(digest_dir / "digest-2026-04-14.md", SAMPLE_DIGEST)

        # Tracker has Wiz but NOT Tenable or Datadog
        tracker = _tracker(tmp_path, [
            {"company": "Wiz", "role": "Staff Vulnerability Engineer",
             "status": "ack", "url": "<https://boards.greenhouse.io/wizinc/jobs/55555>"},
        ])

        rc = run_backfill(digest_dir, tracker, days=30, limit=10)
        assert rc == 0
        backfill_files = list(digest_dir.glob("backfill-*.md"))
        assert len(backfill_files) == 1
        content = backfill_files[0].read_text()
        assert "Tenable" in content
        assert "Datadog" in content
        assert "Wiz" not in content  # excluded: already in tracker

    def test_company_title_secondary_match(self, tmp_path):
        """Digest has LinkedIn URL, tracker has ATS URL - same role should be excluded."""
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        _write(digest_dir / "digest-2026-04-14.md", SAMPLE_DIGEST)

        # Tracker has Datadog with a DIFFERENT URL than the digest (which has LinkedIn).
        # Pure URL match would miss this, but (company, title) match catches it.
        tracker = _tracker(tmp_path, [
            {"company": "Datadog", "role": "Staff Security Engineer",
             "status": "ack", "url": "<https://careers.datadog.com/jobs/12345>"},
        ])

        rc = run_backfill(digest_dir, tracker, days=30, limit=10)
        assert rc == 0
        content = list(digest_dir.glob("backfill-*.md"))[0].read_text()
        assert "Datadog" not in content  # excluded via (company, title) fallback

    def test_days_window_limits_scope(self, tmp_path):
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        today = datetime.now(timezone.utc).date()
        old_date = today - timedelta(days=60)
        recent_date = today - timedelta(days=5)

        old_digest = f"""# Digest

## tier_1_saas

### OldCo - Senior Eng
- **Score:** 20
- **Apply:** https://boards.greenhouse.io/old/jobs/1
"""
        recent_digest = f"""# Digest

## tier_1_saas

### NewCo - Senior Eng
- **Score:** 15
- **Apply:** https://boards.greenhouse.io/new/jobs/2
"""
        _write(digest_dir / f"digest-{old_date.isoformat()}.md", old_digest)
        _write(digest_dir / f"digest-{recent_date.isoformat()}.md", recent_digest)

        rc = run_backfill(digest_dir, None, days=14, limit=10)
        assert rc == 0
        content = list(digest_dir.glob("backfill-*.md"))[0].read_text()
        assert "NewCo" in content
        assert "OldCo" not in content  # out of window

    def test_score_filters(self, tmp_path):
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        _write(digest_dir / "digest-2026-04-14.md", SAMPLE_DIGEST)

        # max_score=15 should exclude Wiz (25) and Tenable (18), keep Datadog (12)
        rc = run_backfill(digest_dir, None, days=30, limit=10, max_score=15)
        assert rc == 0
        backfill = sorted(digest_dir.glob("backfill-*.md"))[-1]
        content = backfill.read_text()
        assert "Datadog" in content
        assert "Wiz" not in content
        assert "Tenable" not in content

    def test_dedup_across_digests(self, tmp_path):
        """Same URL in multiple digests collapses into one entry with appearance count."""
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        d1 = """# D1

## tier_1_saas

### Foo - Role
- **Score:** 10
- **Apply:** https://boards.greenhouse.io/foo/jobs/1
"""
        d2 = """# D2

## tier_1_saas

### Foo - Role
- **Score:** 15
- **Apply:** https://boards.greenhouse.io/foo/jobs/1
"""
        _write(digest_dir / "digest-2026-04-14.md", d1)
        _write(digest_dir / "digest-2026-04-15.md", d2)

        rc = run_backfill(digest_dir, None, days=30, limit=10)
        assert rc == 0
        content = list(digest_dir.glob("backfill-*.md"))[0].read_text()
        # Only one row for Foo despite appearing in both digests
        assert content.count("### Foo - Role") == 1
        # Highest score preserved
        assert "Score:** 15" in content
        # Appearance count surfaced
        assert "seen 2x" in content

    def test_empty_digest_dir(self, tmp_path):
        digest_dir = tmp_path / "empty"
        digest_dir.mkdir()
        rc = run_backfill(digest_dir, None, days=30, limit=10)
        assert rc == 0
        # Still writes a backfill file (empty summary) so the slash command has something
        assert list(digest_dir.glob("backfill-*.md"))

    def test_no_digest_dir(self, tmp_path):
        rc = run_backfill(tmp_path / "does-not-exist", None, days=30, limit=10)
        assert rc == 1

    def test_tracking_param_drift_matching(self, tmp_path):
        """URL in tracker has session-A tracking; digest has session-B tracking. Must match."""
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        digest = """# D

## tier_1_saas

### Foo - Role
- **Score:** 10
- **Apply:** https://www.linkedin.com/jobs/view/42?trk=session_b&utm_source=news
"""
        _write(digest_dir / "digest-2026-04-14.md", digest)

        tracker = _tracker(tmp_path, [
            {"company": "Foo", "role": "Role", "status": "applied",
             "url": "<https://www.linkedin.com/jobs/view/42?trk=session_a&refId=xyz>"},
        ])

        rc = run_backfill(digest_dir, tracker, days=30, limit=10)
        assert rc == 0
        content = list(digest_dir.glob("backfill-*.md"))[0].read_text()
        assert "Foo" not in content  # URL normalization caught the match

    def test_title_suffix_drift_matching(self, tmp_path):
        """Tracker has bare title ('Senior GRC Security Analyst'); digest has the
        same role with a work-arrangement suffix ('Senior GRC Security Analyst (remote)'
        or 'Director, IT Security & Compliance - Remote'). Backfill must fold these
        through normalize_title and exclude both."""
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        digest = """# D

## tier_1_saas

### Acme Health - Senior GRC Security Analyst (remote)
- **Score:** 15
- **Apply:** https://www.linkedin.com/jobs/view/4391183156

### Globex Wellness - Director, IT Security & Compliance - Remote
- **Score:** 11
- **Apply:** https://www.linkedin.com/jobs/view/4400307556
"""
        _write(digest_dir / "digest-2026-04-17.md", digest)

        # Tracker rows have bare titles AND different (ATS) URLs - both layers
        # of the dedup must work together.
        tracker = _tracker(tmp_path, [
            {"company": "Acme Health", "role": "Senior GRC Security Analyst", "status": "ack",
             "url": "<https://myjobs.adp.com/acmehealthcareers/cx/job-details?reqId=5001185894306>"},
            {"company": "Globex Wellness", "role": "Director, IT Security & Compliance", "status": "ack",
             "url": "<https://globexwellness.wd1.myworkdayjobs.com/en-US/GlobexWellness_Careers/job/Anywhere/Director--IT-Security---Compliance---Remote_R-101803>"},
        ])

        rc = run_backfill(digest_dir, tracker, days=30, limit=10)
        assert rc == 0
        content = list(digest_dir.glob("backfill-*.md"))[0].read_text()
        assert "Acme Health" not in content  # title-suffix normalization caught it
        assert "Globex Wellness" not in content


# ---------------------------------------------------------------------------
# write_backfill_digest formatting
# ---------------------------------------------------------------------------

class TestWriteBackfillDigest:
    def test_empty_renders_friendly_message(self, tmp_path):
        out = write_backfill_digest([], 0, 3, 30, 10, None, None, tmp_path)
        content = out.read_text()
        assert "No backfill candidates" in content
        assert "Digests scanned:** 3" in content

    def test_timestamped_filename(self, tmp_path):
        out = write_backfill_digest([], 0, 1, 30, 10, None, None, tmp_path)
        assert out.name.startswith("backfill-")
        assert out.name.endswith(".md")

    def test_score_filter_shown_in_header(self, tmp_path):
        out = write_backfill_digest([], 0, 1, 30, 10, min_score=5, max_score=15, digest_dir=tmp_path)
        content = out.read_text()
        assert "Score floor:** 5" in content
        assert "Score ceiling:** 15" in content

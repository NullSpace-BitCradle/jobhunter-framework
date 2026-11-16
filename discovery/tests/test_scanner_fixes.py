"""Regression tests for the scanner robustness fixes:

atomic state writes, unreadable-state refusal, unique digests (and dry-run
isolation), seen-id refresh, timezone handling, UTF-8 I/O, whole-word keyword
matching, the single title normalizer, and lazy description enrichment.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import fsutil
import main as main_mod
from dedup import normalize_title as dedup_normalize_title
from fsutil import StateFileError
from main import (
    configure_timezone, explain_match_rejection, is_anti_target, load_state,
    local_now, match_title, normalize_title, parse_digest_matches,
    refresh_seen_ids, run_backfill, save_state, write_digest,
    SEEN_ID_MAX_AGE_DAYS,
)
from scrapers.base import Job, Scraper


def _job(**kw) -> Job:
    d = dict(
        id="greenhouse:acme:1", company="Acme", company_slug="acme",
        company_tier="tier_1_saas", title="Senior Security Engineer",
        location="Remote - US", remote=True, url="https://example.com/jobs/1",
        posted_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        description_text="Build secure systems.", source="ats",
    )
    d.update(kw)
    return Job(**d)


STATS = {"companies_scanned": 1, "total_fetched": 1, "new_jobs": 1}


def _recent(days_ago=1):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# fsutil: atomic writes, unreadable files
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_replaces_content_and_leaves_no_temp_files(self, tmp_path):
        target = tmp_path / "state.json"
        target.write_text("old", encoding="utf-8")
        fsutil.atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]

    def test_failed_replace_keeps_old_file_and_cleans_temp(self, tmp_path, monkeypatch):
        """The old open('w') truncated the file first; a failure mid-write lost it."""
        target = tmp_path / "state.json"
        target.write_text('{"keep": true}', encoding="utf-8")

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(fsutil.os, "replace", boom)
        with pytest.raises(OSError):
            fsutil.atomic_write_text(target, "partial")
        assert target.read_text(encoding="utf-8") == '{"keep": true}'
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]

    def test_permission_error_on_replace_is_retried(self, tmp_path, monkeypatch):
        """Windows: AV / OneDrive can hold the target briefly."""
        target = tmp_path / "state.json"
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("in use")
            return real_replace(src, dst)

        monkeypatch.setattr(fsutil.os, "replace", flaky)
        monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
        fsutil.atomic_write_text(target, "ok")
        assert target.read_text(encoding="utf-8") == "ok"
        assert calls["n"] == 3

    def test_utf8_and_lf_regardless_of_platform(self, tmp_path):
        target = tmp_path / "f.txt"
        fsutil.atomic_write_text(target, "café — rôle\nline2\n")
        raw = target.read_bytes()
        assert "café — rôle".encode("utf-8") in raw
        assert b"\r\n" not in raw

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "s.json"
        fsutil.atomic_write_json(target, {"x": 1})
        assert json.loads(target.read_text(encoding="utf-8")) == {"x": 1}


class TestWriteNewText:
    def test_never_overwrites(self, tmp_path):
        a = fsutil.write_new_text(tmp_path, "digest-2026-04-10-120000", "first")
        b = fsutil.write_new_text(tmp_path, "digest-2026-04-10-120000", "second")
        c = fsutil.write_new_text(tmp_path, "digest-2026-04-10-120000", "third")
        assert len({a, b, c}) == 3
        assert a.read_text(encoding="utf-8") == "first"
        assert b.name == "digest-2026-04-10-120000_2.md"
        assert c.name == "digest-2026-04-10-120000_3.md"


class TestLoadJsonObject:
    def test_missing_is_none(self, tmp_path):
        assert fsutil.load_json_object(tmp_path / "nope.json") is None

    @pytest.mark.parametrize("content", ["", "{broken", "[1, 2, 3]", '"str"', "null"])
    def test_unusable_content_raises_and_keeps_a_copy(self, tmp_path, content):
        p = tmp_path / "state.json"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(StateFileError):
            fsutil.load_json_object(p)
        assert p.read_text(encoding="utf-8") == content
        assert (tmp_path / "state.json.corrupt").exists()

    def test_undecodable_bytes_raise(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_bytes(b"\xff\xfe\x00bad")
        with pytest.raises(StateFileError):
            fsutil.load_json_object(p)

    def test_keep_backup_skips_a_corrupt_file(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"good": 1}', encoding="utf-8")
        fsutil.keep_backup(p)
        p.write_text("{garbage", encoding="utf-8")
        fsutil.keep_backup(p)          # must NOT overwrite the good backup
        assert (tmp_path / "s.json.bak").read_text(encoding="utf-8") == '{"good": 1}'


# ---------------------------------------------------------------------------
# state: refuse unreadable, refresh seen ids
# ---------------------------------------------------------------------------

class TestState:
    def test_wrong_shaped_seen_ids_refused(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text('{"seen_ids": "oops"}', encoding="utf-8")
        with pytest.raises(StateFileError):
            load_state(p)

    def test_legacy_list_seen_ids_still_loads(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text('{"seen_ids": ["a", "b"]}', encoding="utf-8")
        assert load_state(p)["seen_ids"] == ["a", "b"]

    def test_save_keeps_previous_state_as_bak(self, tmp_path):
        p = tmp_path / "state.json"
        save_state({"seen_ids": {"one": _recent()}}, p)
        save_state({"seen_ids": {"two": _recent()}}, p)
        assert "one" in json.loads((tmp_path / "state.json.bak").read_text(encoding="utf-8"))["seen_ids"]
        assert "two" in json.loads(p.read_text(encoding="utf-8"))["seen_ids"]

    def test_refresh_stamps_new_and_known_ids(self):
        old = _recent(50)
        seen = {"known": old, "untouched": old}
        refresh_seen_ids(seen, ["known", "fresh"], "NOW")
        assert seen == {"known": "NOW", "fresh": "NOW", "untouched": old}

    def test_long_open_posting_survives_pruning_only_when_refreshed(self, tmp_path):
        """Without a refresh, a posting first seen 59 days ago is pruned two days
        later even though it is still open, and reappears as new."""
        first_seen = _recent(SEEN_ID_MAX_AGE_DAYS - 1)
        # Two days later, no refresh (old behaviour): dropped.
        later = datetime.now(timezone.utc) + timedelta(days=2)
        assert first_seen < (later - timedelta(days=SEEN_ID_MAX_AGE_DAYS)).isoformat()
        # With the refresh it carries today's timestamp and is kept.
        seen = refresh_seen_ids({"open": first_seen}, ["open"], datetime.now(timezone.utc).isoformat())
        p = tmp_path / "state.json"
        save_state({"seen_ids": seen}, p)
        assert "open" in json.loads(p.read_text(encoding="utf-8"))["seen_ids"]


# ---------------------------------------------------------------------------
# timezone
# ---------------------------------------------------------------------------

class TestTimezone:
    def setup_method(self):
        configure_timezone({})

    def teardown_method(self):
        configure_timezone({})

    def test_default_is_system_local_and_aware(self):
        now = local_now()
        assert now.tzinfo is not None
        assert abs((now - datetime.now(timezone.utc)).total_seconds()) < 5

    def test_configured_iana_zone(self):
        pytest.importorskip("zoneinfo")
        try:
            import zoneinfo
            zoneinfo.ZoneInfo("America/New_York")
        except Exception:
            pytest.skip("no tz database available (install tzdata)")
        configure_timezone({"discovery": {"timezone": "America/New_York"}})
        assert local_now().utcoffset() == timedelta(hours=-7)

    def test_bad_zone_falls_back_to_system(self):
        configure_timezone({"discovery": {"timezone": "Not/A_Zone"}})
        assert main_mod.LOCAL_TZ is None
        assert local_now().tzinfo is not None


# ---------------------------------------------------------------------------
# digests: unique names, dry-run isolation, backfill compatibility, UTF-8
# ---------------------------------------------------------------------------

class TestDigestFiles:
    def test_two_runs_in_the_same_second_do_not_overwrite(self, tmp_path, monkeypatch):
        fixed = datetime(2026, 11, 16, 9, 30, 5, tzinfo=timezone.utc)
        monkeypatch.setattr(main_mod, "local_now", lambda: fixed)
        first = write_digest([(10, _job(id="a", title="Senior Security Engineer A"), True, "")], STATS, tmp_path)
        second = write_digest([], STATS, tmp_path)
        assert first != second
        assert first.name == "digest-2026-11-16-093005.md"
        assert "Senior Security Engineer A" in first.read_text(encoding="utf-8")
        assert "No new matching jobs" in second.read_text(encoding="utf-8")

    def test_dry_run_uses_a_separate_name(self, tmp_path):
        real = write_digest([(10, _job(), True, "")], STATS, tmp_path)
        dry = write_digest([], STATS, tmp_path, dry_run=True)
        assert real.name.startswith("digest-")
        assert dry.name.startswith("dryrun-")
        assert "Senior Security Engineer" in real.read_text(encoding="utf-8")   # not clobbered
        assert "DRY RUN" in dry.read_text(encoding="utf-8")

    def test_non_ascii_round_trips_as_utf8(self, tmp_path):
        job = _job(title="Senior Security Engineer — Sécurité", company="Café Corp")
        out = write_digest([(12, job, True, "")], STATS, tmp_path)
        assert "Sécurité".encode("utf-8") in out.read_bytes()
        parsed = parse_digest_matches(out, date(2026, 11, 16))
        assert parsed[0]["company"] == "Café Corp"
        assert "Sécurité" in parsed[0]["title"]


class TestBackfillReadsNewAndLegacyNames:
    DIGEST = (
        "# D\n\n## tier_1_saas\n\n### {company} - {title}\n- **Score:** {score}\n"
        "- **Apply:** {url}\n"
    )

    def _digest(self, d: Path, name: str, company, title, score, url):
        (d / name).write_text(self.DIGEST.format(company=company, title=title, score=score, url=url),
                              encoding="utf-8")

    def test_timestamped_legacy_and_dryrun(self, tmp_path):
        d = tmp_path / "out"
        d.mkdir()
        today = date.today()
        old = today - timedelta(days=2)
        self._digest(d, f"digest-{old.isoformat()}.md", "Legacy", "Staff Security Engineer", 9,
                     "https://boards.greenhouse.io/legacy/jobs/1")
        self._digest(d, f"digest-{today.isoformat()}-090000.md", "Morning", "Senior Security Engineer", 11,
                     "https://boards.greenhouse.io/morning/jobs/2")
        self._digest(d, f"digest-{today.isoformat()}-150000.md", "Afternoon", "Principal Security Engineer", 14,
                     "https://boards.greenhouse.io/afternoon/jobs/3")
        self._digest(d, f"digest-{today.isoformat()}-150000_2.md", "Collided", "Lead Security Engineer", 8,
                     "https://boards.greenhouse.io/collided/jobs/4")
        self._digest(d, f"dryrun-{today.isoformat()}-160000.md", "DryRun", "Head of Security", 99,
                     "https://boards.greenhouse.io/dryrun/jobs/5")
        assert run_backfill(d, None, days=30, limit=50) == 0
        backfill = next(d.glob("backfill-*.md")).read_text(encoding="utf-8")
        assert "Digests scanned:** 4" in backfill
        for company in ("Legacy", "Morning", "Afternoon", "Collided"):
            assert company in backfill
        assert "DryRun" not in backfill

    def test_same_job_in_two_same_day_digests_counts_twice_and_uses_latest_score(self, tmp_path):
        d = tmp_path / "out"
        d.mkdir()
        today = date.today().isoformat()
        url = "https://boards.greenhouse.io/acme/jobs/7"
        self._digest(d, f"digest-{today}-080000.md", "Acme", "Staff Security Engineer", 20, url)
        self._digest(d, f"digest-{today}-180000.md", "Acme", "Staff Security Engineer", 12, url)
        run_backfill(d, None, days=30, limit=50)
        text = next(d.glob("backfill-*.md")).read_text(encoding="utf-8")
        assert "seen 2x" in text and "latest run: 12" in text


# ---------------------------------------------------------------------------
# keyword matching: lowercase config, whole-word exclusions
# ---------------------------------------------------------------------------

RULES = {
    "tier_keywords_in_title": ["Senior", "STAFF"],
    "domain_keywords_in_title": ["Security"],
    "title_exclusions": ["Intern", "tier 1 "],
}


class TestKeywordMatching:
    def test_mixed_case_config_matches_lowercased_titles(self):
        assert match_title(_job(title="staff security engineer"), RULES)
        assert match_title(_job(title="SENIOR SECURITY ENGINEER"), RULES)

    @pytest.mark.parametrize("title", [
        "Senior Internal Tools Security Engineer",
        "Staff International Security Engineer",
    ])
    def test_intern_exclusion_does_not_hit_internal_or_international(self, title):
        assert match_title(_job(title=title), RULES)

    @pytest.mark.parametrize("title", [
        "Senior Security Intern",
        "Staff Security Engineer (Intern)",
        "Senior Security Engineer - Tier 1 Support",
    ])
    def test_real_exclusions_still_excluded(self, title):
        assert not match_title(_job(title=title), RULES)

    def test_inclusion_keywords_stay_substring(self):
        """'security' must still match 'Cybersecurity' (inclusion is lenient on purpose)."""
        assert match_title(_job(title="Senior Cybersecurity Engineer"), RULES)

    def test_blank_exclusion_does_not_exclude_everything(self):
        rules = {"title_exclusions": ["", "  "]}
        assert match_title(_job(), rules)

    def test_punctuated_keyword(self):
        rules = {"title_exclusions": ["C++"]}
        assert not match_title(_job(title="Senior C++ Security Engineer"), rules)
        assert match_title(_job(title="Senior C Security Engineer"), rules)

    def test_explain_uses_the_same_rules(self):
        assert explain_match_rejection(_job(title="Senior Internal Security Engineer", remote=True), RULES) is None
        msg = explain_match_rejection(_job(title="Senior Security Intern"), RULES)
        assert msg == "title contains excluded keyword 'intern'"

    def test_anti_target_patterns_lowercased_and_whole_word(self):
        filters = {"anti_target_patterns": {
            "waf": {"description": "WAF role",
                    "title_contains_any": ["Security Engineer"],
                    "description_contains_any": ["WAF"]},
        }}
        hit, _ = is_anti_target(_job(description_text="Operate the WAF daily."), filters)
        miss, _ = is_anti_target(_job(description_text="We serve waffles on Fridays."), filters)
        assert hit and not miss

    def test_anti_target_negation_is_case_insensitive(self):
        filters = {"anti_target_patterns": {"onsite": {
            "description": "on-site", "location_contains_any": ["On-Site"],
            "negates_if_location_also_contains": ["REMOTE"]}}}
        assert is_anti_target(_job(location="NYC (On-Site)"), filters)[0]
        assert not is_anti_target(_job(location="Remote or On-Site"), filters)[0]


# ---------------------------------------------------------------------------
# the single title normalizer
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    def test_main_and_dedup_share_one_normalizer(self):
        assert normalize_title is dedup_normalize_title

    def test_distinct_roles_are_not_collapsed(self):
        a = normalize_title("Senior Security Engineer - Detection")
        b = normalize_title("Senior Security Engineer - Cloud")
        assert a != b
        assert normalize_title("Security Engineer | Detection") != normalize_title("Security Engineer | Cloud")

    @pytest.mark.parametrize("decorated", [
        "Senior Security Engineer - Remote",
        "Senior Security Engineer | US",
        "Senior Security Engineer - Remote, USA",
        "Senior Security Engineer (Remote)",
        "Senior Security Engineer (Remote, USA)",
        "Senior Security Engineer – Hybrid",
        "Senior Security Engineer - US Remote",
        "Senior Security Engineer - Remote (US)",
    ])
    def test_location_and_arrangement_suffixes_are_stripped(self, decorated):
        assert normalize_title(decorated) == normalize_title("Senior Security Engineer")

    def test_remote_access_is_a_role_not_a_location(self):
        assert normalize_title("Security Engineer - Remote Access") != normalize_title("Security Engineer")

    def test_level_markers_survive(self):
        assert normalize_title("Security Engineer (L4)") != normalize_title("Security Engineer (L5)")

    def test_empty_and_none_safe(self):
        assert normalize_title("") == ""
        assert normalize_title(None) == ""


# ---------------------------------------------------------------------------
# end to end: main() with a fake scraper and temp config
# ---------------------------------------------------------------------------

class FakeScraper(Scraper):
    ats_name = "fake"

    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs
        self.enriched = []

    def fetch_jobs(self, slug, name, tier, timeout=None):
        return list(self.jobs)

    def enrich_job(self, job):
        self.enriched.append(job.id)
        job.description_text = "CISSP is required for this role."
        return True


@pytest.fixture
def scan_env(tmp_path, monkeypatch):
    """A complete temp scan environment; returns a runner(args) -> exit code."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "companies.yaml").write_text(
        "tier_1_saas:\n  - {name: Acme, ats: greenhouse, slug: acme}\n", encoding="utf-8")
    (cfg_dir / "keywords.yaml").write_text(
        "match_rules:\n  tier_keywords_in_title: [senior]\n  domain_keywords_in_title: [security]\n"
        "  title_exclusions: [intern]\n  require_remote: false\nscoring: {}\n", encoding="utf-8")
    (cfg_dir / "filters.yaml").write_text(
        "anti_target_patterns:\n  cert:\n    description: cert hard-required\n"
        "    description_contains_any: ['cissp is required']\n", encoding="utf-8")
    state_file = tmp_path / "state" / "seen-jobs.json"
    digest_dir = tmp_path / "out"
    config = tmp_path / "config.yaml"
    config.write_text(
        "discovery:\n"
        f"  config_dir: '{cfg_dir.as_posix()}'\n"
        f"  state_file: '{state_file.as_posix()}'\n"
        f"  digest_dir: '{digest_dir.as_posix()}'\n", encoding="utf-8")

    class Env:
        pass

    env = Env()
    env.state_file, env.digest_dir = state_file, digest_dir
    env.fixed = datetime(2026, 11, 16, 9, 30, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(main_mod, "local_now", lambda: env.fixed)

    def run(jobs, *extra):
        fake = FakeScraper(jobs)
        monkeypatch.setitem(main_mod.SCRAPER_REGISTRY, "greenhouse", fake)
        monkeypatch.setattr("sys.argv", ["main.py", "--config", str(config), *extra])
        env.scraper = fake
        return main_mod.main()

    env.run = run
    return env


class TestMainEndToEnd:
    JOBS = [_job(id="greenhouse:acme:1", title="Senior Security Engineer", url="https://x.example/1")]

    def test_second_same_day_run_keeps_the_first_digest(self, scan_env):
        assert scan_env.run(self.JOBS) == 0
        assert scan_env.run(self.JOBS) == 0       # same fixed second, nothing new
        digests = sorted(scan_env.digest_dir.glob("digest-*.md"))
        assert len(digests) == 2
        assert "Senior Security Engineer" in digests[0].read_text(encoding="utf-8")
        assert "No new matching jobs" in digests[1].read_text(encoding="utf-8")

    def test_dry_run_neither_clobbers_the_real_digest_nor_marks_seen(self, scan_env):
        scan_env.run(self.JOBS)
        real = next(scan_env.digest_dir.glob("digest-*.md"))
        before = real.read_text(encoding="utf-8")
        state_before = scan_env.state_file.read_text(encoding="utf-8")
        assert scan_env.run(self.JOBS, "--dry-run") == 0
        assert real.read_text(encoding="utf-8") == before
        assert list(scan_env.digest_dir.glob("dryrun-*.md"))
        assert scan_env.state_file.read_text(encoding="utf-8") == state_before

    def test_dry_run_alone_creates_no_state(self, scan_env):
        scan_env.run(self.JOBS, "--dry-run")
        assert not scan_env.state_file.exists()

    def test_seen_timestamp_is_refreshed_every_run(self, scan_env):
        scan_env.run(self.JOBS)
        stale = _recent(SEEN_ID_MAX_AGE_DAYS - 1)
        state = json.loads(scan_env.state_file.read_text(encoding="utf-8"))
        state["seen_ids"]["greenhouse:acme:1"] = stale
        scan_env.state_file.write_text(json.dumps(state), encoding="utf-8")
        scan_env.run(self.JOBS)
        after = json.loads(scan_env.state_file.read_text(encoding="utf-8"))["seen_ids"]["greenhouse:acme:1"]
        assert after > stale

    def test_corrupt_state_stops_the_run(self, scan_env, capsys):
        scan_env.state_file.parent.mkdir(parents=True)
        scan_env.state_file.write_text("{corrupt", encoding="utf-8")
        assert scan_env.run(self.JOBS) == 1
        out = capsys.readouterr().out
        assert "ERROR" in out and "flood" in out
        assert scan_env.state_file.read_text(encoding="utf-8") == "{corrupt"
        assert not scan_env.digest_dir.exists()

    def test_description_fetched_only_for_title_matched_jobs_then_antitarget_checked(self, scan_env):
        pending = {"description_pending": True}
        jobs = [
            _job(id="greenhouse:acme:10", title="Senior Security Engineer", description_text="", raw=dict(pending)),
            _job(id="greenhouse:acme:11", title="Office Manager", description_text="", raw=dict(pending)),
            _job(id="greenhouse:acme:12", title="Senior Security Intern", description_text="", raw=dict(pending)),
            _job(id="greenhouse:acme:13", title="Senior Security Analyst", description_text="have text",
                 raw=dict(pending)),
        ]
        assert scan_env.run(jobs) == 0
        # Exactly one detail request: job 10 passed title + location and has no
        # description. 11 (title miss), 12 (excluded) and 13 (has text) do not.
        assert scan_env.scraper.enriched == ["greenhouse:acme:10"]
        digest = next(scan_env.digest_dir.glob("digest-*.md")).read_text(encoding="utf-8")
        # The fetched description ("CISSP is required") triggered the anti-target rule.
        assert "1x cert hard-required" in digest
        assert "Matches (post-filter):** 1" in digest        # job 13 (analyst) still matches

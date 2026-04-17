#!/usr/bin/env python3
"""
Job Discovery Agent - scans target company ATS APIs for new relevant roles.

Usage:
    python main.py                 # Full scan, writes digest + updates state
    python main.py --dry-run       # Scan without updating state
    python main.py --verify        # Verify configured company slugs return results
    python main.py -v              # Verbose logging
"""
import argparse
import fcntl
import json
import logging
import re
import sys
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import yaml

from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.ashby import AshbyScraper
from scrapers.smartrecruiters import SmartRecruitersScraper
from scrapers.workable import WorkableScraper
from scrapers.jobspy import JobspyScraper
from dedup import deduplicate_cross_source
import candidates as candidates_mod


PROJECT_ROOT = Path(__file__).resolve().parent      # discovery/
REPO_ROOT = PROJECT_ROOT.parent                      # jobhunter-framework/

SCRAPER_REGISTRY = {
    "greenhouse": GreenhouseScraper(),
    "lever": LeverScraper(),
    "ashby": AshbyScraper(timeout=20),  # Ashby API is slower than other ATS; 10s default times out intermittently
    "smartrecruiters": SmartRecruitersScraper(),
    "workable": WorkableScraper(),
}


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_framework_config(explicit_path: Path | None = None) -> dict:
    """Optional framework-level config.yaml at repo root. Empty dict if absent."""
    path = explicit_path or (REPO_ROOT / "config.yaml")
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        logging.warning("Failed to read %s: %s", path, e)
        return {}


def resolve_paths(framework_config: dict) -> tuple[Path, Path, Path]:
    """Resolve (config_dir, state_file, digest_dir) honoring framework-config overrides.

    Defaults keep the discovery tool usable standalone from its own directory.
    """
    disco = framework_config.get("discovery") or {}

    raw_cfg = disco.get("config_dir")
    config_dir = Path(raw_cfg).expanduser() if raw_cfg else PROJECT_ROOT / "config"

    raw_state = disco.get("state_file")
    state_file = Path(raw_state).expanduser() if raw_state else PROJECT_ROOT / "state" / "seen-jobs.json"

    raw_digest = disco.get("digest_dir")
    digest_dir = Path(raw_digest).expanduser() if raw_digest else PROJECT_ROOT / "output"

    return config_dir, state_file, digest_dir


SEEN_ID_MAX_AGE_DAYS = 60
SEEN_ID_MAX_COUNT = 50000

# Matches a tracker row where the first cell is an ISO date (YYYY-MM-DD). Robust
# against year boundaries and accidental text that happens to start with "| 20".
_TRACKER_DATE_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\b")

_TRACKER_TERMINAL_STATUSES = {"withdrawn", "rejected", "declined_anti_target"}


def _parse_tracker_header(lines: list[str]) -> dict[str, int] | None:
    """Find the markdown table header and return {column_name: index}.

    Returns None if the header isn't found. Column names are lowercased and
    stripped so callers can do case-insensitive lookups.
    """
    for line in lines:
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip().lower() for c in line.split("|")]
        # Header cells include leading/trailing empties from `|...|`; drop them
        cells_nonempty = [c for c in cells if c]
        if "status" in cells_nonempty and "url" in cells_nonempty:
            # Return index map using the full split (including empty sentinel cells)
            # so indices line up with data-row cells produced by the same `split("|")`.
            return {name: idx for idx, name in enumerate(cells) if name}
    return None


def load_declined_urls(applications_file: Path | None) -> set[str]:
    """Parse the applications tracker and return URLs of rows with terminal status.

    The state file (seen-jobs.json) gets cleared periodically. The applications
    tracker is the durable record of roles Joshua has already reviewed and
    rejected - we use it as a persistent skip list so withdrawn/rejected roles
    don't re-surface in discovery digests.

    Uses header-based column lookup so tracker schema changes (adding a Salary
    column, reordering, etc.) don't silently break the parser.
    """
    if not applications_file or not applications_file.exists():
        return set()
    declined: set[str] = set()
    try:
        lines = applications_file.read_text().splitlines()
        header = _parse_tracker_header(lines)
        if not header:
            logging.warning("Applications tracker %s: no header row found", applications_file)
            return set()
        status_idx = header.get("status")
        url_idx = header.get("url")
        if status_idx is None or url_idx is None:
            logging.warning("Applications tracker %s: missing Status or URL column", applications_file)
            return set()
        for line in lines:
            if not _TRACKER_DATE_ROW.match(line):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) <= max(status_idx, url_idx):
                continue
            status = cells[status_idx].lower()
            url = cells[url_idx]
            # Strip markdown auto-link wrappers (<url>) and link syntax ([text](url))
            url = url.strip().strip("<>")
            if url.startswith("[") and "](" in url and url.endswith(")"):
                url = url.split("](", 1)[1].rstrip(")")
            if status in _TRACKER_TERMINAL_STATUSES and url:
                declined.add(url)
    except Exception as e:
        logging.warning("Could not parse applications tracker %s: %s", applications_file, e)
    return declined


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            with state_file.open() as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
                return data
        except (json.JSONDecodeError, ValueError) as e:
            logging.warning("Corrupt state file %s: %s - starting fresh", state_file, e)
    return {"seen_ids": {}, "last_run": None}


def save_state(state: dict, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    state["last_run"] = now.isoformat()

    # Prune seen_ids older than SEEN_ID_MAX_AGE_DAYS, then cap at
    # SEEN_ID_MAX_COUNT most-recent entries. Two-stage prune prevents the file
    # from growing unbounded even during high-volume scan periods.
    cutoff = (now - timedelta(days=SEEN_ID_MAX_AGE_DAYS)).isoformat()
    if isinstance(state.get("seen_ids"), dict):
        fresh = {k: v for k, v in state["seen_ids"].items() if v >= cutoff}
        if len(fresh) > SEEN_ID_MAX_COUNT:
            # Keep the newest SEEN_ID_MAX_COUNT entries by timestamp
            sorted_items = sorted(fresh.items(), key=lambda kv: kv[1], reverse=True)
            fresh = dict(sorted_items[:SEEN_ID_MAX_COUNT])
        state["seen_ids"] = fresh

    with state_file.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(state, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)


def match_title(job: Job, rules: dict) -> bool:
    title_lower = job.title.lower()
    if not title_lower:
        return False

    tier_kw = rules.get("tier_keywords_in_title", [])
    if tier_kw and not any(k in title_lower for k in tier_kw):
        return False

    domain_kw = rules.get("domain_keywords_in_title", [])
    if domain_kw and not any(k in title_lower for k in domain_kw):
        return False

    exclusions = rules.get("title_exclusions", [])
    if any(ex in title_lower for ex in exclusions):
        return False

    return True


def match_location(job: Job, rules: dict) -> bool:
    if not rules.get("require_remote", False):
        return True

    loc_lower = (job.location or "").lower()

    # First: must be remote - OR match the local on-site allow regex.
    is_remote = job.remote is True or "remote" in loc_lower
    if not is_remote:
        local_pattern = rules.get("local_location_regex")
        if local_pattern:
            try:
                if re.search(local_pattern, loc_lower, re.IGNORECASE):
                    return True
            except re.error as e:
                logging.warning("Invalid local_location_regex: %s", e)
        return False

    # Second: if required_location_regex is set, location must match it.
    # This is how we filter international-remote when the user needs US-remote.
    pattern = rules.get("required_location_regex")
    if pattern:
        try:
            if not re.search(pattern, loc_lower, re.IGNORECASE):
                return False
        except re.error as e:
            logging.warning("Invalid required_location_regex: %s", e)
            # Fall through - don't block on config error

    return True


def is_anti_target(job: Job, filters: dict) -> tuple[bool, str]:
    """Check job against anti-target patterns. Returns (matched, reason)."""
    title_lower = job.title.lower()
    desc_lower = (job.description_text or "").lower()
    loc_lower = (job.location or "").lower()

    patterns = filters.get("anti_target_patterns", {}) or {}
    for name, rule in patterns.items():
        reason = rule.get("description", name)

        title_any = rule.get("title_contains_any") or []
        desc_any = rule.get("description_contains_any") or []
        desc_all = rule.get("description_contains_all") or []
        desc_negate = rule.get("negates_if_description_also_contains") or []
        loc_any = rule.get("location_contains_any") or []
        loc_negate = rule.get("negates_if_location_also_contains") or []

        # Any specified condition must match; unspecified conditions are ignored.
        # ALL specified condition groups must match for the pattern to fire.
        conditions_specified = False
        all_matched = True

        if title_any:
            conditions_specified = True
            if not any(t in title_lower for t in title_any):
                all_matched = False

        if all_matched and desc_any:
            conditions_specified = True
            if not any(d in desc_lower for d in desc_any):
                all_matched = False

        if all_matched and desc_all:
            conditions_specified = True
            if not all(d in desc_lower for d in desc_all):
                all_matched = False

        if all_matched and desc_negate and any(n in desc_lower for n in desc_negate):
            all_matched = False

        if all_matched and loc_any:
            conditions_specified = True
            if not any(l in loc_lower for l in loc_any):
                all_matched = False
            elif loc_negate and any(n in loc_lower for n in loc_negate):
                # Location matched anti-target but also has a negating keyword (e.g., "remote")
                all_matched = False

        if conditions_specified and all_matched:
            return True, reason

    return False, ""


def score_job(job: Job, scoring: dict) -> int:
    score = 0
    title_lower = job.title.lower()

    for keyword, bonus in (scoring.get("title_bonus") or {}).items():
        if keyword.lower() in title_lower:
            score += bonus

    for keyword, penalty in (scoring.get("title_penalty") or {}).items():
        if keyword.lower() in title_lower:
            score -= penalty

    description_lower = (getattr(job, "description_text", "") or "").lower()
    if description_lower:
        for keyword, bonus in (scoring.get("description_bonus") or {}).items():
            if keyword.lower() in description_lower:
                score += bonus
        for keyword, penalty in (scoring.get("description_penalty") or {}).items():
            if keyword.lower() in description_lower:
                score -= penalty

    score += (scoring.get("tier_bonus") or {}).get(job.company_tier, 0)

    company_bonus = scoring.get("company_bonus") or {}
    company_lower = (job.company or "").lower()
    for name, bonus in company_bonus.items():
        if name.lower() in company_lower:
            score += bonus
            break

    # Freshness bonus - newer postings score higher
    if job.posted_at:
        now_utc = datetime.now(timezone.utc)
        if job.posted_at.tzinfo:
            posted_utc = job.posted_at.astimezone(timezone.utc)
        else:
            # Treat naive datetimes as UTC
            posted_utc = job.posted_at.replace(tzinfo=timezone.utc)
        age_days = (now_utc - posted_utc).days
        if age_days < 3:
            score += 3
        elif age_days < 7:
            score += 1

    return score


def normalize_title(title: str) -> str:
    """Collapse regional variants by taking the title chunk before ' | ' or ' - '."""
    first = re.split(r"\s*\|\s*|\s+-\s+", title, maxsplit=1)[0]
    return first.strip().lower()


def iter_company_entries(companies: dict):
    """Yield (tier_name, entry) for every company in tiers we know how to scrape."""
    for tier_name, entries in companies.items():
        if tier_name == "manual_check":
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            yield tier_name, entry


def fetch_all_jobs(companies: dict, verify_mode: bool = False) -> list[Job]:
    all_jobs: list[Job] = []
    for tier_name, entry in iter_company_entries(companies):
        ats = entry.get("ats")
        slug = entry.get("slug")
        name = entry.get("name", slug)
        # Optional per-company timeout override. Falls back to the scraper's
        # class-level default when absent. Use for slow ATS tenants that need
        # longer than the 10s default (e.g., some Workday-adjacent or SmartRecruiters tenants).
        timeout = entry.get("timeout")
        scraper = SCRAPER_REGISTRY.get(ats)
        if not scraper:
            logging.warning("%s: no scraper for ATS '%s'", name, ats)
            continue
        jobs = scraper.fetch_jobs(slug, name, tier_name, timeout=timeout)
        if verify_mode:
            status = "OK " if jobs else "EMPTY/404"
            print(f"  [{status}] {name:28s} {ats:11s} slug={slug:22s} -> {len(jobs):3d} jobs")
        all_jobs.extend(jobs)
    return all_jobs


def write_digest(results: list[tuple[int, Job, bool, str]], stats: dict, digest_dir: Path) -> Path:
    """
    Write a markdown digest.

    results: list of (score, job, is_match, anti_target_reason).
        is_match=True and anti_target_reason="" -> matched job
        is_match=False and anti_target_reason set -> anti-target
        is_match=False and anti_target_reason="" -> silently filtered (not in digest)
    """
    today_str = datetime.now(timezone.utc).date().isoformat()
    digest_dir.mkdir(parents=True, exist_ok=True)
    out_path = digest_dir / f"digest-{today_str}.md"

    matches = sorted([x for x in results if x[2]], key=lambda x: -x[0])
    skipped_anti = [x for x in results if not x[2] and x[3]]

    by_tier: dict[str, list] = {}
    for item in matches:
        by_tier.setdefault(item[1].company_tier, []).append(item)

    lines: list[str] = []
    lines.append(f"# Job Discovery Digest - {today_str}")
    lines.append("")
    lines.append(f"- **Companies scanned:** {stats['companies_scanned']}")
    lines.append(f"- **Total jobs fetched:** {stats['total_fetched']}")
    if stats.get('board_fetched'):
        lines.append(f"- **Board jobs (JobSpy):** {stats['board_fetched']}")
    lines.append(f"- **New since last run:** {stats['new_jobs']}")
    lines.append(f"- **Matches (post-filter):** {len(matches)}")
    lines.append(f"- **Skipped (anti-target):** {len(skipped_anti)}")
    if stats.get('declined_filtered'):
        lines.append(f"- **Skipped (previously declined in tracker):** {stats['declined_filtered']}")
    lines.append("")

    if not matches:
        lines.append("_No new matching jobs today._")
        lines.append("")
    else:
        lines.append("---")
        lines.append("")
        # Order tiers: security vendors first (direct-hook), then SaaS, then consulting
        tier_order = ["tier_1_security_vendor", "tier_1_saas", "tier_1_consulting"]
        ordered_tiers = [t for t in tier_order if t in by_tier] + \
                        [t for t in sorted(by_tier.keys()) if t not in tier_order]
        for tier in ordered_tiers:
            lines.append(f"## {tier}")
            lines.append("")
            for score, job, _, _ in by_tier[tier]:
                source_tag = " _(via board)_" if job.source == "board" else ""
                lines.append(f"### {job.company} - {job.title}{source_tag}")
                lines.append(f"- **Location:** {job.location or 'unspecified'}")
                if job.posted_at:
                    lines.append(f"- **Posted:** {job.posted_at.date().isoformat()}")
                lines.append(f"- **Score:** {score}")
                lines.append(f"- **Apply:** {job.url}")
                lines.append("")

    if skipped_anti:
        lines.append("---")
        lines.append("")
        lines.append("## Skipped (Anti-Target Lanes)")
        lines.append("")
        reason_counts: dict[str, int] = {}
        for _, _, _, reason in skipped_anti:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {count}x {reason}")
        lines.append("")

    candidate_companies = stats.get("candidate_companies") or []
    if candidate_companies:
        lines.append("---")
        lines.append("")
        lines.append("## Candidate companies (not yet in companies.yaml)")
        lines.append("")
        lines.append(
            "Board-source companies that keep appearing with relevant matches. "
            "Consider promoting any whose ATS is detected below to the appropriate tier."
        )
        lines.append("")
        for c in candidate_companies:
            lines.append(f"### {c.get('display_name', c.get('normalized_name', 'unknown'))}")
            lines.append(f"- **Matches:** {c.get('total_matches', 0)} | **Cumulative score:** {c.get('total_score', 0)}")
            first = (c.get('first_seen') or '')[:10] or 'unknown'
            last = (c.get('last_seen') or '')[:10] or 'unknown'
            lines.append(f"- **First seen:** {first} | **Last seen:** {last}")
            if c.get('discovered_ats') and c.get('discovered_slug'):
                lines.append(f"- **ATS detected:** `{c['discovered_ats']}`, slug `{c['discovered_slug']}`")
                lines.append("  ```yaml")
                lines.append(f"  - name: {c.get('display_name', '')}")
                lines.append(f"    ats: {c['discovered_ats']}")
                lines.append(f"    slug: {c['discovered_slug']}")
                lines.append("  ```")
            else:
                lines.append(
                    "- **ATS:** not detected (Easy Apply only or custom portal); "
                    "check the company's careers page manually"
                )
            titles = c.get('sample_titles') or []
            if titles:
                lines.append(f"- **Sample roles:** {'; '.join(titles[:3])}")
            lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Job Discovery Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run scan but don't update state")
    parser.add_argument("--verify", action="store_true", help="Verify company slugs return results")
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to framework config.yaml (default: ../config.yaml relative to this file)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    framework_config = load_framework_config(args.config)
    config_dir, state_file, digest_dir = resolve_paths(framework_config)

    if framework_config:
        logging.info("Framework config active:")
        logging.info("  config_dir: %s", config_dir)
        logging.info("  state_file: %s", state_file)
        logging.info("  digest_dir: %s", digest_dir)

    required_configs = {
        "companies.yaml": "companies",
        "keywords.yaml": "keywords/scoring rules",
        "filters.yaml": "anti-target filters",
    }
    for filename, purpose in required_configs.items():
        path = config_dir / filename
        if not path.exists():
            example = config_dir / filename.replace(".yaml", ".example.yaml")
            hint = f"  cp {example} {path}" if example.exists() else f"  Create {path} (see config/ for examples)"
            print(f"ERROR: Missing {path} ({purpose})")
            print(f"  Copy the example config and customize it:\n{hint}")
            return 1

    companies = load_yaml(config_dir / "companies.yaml")
    keywords = load_yaml(config_dir / "keywords.yaml")
    filters = load_yaml(config_dir / "filters.yaml")
    state = load_state(state_file)

    total_companies = sum(1 for _ in iter_company_entries(companies))
    manual_check_count = len(companies.get("manual_check") or []) if isinstance(companies.get("manual_check"), list) else 0

    if args.verify:
        print(f"Verifying {total_companies} configured companies:")
        fetch_all_jobs(companies, verify_mode=True)
        return 0

    scan_note = f" ({manual_check_count} in manual_check, not scanned)" if manual_check_count else ""
    print(f"Scanning {total_companies} companies{scan_note}...")
    all_jobs = fetch_all_jobs(companies)
    print(f"Fetched {len(all_jobs)} total jobs from ATS scrapers")

    # JobSpy board search (LinkedIn, Indeed, etc.)
    jobspy_config = keywords.get("jobspy") or {}
    jobspy_enabled = jobspy_config.get("enabled", False)
    board_jobs: list[Job] = []
    if jobspy_enabled:
        jobspy_scraper = JobspyScraper()
        match_rules_for_search = keywords.get("match_rules") or {}
        board_jobs = jobspy_scraper.fetch_jobs_by_search(jobspy_config, match_rules_for_search)
        print(f"Fetched {len(board_jobs)} jobs from job boards (JobSpy)")
        all_jobs.extend(board_jobs)
    else:
        logging.info("JobSpy disabled - set jobspy.enabled: true in keywords.yaml to activate")

    # Cross-source dedup (removes board duplicates when ATS version exists)
    if board_jobs:
        pre_dedup = len(all_jobs)
        all_jobs = deduplicate_cross_source(all_jobs)
        removed = pre_dedup - len(all_jobs)
        if removed:
            print(f"Cross-source dedup removed {removed} duplicate(s)")

    print(f"Total unique jobs: {len(all_jobs)}")

    # Migrate legacy list-based seen_ids to timestamped dict format
    raw_seen = state.get("seen_ids") or {}
    if isinstance(raw_seen, list):
        now_iso = datetime.now(timezone.utc).isoformat()
        raw_seen = {sid: now_iso for sid in raw_seen}
        state["seen_ids"] = raw_seen

    seen_ids = set(raw_seen.keys())
    new_jobs = [j for j in all_jobs if j.id not in seen_ids]
    print(f"New since last run: {len(new_jobs)}")

    # Filter out URLs already marked withdrawn / rejected / declined_anti_target
    # in the applications tracker. Survives state-file resets.
    apps_raw = framework_config.get("applications_file")
    apps_file = Path(apps_raw).expanduser() if apps_raw else None
    declined_urls = load_declined_urls(apps_file)
    declined_filtered = 0
    if declined_urls:
        before = len(new_jobs)
        new_jobs = [j for j in new_jobs if getattr(j, "url", None) not in declined_urls]
        declined_filtered = before - len(new_jobs)
        if declined_filtered:
            print(f"Filtered {declined_filtered} previously declined/withdrawn role(s) from tracker")

    match_rules = keywords.get("match_rules") or {}
    scoring = keywords.get("scoring") or {}

    results: list[tuple[int, Job, bool, str]] = []
    for job in new_jobs:
        is_anti, reason = is_anti_target(job, filters)
        if is_anti:
            results.append((0, job, False, reason))
            continue
        if not match_title(job, match_rules):
            continue
        if not match_location(job, match_rules):
            continue
        score = score_job(job, scoring)
        results.append((score, job, True, ""))

    # Collapse regional variants: for matched rows, keep highest-scored per (company, normalized_title)
    best: dict = {}
    deduped: list = []
    for tup in results:
        _score, _job, _matched, _ = tup
        if not _matched:
            deduped.append(tup)
            continue
        key = ((_job.company or "").lower(), normalize_title(_job.title))
        if key not in best or best[key][0] < _score:
            best[key] = tup
    deduped.extend(best.values())
    results = deduped

    # Candidate company tracking: aggregate board-source matches over time and
    # surface promotion suggestions with auto-detected ATS slugs. Load always so
    # the digest can render current promotable state even on dry runs; update
    # and persist only on non-dry runs.
    candidate_file = state_file.parent / "candidate-companies.json"
    candidates_state = candidates_mod.load_candidates(candidate_file)
    if not args.dry_run:
        now_iso_candidates = datetime.now(timezone.utc).isoformat()
        for score_val, job_val, is_match, _ in results:
            if not is_match or job_val.source != "board":
                continue
            if candidates_mod.is_known_company(job_val.company, companies):
                continue
            candidates_mod.update_candidate(candidates_state, job_val, score_val, now_iso_candidates)
        candidates_mod.save_candidates(candidates_state, candidate_file)

    stats = {
        "companies_scanned": total_companies,
        "total_fetched": len(all_jobs),
        "board_fetched": len(board_jobs),
        "new_jobs": len(new_jobs),
        "declined_filtered": declined_filtered,
        "candidate_companies": candidates_mod.promotable_candidates(candidates_state),
    }
    out_path = write_digest(results, stats, digest_dir)
    print(f"Digest written to: {out_path}")

    matches_count = sum(1 for _, _, m, _ in results if m)
    skipped_count = sum(1 for _, _, m, r in results if not m and r)
    print(f"Matches surfaced: {matches_count} | Anti-target skipped: {skipped_count}")

    if not args.dry_run:
        now_iso = datetime.now(timezone.utc).isoformat()
        new_seen = {j.id: now_iso for j in all_jobs if j.id not in raw_seen}
        raw_seen.update(new_seen)
        state["seen_ids"] = raw_seen
        save_state(state, state_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())

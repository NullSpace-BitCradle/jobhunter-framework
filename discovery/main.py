#!/usr/bin/env python3
"""
Job Discovery Agent - scans target company ATS APIs for new relevant roles.

Usage:
    python main.py                    # Full scan, writes digest + updates state
    python main.py --dry-run          # Scan without updating state
    python main.py --verify           # Verify configured company slugs return results
    python main.py --ingest <file>    # Process manually-supplied postings through the filter pipeline
    python main.py -v                 # Verbose logging
"""
import argparse
import fcntl
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Phoenix")

import yaml

from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.ashby import AshbyScraper
from scrapers.smartrecruiters import SmartRecruitersScraper
from scrapers.workable import WorkableScraper
from scrapers.jobspy import JobspyScraper
from dedup import deduplicate_cross_source, normalize_company
from url_utils import normalize_url
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

# (Company, normalized-title) pairs declined this many times in the applications
# tracker get auto-filtered at discovery time. Each new posting of a repeated
# decline gets a fresh URL (gh_jid increments), so URL-based blocking misses
# them. Threshold 3 means: first three declines accumulate signal manually,
# fourth and onward are auto-skipped. Lowering risks false positives; raising
# means more wasted /apply triage cycles for known-dead patterns.
REPEAT_DECLINE_THRESHOLD = 3

# Sidecar file (next to seen-jobs.json) where JobSpy-fetched LinkedIn / board
# JD body text is persisted. /apply reads from this cache before falling back
# to WebFetch, avoiding LinkedIn 403s on URLs the scanner has already pulled.
BOARD_DESCRIPTIONS_FILENAME = "board-descriptions.json"

# Matches a tracker row where the first cell is an ISO date (YYYY-MM-DD). Robust
# against year boundaries and accidental text that happens to start with "| 20".
_TRACKER_DATE_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\b")

# Terminal statuses used as the durable discovery skip-list. The canonical
# spelling is `withdrew` (matching README, CLAUDE.md, applications.template.md,
# /decline, and every row set by slash commands). `withdrawn` is tolerated as
# a historical alias so tracker files hand-edited before the 4-section
# restructure don't silently drop from the skip-list.
_TRACKER_TERMINAL_STATUSES = {"withdrew", "withdrawn", "rejected", "declined_anti_target"}

# Valid tracker statuses for reference and filtering. Ordered by state-machine
# position (first = earliest, last = terminal). Both `withdrew` (canonical) and
# `withdrawn` (legacy alias) are accepted.
_TRACKER_ALL_STATUSES = {
    "queued", "applied", "ack", "screen", "interview", "offer",
    "rejected", "withdrew", "withdrawn", "declined_anti_target",
}


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


def load_tracker_urls(
    applications_file: Path | None,
    statuses: set[str] | None = None,
) -> set[str]:
    """Return normalized URLs from the applications tracker.

    If statuses is None, returns URLs from rows in ANY status. If statuses is
    a set, returns only URLs in rows whose status matches. Always normalizes
    URLs so tracking-param drift doesn't defeat equality checks.

    Uses header-based column lookup so tracker schema changes don't silently
    break the parser.
    """
    if not applications_file or not applications_file.exists():
        return set()
    urls: set[str] = set()
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
            if statuses is not None and status not in statuses:
                continue
            url = cells[url_idx]
            # Strip markdown auto-link wrappers (<url>) and link syntax ([text](url))
            url = url.strip().strip("<>")
            if url.startswith("[") and "](" in url and url.endswith(")"):
                url = url.split("](", 1)[1].rstrip(")")
            normalized = normalize_url(url)
            if normalized:
                urls.add(normalized)
    except Exception as e:
        logging.warning("Could not parse applications tracker %s: %s", applications_file, e)
    return urls


def load_declined_urls(applications_file: Path | None) -> set[str]:
    """URLs of rows with terminal status (skip-list for discovery).

    Thin wrapper over load_tracker_urls. The state file (seen-jobs.json) gets
    pruned periodically, but the applications tracker is a durable record;
    terminal rows (rejected/withdrew/declined_anti_target) should never
    re-surface in discovery.
    """
    return load_tracker_urls(applications_file, statuses=_TRACKER_TERMINAL_STATUSES)


def load_tracker_identity_keys(
    applications_file: Path | None,
    statuses: set[str] | None = None,
) -> tuple[set[str], set[tuple[str, str]]]:
    """Return (normalized_url_set, (normalized_company, lowercased_title) set) from tracker.

    Backfill needs a secondary match key because historical digests (pre URL
    canonicalization) record the LinkedIn URL while the tracker may hold the
    ATS URL for the same posting. Pure URL matching misses these; adding
    (company, title) catches them.
    """
    if not applications_file or not applications_file.exists():
        return set(), set()
    urls: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    try:
        lines = applications_file.read_text().splitlines()
        header = _parse_tracker_header(lines)
        if not header:
            return set(), set()
        status_idx = header.get("status")
        url_idx = header.get("url")
        company_idx = header.get("company")
        role_idx = header.get("role")
        if any(i is None for i in (status_idx, url_idx, company_idx, role_idx)):
            return set(), set()
        for line in lines:
            if not _TRACKER_DATE_ROW.match(line):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) <= max(status_idx, url_idx, company_idx, role_idx):
                continue
            status = cells[status_idx].lower()
            if statuses is not None and status not in statuses:
                continue
            # URL
            url = cells[url_idx].strip().strip("<>")
            if url.startswith("[") and "](" in url and url.endswith(")"):
                url = url.split("](", 1)[1].rstrip(")")
            normalized = normalize_url(url)
            if normalized:
                urls.add(normalized)
            # Company + Role pair
            company = cells[company_idx]
            role = cells[role_idx]
            if company and role:
                pairs.add((normalize_company(company), role.lower().strip()))
    except Exception as e:
        logging.warning("Could not parse applications tracker %s: %s", applications_file, e)
    return urls, pairs


def load_repeat_decline_pairs(
    applications_file: Path | None,
    threshold: int = REPEAT_DECLINE_THRESHOLD,
) -> set[tuple[str, str]]:
    """Return (normalized_company, normalized_title) pairs declined as anti-target
    `threshold` or more times in the applications tracker.

    Same-company-same-role postings frequently re-appear with a fresh URL
    (gh_jid increments at the source ATS) which defeats URL-based skip logic.
    Counting tracker rows by (company, title) and surfacing the repeat-offender
    pairs lets discovery auto-skip the next posting without waiting for /apply
    triage to re-derive the same anti-target reasoning. Fivetran Senior Sales
    Engineer is the canonical motivating case (4 declines in 5 days, identical
    SE-function-mismatch reasoning each time).

    The threshold (default 3) balances signal-gathering against noise: first
    three declines accumulate manually; the fourth is the cost the heuristic
    saves. Lower thresholds risk filtering away genuine retries; higher
    thresholds delay the savings.
    """
    if not applications_file or not applications_file.exists():
        return set()
    counts: dict[tuple[str, str], int] = {}
    try:
        lines = applications_file.read_text().splitlines()
        header = _parse_tracker_header(lines)
        if not header:
            return set()
        status_idx = header.get("status")
        company_idx = header.get("company")
        role_idx = header.get("role")
        if any(i is None for i in (status_idx, company_idx, role_idx)):
            return set()
        for line in lines:
            if not _TRACKER_DATE_ROW.match(line):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) <= max(status_idx, company_idx, role_idx):
                continue
            if cells[status_idx].lower() != "declined_anti_target":
                continue
            company = cells[company_idx]
            role = cells[role_idx]
            if not company or not role:
                continue
            key = (normalize_company(company), normalize_title(role))
            counts[key] = counts.get(key, 0) + 1
    except Exception as e:
        logging.warning("Could not parse repeat declines from %s: %s", applications_file, e)
    return {pair for pair, count in counts.items() if count >= threshold}


def persist_board_descriptions(board_jobs: list[Job], path: Path) -> None:
    """Write a URL-keyed JSON map of board-source JD bodies so /apply can read
    locally instead of re-WebFetching LinkedIn URLs (which often 403).

    JobSpy's `linkedin_fetch_description=true` mode pulls full JD text during
    discovery, but the data is currently discarded after digest write. Today's
    batched /apply run hit 10 LinkedIn URLs in sequence; persisting at scan
    time eliminates those redundant fetches and removes the LinkedIn rate-limit
    risk from the apply-time critical path.

    Stores `{normalized_url: {company, title, location, description, posted_at,
    saved_at}}`. Prunes entries older than SEEN_ID_MAX_AGE_DAYS to bound size.
    Atomic via fcntl flock to match save_state's locking discipline.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=SEEN_ID_MAX_AGE_DAYS)).isoformat()

    existing: dict = {}
    if path.exists():
        try:
            with path.open() as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                existing = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logging.warning("Corrupt board-descriptions cache at %s: %s - starting fresh", path, e)
            existing = {}

    if not isinstance(existing, dict):
        existing = {}

    # Prune stale entries before adding new ones.
    existing = {
        url: data for url, data in existing.items()
        if isinstance(data, dict) and data.get("saved_at", "") >= cutoff
    }

    now_iso = now.isoformat()
    for job in board_jobs:
        if not job.url or not job.description_text:
            continue
        url_key = normalize_url(job.url)
        if not url_key:
            continue
        existing[url_key] = {
            "company": job.company,
            "title": job.title,
            "location": job.location,
            "description": job.description_text,
            "posted_at": job.posted_at.isoformat() if job.posted_at else None,
            "saved_at": now_iso,
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(existing, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)


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


def classify_work_mode(job: Job) -> str:
    """Classify a job's work mode as 'remote', 'hybrid', or 'on_site'.

    Precedence: 'hybrid' in location string wins first (so 'Remote - Hybrid'
    is treated as hybrid, the less-generous classification). Then remote flag
    or 'remote' in location. Else on_site.
    """
    loc_lower = (job.location or "").lower()
    if "hybrid" in loc_lower:
        return "hybrid"
    if job.remote is True or "remote" in loc_lower:
        return "remote"
    return "on_site"


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

    # Work-mode preference: remote > hybrid > on_site. Applied only when the
    # scoring config declares location_mode_bonus so pre-existing configs
    # without this section keep their current scoring behavior unchanged.
    mode_bonus = scoring.get("location_mode_bonus") or {}
    if mode_bonus:
        score += mode_bonus.get(classify_work_mode(job), 0)

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


def explain_match_rejection(job: Job, rules: dict) -> str | None:
    """Return a short string describing why a job fails match_title/match_location,
    or None if it would pass. Used by ingest mode to explain filtered rows -
    in a scan, these get silently dropped; in an ingest, the user explicitly
    submitted each URL and wants to know why anything was rejected.
    """
    title_lower = (job.title or "").lower()
    if not title_lower:
        return "empty title"

    tier_kw = rules.get("tier_keywords_in_title", [])
    if tier_kw and not any(k in title_lower for k in tier_kw):
        return f"title missing a tier keyword ({', '.join(tier_kw[:5])}{'...' if len(tier_kw) > 5 else ''})"

    domain_kw = rules.get("domain_keywords_in_title", [])
    if domain_kw and not any(k in title_lower for k in domain_kw):
        return f"title missing a domain keyword ({', '.join(domain_kw[:5])}{'...' if len(domain_kw) > 5 else ''})"

    exclusions = rules.get("title_exclusions", [])
    for ex in exclusions:
        if ex in title_lower:
            return f"title contains excluded keyword '{ex}'"

    if rules.get("require_remote", False):
        loc_lower = (job.location or "").lower()
        is_remote = job.remote is True or "remote" in loc_lower
        if not is_remote:
            local_pattern = rules.get("local_location_regex")
            if local_pattern:
                try:
                    if not re.search(local_pattern, loc_lower, re.IGNORECASE):
                        return "not remote and location doesn't match local allow regex"
                    # Local on-site allow-regex matched; skip the US-remote check
                    # since match_location also exits True at this point.
                    return None
                except re.error:
                    pass
            else:
                return "not remote (and require_remote is true)"

        pattern = rules.get("required_location_regex")
        if pattern:
            try:
                if not re.search(pattern, loc_lower, re.IGNORECASE):
                    return "location doesn't match required_location_regex (not US-remote?)"
            except re.error:
                pass

    return None


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
    today_str = datetime.now(LOCAL_TZ).date().isoformat()
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
    if stats.get('repeat_decline_filtered'):
        lines.append(
            f"- **Skipped (Company+Role declined {REPEAT_DECLINE_THRESHOLD}+ times):** "
            f"{stats['repeat_decline_filtered']}"
        )
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


def _build_manual_jobs(postings: list[dict]) -> tuple[list[Job], list[dict]]:
    """Convert raw ingest JSON entries into Job objects. Returns (jobs, skipped).

    skipped is a list of {'entry': dict, 'reason': str} for entries that couldn't
    be parsed (missing required fields, bad dates, etc.).
    """
    jobs: list[Job] = []
    skipped: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for p in postings:
        if not isinstance(p, dict):
            skipped.append({"entry": p, "reason": "not a JSON object"})
            continue
        url = (p.get("url") or "").strip()
        title = (p.get("title") or "").strip()
        company = (p.get("company") or "").strip()
        if not url:
            skipped.append({"entry": p, "reason": "missing url"})
            continue
        if not title:
            skipped.append({"entry": p, "reason": "missing title"})
            continue
        if not company:
            skipped.append({"entry": p, "reason": "missing company"})
            continue

        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        posted_at: datetime | None = None
        raw_date = p.get("posted_at")
        if raw_date:
            try:
                parsed = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                posted_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass  # ignore unparseable dates rather than skip the whole entry

        location = (p.get("location") or "").strip()
        loc_lower = location.lower()
        remote: bool | None = None
        if p.get("remote") is not None:
            remote = bool(p["remote"])
        elif "remote" in loc_lower:
            remote = True

        jobs.append(Job(
            id=f"manual:{url_hash}",
            company=company,
            company_slug=company.lower().replace(" ", "-"),
            company_tier="manual_ingest",
            title=title,
            location=location,
            remote=remote,
            url=url,
            posted_at=posted_at,
            description_text=(p.get("description") or "").strip(),
            source="manual",
            raw={"ingested_at": now},
        ))
    return jobs, skipped


def write_ingest_digest(
    results: list[tuple[int, Job, bool, str, str]],
    skipped: list[dict],
    declined_hits: list[Job],
    new_candidates: list[dict],
    digest_dir: Path,
) -> Path:
    """Write a manual-ingest digest with richer explanation than write_digest.

    results: list of (score, job, is_match, anti_target_reason, filter_reason).
        is_match=True                        -> matched job (filter_reason="")
        is_match=False, anti_target_reason!="" -> anti-target hit
        is_match=False, filter_reason!=""     -> filtered by match_rules; reason explains which
    """
    now = datetime.now(LOCAL_TZ)
    today_str = now.date().isoformat()
    stamp = now.strftime("%H%M%S")
    digest_dir.mkdir(parents=True, exist_ok=True)
    out_path = digest_dir / f"ingest-{today_str}-{stamp}.md"

    matches = sorted([x for x in results if x[2]], key=lambda x: -x[0])
    anti_hits = [x for x in results if not x[2] and x[3]]
    filtered = [x for x in results if not x[2] and not x[3] and x[4]]

    lines: list[str] = []
    lines.append(f"# Manual Ingest Digest - {today_str} {stamp[:2]}:{stamp[2:4]}:{stamp[4:6]}")
    lines.append("")
    lines.append(f"- **Postings submitted:** {len(results) + len(skipped)}")
    lines.append(f"- **Parsed and processed:** {len(results)}")
    if skipped:
        lines.append(f"- **Skipped (malformed entries):** {len(skipped)}")
    lines.append(f"- **Matches (post-filter):** {len(matches)}")
    lines.append(f"- **Anti-target hits:** {len(anti_hits)}")
    lines.append(f"- **Filtered (no match):** {len(filtered)}")
    if declined_hits:
        lines.append(f"- **Previously declined in tracker:** {len(declined_hits)}")
    if new_candidates:
        lines.append(f"- **New candidate companies:** {len(new_candidates)}")
    lines.append("")

    if declined_hits:
        lines.append("---")
        lines.append("")
        lines.append("## Previously declined in tracker")
        lines.append("")
        lines.append(
            "These URLs match rows in your applications tracker with terminal status "
            "(rejected, withdrew, declined_anti_target). Still processed below - surfaced here so you know."
        )
        lines.append("")
        for j in declined_hits:
            lines.append(f"- {j.company} - {j.title}")
            lines.append(f"  - {j.url}")
        lines.append("")

    if matches:
        lines.append("---")
        lines.append("")
        lines.append("## Matches")
        lines.append("")
        for score, job, _, _, _ in matches:
            lines.append(f"### {job.company} - {job.title}")
            lines.append(f"- **Score:** {score}")
            if job.location:
                lines.append(f"- **Location:** {job.location}")
            if job.posted_at:
                lines.append(f"- **Posted:** {job.posted_at.date().isoformat()}")
            lines.append(f"- **URL:** {job.url}")
            lines.append("")

    if anti_hits:
        lines.append("---")
        lines.append("")
        lines.append("## Anti-target hits")
        lines.append("")
        lines.append("These match patterns in your MCD's Anti-Target Lanes. Not recommended to tailor for these.")
        lines.append("")
        for _, job, _, reason, _ in anti_hits:
            lines.append(f"### {job.company} - {job.title}")
            lines.append(f"- **Reason:** {reason}")
            lines.append(f"- **URL:** {job.url}")
            lines.append("")

    if filtered:
        lines.append("---")
        lines.append("")
        lines.append("## Filtered (no match)")
        lines.append("")
        lines.append(
            "These postings did not match your keyword/location rules. "
            "Submitted for analysis, rejected by the filter. Useful for calibration."
        )
        lines.append("")
        for _, job, _, _, filter_reason in filtered:
            lines.append(f"### {job.company} - {job.title}")
            lines.append(f"- **Rejected by:** {filter_reason}")
            if job.location:
                lines.append(f"- **Location:** {job.location}")
            lines.append(f"- **URL:** {job.url}")
            lines.append("")

    if new_candidates:
        lines.append("---")
        lines.append("")
        lines.append("## New candidate companies")
        lines.append("")
        lines.append(
            "Manual-ingest rows from companies not in companies.yaml. Tracked for future "
            "promotion; full candidate list appears in the daily discovery digest."
        )
        lines.append("")
        for c in new_candidates:
            lines.append(f"- **{c.get('display_name', '')}**"
                         + (f" (ATS detected: `{c['discovered_ats']}` slug `{c['discovered_slug']}`)"
                            if c.get('discovered_ats') else ""))
        lines.append("")

    if skipped:
        lines.append("---")
        lines.append("")
        lines.append("## Skipped (malformed entries)")
        lines.append("")
        for s in skipped:
            entry = s.get("entry")
            url = ""
            if isinstance(entry, dict):
                url = entry.get("url") or ""
            reason = s.get("reason", "unknown")
            lines.append(f"- {reason}{' - ' + url if url else ''}")
        lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def run_ingest(
    postings_path: Path,
    config_dir: Path,
    state_file: Path,
    digest_dir: Path,
    framework_config: dict,
    dry_run: bool = False,
) -> int:
    """Process manually-supplied postings through the discovery pipeline.

    Reads JSON array from postings_path, runs each posting through the same
    anti-target / match / score / candidate-tracking pipeline as a scan,
    writes a timestamped ingest digest, and updates candidate state.

    Does NOT touch seen-jobs.json - the user may want to re-ingest a URL if
    the JD changed, and manual ingest should never silently filter on repeat.
    """
    try:
        raw = json.loads(postings_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: Could not read postings file {postings_path}: {e}")
        return 1
    if not isinstance(raw, list):
        print("ERROR: Postings file must be a JSON array of objects.")
        return 1

    required = {"companies.yaml": "companies", "keywords.yaml": "keywords/scoring rules",
                "filters.yaml": "anti-target filters"}
    for filename, purpose in required.items():
        path = config_dir / filename
        if not path.exists():
            print(f"ERROR: Missing {path} ({purpose})")
            return 1

    companies = load_yaml(config_dir / "companies.yaml")
    keywords = load_yaml(config_dir / "keywords.yaml")
    filters = load_yaml(config_dir / "filters.yaml")
    match_rules = keywords.get("match_rules") or {}
    scoring = keywords.get("scoring") or {}

    jobs, skipped = _build_manual_jobs(raw)
    if not jobs and not skipped:
        print("No postings to process.")
        return 0
    print(f"Ingesting {len(jobs)} posting(s) ({len(skipped)} skipped as malformed)")

    # Previously-declined warning (no skip, just surface)
    apps_raw = framework_config.get("applications_file")
    apps_file = Path(apps_raw).expanduser() if apps_raw else None
    declined_urls = load_declined_urls(apps_file)
    declined_hits = [
        j for j in jobs
        if normalize_url(j.url or "") in declined_urls
    ]

    # Filter + score + track (results tuple: score, job, is_match, anti_reason, filter_reason)
    results: list[tuple[int, Job, bool, str, str]] = []
    for job in jobs:
        is_anti, anti_reason = is_anti_target(job, filters)
        if is_anti:
            results.append((0, job, False, anti_reason, ""))
            continue
        rejection = explain_match_rejection(job, match_rules)
        if rejection:
            results.append((0, job, False, "", rejection))
            continue
        score = score_job(job, scoring)
        results.append((score, job, True, "", ""))

    # Candidate tracking - same treatment as board-source jobs
    candidate_file = state_file.parent / "candidate-companies.json"
    candidates_state = candidates_mod.load_candidates(candidate_file)
    new_candidate_entries: list[dict] = []
    if not dry_run:
        now_iso = datetime.now(timezone.utc).isoformat()
        for score, job, is_match, _, _ in results:
            if not is_match:
                continue
            if candidates_mod.is_known_company(job.company, companies):
                continue
            key = normalize_company(job.company or "")
            # Track whether this was a pre-existing candidate or a new one
            existed = key in candidates_state.get("candidates", {})
            candidates_mod.update_candidate(candidates_state, job, score, now_iso)
            if not existed:
                entry = candidates_state["candidates"].get(key)
                if entry:
                    new_candidate_entries.append(entry)
        candidates_mod.save_candidates(candidates_state, candidate_file)

    out_path = write_ingest_digest(
        results, skipped, declined_hits, new_candidate_entries, digest_dir,
    )

    matches_count = sum(1 for _, _, m, _, _ in results if m)
    anti_count = sum(1 for _, _, m, r, _ in results if not m and r)
    filtered_count = sum(1 for _, _, m, r, f in results if not m and not r and f)
    print(f"Matches: {matches_count} | Anti-target: {anti_count} | "
          f"Filtered: {filtered_count} | Prev-declined: {len(declined_hits)}")
    print(f"Digest written: {out_path}")
    return 0


def parse_digest_matches(path: Path, digest_date: date) -> list[dict]:
    """Extract matched jobs from a `digest-YYYY-MM-DD.md` file.

    Returns list of dicts with keys: company, title, score, url, location,
    posted_at (date or None), tier, digest_date, source ('ats' or 'board').
    Jobs in the 'Skipped (Anti-Target Lanes)' and 'Candidate companies'
    sections are NOT captured - only actual matches from tier sections.
    """
    if not path.exists():
        return []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []

    results: list[dict] = []
    current_tier: str | None = None
    current_job: dict | None = None

    def finalize(job: dict | None) -> None:
        if job and job.get("url") and job.get("score") is not None:
            results.append(job)

    for line in lines:
        h2 = re.match(r"^##\s+(.+?)\s*$", line)
        if h2:
            finalize(current_job)
            current_job = None
            name = h2.group(1)
            # Only tier sections or board_match contain actual matches
            if name.startswith("tier_") or name == "board_match":
                current_tier = name
            else:
                current_tier = None
            continue

        if not current_tier:
            continue

        h3 = re.match(r"^###\s+(.+?)(\s+_\(via board\)_)?\s*$", line)
        if h3:
            finalize(current_job)
            header = h3.group(1).strip()
            source_tag = "board" if h3.group(2) else "ats"
            # Split on first hyphen, em-dash (U+2014), or en-dash (U+2013)
            # surrounded by whitespace. Covers historical digests from before
            # the dash cleanup that used em-dash separators in headers.
            parts = re.split(r"\s+[-\u2014\u2013]\s+", header, maxsplit=1)
            if len(parts) == 2:
                company, title = parts[0].strip(), parts[1].strip()
            else:
                company, title = "", header
            current_job = {
                "company": company, "title": title,
                "score": None, "url": "", "location": "",
                "posted_at": None, "tier": current_tier,
                "digest_date": digest_date, "source": source_tag,
            }
            continue

        if current_job is None:
            continue

        m = re.match(r"^- \*\*Score:\*\*\s+(-?\d+)", line)
        if m:
            current_job["score"] = int(m.group(1))
            continue
        m = re.match(r"^- \*\*Location:\*\*\s+(.+)$", line)
        if m:
            current_job["location"] = m.group(1).strip()
            continue
        m = re.match(r"^- \*\*Posted:\*\*\s+(\d{4}-\d{2}-\d{2})$", line)
        if m:
            try:
                current_job["posted_at"] = datetime.fromisoformat(m.group(1)).date()
            except ValueError:
                pass
            continue
        m = re.match(r"^- \*\*Apply:\*\*\s+(.+)$", line)
        if m:
            current_job["url"] = m.group(1).strip().strip("<>")
            continue

    finalize(current_job)
    return results


def write_backfill_digest(
    candidates: list[dict],
    total_missed: int,
    digests_scanned: int,
    days: int,
    limit: int,
    min_score: int | None,
    max_score: int | None,
    digest_dir: Path,
) -> Path:
    """Render the backfill digest with aggregated sighting info per posting."""
    now = datetime.now(LOCAL_TZ)
    today_str = now.date().isoformat()
    stamp = now.strftime("%H%M%S")
    digest_dir.mkdir(parents=True, exist_ok=True)
    out_path = digest_dir / f"backfill-{today_str}-{stamp}.md"

    lines: list[str] = []
    lines.append(f"# Backfill Digest - {today_str} {stamp[:2]}:{stamp[2:4]}:{stamp[4:6]}")
    lines.append("")
    window = f"last {days} day(s)" if days > 0 else "all time"
    lines.append(f"- **Digests scanned:** {digests_scanned} ({window})")
    lines.append(f"- **Missed opportunities total:** {total_missed}")
    lines.append(f"- **Shown below:** {len(candidates)} (limit {limit})")
    if min_score is not None:
        lines.append(f"- **Score floor:** {min_score}")
    if max_score is not None:
        lines.append(f"- **Score ceiling:** {max_score}")
    lines.append("")
    lines.append(
        "Matched jobs surfaced in prior digests that were never logged to the "
        "applications tracker. Sorted by highest-ever score, with last-seen date as tiebreaker."
    )
    lines.append("")

    if not candidates:
        lines.append("_No backfill candidates matched the filters._")
        lines.append("")
        out_path.write_text("\n".join(lines))
        return out_path

    lines.append("---")
    lines.append("")
    for c in candidates:
        src_tag = " _(via board)_" if c.get("source") == "board" else ""
        lines.append(f"### {c['company']} - {c['title']}{src_tag}")
        appearance_detail = ""
        if c.get("appearance_count", 1) > 1:
            appearance_detail = (
                f" (seen {c['appearance_count']}x, "
                f"latest run: {c.get('latest_score', c['highest_score'])})"
            )
        lines.append(f"- **Score:** {c['highest_score']}{appearance_detail}")
        if c.get("location"):
            lines.append(f"- **Location:** {c['location']}")
        lines.append(f"- **Tier:** {c['tier']}")
        if c["first_seen_date"] == c["last_seen_date"]:
            lines.append(f"- **Seen in digest:** {c['first_seen_date'].isoformat()}")
        else:
            lines.append(
                f"- **First seen:** {c['first_seen_date'].isoformat()} | "
                f"**Last seen:** {c['last_seen_date'].isoformat()}"
            )
        lines.append(f"- **Apply:** {c['url']}")
        lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def run_backfill(
    digest_dir: Path,
    applications_file: Path | None,
    days: int = 30,
    limit: int = 30,
    min_score: int | None = None,
    max_score: int | None = None,
) -> int:
    """Scan past digests for matched jobs not yet in the tracker.

    Filters out anything present in applications.md at any status (a `queued`
    row means the user started on it; subsequent status states all mean they
    took action, so no row regardless of status should be surfaced).
    """
    if not digest_dir.exists():
        print(f"No digest directory at {digest_dir}")
        return 1

    cutoff: date | None = None
    if days > 0:
        cutoff = datetime.now(LOCAL_TZ).date() - timedelta(days=days)

    digest_files = sorted(digest_dir.glob("digest-*.md"))
    all_entries: list[dict] = []
    digests_scanned = 0
    for path in digest_files:
        m = re.match(r"^digest-(\d{4}-\d{2}-\d{2})\.md$", path.name)
        if not m:
            continue
        try:
            digest_date = datetime.fromisoformat(m.group(1)).date()
        except ValueError:
            continue
        if cutoff and digest_date < cutoff:
            continue
        digests_scanned += 1
        all_entries.extend(parse_digest_matches(path, digest_date))

    if not all_entries:
        print(f"No matched jobs found in digests ({digests_scanned} file(s) scanned).")
        # Still write an empty digest so the slash command has something to report
        out_path = write_backfill_digest([], 0, digests_scanned, days, limit, min_score, max_score, digest_dir)
        print(f"Digest written: {out_path}")
        return 0

    tracker_urls, tracker_pairs = load_tracker_identity_keys(applications_file)

    # Aggregate across digests by normalized URL: keep highest score ever seen,
    # first/last seen dates, appearance count, and the most-recent metadata.
    # Exclude on both URL match AND (company, title) match - the latter catches
    # historical digests (pre canonicalization) where the digest has the
    # LinkedIn URL but the tracker has the ATS URL for the same posting.
    by_url: dict[str, dict] = {}
    for entry in all_entries:
        url_n = normalize_url(entry["url"] or "")
        if not url_n:
            continue
        if url_n in tracker_urls:
            continue
        ct_key = (
            normalize_company(entry.get("company", "")),
            (entry.get("title", "") or "").lower().strip(),
        )
        if ct_key[0] and ct_key[1] and ct_key in tracker_pairs:
            continue
        score = entry.get("score") or 0

        agg = by_url.get(url_n)
        if agg is None:
            by_url[url_n] = {
                "company": entry["company"], "title": entry["title"],
                "url": entry["url"], "location": entry.get("location", ""),
                "tier": entry["tier"], "source": entry.get("source", "ats"),
                "first_seen_date": entry["digest_date"],
                "last_seen_date": entry["digest_date"],
                "highest_score": score, "latest_score": score,
                "appearance_count": 1,
            }
        else:
            agg["appearance_count"] += 1
            if score > agg["highest_score"]:
                agg["highest_score"] = score
            if entry["digest_date"] > agg["last_seen_date"]:
                agg["last_seen_date"] = entry["digest_date"]
                agg["latest_score"] = score
                # Refresh display fields from the most-recent sighting
                agg["company"] = entry["company"]
                agg["title"] = entry["title"]
                if entry.get("location"):
                    agg["location"] = entry["location"]
            if entry["digest_date"] < agg["first_seen_date"]:
                agg["first_seen_date"] = entry["digest_date"]

    filtered = []
    for agg in by_url.values():
        score = agg["highest_score"]
        if min_score is not None and score < min_score:
            continue
        if max_score is not None and score > max_score:
            continue
        filtered.append(agg)

    filtered.sort(
        key=lambda a: (-a["highest_score"], -a["last_seen_date"].toordinal()),
    )
    candidates = filtered[:limit]

    out_path = write_backfill_digest(
        candidates, len(filtered), digests_scanned, days, limit,
        min_score, max_score, digest_dir,
    )
    print(
        f"Backfill: {len(filtered)} missed opportunity/ies across {digests_scanned} digest(s) "
        f"(last {days}d). Top {len(candidates)} written to {out_path}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Job Discovery Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run scan but don't update state")
    parser.add_argument("--verify", action="store_true", help="Verify company slugs return results")
    parser.add_argument("--ingest", type=Path, default=None,
                        help="Process manually-supplied postings from a JSON array file")
    parser.add_argument("--backfill", action="store_true",
                        help="Scan past digests for matched jobs not yet in the applications tracker")
    parser.add_argument("--days", type=int, default=30,
                        help="Backfill window in days (default 30; 0 = all time)")
    parser.add_argument("--limit", type=int, default=30,
                        help="Backfill max results shown (default 30)")
    parser.add_argument("--min-score", type=int, default=None,
                        help="Backfill: only include jobs with highest-ever score >= N")
    parser.add_argument("--max-score", type=int, default=None,
                        help="Backfill: only include jobs with highest-ever score <= N (useful for the 'cast wider net' use case)")
    parser.add_argument("--normalize-url", dest="normalize_url_cli", default=None,
                        help="Print the canonical form of a URL and exit. Used by slash commands for consistent URL comparison.")
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to framework config.yaml (default: ../config.yaml relative to this file)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    # URL normalization short-circuit - no config/state needed.
    if args.normalize_url_cli:
        print(normalize_url(args.normalize_url_cli))
        return 0

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

    # Ingest mode short-circuits the normal scan - it reads postings from JSON
    # and runs them through the same filter/score/candidate pipeline.
    if args.ingest:
        return run_ingest(
            postings_path=args.ingest,
            config_dir=config_dir,
            state_file=state_file,
            digest_dir=digest_dir,
            framework_config=framework_config,
            dry_run=args.dry_run,
        )

    # Backfill mode reads past digests and reports missed opportunities.
    if args.backfill:
        apps_raw = framework_config.get("applications_file")
        apps_file = Path(apps_raw).expanduser() if apps_raw else None
        return run_backfill(
            digest_dir=digest_dir,
            applications_file=apps_file,
            days=args.days,
            limit=args.limit,
            min_score=args.min_score,
            max_score=args.max_score,
        )

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

    # Persist board-source JD descriptions so /apply can skip WebFetch on
    # LinkedIn URLs the scanner has already pulled. Skipped on --dry-run since
    # this writes to disk.
    if board_jobs and not args.dry_run:
        persist_board_descriptions(
            board_jobs,
            state_file.parent / BOARD_DESCRIPTIONS_FILENAME,
        )

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

    # Filter out URLs already marked withdrew / rejected / declined_anti_target
    # in the applications tracker. Survives state-file resets.
    apps_raw = framework_config.get("applications_file")
    apps_file = Path(apps_raw).expanduser() if apps_raw else None
    declined_urls = load_declined_urls(apps_file)
    declined_filtered = 0
    if declined_urls:
        before = len(new_jobs)
        new_jobs = [
            j for j in new_jobs
            if normalize_url(getattr(j, "url", "") or "") not in declined_urls
        ]
        declined_filtered = before - len(new_jobs)
        if declined_filtered:
            print(f"Filtered {declined_filtered} previously declined/withdrawn role(s) from tracker")

    # Filter out (Company, normalized-title) pairs declined as anti-target
    # REPEAT_DECLINE_THRESHOLD or more times. Each new posting of the same
    # role gets a fresh URL so URL-based filtering above misses them; pair
    # matching catches repeat patterns like Fivetran Senior SE (4x in 5 days).
    repeat_pairs = load_repeat_decline_pairs(apps_file)
    repeat_decline_filtered = 0
    if repeat_pairs:
        before = len(new_jobs)
        new_jobs = [
            j for j in new_jobs
            if (normalize_company(j.company or ""), normalize_title(j.title or "")) not in repeat_pairs
        ]
        repeat_decline_filtered = before - len(new_jobs)
        if repeat_decline_filtered:
            print(
                f"Filtered {repeat_decline_filtered} repeat-decline (Company+Role declined "
                f"{REPEAT_DECLINE_THRESHOLD}+ times in tracker) job(s)"
            )

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
        "repeat_decline_filtered": repeat_decline_filtered,
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

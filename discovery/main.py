#!/usr/bin/env python3
"""
Job Discovery Agent — scans target company ATS APIs for new relevant roles.

Usage:
    python main.py                 # Full scan, writes digest + updates state
    python main.py --dry-run       # Scan without updating state
    python main.py --verify        # Verify configured company slugs return results
    python main.py -v              # Verbose logging
"""
import argparse
import json
import logging
import re
import sys
from datetime import datetime, date, timezone
from pathlib import Path

import yaml

from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.ashby import AshbyScraper
from scrapers.smartrecruiters import SmartRecruitersScraper
from scrapers.workable import WorkableScraper


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
STATE_DIR = PROJECT_ROOT / "state"
OUTPUT_DIR = PROJECT_ROOT / "output"
STATE_FILE = STATE_DIR / "seen-jobs.json"

SCRAPER_REGISTRY = {
    "greenhouse": GreenhouseScraper(),
    "lever": LeverScraper(),
    "ashby": AshbyScraper(),
    "smartrecruiters": SmartRecruitersScraper(),
    "workable": WorkableScraper(),
}


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_state() -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open() as f:
            return json.load(f)
    return {"seen_ids": [], "last_run": None}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


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

    # First: must be remote at all
    is_remote = job.remote is True or "remote" in loc_lower
    if not is_remote:
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
            # Fall through — don't block on config error

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

    score += (scoring.get("tier_bonus") or {}).get(job.company_tier, 0)

    # Freshness bonus — newer postings score higher
    if job.posted_at:
        posted_naive = job.posted_at.astimezone(timezone.utc).replace(tzinfo=None) if job.posted_at.tzinfo else job.posted_at
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        age_days = (now_naive - posted_naive).days
        if age_days < 3:
            score += 5
        elif age_days < 7:
            score += 2

    return score


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
        scraper = SCRAPER_REGISTRY.get(ats)
        if not scraper:
            logging.warning("%s: no scraper for ATS '%s'", name, ats)
            continue
        jobs = scraper.fetch_jobs(slug, name, tier_name)
        if verify_mode:
            status = "OK " if jobs else "EMPTY/404"
            print(f"  [{status}] {name:28s} {ats:11s} slug={slug:22s} → {len(jobs):3d} jobs")
        all_jobs.extend(jobs)
    return all_jobs


def write_digest(results: list[tuple[int, Job, bool, str]], stats: dict) -> Path:
    """
    Write a markdown digest.

    results: list of (score, job, is_match, anti_target_reason).
        is_match=True and anti_target_reason="" → matched job
        is_match=False and anti_target_reason set → anti-target
        is_match=False and anti_target_reason="" → silently filtered (not in digest)
    """
    today_str = date.today().isoformat()
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"digest-{today_str}.md"

    matches = sorted([x for x in results if x[2]], key=lambda x: -x[0])
    skipped_anti = [x for x in results if not x[2] and x[3]]

    by_tier: dict[str, list] = {}
    for item in matches:
        by_tier.setdefault(item[1].company_tier, []).append(item)

    lines: list[str] = []
    lines.append(f"# Job Discovery Digest — {today_str}")
    lines.append("")
    lines.append(f"- **Companies scanned:** {stats['companies_scanned']}")
    lines.append(f"- **Total jobs fetched:** {stats['total_fetched']}")
    lines.append(f"- **New since last run:** {stats['new_jobs']}")
    lines.append(f"- **Matches (post-filter):** {len(matches)}")
    lines.append(f"- **Skipped (anti-target):** {len(skipped_anti)}")
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
                lines.append(f"### {job.company} — {job.title}")
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
            lines.append(f"- {count}× {reason}")
        lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Job Discovery Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run scan but don't update state")
    parser.add_argument("--verify", action="store_true", help="Verify company slugs return results")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    companies = load_yaml(CONFIG_DIR / "companies.yaml")
    keywords = load_yaml(CONFIG_DIR / "keywords.yaml")
    filters = load_yaml(CONFIG_DIR / "filters.yaml")
    state = load_state()

    total_companies = sum(1 for _ in iter_company_entries(companies))

    if args.verify:
        print(f"Verifying {total_companies} configured companies:")
        fetch_all_jobs(companies, verify_mode=True)
        return 0

    print(f"Scanning {total_companies} companies...")
    all_jobs = fetch_all_jobs(companies)
    print(f"Fetched {len(all_jobs)} total jobs across all companies")

    seen_ids = set(state.get("seen_ids") or [])
    new_jobs = [j for j in all_jobs if j.id not in seen_ids]
    print(f"New since last run: {len(new_jobs)}")

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

    stats = {
        "companies_scanned": total_companies,
        "total_fetched": len(all_jobs),
        "new_jobs": len(new_jobs),
    }
    out_path = write_digest(results, stats)
    print(f"Digest written to: {out_path}")

    matches_count = sum(1 for _, _, m, _ in results if m)
    skipped_count = sum(1 for _, _, m, r in results if not m and r)
    print(f"Matches surfaced: {matches_count} | Anti-target skipped: {skipped_count}")

    if not args.dry_run:
        state["seen_ids"] = list(seen_ids | {j.id for j in all_jobs})
        save_state(state)

    return 0


if __name__ == "__main__":
    sys.exit(main())

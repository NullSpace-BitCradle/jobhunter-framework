"""Candidate company tracking - aggregate board-source matches over time and
surface promotion suggestions with auto-detected ATS slugs.

Board-source jobs (from LinkedIn/Indeed/Glassdoor via JobSpy) that match the
user's filters but come from companies NOT in companies.yaml are recorded here.
Over time, companies that keep appearing with high-quality matches cross a
threshold and get surfaced in the digest as promotion candidates. When the
JobSpy URL resolves to a known ATS, the slug is extracted automatically so the
suggestion includes a copy-paste-ready companies.yaml entry.
"""
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from scrapers.base import Job
from dedup import normalize_company
from fsutil import StateFileError, atomic_write_json, keep_backup, load_json_object
# Re-export detect_ats_from_url for backward compat with any caller importing
# it from candidates. url_utils is the single source of truth for ATS URL
# pattern matching; both candidate tracking and jobspy canonicalization use it.
from url_utils import detect_ats_from_url, normalize_url  # noqa: F401

logger = logging.getLogger(__name__)

# Promotion thresholds - surface a candidate in the digest when EITHER crosses
CANDIDATE_SCORE_THRESHOLD = 30   # cumulative score across all matches
CANDIDATE_MATCH_THRESHOLD = 3    # minimum match count

# Prune entries with no activity for this long (keeps the state file bounded)
CANDIDATE_MAX_AGE_DAYS = 90

# Max sample titles / urls stored per candidate (for digest display)
MAX_SAMPLE_TITLES = 5
MAX_SAMPLE_URLS = 3


def is_known_company(company: str, companies_cfg: dict) -> bool:
    """True if the normalized company name matches any entry across all tiers.

    manual_check entries count as known so we don't keep suggesting companies
    the user already evaluated and decided to handle manually.
    """
    if not company:
        return False
    target = normalize_company(company)
    if not target:
        return False
    for tier_name, entries in (companies_cfg or {}).items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            entry_name = e.get("name", "") if isinstance(e, dict) else ""
            if normalize_company(entry_name) == target:
                return True
    return False


def _default_state() -> dict:
    return {"candidates": {}, "last_update": None}


def load_candidates(path: Path) -> dict:
    """Load candidate state. Returns an empty structure if the file is missing.

    This file is a derived tally (not the seen-jobs dedup record), so an
    unreadable or wrong-shaped file does not abort the scan: a copy is kept at
    `<name>.corrupt`, a warning is logged, and tracking restarts from empty.
    """
    try:
        data = load_json_object(path)
    except StateFileError as e:
        logger.warning("Unreadable candidate state: %s - starting fresh (original kept as .corrupt)", e)
        return _default_state()
    if data is None:
        return _default_state()
    if not isinstance(data.get("candidates"), dict):
        logger.warning("Candidate state %s has no usable 'candidates' object - starting fresh", path)
        return _default_state()
    return data


def save_candidates(state: dict, path: Path) -> None:
    """Save candidate state, pruning entries past CANDIDATE_MAX_AGE_DAYS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    state["last_update"] = now.isoformat()

    cutoff = (now - timedelta(days=CANDIDATE_MAX_AGE_DAYS)).isoformat()
    candidates = state.get("candidates", {})
    state["candidates"] = {
        k: v for k, v in candidates.items()
        if v.get("last_seen", "") >= cutoff
    }

    keep_backup(path)
    atomic_write_json(path, state)


def update_candidate(state: dict, job: Job, score: int, now_iso: str) -> None:
    """Record a board-source match against its candidate-company entry.

    Updates first_seen / last_seen / total_matches / total_score, collects
    sample titles and URLs, and attempts best-effort ATS detection from
    job.url plus any supplementary URLs preserved in job.raw.
    """
    key = normalize_company(job.company or "")
    if not key:
        return

    candidates = state.setdefault("candidates", {})
    entry = candidates.get(key)
    if entry is None:
        entry = {
            "display_name": job.company,
            "normalized_name": key,
            "first_seen": now_iso,
            "last_seen": now_iso,
            "total_matches": 0,
            "total_score": 0,
            "discovered_ats": None,
            "discovered_slug": None,
            "sample_titles": [],
            "sample_urls": [],
        }
        candidates[key] = entry

    entry["last_seen"] = now_iso
    entry["total_matches"] = entry.get("total_matches", 0) + 1
    # Avoid negative scores pulling a candidate's total down
    entry["total_score"] = entry.get("total_score", 0) + max(0, score)

    if job.title:
        titles = entry.get("sample_titles", [])
        if job.title not in titles:
            titles.append(job.title)
            entry["sample_titles"] = titles[-MAX_SAMPLE_TITLES:]

    if job.url:
        urls = entry.get("sample_urls", [])
        if job.url not in urls:
            urls.append(job.url)
            entry["sample_urls"] = urls[-MAX_SAMPLE_URLS:]

    # Best-effort ATS detection. Check job.url first, then supplementary URLs
    # JobSpy stores in raw (job_url_direct, company_url, company_url_direct).
    # Stop at first hit - ATS rarely changes for a company, and first hit
    # preserves the provenance of the discovery.
    if not entry.get("discovered_ats"):
        raw = getattr(job, "raw", {}) or {}
        urls_to_try = [
            job.url,
            raw.get("job_url_direct"),
            raw.get("company_url"),
            raw.get("company_url_direct"),
        ]
        for url in urls_to_try:
            detected = detect_ats_from_url(url)
            if detected:
                entry["discovered_ats"], entry["discovered_slug"] = detected
                break


def promotable_candidates(state: dict) -> list[dict]:
    """Return candidates meeting the promotion thresholds, sorted by score desc."""
    result = []
    for entry in state.get("candidates", {}).values():
        score = entry.get("total_score", 0)
        matches = entry.get("total_matches", 0)
        if score >= CANDIDATE_SCORE_THRESHOLD or matches >= CANDIDATE_MATCH_THRESHOLD:
            result.append(entry)
    result.sort(key=lambda e: e.get("total_score", 0), reverse=True)
    return result

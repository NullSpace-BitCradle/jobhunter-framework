"""Cross-source deduplication for job postings.

When the same job appears on both a direct ATS scraper (Greenhouse, Lever, etc.)
and a board aggregator (LinkedIn via JobSpy), we want to keep only one copy.
ATS versions are preferred because they have richer descriptions and direct apply links.
"""
import re
import logging

from scrapers.base import Job

logger = logging.getLogger(__name__)

# Common suffixes to strip when normalizing company names
_COMPANY_SUFFIXES = re.compile(
    r",?\s*\b(inc\.?|llc\.?|ltd\.?|corp\.?|corporation|company|co\.?|"
    r"group|holdings|technologies|technology|software|solutions|plc|gmbh|"
    r"s\.?a\.?|ag)\b\.?",
    re.IGNORECASE,
)

# Extra whitespace / punctuation cleanup
_EXTRA_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^\w\s]")
_PARENS = re.compile(r"\(.*?\)")


def normalize_company(name: str) -> str:
    """Normalize a company name for fuzzy matching."""
    name = name.lower().strip()
    name = _COMPANY_SUFFIXES.sub("", name)
    name = _NON_ALNUM.sub("", name)
    name = _EXTRA_WS.sub(" ", name).strip()
    return name


def normalize_title(title: str) -> str:
    """Normalize a job title for fuzzy matching."""
    title = title.lower().strip()
    # Remove common filler like location hints in parens
    title = _PARENS.sub("", title)
    title = _NON_ALNUM.sub(" ", title)
    title = _EXTRA_WS.sub(" ", title).strip()
    return title


def deduplicate_cross_source(jobs: list[Job]) -> list[Job]:
    """Remove cross-source duplicates, preferring ATS over board sources.

    Two jobs are considered duplicates if their normalized company name AND
    normalized title are identical.

    Returns:
        Deduplicated list of jobs. When a duplicate pair is found, the ATS
        version is kept and the board version is dropped.
    """
    # Index: (normalized_company, normalized_title) -> Job
    seen: dict[tuple[str, str], Job] = {}
    dupes_removed = 0

    for job in jobs:
        key = (normalize_company(job.company), normalize_title(job.title))

        if key not in seen:
            seen[key] = job
        else:
            existing = seen[key]
            # Prefer ATS over board
            if existing.source == "ats" and job.source == "board":
                # Keep existing ATS version, drop board duplicate
                dupes_removed += 1
                logger.debug("Dedup: dropping board duplicate '%s' at '%s' (ATS version kept)",
                             job.title, job.company)
            elif existing.source == "board" and job.source == "ats":
                # Replace board version with ATS version
                seen[key] = job
                dupes_removed += 1
                logger.debug("Dedup: replacing board version with ATS for '%s' at '%s'",
                             job.title, job.company)
            else:
                # Same source - keep the first one (already deduped by ID within source)
                pass

    if dupes_removed:
        logger.info("Cross-source dedup removed %d duplicate(s)", dupes_removed)

    return list(seen.values())

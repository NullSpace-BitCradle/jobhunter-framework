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

# Location / work-arrangement vocabulary. Used in two places: parenthesized
# groups ("(Remote, USA)") are dropped wherever they appear, and a trailing
# " - X" / " | X" segment is dropped only when it is made up ENTIRELY of these
# words. Level markers like "(L4)" and role qualifiers like "- Detection" or
# "- Remote Access Security" are NOT in this vocabulary and are preserved.
_LOC_WORDS = (
    r"remote|hybrid|onsite|on-site|on site|usa|us|u\.s\.a?\.?|united states|"
    r"americas|emea|apac|na|east coast|west coast|north america|"
    r"est|pst|cst|mst|edt|pdt|cdt|mdt"
)

_LOC_ARRANGEMENT_PARENS = re.compile(
    r"\s*\([^)]*\b(?:" + _LOC_WORDS + r")\b[^)]*\)",
    re.IGNORECASE,
)

# A whole segment of location words joined by commas, slashes, ampersands,
# hyphens or "and": "Remote", "US Remote", "Remote, USA", "Hybrid / EMEA".
_LOC_SEGMENT = re.compile(
    r"^(?:" + _LOC_WORDS + r")(?:[\s,/&\-]+(?:and\s+)?(?:" + _LOC_WORDS + r"))*$",
    re.IGNORECASE,
)

# Title separators: spaced hyphen / en dash / em dash, or a pipe.
_TITLE_SEP = re.compile(r"\s+[-–—]\s+|\s*\|\s*")


def normalize_company(name: str) -> str:
    """Normalize a company name for fuzzy matching."""
    name = name.lower().strip()
    name = _COMPANY_SUFFIXES.sub("", name)
    name = _NON_ALNUM.sub("", name)
    name = _EXTRA_WS.sub(" ", name).strip()
    return name


def normalize_title(title: str) -> str:
    """Normalize a job title for fuzzy matching. The ONE title normalizer.

    1. Drop parenthesized groups that mention a location or work arrangement
       ("(Remote)", "(Remote, USA)"). Parens with level markers such as "(L4)"
       are kept so an L4 posting does not collapse into an L5 one.
    2. Drop trailing " - X" / " | X" segments ONLY when the whole segment is
       location / work-arrangement vocabulary ("Director, IT Security - Remote").
       "Senior Security Engineer - Detection" and "... - Cloud" stay distinct.
    3. Lowercase, replace punctuation with spaces, collapse whitespace.
    """
    text = _LOC_ARRANGEMENT_PARENS.sub("", (title or "").strip())
    parts = _TITLE_SEP.split(text)
    while len(parts) > 1 and _LOC_SEGMENT.match(parts[-1].strip()):
        parts.pop()
    text = " ".join(parts).lower()
    text = _NON_ALNUM.sub(" ", text)
    return _EXTRA_WS.sub(" ", text).strip()


def _key(job: Job) -> tuple[str, str]:
    return (normalize_company(job.company or ""), normalize_title(job.title or ""))


def deduplicate_cross_source(jobs: list[Job]) -> list[Job]:
    """Remove duplicates, preferring direct ATS postings over board listings.

    * A board job is dropped when a non-board (ATS) job has the same normalized
      company AND title: the ATS copy has the richer description and the direct
      apply link.
    * A job whose `id` already appeared is dropped (the same board posting
      comes back from the main search and from each `local_searches` pass).
    * Distinct jobs from the SAME source are all kept even when their titles
      match: "Security Engineer" in London and "Security Engineer" in Remote-US
      are different postings, and dropping the second could drop the only one
      that passes the location filter. `main` later collapses matched variants.

    Input order is preserved.
    """
    ats_keys = {_key(j) for j in jobs if j.source != "board"}
    seen_ids: set[str] = set()
    result: list[Job] = []
    dupes_removed = 0

    for job in jobs:
        if job.id in seen_ids:
            dupes_removed += 1
            continue
        if job.source == "board" and _key(job) in ats_keys:
            dupes_removed += 1
            logger.debug("Dedup: dropping board duplicate '%s' at '%s' (ATS version kept)",
                         job.title, job.company)
            continue
        seen_ids.add(job.id)
        result.append(job)

    if dupes_removed:
        logger.info("Cross-source dedup removed %d duplicate(s)", dupes_removed)

    return result

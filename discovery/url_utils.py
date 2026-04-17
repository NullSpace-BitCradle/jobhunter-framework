"""URL canonicalization and normalization.

Two jobs:

1. **Canonicalize**: pick the best URL for a job posting. LinkedIn/Indeed often
   expose an "external apply" URL (`job_url_direct`) that resolves to the
   company's real ATS (Greenhouse, Lever, etc.). The ATS URL is more stable
   than the aggregator URL, carries the real slug, and matches what the
   company sends in ack/interview emails. Prefer ATS URLs whenever present.

2. **Normalize**: strip tracking query parameters (utm_*, trk, refId, etc.),
   drop fragments, lowercase scheme + host, strip trailing slash. The same
   posting shared across sessions often arrives with different tracking
   params appended; normalizing lets the tracker skip-list, candidate state,
   and dedup logic compare apples to apples.

The two belong together because write-time canonicalization (jobspy, /apply)
and comparison-time normalization (load_declined_urls, candidate tracking)
need to use the same function, keyed by the same ATS pattern list.
"""
import re
from typing import Iterable, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


# ---------------------------------------------------------------------------
# ATS URL patterns - extract (ats_name, slug) from URLs on these platforms.
# Order matters: more-specific patterns (api.*) come before general (jobs.*).
# ---------------------------------------------------------------------------

_ATS_URL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Greenhouse: boards.greenhouse.io/foo, job-boards.greenhouse.io/foo,
    # boards-api.greenhouse.io/v1/boards/foo
    (re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v\d+/boards/)?([a-zA-Z0-9_.-]+?)(?:/|$)"), "greenhouse"),
    # Lever
    (re.compile(r"api\.lever\.co/v\d+/postings/([a-zA-Z0-9_.-]+?)(?:/|$)"), "lever"),
    (re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_.-]+?)(?:/|$)"), "lever"),
    # Ashby
    (re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_.-]+?)(?:/|$)"), "ashby"),
    (re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+?)(?:/|$)"), "ashby"),
    # SmartRecruiters
    (re.compile(r"api\.smartrecruiters\.com/v\d+/companies/([a-zA-Z0-9_.-]+?)(?:/|$)"), "smartrecruiters"),
    (re.compile(r"jobs\.smartrecruiters\.com/([a-zA-Z0-9_.-]+?)(?:/|$)"), "smartrecruiters"),
    # Workable
    (re.compile(r"apply\.workable\.com/([a-zA-Z0-9_.-]+?)(?:/|$)"), "workable"),
]

# Slugs that look like path segments of the ATS itself, not company slugs.
# Filter these out to avoid false positives.
_SLUG_FALSE_POSITIVES = {"embed", "v1", "v2", "api", "boards", "jobs", "postings"}


def detect_ats_from_url(url: Optional[str]) -> Optional[tuple[str, str]]:
    """Return (ats_name, slug) if URL matches a known ATS pattern, else None."""
    if not url:
        return None
    for pattern, ats in _ATS_URL_PATTERNS:
        m = pattern.search(url)
        if m:
            slug = m.group(1)
            if slug in _SLUG_FALSE_POSITIVES:
                continue
            return ats, slug
    return None


# ---------------------------------------------------------------------------
# Tracking query parameters to strip during normalization
# ---------------------------------------------------------------------------

_TRACKING_PARAMS: frozenset[str] = frozenset({
    # UTM family
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "utm_id", "utm_name", "utm_reader", "utm_viz_id", "utm_pubreferrer", "utm_swu",
    # LinkedIn tracking
    "trk", "trkinfo", "trkCampaign", "refId", "originalReferer",
    "originalSubdomain", "lipi", "eBP", "lici", "_l",
    # Indeed tracking
    "vjs", "advn", "tk",
    # Google / Facebook / Microsoft ad tracking
    "gclid", "dclid", "fbclid", "yclid", "msclkid",
    # Mailchimp
    "mc_cid", "mc_eid",
    # Generic referral tracking
    "ref_src", "ref_url",
})

# Parameter name prefixes that indicate tracking (case-sensitive; most
# tracking uses lowercase). Used in addition to the explicit list above.
_TRACKING_PREFIXES: tuple[str, ...] = ("utm_",)


def _is_tracking_param(key: str) -> bool:
    if key in _TRACKING_PARAMS:
        return True
    return any(key.startswith(prefix) for prefix in _TRACKING_PREFIXES)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

# Trailing punctuation that commonly sneaks in from sloppy copy-paste of URLs
# embedded in prose.
_TRAILING_JUNK = ".,);]>'\""


def normalize_url(url: Optional[str]) -> str:
    """Return a canonical form of a URL suitable for equality comparison.

    - Lowercases scheme and host (preserves path case - some ATS slugs are case-sensitive)
    - Strips fragment identifiers
    - Removes tracking query params (UTM family + LinkedIn/Indeed/ad-network keys)
    - Strips trailing slash on non-root paths
    - Trims trailing punctuation from sloppy copy-paste

    Empty or malformed URLs return empty string (for None) or the original
    string (for unparseable non-empty input).
    """
    if not url:
        return ""
    url = url.strip().rstrip(_TRAILING_JUNK)
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    # Without scheme+netloc we can't normalize safely; return as-is
    if not parsed.scheme or not parsed.netloc:
        return url

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Preserve only non-tracking query params, maintaining original order
    if parsed.query:
        pairs = parse_qsl(parsed.query, keep_blank_values=False)
        filtered = [(k, v) for k, v in pairs if not _is_tracking_param(k)]
        query = urlencode(filtered)
    else:
        query = ""

    path = parsed.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Drop fragment always
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def canonical_url(primary: Optional[str], candidates: Optional[Iterable[str]] = None) -> str:
    """Pick the best URL from primary + candidates, normalized.

    Priority: the first URL matching a supported ATS pattern wins (ATS URLs
    are more stable and carry the real slug). If none match, returns the
    normalized primary URL.

    Use when building a Job from a JobSpy row where `job_url` is the
    aggregator URL and `job_url_direct` is the company's external-apply URL.
    """
    all_urls: list[str] = []
    if primary:
        all_urls.append(primary)
    if candidates:
        all_urls.extend(c for c in candidates if c)

    for url in all_urls:
        if detect_ats_from_url(url):
            return normalize_url(url)

    return normalize_url(primary or "")


__all__ = [
    "detect_ats_from_url",
    "normalize_url",
    "canonical_url",
]

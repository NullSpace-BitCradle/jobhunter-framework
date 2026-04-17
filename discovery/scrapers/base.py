"""Abstract base class for ATS scrapers. All scrapers produce normalized Job objects."""
import html as html_mod
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional

import requests


@dataclass
class Job:
    """Normalized job posting across all ATS platforms."""
    id: str                    # Unique ID, prefixed with ATS name + company slug
    company: str               # Display name
    company_slug: str          # ATS slug
    company_tier: str          # Tier group from companies.yaml
    title: str
    location: str
    remote: Optional[bool]     # True/False/None (unknown)
    url: str
    posted_at: Optional[datetime]
    description_text: str      # Plain-text description for keyword matching
    source: str = "ats"        # "ats" for direct ATS scrapers, "board" for job-board aggregators
    raw: dict = field(default_factory=dict)

    def to_serializable(self) -> dict:
        d = asdict(self)
        if self.posted_at:
            d["posted_at"] = self.posted_at.isoformat()
        d.pop("raw", None)
        return d


# --- Shared utilities (extracted from individual scrapers) ---

_HTML_TAG = re.compile(r"<[^>]+>")
_MULTI_WS = re.compile(r"\s+")

def strip_html(s: str) -> str:
    """Strip HTML tags and collapse whitespace. Safe for None/empty input."""
    if not s:
        return ""
    s = _HTML_TAG.sub(" ", s)
    s = html_mod.unescape(s)
    s = _MULTI_WS.sub(" ", s).strip()
    return s


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string, handling the 'Z' suffix. Returns None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


USER_AGENT = "JobHunter-Framework/1.0 (personal job search tool)"

REQUEST_DELAY_SECONDS = 0.5


class Scraper:
    """Base class for ATS scrapers. Subclasses implement fetch_jobs."""
    ats_name: str = "base"

    def __init__(self, timeout: int = 10, delay: float = REQUEST_DELAY_SECONDS):
        self.timeout = timeout
        self.delay = delay
        self._last_request_time = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _throttled_get(self, url: str, **kwargs) -> requests.Response:
        """GET with rate limiting between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_time = time.monotonic()
        return self.session.get(url, **kwargs)

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str) -> list[Job]:
        raise NotImplementedError

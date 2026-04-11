"""Abstract base class for ATS scrapers. All scrapers produce normalized Job objects."""
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional


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
    raw: dict = field(default_factory=dict)

    def to_serializable(self) -> dict:
        d = asdict(self)
        if self.posted_at:
            d["posted_at"] = self.posted_at.isoformat()
        # raw dict can be huge; omit from serialization by default
        d.pop("raw", None)
        return d


class Scraper:
    """Base class for ATS scrapers. Subclasses implement fetch_jobs."""
    ats_name: str = "base"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def fetch_jobs(self, company_slug: str, company_name: str, company_tier: str) -> list[Job]:
        raise NotImplementedError

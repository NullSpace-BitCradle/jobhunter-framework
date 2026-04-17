"""Unit tests for URL canonicalization and normalization."""
import pytest

from url_utils import normalize_url, canonical_url, detect_ats_from_url


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------

class TestNormalizeUrl:
    def test_empty(self):
        assert normalize_url("") == ""

    def test_none(self):
        assert normalize_url(None) == ""

    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://WWW.Foo.Com/Path/Here") == "https://www.foo.com/Path/Here"

    def test_preserves_path_case(self):
        """ATS slugs like SmartRecruiters 'OracleCorporation' are case-sensitive."""
        url = "https://jobs.smartrecruiters.com/OracleCorporation/744000"
        assert normalize_url(url) == url

    def test_strips_fragment(self):
        assert normalize_url("https://foo.com/bar#apply") == "https://foo.com/bar"

    def test_strips_utm_params(self):
        url = "https://linkedin.com/jobs/view/12345?utm_source=foo&utm_medium=bar"
        assert normalize_url(url) == "https://linkedin.com/jobs/view/12345"

    def test_strips_linkedin_tracking(self):
        url = "https://www.linkedin.com/jobs/view/12345?trk=public_jobs&refId=abc&originalSubdomain=us"
        assert normalize_url(url) == "https://www.linkedin.com/jobs/view/12345"

    def test_strips_indeed_tracking(self):
        url = "https://www.indeed.com/viewjob?jk=abc123&vjs=1&tk=xyz"
        assert normalize_url(url) == "https://www.indeed.com/viewjob?jk=abc123"

    def test_strips_ad_network_click_ids(self):
        url = "https://foo.com/job/123?gclid=abc&fbclid=def&msclkid=ghi"
        assert normalize_url(url) == "https://foo.com/job/123"

    def test_preserves_non_tracking_query_params(self):
        """gh_jid, page, id, etc. are not tracking - preserve."""
        url = "https://careers.foo.com/apply?gh_jid=12345"
        assert normalize_url(url) == "https://careers.foo.com/apply?gh_jid=12345"

    def test_mixed_tracking_and_legit(self):
        url = "https://foo.com/job?id=5&utm_source=x&trk=y&page=2"
        assert normalize_url(url) == "https://foo.com/job?id=5&page=2"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://foo.com/bar/") == "https://foo.com/bar"

    def test_preserves_root_slash(self):
        assert normalize_url("https://foo.com/") == "https://foo.com/"

    def test_trims_trailing_punctuation(self):
        assert normalize_url("https://foo.com/bar.") == "https://foo.com/bar"
        assert normalize_url("https://foo.com/bar),") == "https://foo.com/bar"

    def test_strips_whitespace(self):
        assert normalize_url("  https://foo.com/bar  ") == "https://foo.com/bar"

    def test_malformed_url_returns_as_is(self):
        """No scheme and no netloc - don't corrupt it further."""
        assert normalize_url("not a url") == "not a url"

    def test_idempotent(self):
        """Normalizing twice should produce the same result as normalizing once."""
        url = "HTTPS://FOO.COM/bar?utm_source=x&id=5#frag"
        once = normalize_url(url)
        twice = normalize_url(once)
        assert once == twice

    def test_different_tracking_same_posting(self):
        """Same LinkedIn posting with different tracking should normalize identically."""
        url_a = "https://www.linkedin.com/jobs/view/4401234567?trk=public_jobs&refId=abc"
        url_b = "https://www.linkedin.com/jobs/view/4401234567?trk=recommended&originalSubdomain=us&lipi=foo"
        assert normalize_url(url_a) == normalize_url(url_b)


# ---------------------------------------------------------------------------
# detect_ats_from_url
# ---------------------------------------------------------------------------

class TestDetectAts:
    """Delta coverage vs test_candidates.py - both test the same function but
    from different import paths (candidates re-exports from url_utils). Keep a
    handful here to catch url_utils-specific regressions."""

    def test_greenhouse(self):
        assert detect_ats_from_url("https://boards.greenhouse.io/anthropic/jobs/12345") == ("greenhouse", "anthropic")

    def test_lever(self):
        assert detect_ats_from_url("https://jobs.lever.co/vercel/abc-def") == ("lever", "vercel")

    def test_ashby(self):
        assert detect_ats_from_url("https://jobs.ashbyhq.com/openai/abc") == ("ashby", "openai")

    def test_smartrecruiters(self):
        assert detect_ats_from_url("https://jobs.smartrecruiters.com/OracleCorporation/123") == ("smartrecruiters", "OracleCorporation")

    def test_workable(self):
        assert detect_ats_from_url("https://apply.workable.com/toptal/j/ABC/") == ("workable", "toptal")

    def test_linkedin_returns_none(self):
        assert detect_ats_from_url("https://linkedin.com/jobs/view/123") is None

    def test_none(self):
        assert detect_ats_from_url(None) is None


# ---------------------------------------------------------------------------
# canonical_url
# ---------------------------------------------------------------------------

class TestCanonicalUrl:
    def test_primary_is_ats_returns_normalized_primary(self):
        url = "https://boards.greenhouse.io/foocorp/jobs/123"
        assert canonical_url(url, []) == url

    def test_primary_is_linkedin_with_ats_candidate(self):
        """JobSpy case: job_url is LinkedIn, job_url_direct is the ATS - prefer ATS."""
        linkedin = "https://www.linkedin.com/jobs/view/12345?trk=abc"
        ats = "https://boards.greenhouse.io/foocorp/jobs/99"
        assert canonical_url(linkedin, [ats]) == ats

    def test_no_ats_in_any_url_returns_normalized_linkedin(self):
        linkedin = "https://www.linkedin.com/jobs/view/12345?trk=abc&utm_source=x"
        normalized = "https://www.linkedin.com/jobs/view/12345"
        assert canonical_url(linkedin, []) == normalized

    def test_no_ats_with_candidates(self):
        linkedin = "https://linkedin.com/jobs/view/12345"
        indeed = "https://indeed.com/viewjob?jk=xyz"
        assert canonical_url(linkedin, [indeed]) == normalize_url(linkedin)

    def test_empty_candidates_list(self):
        url = "https://boards.greenhouse.io/foo/jobs/1"
        assert canonical_url(url, None) == url
        assert canonical_url(url) == url

    def test_primary_is_empty_candidate_is_ats(self):
        ats = "https://jobs.lever.co/netlify/abc"
        assert canonical_url("", [ats]) == ats

    def test_all_empty(self):
        assert canonical_url("", [""]) == ""

    def test_candidate_with_tracking_still_wins_if_ats(self):
        """The ATS candidate wins even if it has tracking - it gets normalized."""
        linkedin = "https://linkedin.com/jobs/view/123"
        ats_with_tracking = "https://boards.greenhouse.io/foo/jobs/55?utm_source=linkedin&gh_jid=55"
        result = canonical_url(linkedin, [ats_with_tracking])
        assert result == "https://boards.greenhouse.io/foo/jobs/55?gh_jid=55"

    def test_multiple_candidates_first_ats_wins(self):
        """Order matters: the first URL matching an ATS pattern is chosen."""
        linkedin = "https://linkedin.com/jobs/view/1"
        greenhouse = "https://boards.greenhouse.io/a/jobs/1"
        lever = "https://jobs.lever.co/b/x"
        result = canonical_url(linkedin, [greenhouse, lever])
        assert result == greenhouse


# ---------------------------------------------------------------------------
# Integration-ish: make sure normalize_url and canonical_url play nicely with
# the tracker skip-list + candidate-tracking flows.
# ---------------------------------------------------------------------------

class TestComparisonConsistency:
    def test_same_posting_different_sessions_compare_equal(self):
        session_a = "https://www.linkedin.com/jobs/view/4401234567?trk=public_jobs_topcard&refId=session_a"
        session_b = "https://www.linkedin.com/jobs/view/4401234567?utm_source=newsletter&trk=session_b"
        assert normalize_url(session_a) == normalize_url(session_b)

    def test_jobspy_canonicalization_matches_tracker_after_redirect(self):
        """A LinkedIn URL whose apply link lands on Greenhouse should normalize
        to the Greenhouse URL - same as what /apply would store after WebFetch
        follows the redirect and records the final URL."""
        jobspy_result = canonical_url(
            "https://www.linkedin.com/jobs/view/999?trk=abc",
            ["https://boards.greenhouse.io/foocorp/jobs/5555"],
        )
        tracker_after_apply = normalize_url("https://boards.greenhouse.io/foocorp/jobs/5555")
        assert jobspy_result == tracker_after_apply

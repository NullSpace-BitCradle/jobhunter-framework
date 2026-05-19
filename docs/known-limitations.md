# Known limitations

This document collects the rough edges that real users will hit and how to work around them. Updated as new ones surface.

## LaTeX setup is the most common new-user pain point

The resume writer compiles LaTeX. If your system does not already have `pdflatex` and `xelatex`, you will spend more time on the LaTeX install than on any other quickstart step. The README's [Detailed setup: LaTeX](../README.md#detailed-setup-latex) section lists the required TeX Live collections.

Two specific gotchas:

- **`xelatex` is required** for the cover letter template (it uses `fontspec` + `\setmainfont{Bitstream Charter}`). A `pdflatex`-only install will produce the resume but not the cover letter.
- **Missing `.sty` files** during first compile are common. Install via `tlmgr install <pkg>` or `tlmgr --usermode install <pkg>` (no root needed in usermode).

If you do not want to deal with LaTeX, you can still use the discovery + tracker layers - only `/apply` requires PDF compilation.

## JobSpy is fragile

The job-board aggregator path (`discovery/scrapers/jobspy.py`) uses [python-jobspy](https://github.com/speedyapply/jobspy) to scrape LinkedIn, Indeed, Glassdoor, Google, and ZipRecruiter. LinkedIn and Indeed actively change their pages and block automated traffic. Expect this path to break periodically and need a JobSpy upstream update.

**If JobSpy is broken on your platform of choice:**
- Set `jobspy.enabled: false` in `keywords.yaml` to disable that path entirely.
- Use `/ingest` with URLs you find manually - same filter / score / tracking pipeline.
- The direct ATS scrapers (Greenhouse, Lever, Ashby, SmartRecruiters, Workable) are stable; they hit official public APIs.

See the README's [Responsible use](../README.md#responsible-use) section for the LinkedIn / Indeed ToS context.

## No Workday support

Workday-hosted job boards (`*.myworkdayjobs.com`) are not yet supported. The Workday public surface is not API-shaped and would need a separate scraper design. Many enterprise companies post exclusively on Workday, so this is a real coverage gap.

**Workaround:** use `/ingest` with the Workday URL to feed the role through the same filter / score / tailoring pipeline. The framework will not auto-discover Workday postings, but you can apply through it manually.

Tracking issue welcome with a specific company / role and the Workday URL pattern you would want covered.

## ATS-only mode loses board coverage

If you set `jobspy.enabled: false` (recommended for compliance), you lose coverage of any company that does NOT publish to one of the supported ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable). For senior IC roles at large companies, this is fine - most of them are on one of the supported ATS platforms. For smaller companies and recruiter-routed roles, you will need `/ingest` to bring those in by URL.

## Test suite has 1 collection error without `pandas`

`discovery/tests/test_jobspy.py` imports pandas via JobSpy. If you only want to run the framework's own tests (not JobSpy integration tests), use:

```bash
python3 -m pytest --ignore=tests/test_jobspy.py -q
```

The 230 framework tests are fully independent of JobSpy.

## The MCD interview can hallucinate prompts on cold start

The career document builder agent (`.claude/agents/career-doc-builder.md`) runs an 18-section interview. On very long sessions (50+ turns), Claude can drift and start asking questions that have already been answered. **Mitigation:** save the MCD draft frequently and run the interview in chunks across multiple sessions if needed. The agent is designed to resume from a partial MCD.

## Hyphens-only output is strict

Generated resumes and cover letters use ASCII hyphens (`-`) exclusively - no em-dashes, en-dashes, or double-hyphens. This is intentional (ATS parsers render Unicode dashes unpredictably) but does mean the output prose reads more clipped than typical human writing. If you want long dashes in your final PDFs, you will need to post-edit them after compilation - the framework will strip them on regeneration.

## Filing a new limitation

If you hit something that belongs here, open an issue describing the surface that failed and what you expected. PRs to this file are welcome.

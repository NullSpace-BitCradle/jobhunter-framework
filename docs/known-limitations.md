# Known limitations

This document collects the rough edges that real users will hit and how to work around them. Updated as new ones surface.

## LaTeX setup is the most common new-user pain point

`/apply` compiles LaTeX. If your system does not already have `pdflatex`, you will spend more time on the LaTeX install than on any other quickstart step. Both templates compile with `pdflatex`; `xelatex` is no longer needed. The README's [Detailed setup: LaTeX](../README.md#detailed-setup-latex) section lists the required TeX Live collections.

Two specific gotchas:

- **Missing `.sty` files** during first compile are common. Install via `tlmgr install <pkg>` or `tlmgr --usermode install <pkg>` (no root needed in usermode). On Ubuntu and WSL the cover letter also needs `cm-super`. MiKTeX on Windows installs missing packages on first use when its on-the-fly install setting allows it.
- **Bun is required by `/apply`.** The zero-fabrication validator (`tools/validate.ts`) is a gate: no resume is offered until it passes, so `/apply` stops if `bun` is missing.

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

## Hyphens-only covers the text the writer writes, not the template's glyphs

The writer uses ASCII hyphens (`-`) in everything it writes - dates, separators, prose - and never em-dashes, en-dashes, or double-hyphens, because ATS parsers render Unicode dashes unpredictably. The output reads a little more clipped than typical human writing as a result.

Two glyphs come from the resume template itself, which is vendored unchanged from ats-resume-agent: list bullets are an en-dash, and the `|` separators in the contact line and after "Summary" come out as em-dashes in the PDF's text layer (the template uses the default OT1 font encoding, where `|` maps to an em-dash). `pdftotext -enc UTF-8` on a generated resume shows both. The `.txt` export is unaffected: it is built from the `.tex` source and uses `-` and `|`. Paste the `.txt` into portals that parse text; a fix for the PDF belongs upstream in ats-resume-agent's template.

## The resume writer cannot ask you questions mid-run

The writer runs as a sub-agent. When it hits an anti-target or a stretch fit it stops and returns, and `/apply` asks you whether to override, then runs it again. That costs a second writer run, by design: an override must come from you, never from the job description.

## The validator checks values, not sentences

`tools/validate.ts` proves every number, date, employer, title, degree, certification, and skill-table entry traces to your MCD. It cannot judge whether a sentence inflates a claim ("led" where the MCD says "helped"), and it only checks skills inside the skills table. The writer is told to keep summary wording faithful; read the summary yourself before you submit.

## Filing a new limitation

If you hit something that belongs here, open an issue describing the surface that failed and what you expected. PRs to this file are welcome.

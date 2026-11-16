# jobhunter-framework

**Tailor three resumes instead of spamming fifty, and have a tracker that actually tells you the truth at the end of the week.**

> Status: **beta, released as-is.** Main is the truth - clone or `git pull` for the latest. No versioned releases; this is a Claude Code framework distributed as source, not a packaged library. Issues welcome, no commitment to response time. Use at your own risk; see [Responsible use](#responsible-use) before running discovery, and [docs/known-limitations.md](docs/known-limitations.md) for the rough edges you will probably hit.

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) job-hunting workbench. Covers the full pipeline: career intake, role discovery across ATS platforms and job boards, tailored resume and cover letter generation with a hard zero-fabrication policy, and end-to-end application tracking.

Built for candidates who care more about signal than volume, in any field and at any level. The lane-fit scoring and crown-jewel placement earn their keep whenever you are choosing among a few roles worth tailoring for, and the writer handles careers that include a break: recency is measured from the end of your most recent role, and a gap can be shown as a career break instead of costing you your earlier roles. The discovery examples are tuned for one tech search; [`examples/`](examples/) has a non-tech career document to start from.

## Responsible use

This framework runs against external job boards and ATS APIs. Read this before you run discovery:

- **JobSpy (LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter):** [python-jobspy](https://github.com/speedyapply/jobspy) is the underlying library. LinkedIn and Indeed prohibit scraping in their Terms of Service. JobSpy works by mimicking browser traffic and will break without notice when those sites change. **Whether you use it on those boards is your call and your responsibility.** Set `jobspy.enabled: false` in `keywords.yaml` if you want ATS-only coverage.
- **Rate limits.** The ATS scrapers (Greenhouse, Lever, Ashby, SmartRecruiters, Workable) hit public APIs. Default scan cadence is once per day. Do not loop the scanner; do not run it from CI without backoff.
- **Your data.** Your Master Career Document, generated PDFs, and applications tracker live in a user-data directory outside the repo. The framework never uploads them anywhere. Whatever you push from your fork is your responsibility.
- **Compliance.** Job-board ToS, scraping law, and employer data-handling expectations vary by jurisdiction. You are responsible for your own compliance with the platforms you choose to use.

## What makes this different

- **Writes only from verified content, and checks it.** Every line in the resume and cover letter comes from your Master Career Document. If a metric is not in the MCD, it is not in the output. A deterministic validator traces every number, date, employer, title, degree, certification, and listed skill back to the MCD, and `/apply` offers nothing until it passes.
- **Scores lane fit before tailoring.** Each generation run rates the target job against your MCD's positioning lanes. Stretch fits require explicit confirmation; anti-target lanes block generation entirely.
- **Enforces crown-jewel placement.** Your MCD flags verifiable, differentiated achievements only you can credibly claim. The writer places those in the summary paragraph for principal- and staff-tier roles.
- **Anchors the summary to roles you have held.** The opening title maps to a role you have genuinely held or performed. No "Nurse Manager with 10 years of experience" for a charge nurse, no "Engineering Manager" for an individual contributor.
- **Keeps your data out of the repo.** Your MCD, job descriptions, generated materials, and application tracker live in a user-data directory. The repo stays code-only.
- **Plain-markdown state you can grep.** Applications, statuses, and interview notes live in a human-readable tracker you can search, version, or hand-edit with any tool.
- **Hyphens in everything the writer writes.** Dates, separators, and prose use single hyphens - no em-dashes, en-dashes, or typographic double-hyphens - because ATS parsers render Unicode dashes unpredictably. The template's own bullet and separator glyphs are the exception; see [known limitations](docs/known-limitations.md).
- **Plain-text export.** Each resume and cover letter also lands as a `.txt` built from the same source, for portals that parse text better than PDF.

## Components

- **Career Document Builder** (`.claude/agents/career-doc-builder.md`) - interactive interview producing a structured Master Career Document (MCD), an 18-section document covering positioning, skills, metrics, work history, lane definitions, and anti-target rules.
- **Job Discovery** (`discovery/main.py`) - Python CLI that scans two complementary source types:
  - **Direct ATS APIs**: Greenhouse, Lever, Ashby, SmartRecruiters, Workable
  - **Job board aggregator**: LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter via [python-jobspy](https://github.com/speedyapply/jobspy)
  Results pass through keyword, location, remote, and anti-target filters, then get ranked by a configurable scoring function and written to a daily digest.
- **Resume Writer** (`.claude/agents/ats-resume-writer.md`) - writes ATS-optimized LaTeX resumes and cover letters from MCD + job description, with anti-target, stretch-fit, crown-jewel, summary-authenticity, and distinctive-career-fact guards running before any tailoring. It only reads and writes files; it has no shell or web access.
- **Templates and validator** (`templates/`, `tools/validate.ts`, `tools/export-text.ts`) - the LaTeX templates, the zero-fabrication validator, and the plain-text export, vendored unchanged from [ats-resume-agent](https://github.com/NullSpace-BitCradle/ats-resume-agent).
- **Orchestration commands** (`.claude/commands/`) - slash commands that chain the pieces into one workflow.
- **Application tracker** - plain-markdown file with four sections (`## Queued`, `## In Process`, `## Rejected`, `## Declined`) and an explicit status state machine: `queued -> applied -> ack -> screen -> interview -> offer | rejected | withdrew`. Rows move across sections as status changes. Resume and cover letter files are deleted when a row lands in `## Declined` - only materials for roles the user actually submitted are retained.

## Architecture

The framework separates code (this repo) from personal data (your user-data directory). Everything personal stays outside the repo so nothing private ever lands in a commit.

```
~/JobHunt/                          # user-data directory (yours, gitignored via config.yaml)
  Master_Career_Document.md         # single source of truth for all your content
  applications.md                   # append-only tracker
  jobs/
    Job_Description-<Co>-<Role>.md  # saved JDs
  output/
    Resume-<You>-<Co>-<Role>.pdf    # generated resumes (+ .tex source, .txt export)
    CoverLetter-<You>-<Co>-<Role>.pdf
  discovery/
    config/                         # your target companies and anti-target rules
    state/seen-jobs.json            # dedup state
    output/digest-YYYY-MM-DD.md     # daily ranked digest

~/Projects/jobhunter-framework/     # this repo - code only
  .claude/agents/                   # ats-resume-writer, career-doc-builder
  .claude/commands/                 # /discover, /apply, /submitted, /triage, /sync-filters
  discovery/                        # Python scanner
    main.py, dedup.py
    scrapers/                       # 6 scrapers (5 ATS + JobSpy)
    tests/
    config/*.example.yaml           # tracked examples
  templates/                        # LaTeX resume + cover letter (vendored)
  tools/                            # validator + text export (vendored), sync check
  examples/                         # fictional MCD + resume (non-tech, with a career break)
  docs/                             # MCD conventions, known limitations
  config.example.yaml               # framework config template
```

A `config.yaml` at the repo root (gitignored) maps the framework at every user-data path. Commands and the discovery tool read it at startup.

## How the pieces fit

```
career-doc-builder ──> Master Career Document <── /sync-filters ──> discovery filters.yaml
                              │
/discover, /ingest ──> digest ┤
                              ▼
/apply:  fetch JD ─> duplicate check ─> save JD ─> ats-resume-writer (.tex only)
                                                     │  STATUS: stop-anti-target / stop-stretch → ask you
                                                     ▼
         pdflatex ─> tools/validate.ts (must PASS) ─> tools/export-text.ts ─> tracker row in ## Queued
                          │ FAIL: findings back to the writer, at most 3 rounds; never edits the MCD
/submitted, /decline, /triage ──> move rows across tracker sections
```

**What this repo owns and what it borrows.** [ats-resume-agent](https://github.com/NullSpace-BitCradle/ats-resume-agent) is the single source of truth for the LaTeX templates and the validator/export tools. This repo carries byte-identical copies, pinned to an upstream commit in `tools/ats-vendor.json`, so `/apply` works offline and on any OS without installing a plugin. `bun tools/sync-ats.ts check` fails if a copy drifts from the pin; to take an upstream change, run `bun tools/sync-ats.ts pull <path to an ats-resume-agent checkout> --ref <tag>`, review the diff, and commit. Never edit the vendored files here.

Everything jobhunter-specific stays here: `config.yaml` path resolution, the anti-target, lane-fit, stretch, crown-jewel, and distinctive-fact gates, career-break and recency handling, the duplicate check, and the tracker. The two agent prompts are jobhunter's own, built on ats-resume-agent's v1.3.0 writer and builder; when the upstream prompts improve, port the change by hand.

Why vendoring rather than requiring the ats-resume-agent plugin: a plugin install would bring a second, differently-behaving `ats-resume-writer` and `career-doc-builder` into the same session, and the marketplace serves whatever upstream `master` holds rather than the version this repo was tested against. A pinned copy plus a drift check gives one source of truth without either problem.

## Quickstart

### 1. Prerequisites

You need:

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- Python 3.10+ (required by python-jobspy)
- LaTeX (for resume and cover letter PDF compilation)
- [Bun](https://bun.sh) (runs the zero-fabrication validator and the text export; `/apply` will not offer a resume without it)

If you already have a working LaTeX install with `pdflatex` available, skip ahead to step 2. Otherwise, LaTeX install commands and the required TeX Live collections are in [Detailed setup: LaTeX](#detailed-setup-latex) at the bottom of this README.

### 2. Clone and configure

```bash
git clone https://github.com/NullSpace-BitCradle/jobhunter-framework.git
cd jobhunter-framework
cp config.example.yaml config.yaml
```

Edit `config.yaml` to point at your user-data directory. The default template uses `~/JobHunt/`:

```yaml
user_data_dir: ~/JobHunt

mcd_path: ~/JobHunt/Master_Career_Document.md
output_dir: ~/JobHunt/output
jd_dir: ~/JobHunt/jobs
applications_file: ~/JobHunt/applications.md

discovery:
  config_dir: ~/JobHunt/discovery/config
  state_file: ~/JobHunt/discovery/state/seen-jobs.json
  digest_dir: ~/JobHunt/discovery/output
```

Create the directories:

```bash
mkdir -p ~/JobHunt/jobs ~/JobHunt/output ~/JobHunt/discovery/{config,state,output}
```

### 3. Build your Master Career Document

Launch Claude Code from the repo root and ask:

```
Help me build my career document
```

This invokes the `career-doc-builder` agent, which walks you through an 18-section interview covering positioning, skills, work history with metrics, hybrid strengths, compliance experience, education, publications, and the lane / anti-target guidance the other components depend on. Expect 45-90 minutes the first time. After that you reuse it.

If you already have a long-form career document, point Claude at it and the builder will import and extend rather than interview from scratch.

### 4. Configure job discovery

```bash
cd discovery
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy examples into your user-data discovery config dir
cp config/companies.example.yaml ~/JobHunt/discovery/config/companies.yaml
cp config/keywords.example.yaml  ~/JobHunt/discovery/config/keywords.yaml
cp config/filters.example.yaml   ~/JobHunt/discovery/config/filters.yaml
```

Customize:

- `companies.yaml` - target companies grouped by tier, each with its ATS slug
- `keywords.yaml` - title keywords, scoring weights, JobSpy board settings
- `filters.yaml` - anti-target patterns (can be regenerated from MCD via `/sync-filters`)

Verify your slugs resolve:

```bash
python main.py --verify
```

Run a scan:

```bash
python main.py            # full scan, writes digest, updates state
python main.py --dry-run  # scan without updating state
python main.py -v         # verbose logging
```

Output lands at `~/JobHunt/discovery/output/digest-YYYY-MM-DD.md`.

### 5. Generate resumes and track applications

From Claude Code, use the orchestration commands. See the commands reference below.

## The Master Career Document

The MCD is the single source of truth. Every generated resume and cover letter pulls from it. Key design choices:

- **18 structured sections** cover positioning, summaries (multiple versions for different role types), hybrid strengths, a full skills inventory, industry history, work experience with metrics, education, publications, key achievements, soft skills, project deep-dives, compliance frameworks, volunteer work, historical career objectives, address history, work preferences, customization notes, and a legacy-skills section that is never pulled into output.
- **Positioning lanes**: the MCD names 2 to 8 positioning angles (for example Clinical Leadership and Clinical Education for a nurse, or Platform Engineering and Developer Experience for an engineer) and marks one as the default lane. The resume writer picks the lane that best matches the target job.
- **Anti-Target Lanes**: categories of roles you should NOT apply to (a license or degree you lack required as a hard requirement, on-site work outside your area, a level you are not targeting, a specialty you would have to reshape your experience into). The resume writer blocks generation when the JD matches any of these. The discovery tool pattern-matches against the same list via `/sync-filters`.
- **Crown Jewel markers**: you flag one or more verifiable, differentiated achievements. The resume writer puts them in the summary paragraph for the lanes you name.
- **Career breaks and recency**: a `### Career Break -- <reason>` entry is shown as its own resume entry, worded exactly as you wrote it. Recency is measured from the end of your most recent role, not from today, and you can change the window and cutoff.
- **Agent Notes**: inline `> **Agent Note:**` lines are binding instructions that the writer respects. They are instructions, not evidence: anything that should appear on a resume has to be in the MCD body, and guidance that must never appear on one (lanes, anti-targets, recency settings) is written as notes so the validator can't count it as experience.

[docs/mcd-conventions.md](docs/mcd-conventions.md) is the reference for every marker, and [examples/career-document.example.md](examples/career-document.example.md) is a short worked example (a nurse returning from a career break) with the resume built from it.

Build the MCD once. Maintain it as a living document. Update it when you take a new role or pick up a new domain. The framework never regenerates it from scratch.

## Commands reference

| Command | Purpose |
|---|---|
| `/discover [--verify \| --dry-run \| -v]` | Run a discovery scan, show the top new matches by score. |
| `/ingest <urls \| file>` | Feed manually-found postings (LinkedIn, recruiter emails, etc.) through the same filter + score + candidate pipeline as discovery. Separate digest, read-only against the tracker. |
| `/backfill [--days N] [--limit N] [--min-score N] [--max-score N]` | Walk back over past digests for matched jobs never logged to the tracker. For casting a wider net or filling gaps when new postings are light. |
| `/apply <url \| company \| JD path>` | End-to-end: fetch JD, check the tracker for duplicates, run lane-fit and anti-target checks, tailor resume and cover letter, compile, gate on the validator, export plain text, log application into `## Queued` as `queued`. |
| `/submitted <company> [role hint]` | Move a `queued` row to `## In Process` with status `applied` after you submit via the company portal. Also relocates the resume and cover letter files (`.pdf`, `.tex`, `.txt`) into `output/applied/`. |
| `/decline <company> [reason]` | Move a `queued` row to `## Declined` with status `withdrew` when you decide not to pursue it (posting closed, role turned out to be part-time, etc.). Deletes the resume and cover letter files; keeps the JD. |
| `/triage [--limit N] [--days N]` | Classify recent mail in the job-hunt inbox, update tracker status forward along the state machine, move rows across sections as state transitions, schedule interviews on calendar. |
| `/sync-filters [--check]` | Regenerate `discovery/config/filters.yaml` from the MCD's Anti-Target Lanes section (or check drift only). |

`/apply` stops and asks you before going on when:

- **Already tracked** - the role (same canonical URL, or same company and role) is already in the tracker. Checked before anything is generated.
- **Anti-target detected** - JD matches a lane your MCD explicitly blocks. Application gets logged as `declined_anti_target` with the reason; no materials generated unless you confirm override.
- **Stretch fit** - JD is more than one lane from your default or has a low skills match. The writer names the closest lane and `/apply` waits for your confirmation.
- **Validator still failing** - after three fix rounds some claim still doesn't trace to the MCD. Nothing is logged; the findings are shown.

## Application tracker

Every application lives as a row in `applications.md`. The file holds four sections with identical columns (Date Applied, Company, Role, Status, Last Update, Score, Files, URL, Notes):

- `## Queued` - status `queued`. Resume + cover letter generated, not yet submitted.
- `## In Process` - statuses `applied`, `ack`, `screen`, `interview`, `offer`. Submitted and active.
- `## Rejected` - status `rejected`. The company declined or ghosted. Files retained because the user did apply.
- `## Declined` - statuses `withdrew`, `declined_anti_target`. User chose not to pursue, or framework refused to tailor. Resume and cover letter files are deleted when a row lands here; JD stays.

The status state machine:

```
queued -> applied -> ack -> screen -> interview -> offer | rejected | withdrew
```

Plus one status set directly by `/apply` when the MCD anti-target check refuses:
- `declined_anti_target` - no resume or cover letter generated; the row is logged to `## Declined` immediately.

**Files column format:** `[resume](<path>) / [cover](<path>) / [jd](<path>)` as three markdown links. The JD link is always present once a JD is fetched; resume and cover are present while the files exist.

**File retention rule:** resume + cover letter files are retained only for roles the user actually submitted. `## Queued`, `## In Process`, and `## Rejected` rows keep their files. `## Declined` rows have their resume + cover files deleted. The JD file under `jobs/` is kept regardless - it is the record of the posting.

**Commands that move rows across sections:**
- `/apply` appends new rows to `## Queued` (or `## Declined` if the anti-target check refuses)
- `/submitted <company>` moves a row from `## Queued` to `## In Process` (status `queued -> applied`) and relocates the resume and cover PDFs into `output/applied/`
- `/decline <company> [reason]` moves a row from `## Queued` to `## Declined` (status `queued -> withdrew`) and deletes the resume and cover files
- `/triage` fast-forwards status from inbox mail and moves rows across section boundaries automatically (ack -> In Process; rejection -> Rejected)
- Hand-edits are welcome - plain markdown. If you hand-edit a row into `## Declined`, delete the resume + cover files yourself or run `/decline` instead of editing manually

**Stats line:** a one-line summary sits at the top of `applications.md` just above `## Status legend`, showing the count in each section plus the total:

```
**Current state:** Queued 17 | In Process 22 | Rejected 10 | Declined 58 | **Total 107** | Updated 2026-04-22
```

Refresh it manually with `python3 discovery/update_tracker_stats.py`. For auto-update, add a `PostToolUse` hook to `.claude/settings.local.json` that fires whenever `applications.md` is touched via Edit/Write:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_input.file_path // .tool_response.filePath // empty' | grep -qF '/applications.md' && python3 /absolute/path/to/jobhunter-framework/discovery/update_tracker_stats.py 2>/dev/null || true"
      }]
    }]
  }
}
```

Adjust the absolute path to match your clone. The script is idempotent, so re-runs on unchanged state are safe.

The discovery scanner uses rows with terminal status (`rejected`, `withdrew`, `declined_anti_target`) as a durable skip list regardless of which section they live in, so roles you have already declined will not resurface even if the state file gets cleared.

## Discovery details

### Company configuration

Companies are grouped by tier in `companies.yaml`. Each entry specifies its ATS and public slug:

```yaml
tier_1_security_vendor:
  - name: Wiz
    ats: greenhouse
    slug: wizinc
  - name: Chainguard
    ats: greenhouse
    slug: chainguard

tier_1_saas:
  - name: Datadog
    ats: greenhouse
    slug: datadog
```

To find a slug, open any job posting on the company's careers page. The URL reveals the ATS and slug:

- `boards.greenhouse.io/<slug>/...` -> `ats: greenhouse`
- `jobs.lever.co/<slug>/...` -> `ats: lever`
- `jobs.ashbyhq.com/<slug>/...` -> `ats: ashby`
- `jobs.smartrecruiters.com/<slug>/...` -> `ats: smartrecruiters`
- `apply.workable.com/<slug>/...` -> `ats: workable`

Companies on Workday, iCIMS, Google Careers, or unknown ATS platforms live in a `manual_check` section with a direct careers URL. The scanner reports the manual_check count at startup so you know how many targets you are tracking manually.

### Job board aggregator (JobSpy)

To also pull from LinkedIn, Indeed, and similar boards, enable the `jobspy` block in `keywords.yaml`:

```yaml
jobspy:
  enabled: true
  sites:
    - linkedin
    # - indeed
    # - glassdoor
  location: "USA"
  results_wanted: 50
  hours_old: 72
  linkedin_fetch_description: false
```

If `search_term` is not set, discovery auto-builds one from your `domain_keywords_in_title`.

### URL canonicalization

Every URL that lands in the digest, tracker, or candidate state goes through a canonical form:

- When JobSpy returns a LinkedIn or Indeed posting with an external `job_url_direct` that resolves to a supported ATS (`boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `jobs.smartrecruiters.com`, `apply.workable.com`), the **ATS URL becomes the URL of record**, not the LinkedIn URL. ATS URLs are more stable, carry the company slug for candidate tracking, and match what the company uses in ack emails so `/triage` can correlate mail reliably.
- Tracking query parameters (`utm_*`, `trk`, `refId`, `originalReferer`, `gclid`, etc.) are stripped. Same posting shared across sessions with different tracking still matches for dedup purposes.
- Fragment identifiers are dropped, trailing slashes normalized, scheme + host lowercased. Path case preserved (SmartRecruiters slugs can be `OracleCorporation`, etc.).
- `/apply` and `/ingest` both apply the same canonicalization. When WebFetch on a LinkedIn URL resolves to an ATS page, the tracker entry uses the ATS URL.
- `load_declined_urls` (which builds the skip-list from terminal-status tracker rows) normalizes URLs at read time, so scan-side comparisons match regardless of tracking param drift.

Slash commands that need canonical form on demand can call `python discovery/main.py --normalize-url "<url>"`.

### Deduplication

Discovery deduplicates at two levels:

1. **Within-source** - every job gets a unique ID (`{ats}:{slug}:{id}` for ATS, `jobspy:{site}:{hash}` for boards; the `{hash}` is of the canonicalized URL, so tracking-param variations don't fork the ID). IDs are tracked in `seen-jobs.json`; jobs seen on prior runs are filtered out. IDs age out after 60 days or when the file hits 50,000 entries, whichever comes first.
2. **Cross-source** - when the same role appears on both an ATS and a job board, fuzzy matching on normalized company name and title keeps only the ATS version (richer description, direct apply link).

### Candidate company discovery

Board-source matches (LinkedIn/Indeed/Glassdoor) from companies NOT already in your `companies.yaml` get tracked in `candidate-companies.json` alongside the seen-jobs state. Each run:

1. Records the board-source company, title, URL, and match score against a persistent candidate log.
2. Accumulates match count and cumulative score over time (stale entries pruned after 90 days of inactivity).
3. Extracts the ATS platform and slug when the JobSpy result's URL, `job_url_direct`, or `company_url` resolves to one of the supported ATS platforms (Greenhouse, Lever, Ashby, SmartRecruiters, Workable).
4. Surfaces candidates in the digest's **Candidate companies** section once they cross the promotion threshold (default: 3 matches OR cumulative score 30+). The suggestion includes a copy-paste-ready `companies.yaml` entry when the ATS was auto-detected.

Example digest output for a surfaced candidate:

````markdown
### Foo Labs
- **Matches:** 4 | **Cumulative score:** 58
- **First seen:** 2026-04-01 | **Last seen:** 2026-04-17
- **ATS detected:** `greenhouse`, slug `foolabs`
  ```yaml
  - name: Foo Labs
    ats: greenhouse
    slug: foolabs
  ```
- **Sample roles:** Staff Security Engineer; Principal VM Engineer; Senior Detection Engineer
````

This converts passive JobSpy scraping into an active feed of companies worth promoting to direct-ATS scraping, with zero manual slug hunting when the URL resolves cleanly. Companies whose LinkedIn posts use Easy Apply (no external URL) still get tracked by match count but need manual ATS identification.

### Backfill

When new postings are light or you want to widen the net, `/backfill` walks back over recent `digest-*.md` files and surfaces every matched job that never made it into the tracker at any status. Useful for two patterns:

```
/backfill                        # last 30 days, top 30 by score
/backfill --days 14 --limit 50   # two-week window, more results
/backfill --max-score 10         # focus on lower-scored matches you skipped
```

Exclusion is strict: any row in `applications.md` regardless of status counts as "already acted on." Matching uses normalized URL first and `(company, title)` as a secondary key, so historical digests holding a LinkedIn URL correctly match tracker rows holding the ATS URL for the same role. Candidates are aggregated across digest appearances, so a role surfaced on three different days reports as one row with appearance count and highest-ever score.

Writes a timestamped `backfill-YYYY-MM-DD-HHMMSS.md`. Read-only against the tracker and state files.

### Manual ingest

Not every relevant role comes through discovery. When you find one manually (LinkedIn browsing, recruiter email, referrals, a company's careers page), `/ingest` feeds it through the same pipeline so you get a consistent signal before committing tailoring effort:

```
/ingest https://www.linkedin.com/jobs/view/4401234567
/ingest linkedin-urls.txt
```

Behavior:

- WebFetches each URL and parses title, company, location, posted date, and description. LinkedIn 403s fall back to pasting the JD body text.
- Runs each posting through `is_anti_target`, `match_title`, `match_location`, and `score_job` - identical to the scan pipeline.
- Writes a timestamped `ingest-YYYY-MM-DD-HHMMSS.md` digest with four sections: matches, anti-target hits, filtered (with rejection reason so you know WHY a role got rejected, unlike scan mode which silently drops them), and previously-declined warnings (URLs your tracker marks as rejected/withdrew).
- Feeds candidate-company tracking - manual ingests count toward promotion the same as JobSpy results.
- **Does NOT** populate `seen-jobs.json` - re-ingesting the same URL re-processes it, useful when a JD has been updated.
- **Does NOT** modify `applications.md` - read-only against the tracker. Follow up with `/apply <url>` to actually commit.

### Scoring and filtering

Each posting gets a score from configurable title bonuses, description bonuses, tier bonuses, company bonuses, and a freshness bonus (+3 if posted in the last 3 days, +1 in the last 7). Matches below zero get ordered low but still appear in the digest.

Anti-target patterns use conjunction logic: any combination of `title_contains_any`, `description_contains_any`, `description_contains_all`, `location_contains_any`, plus optional `negates_if_description_also_contains` and `negates_if_location_also_contains` for cases like "clearance required" that should NOT fire when the JD also says "or equivalent."

## Resume writer safeguards

Before generating anything, the `ats-resume-writer` agent runs a gated sequence:

1. **Read the MCD.** If missing, abort with a pointer to `career-doc-builder`.
2. **Anti-target check.** If the JD matches any Anti-Target Lane, stop and return; `/apply` asks you whether to override.
3. **Lane-fit scoring.** Rate the JD against each MCD positioning lane on role type, skills match, domain, and level (using your field's own ladder). If the best lane is a stretch (more than one lane from default, or skills match under 50%), stop and return; `/apply` asks you whether to continue.
4. **Crown Jewel applicability.** Determine if the MCD's crown-jewel achievements apply to this role type. For the lanes where the MCD says the crown jewel applies, placement in the summary paragraph is mandatory.
5. **Summary Authenticity.** The opening title or descriptor in the summary must map to a role the user has genuinely held. If no authentic framing fits both the JD and the MCD, that is a signal the lane is a stretch and generation returns to step 3.
6. **Distinctive Career Fact surfacing.** If the MCD has a distinctive career fact (for example, being a founding employee), it appears in the summary or early experience for applications at your usual level and above.

Then the writer picks roles by recency measured from the end of your most recent role, condenses rather than drops any role between two it shows, keeps career breaks as their own entries, and writes LaTeX with only the template's commands and every interpolated value escaped. The job description is treated as untrusted data: instructions inside it are ignored, and an override only counts when it comes from you through `/apply`.

`/apply` then compiles both files with `pdflatex`, runs `tools/validate.ts` on each (the resume against the MCD alone, the cover letter against the MCD plus the JD), sends any findings back to the writer for at most three fix rounds, and exports a `.txt` of each once they pass. Auxiliary files are cleaned up; `.tex`, `.pdf`, and `.txt` remain.

## Development

### Running the test suite

```bash
cd discovery
source venv/bin/activate
python -m pytest tests/ -v
```

238 tests cover all 6 scrapers (mocked), filter and scoring logic, state management, digest writing, cross-source deduplication, URL canonicalization, ingest filter rejection reasons (including local on-site allow-regex short-circuit), tracker parsing across the four-section Queued/In Process/Rejected/Declined layout, and JobSpy field mapping.

The Bun side has its own tests, run from the repo root:

```bash
bun test tools/              # vendored files match the pin; example resume passes the validator; MCD conventions hold
bun tools/sync-ats.ts check  # vendored templates and tools match tools/ats-vendor.json
```

The validator's own test suite lives upstream in ats-resume-agent, next to the example files it reads.

### Project layout

```
jobhunter-framework/
├── .claude/
│   ├── agents/
│   │   ├── ats-resume-writer.md     # resume + cover letter generation
│   │   └── career-doc-builder.md    # MCD interview agent
│   └── commands/
│       ├── discover.md              # /discover
│       ├── ingest.md                # /ingest
│       ├── backfill.md              # /backfill
│       ├── apply.md                 # /apply
│       ├── submitted.md             # /submitted
│       ├── decline.md               # /decline
│       ├── triage.md                # /triage
│       └── sync-filters.md          # /sync-filters
├── discovery/
│   ├── main.py                      # scan, filter, score, dedup, digest
│   ├── dedup.py                     # cross-source dedup
│   ├── migrate_tracker.py           # one-shot: old 2-section layout -> new 4-section layout
│   ├── update_tracker_stats.py      # refresh the stats line at the top of applications.md
│   ├── scrapers/
│   │   ├── base.py                  # Job dataclass + Scraper base + shared helpers
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── ashby.py
│   │   ├── smartrecruiters.py
│   │   ├── workable.py
│   │   └── jobspy.py                # LinkedIn / Indeed / Glassdoor aggregator
│   ├── config/                      # tracked .example.yaml files
│   └── tests/
├── templates/                       # vendored from ats-resume-agent; do not edit here
│   ├── resume-template.tex
│   ├── cover-letter-template.tex
│   └── LICENSE-CC-BY-4.0.md
├── tools/
│   ├── validate.ts                  # zero-fabrication validator (vendored)
│   ├── export-text.ts               # plain-text export (vendored)
│   ├── latex.ts                     # shared LaTeX parsing (vendored)
│   ├── sync-ats.ts                  # check / pull the vendored files against the pin
│   ├── ats-vendor.json              # upstream commit + file hashes
│   └── *.test.ts                    # bun test tools/
├── examples/
│   ├── career-document.example.md   # fictional MCD: nurse with a career break
│   └── resume.example.tex           # resume generated from it
├── docs/
│   ├── mcd-conventions.md           # MCD markers the writer reads
│   └── known-limitations.md
├── config.example.yaml              # framework config template
├── applications.template.md         # tracker schema
├── CLAUDE.md                        # Claude Code project instructions
└── README.md
```

## Roadmap

What works today:

- [x] Scaffold with code / personal-content separation
- [x] Unified `config.yaml` driving every path
- [x] Orchestration slash commands (`/discover`, `/apply`, `/submitted`, `/triage`, `/backfill`, `/ingest`, `/decline`, `/sync-filters`)
- [x] Plain-markdown applications tracker with 4-section state machine
- [x] Job-board aggregation (LinkedIn / Indeed / Glassdoor / Google / ZipRecruiter via JobSpy) and cross-source dedup
- [x] Title-suffix normalization for backfill (collapses `(remote)` / `- Remote` variants of the same role)
- [x] Agent paths entirely config-driven (slash commands self-locate via `git rev-parse --show-toplevel` and pass resolved absolute paths to the agents, which have no shell)

Planned, no committed date:

- [ ] Workday ATS support (largest current gap; many enterprise postings live there)
- [ ] Expanded integration tests (recorded ATS fixtures, end-to-end command flows)
- [ ] Annotated screenshots, worked examples, getting-started tour

If a gap blocks your use, open an issue describing the role you wanted to pursue and the surface that failed. PRs welcome.

## Detailed setup: LaTeX

If you do not already have a working LaTeX install, here is what the framework needs.

Required TeX Live collections beyond base `texlive-latex-base`:

- `texlive-latex-recommended` provides `ragged2e` and `microtype`
- `texlive-latex-extra` provides `tabularx`, `enumitem`, and `titlesec`
- `texlive-fonts-extra` provides `fontawesome`, `fontawesome5`, `CormorantGaramond`, and `charter`
- `cm-super` provides the T1 fonts the cover letter template uses; without it the cover letter fails on Ubuntu and WSL

Debian / Ubuntu / WSL:

```bash
sudo apt-get install texlive-latex-base texlive-latex-recommended \
  texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra cm-super
```

macOS:

```bash
brew install --cask mactex-no-gui
```

Windows: [MiKTeX](https://miktex.org) works; let it install missing packages on first use.

Both templates compile with `pdflatex`, which ships with all of these. `xelatex` is not needed.

If `pdflatex` complains about a missing `.sty` file on first compile, install the individual package via `tlmgr install <pkg>` or `tlmgr --usermode install <pkg>` (no root required).

## License

- Code in this repository (Python scanners, slash commands, tests, agent prompts, documentation, templates authored for this project): MIT.
- Agent prompts under `.claude/agents/` and `.claude/commands/` are treated as software for licensing purposes and fall under the same MIT grant as the code.
- `templates/` and `tools/{validate,export-text,latex}.ts` are vendored unchanged from [ats-resume-agent](https://github.com/NullSpace-BitCradle/ats-resume-agent) (MIT, same copyright holder). The LaTeX resume template (`templates/resume-template.tex`) is CC-BY-4.0, based on work by Michael Lustfield; see `templates/LICENSE-CC-BY-4.0.md`. The cover letter template is MIT.
- Example config files (`*.example.yaml`, `applications.template.md`) are MIT.
- Your personal content (Master Career Document, job descriptions, generated resumes and cover letters, application tracker) lives outside the repo per the user-data separation. Those files are yours; no license from this repository applies to them.

See [LICENSE](LICENSE) for the MIT text and [NOTICE](NOTICE) for third-party attributions.

## Related repositories

- [ats-resume-agent](https://github.com/NullSpace-BitCradle/ats-resume-agent) - the resume / cover letter generation agent, and the source of truth for this repo's templates and validator tools (see [How the pieces fit](#how-the-pieces-fit)). The standalone repo, also installable as a Claude Code plugin, is for users who want only the resume writer without the discovery / tracker layer.

## Contributing

PRs and issues are welcome. There is no committed review timeline. See [CONTRIBUTING.md](CONTRIBUTING.md) before opening one - especially the "what is out of scope" section. Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

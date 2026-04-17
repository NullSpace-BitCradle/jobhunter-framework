# jobhunter-framework

A self-hosted job-hunting workbench built on [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Owns the full pipeline: career intake, role discovery across ATS platforms and job boards, tailored resume and cover letter generation with a hard zero-fabrication policy, and end-to-end application tracking.

Built for senior and principal IC candidates who care more about signal than volume, and who would rather spend tailoring budget on the three right roles than spray-and-pray at fifty.

## What makes this different

Most AI resume tools will happily fabricate metrics, reshape you into a role you have never held, or quietly pad the summary with plausible-sounding claims. This framework refuses to do any of that.

- **Zero fabrication.** The resume writer only uses content that is explicitly in your Master Career Document. If a metric is not in the MCD, it does not appear in the output. No approximations, no proxy numbers, no "estimate conservatively."
- **Lane-fit assessment before tailoring.** Every generation run scores the job against your MCD's positioning lanes. Stretch fits get a warning you have to confirm. Anti-target lanes (roles you should not apply to) get blocked entirely.
- **Crown-jewel awareness.** Your MCD can flag one or more verifiable, differentiated achievements that only you can credibly claim. For principal- and staff-tier applications, the resume writer enforces crown-jewel placement in the summary paragraph.
- **Summary authenticity guard.** The opening line of your summary must match a role you have genuinely held or performed. No inventing a "Staff AppSec Engineer with 10 years of experience" if you have never held that title.
- **Owned data.** Your MCD, job descriptions, generated resumes, and application tracker all live in a user-data directory outside the repo. The repo is code only.
- **Auditable, durable state.** Applications, statuses, and interviews live in a plain-markdown file you can grep, version, or edit by hand. No proprietary database, no vendor lock-in.

## Components

- **Career Document Builder** (`.claude/agents/career-doc-builder.md`) - interactive interview producing a structured Master Career Document (MCD), an 18-section document covering positioning, skills, metrics, work history, lane definitions, and anti-target rules.
- **Job Discovery** (`discovery/main.py`) - Python CLI that scans two complementary source types:
  - **Direct ATS APIs**: Greenhouse, Lever, Ashby, SmartRecruiters, Workable
  - **Job board aggregator**: LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter via [python-jobspy](https://github.com/speedyapply/jobspy)
  Results pass through keyword, location, remote, and anti-target filters, then get ranked by a configurable scoring function and written to a daily digest.
- **Resume Writer** (`.claude/agents/ats-resume-writer.md`) - generates ATS-optimized LaTeX resumes and cover letters from MCD + job description, with anti-target, stretch-fit, crown-jewel, summary-authenticity, and founding-employee guards running before any tailoring.
- **Orchestration commands** (`.claude/commands/`) - slash commands that chain the pieces into one workflow.
- **Application tracker** - plain-markdown table with an explicit status state machine: `queued -> applied -> ack -> screen -> interview -> offer | rejected | withdrew`.

## Architecture

The framework separates code (this repo) from personal data (your user-data directory). Everything personal stays outside the repo so nothing private ever lands in a commit.

```
~/JobHunt/                          # user-data directory (yours, gitignored via config.yaml)
  Master_Career_Document.md         # single source of truth for all your content
  applications.md                   # append-only tracker
  jobs/
    Job_Description-<Co>-<Role>.md  # saved JDs
  output/
    Resume-<You>-<Co>-<Role>.pdf    # generated resumes
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
    tests/                          # 73 tests
    config/*.example.yaml           # tracked examples
  templates/                        # LaTeX resume + cover letter
  config.example.yaml               # framework config template
```

A `config.yaml` at the repo root (gitignored) maps the framework at every user-data path. Commands and the discovery tool read it at startup.

## Quickstart

### 1. Install prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- Python 3.10+ (required by python-jobspy)
- LaTeX with the following TeX Live collections (beyond base `texlive-latex-base`):
  - `texlive-latex-recommended` provides `ragged2e`, `microtype`
  - `texlive-latex-extra` provides `tabularx`, `enumitem`, `titlesec`
  - `texlive-fonts-extra` provides `fontawesome5`, `CormorantGaramond`, `charter`

Debian / Ubuntu / WSL:

```bash
sudo apt-get install texlive-latex-base texlive-latex-recommended \
  texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra
```

macOS:

```bash
brew install --cask mactex-no-gui
```

Cover letters compile with `xelatex` (required for ATS ligature extraction). Both `pdflatex` and `xelatex` ship with all the bundles above.

If `pdflatex` complains about a missing `.sty` file, install the individual package via `tlmgr install <pkg>` or `tlmgr --usermode install <pkg>` (no root required).

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
- **Positioning lanes**: the MCD names 3 to 8 positioning angles (for example, VM Focus, Cloud Security, GRC, Security Engineering, Solutions Engineering) and marks one as the default lane. The resume writer picks the lane that best matches the target job.
- **Anti-Target Lanes**: categories of roles you should NOT apply to (certifications-as-hard-requirement, degree-as-hard-requirement, on-site-only, analyst-tier, production-K8s-at-scale, and similar). The resume writer blocks generation when the JD matches any of these. The discovery tool pattern-matches against the same list via `/sync-filters`.
- **Crown Jewel markers**: you flag one or more verifiable, differentiated achievements. The resume writer enforces summary-paragraph placement when the target role type is applicable.
- **Agent Notes**: inline `> **Agent Note:**` lines are binding instructions that the writer respects.

Build the MCD once. Maintain it as a living document. Update it when you take a new role or pick up a new domain. The framework never regenerates it from scratch.

## Commands reference

| Command | Purpose |
|---|---|
| `/discover [--verify \| --dry-run \| -v]` | Run a discovery scan, show the top new matches by score. |
| `/ingest <urls \| file>` | Feed manually-found postings (LinkedIn, recruiter emails, etc.) through the same filter + score + candidate pipeline as discovery. Separate digest, read-only against the tracker. |
| `/backfill [--days N] [--limit N] [--min-score N] [--max-score N]` | Walk back over past digests for matched jobs never logged to the tracker. For casting a wider net or filling gaps when new postings are light. |
| `/apply <url \| company \| JD path>` | End-to-end: fetch JD, run lane-fit and anti-target checks, tailor resume and cover letter, log application as `queued`. |
| `/submitted <company> [role hint]` | Flip a `queued` row to `applied` after you submit via the company portal. |
| `/triage [--limit N] [--days N]` | Classify recent mail in the job-hunt inbox, update tracker status forward along the state machine, schedule interviews on calendar. |
| `/sync-filters [--check]` | Regenerate `discovery/config/filters.yaml` from the MCD's Anti-Target Lanes section (or check drift only). |

`/apply` surfaces warnings before generating:

- **Anti-target detected** - JD matches a lane your MCD explicitly blocks. Application gets logged as `declined_anti_target` with the reason; no materials generated unless you confirm override.
- **Stretch fit** - JD is more than one lane from your default or has a low tool-stack match. The agent names the closest lane it will work from and waits for confirmation.

## Application tracker

Every application lives as a row in `applications.md`. The status column moves forward along:

```
queued -> applied -> ack -> screen -> interview -> offer | rejected | withdrew
```

Plus two terminal states:
- `declined_anti_target` - framework refused to tailor for an anti-target lane
- `withdrew` - you pulled the application

`/apply` appends rows as `queued` (generated but not submitted). Rows move forward through:
- `/submitted <company>` - manual flip after submitting through the portal
- `/triage` - detects ack, screen, interview, or rejection mail and fast-forwards the row accordingly (status regression is blocked; `ack` rows cannot go back to `queued`)
- Hand-edits - it is plain markdown, edit freely

The discovery scanner uses rows with terminal status (`rejected`, `withdrew`, `declined_anti_target`) as a durable skip list, so roles you have already declined will not resurface even if the state file gets cleared.

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

```markdown
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
```

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
2. **Anti-target check.** If the JD matches any Anti-Target Lane, refuse generation unless the user explicitly confirms override.
3. **Lane-fit scoring.** Rate the JD against each MCD positioning lane on role-type match, tool-stack match, framework/domain match, and tier alignment. If the best lane is a stretch (more than one lane from default, or tool-stack match under 50%), surface a stretch warning and wait for confirmation.
4. **Crown Jewel applicability.** Determine if the MCD's crown-jewel achievements apply to this role type. For principal/staff/architect/security-vendor lanes, crown-jewel placement in the summary paragraph is mandatory.
5. **Summary Authenticity.** The opening title or descriptor in the summary must map to a role the user has genuinely held. If no authentic framing fits both the JD and the MCD, that is a signal the lane is a stretch and generation returns to step 3.
6. **Founding Employee surfacing.** For principal/staff/senior-tier applications, the founding-employee narrative (if present in the MCD) appears in the summary or early experience.

Then generation proceeds: LaTeX with the template's commands, all interpolated values escaped, hyphens only (no em-dashes, en-dashes, or typographic double-hyphens), compiled with `pdflatex` for the resume and `xelatex` for the cover letter. Auxiliary files are cleaned up; only `.tex` and `.pdf` remain.

## Development

### Running the test suite

```bash
cd discovery
source venv/bin/activate
python -m pytest tests/ -v
```

73 tests cover all 6 scrapers (mocked), filter and scoring logic, state management, digest writing, cross-source deduplication, and JobSpy field mapping.

### Project layout

```
jobhunter-framework/
├── .claude/
│   ├── agents/
│   │   ├── ats-resume-writer.md     # resume + cover letter generation
│   │   └── career-doc-builder.md    # MCD interview agent
│   └── commands/
│       ├── discover.md              # /discover
│       ├── apply.md                 # /apply
│       ├── submitted.md             # /submitted
│       ├── triage.md                # /triage
│       └── sync-filters.md          # /sync-filters
├── discovery/
│   ├── main.py                      # scan, filter, score, dedup, digest
│   ├── dedup.py                     # cross-source dedup
│   ├── scrapers/
│   │   ├── base.py                  # Job dataclass + Scraper base + shared helpers
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── ashby.py
│   │   ├── smartrecruiters.py
│   │   ├── workable.py
│   │   └── jobspy.py                # LinkedIn / Indeed / Glassdoor aggregator
│   ├── config/                      # tracked .example.yaml files
│   └── tests/                       # 73 tests
├── templates/
│   ├── resume-template.tex
│   └── cover-letter-template.tex
├── config.example.yaml              # framework config template
├── applications.template.md         # tracker schema
├── CLAUDE.md                        # Claude Code project instructions
└── README.md
```

## Roadmap

- [x] Phase 1: scaffold and code / content separation
- [x] Phase 2: unified config, `config.yaml`-driven paths, user-data separation
- [x] Phase 3: orchestration commands and applications tracker
- [x] Phase 3.5: job board aggregation (LinkedIn via JobSpy) and cross-source dedup
- [ ] Phase 4: agent paths entirely config-driven (no symlinks), expanded test coverage, Workday support, public release polish

## License

MIT for the code in this repository. The LaTeX resume template is CC-BY-4.0, based on work by Michael Lustfield. See [LICENSE](LICENSE) for details.

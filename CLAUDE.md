# jobhunter-framework - Claude Code Instructions

This is a unified job-hunting framework with four integrated capabilities:

1. **Career Document Builder** (`.claude/agents/career-doc-builder.md`) - interactive interview that produces a Master Career Document (MCD)
2. **Job Discovery** (`discovery/main.py`) - Python CLI that scans target company ATS platforms and job boards (LinkedIn, Indeed, Glassdoor via python-jobspy) and writes a ranked digest. Cross-source deduplication prefers ATS results over board-sourced duplicates. Board aggregation is configured in the `jobspy` section of `discovery/config/keywords.yaml`.
3. **Resume Writer** (`.claude/agents/ats-resume-writer.md`) - generates ATS-optimized LaTeX resumes and cover letters from an MCD plus a job description
4. **Orchestration** (`.claude/commands/`) - slash commands that chain the other three together and handle application tracking + inbox triage

## Framework config

A user-local `config.yaml` at the repo root (gitignored) defines where personal data lives:

- `mcd_path` - Master Career Document
- `jd_dir` - where Job_Description-*.md files are saved
- `output_dir` - where generated resumes and cover letters land
- `applications_file` - the applications tracker
- `discovery.config_dir`, `discovery.state_file`, `discovery.digest_dir` - discovery runtime paths
- `gmail.enabled`, `gmail.account` - inbox triage (phase 3+)

Every command and the discovery tool read this file at startup. `config.example.yaml` in the repo root is the tracked template; copy to `config.yaml` and customize.

## Slash commands

- **`/discover`** - run a discovery scan and show the top new matches. Wraps the Python scanner and summarizes the resulting digest. Use when the user asks to find new jobs, check for new postings, or run a scan.
- **`/ingest <urls|file>`** - feed manually-found postings through the same filter / score / candidate-tracking pipeline as /discover. Use when the user finds roles outside the normal discovery surface (LinkedIn browsing, recruiter emails, referrals). Writes a separate ingest digest. Read-only against the applications tracker.
- **`/backfill [--days N] [--limit N] [--min-score N] [--max-score N]`** - walk back over past digests and surface matched jobs never logged to the tracker at any status. Useful when new postings are light or the user wants to cast a wider net. Exclusion uses both URL and (company, title) so historical LinkedIn-URL digest entries correctly match ATS-URL tracker rows for the same role. Read-only.
- **`/apply <url|company>`** - end-to-end pipeline for a single role: fetch the JD, invoke the resume writer, log the application into `## Queued` as `queued`. Use when the user asks to apply for a specific role or to "do the whole thing" for a URL or digest entry. The row stays in `## Queued` until the user actually submits via the company portal - `/submitted` or `/triage` (on ack) promotes it to `## In Process`.
- **`/submitted <company>`** - flip a `queued` tracker row to `applied` and move it from `## Queued` to `## In Process` after the user has hit Submit on the company portal. Also moves the resume and cover letter PDFs into `output/applied/`. Use when no ack email is expected or when the user wants the tracker accurate immediately.
- **`/decline <company> [reason]`** - remove a queued row the user decided NOT to pursue. Moves the row from `## Queued` to `## Declined` with status `withdrew`, and deletes the resume + cover letter files (keeps the JD). Use when the posting closes, turns out to be part-time, or the user otherwise refuses to apply before submission.
- **`/triage`** - classify recent mail in the job-hunt inbox, update the applications tracker, and schedule interviews on the job-hunt calendar. Also moves rows across sections as status transitions (queued -> ack -> In Process; any -> rejected -> Rejected). Use when the user asks to check their job-search inbox, triage recruiter mail, or process interview invites.
- **`/sync-filters`** - regenerate the discovery anti-target filters from the MCD's Anti-Target Lanes section. Use when the user has updated their MCD's anti-targets, or to check for drift between the MCD and the discovery filter config.

## Workflow

### Generating a resume or cover letter

When asked to generate a resume, cover letter, or both for a job posting, use the **ats-resume-writer** agent. Process:

1. Read the user's Master Career Document
2. Analyze the job description (keywords, tier, lane fit, anti-target check)
3. Produce a tailored `.tex` file using **only** the LaTeX commands defined in `templates/resume-template.tex` (or `cover-letter-template.tex`)
4. Compile to PDF using `pdflatex` - run twice for proper cross-references
5. Clean up all auxiliary files (`.aux`, `.log`, `.out`, `.toc`, `.fls`, `.fdb_latexmk`, etc.) - keep only `.tex` and the final `.pdf`

### File naming conventions

All paths below respect `config.yaml` overrides when present. Without a config, they default to the project root and `output/` subdirectory.

- Job descriptions: `<jd_dir>/Job_Description-[Company]-[Role].md`
- Output resume: `<output_dir>/Resume-[Name]-[Company]-[Role].{tex,pdf}`
- Output cover letter: `<output_dir>/CoverLetter-[Name]-[Company]-[Role].{tex,pdf}`
- Master Career Document: `<mcd_path>` (default: `Master_Career_Document.md` in project root)

### Building or updating a Master Career Document

When asked to build, create, update, or review a Master Career Document, use the **career-doc-builder** agent. It runs a multi-phase interview that produces an 18-section structured MCD.

Usage cues that trigger this agent:
- "Help me build my career document"
- "Create my master career document from my old resumes"
- "Update my MCD with my new role"
- "Review my career document for gaps"

### Running job discovery

When asked to scan for new job postings, run:

```bash
cd discovery && source venv/bin/activate && python main.py
```

Output is written to `discovery/output/digest-YYYY-MM-DD.md`. State (seen-job IDs for dedup) is kept in `discovery/state/seen-jobs.json`. Subsequent runs only surface genuinely new postings.

## Hard constraints

- **Zero fabrication.** Only use information explicitly present in the Master Career Document. Do not infer, embellish, fabricate, or generalize beyond what is stated in the source. Skills, experiences, metrics, and claims not directly supported by the MCD must not appear in generated output.
- **Never commit personal data.** Master Career Documents, job description files, application tracker, and generated resumes are gitignored by pattern. If you notice personal content being staged for commit, stop and flag it.
- **Respect agent notes in the MCD.** Any content marked `> **Agent Note:**` is a binding instruction. Any content under a "Legacy & Historical Platforms" section is automatically excluded from all generated resumes.

## Application tracker

`applications.template.md` in the repo root is the schema for the tracker file. On first use of `/apply`, if `applications_file` doesn't exist, copy this template into place and proceed.

The tracker has four markdown sections with identical columns: Date Applied, Company, Role, Status, Last Update, Score, Files, URL, Notes.

- `## Queued` - resume + cover letter generated, not yet submitted. Status `queued`.
- `## In Process` - submitted and active. Statuses `applied`, `ack`, `screen`, `interview`, `offer`.
- `## Rejected` - the company declined or ghosted. Status `rejected`.
- `## Declined` - roles the user declined to pursue or the framework refused. Statuses `withdrew`, `declined_anti_target`.

A row lives in exactly one section, determined by its Status. When status changes cross a section boundary (e.g., `queued -> applied`, or any status -> `rejected`), the row moves to the new section.

**Files column format:** `[resume](<path>) / [cover](<path>) / [jd](<path>)` - three markdown links. JD is always linked when present; resume and cover are dropped when files are deleted (see file retention rule).

**File retention rule:** resume + cover letter files (`.tex` + `.pdf`) are retained ONLY for roles the user actually submitted. Rows in `## Queued`, `## In Process`, and `## Rejected` keep their files. Rows in `## Declined` have their resume + cover letter files deleted - the user chose not to apply, so the generated materials are not retained. The JD file under `jobs/` stays regardless (it is the record of what the posting said).

**Command responsibilities:**
- `/apply` appends rows to `## Queued` (or `## Declined` if the anti-target check refuses - no resume/cover generated in that case).
- `/submitted` moves a row from `## Queued` to `## In Process`, flips status `queued -> applied`, and moves the resume + cover PDFs into `output/applied/`.
- `/decline` moves a row from `## Queued` to `## Declined` with status `withdrew`, and deletes the resume + cover files (keeps JD).
- `/triage` updates Status + Last Update from inbox mail. Cross-section moves happen automatically: `queued -> ack/screen/interview` promotes the row to `## In Process`; any status -> `rejected` moves the row to `## Rejected` (files retained). Never touches rows in `## Declined`.
- Manual hand-edits are welcome - the format is plain markdown. If you hand-edit a row into `## Declined`, remember to delete the resume + cover files yourself (or just run `/decline` instead).

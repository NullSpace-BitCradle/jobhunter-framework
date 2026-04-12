# jobhunter-framework — Claude Code Instructions

This is a unified job-hunting framework with four integrated capabilities:

1. **Career Document Builder** (`.claude/agents/career-doc-builder.md`) — interactive interview that produces a Master Career Document (MCD)
2. **Job Discovery** (`discovery/main.py`) — Python CLI that scans target company ATS platforms and writes a ranked digest
3. **Resume Writer** (`.claude/agents/ats-resume-writer.md`) — generates ATS-optimized LaTeX resumes and cover letters from an MCD plus a job description
4. **Orchestration** (`.claude/commands/`) — slash commands that chain the other three together and handle application tracking + inbox triage

## Framework config

A user-local `config.yaml` at the repo root (gitignored) defines where personal data lives:

- `mcd_path` — Master Career Document
- `jd_dir` — where Job_Description-*.md files are saved
- `output_dir` — where generated resumes and cover letters land
- `applications_file` — the applications tracker
- `discovery.config_dir`, `discovery.state_file`, `discovery.digest_dir` — discovery runtime paths
- `gmail.enabled`, `gmail.account` — inbox triage (phase 3+)

Every command and the discovery tool read this file at startup. `config.example.yaml` in the repo root is the tracked template; copy to `config.yaml` and customize.

## Slash commands

- **`/discover`** — run a discovery scan and show the top new matches. Wraps the Python scanner and summarizes the resulting digest. Use when the user asks to find new jobs, check for new postings, or run a scan.
- **`/apply <url|company>`** — end-to-end pipeline for a single role: fetch the JD, invoke the resume writer, log the application. Use when the user asks to apply for a specific role or to "do the whole thing" for a URL or digest entry.
- **`/triage`** — classify recent unread mail in the job-hunt inbox, update the applications tracker, and schedule interviews on the job-hunt calendar. Use when the user asks to check their job-search inbox, triage recruiter mail, or process interview invites.
- **`/sync-filters`** — regenerate the discovery anti-target filters from the MCD's Anti-Target Lanes section. Use when the user has updated their MCD's anti-targets, or to check for drift between the MCD and the discovery filter config.

## Workflow

### Generating a resume or cover letter

When asked to generate a resume, cover letter, or both for a job posting, use the **ats-resume-writer** agent. Process:

1. Read the user's Master Career Document
2. Analyze the job description (keywords, tier, lane fit, anti-target check)
3. Produce a tailored `.tex` file using **only** the LaTeX commands defined in `templates/resume-template.tex` (or `cover-letter-template.tex`)
4. Compile to PDF using `pdflatex` — run twice for proper cross-references
5. Clean up all auxiliary files (`.aux`, `.log`, `.out`, `.toc`, `.fls`, `.fdb_latexmk`, etc.) — keep only `.tex` and the final `.pdf`

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

Tracker columns: Date Applied, Company, Role, Status, Last Update, Score, Files, URL, Notes. Status progresses through: `queued → applied → ack → screen → interview → offer | rejected | withdrew`.

`/apply` appends rows on submission. `/triage` updates Status and Last Update when it sees relevant mail. Manual hand-edits are welcome — the format is plain markdown.

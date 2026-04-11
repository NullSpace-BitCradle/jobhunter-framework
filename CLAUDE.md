# jobhunter-framework — Claude Code Instructions

This is a unified job-hunting framework with three integrated capabilities:

1. **Career Document Builder** (`.claude/agents/career-doc-builder.md`) — interactive interview that produces a Master Career Document (MCD)
2. **Job Discovery** (`discovery/main.py`) — Python CLI that scans target company ATS platforms and writes a ranked digest
3. **Resume Writer** (`.claude/agents/ats-resume-writer.md`) — generates ATS-optimized LaTeX resumes and cover letters from an MCD plus a job description

## Workflow

### Generating a resume or cover letter

When asked to generate a resume, cover letter, or both for a job posting, use the **ats-resume-writer** agent. Process:

1. Read the user's Master Career Document
2. Analyze the job description (keywords, tier, lane fit, anti-target check)
3. Produce a tailored `.tex` file using **only** the LaTeX commands defined in `templates/resume-template.tex` (or `cover-letter-template.tex`)
4. Compile to PDF using `pdflatex` — run twice for proper cross-references
5. Clean up all auxiliary files (`.aux`, `.log`, `.out`, `.toc`, `.fls`, `.fdb_latexmk`, etc.) — keep only `.tex` and the final `.pdf`

### File naming conventions

- Job descriptions: `Job_Description-[Company]-[Role].md`
- Output resume: `output/Resume-[Name]-[Company]-[Role].{tex,pdf}`
- Output cover letter: `output/CoverLetter-[Name]-[Company]-[Role].{tex,pdf}`

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

## Phase-aware behavior

This framework is being built in phases. The current phase status is tracked in `README.md`. Orchestration slash commands (`/apply`, `/discover`, `/triage`) arrive in Phase 2; Gmail/Calendar integration arrives in Phase 3. Until then, each capability is invoked directly.

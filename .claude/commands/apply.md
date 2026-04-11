---
description: Fetch a JD, tailor resume + cover letter, log the application
argument-hint: <url|company> [role hint]
---

# /apply — End-to-end application pipeline

Chain the three jobhunter-framework capabilities into one flow for a single role:
**fetch JD → tailor resume + cover letter → log to tracker.**

User input: `$ARGUMENTS`

## Step 1 — Load config

Read `config.yaml` at the framework repo root. Expand `~` in all paths. Resolve:

- `mcd_path` — Master Career Document the resume writer will read
- `jd_dir` — where to save the fetched Job_Description-*.md file
- `output_dir` — where the resume writer will write `.tex` and compiled `.pdf`
- `applications_file` — tracker to append to
- `discovery.digest_dir` — needed only if input is a company name rather than a URL

If `config.yaml` is missing, warn the user and fall back to the project root for JDs, `./output` for output, and `./applications.md` for the tracker.

## Step 2 — Acquire the job description

The user's input is one of:

**A URL** — use the WebFetch tool to retrieve the page. Extract:
- Company name (from page title, metadata, or URL)
- Job title
- Location
- Posted date (if visible)
- Full description text, stripped of nav / footer / cookie banners

If the fetch returns 403, an empty body, or obviously wrong content, **stop and ask the user to paste the JD text directly.** Do not fabricate.

**A company or role name** — read the most recent `<discovery.digest_dir>/digest-YYYY-MM-DD.md`. Find the matching entry (ask the user to disambiguate if there are multiple). Extract the URL from that entry, then proceed as URL case.

**An already-saved JD path** — if the user hands you a path to an existing `Job_Description-*.md`, skip straight to Step 3.

### Save the JD file

Write to `<jd_dir>/Job_Description-<Company>-<Role>.md` using this naming convention. Sanitize company + role for filesystem safety (replace spaces with `_`, strip `/`, `:`, `\`, quotes, etc.).

File format:

```markdown
# <Company> — <Role>

**Source:** <url>
**Fetched:** <YYYY-MM-DD>
**Location:** <location>

---

<full JD text>
```

After saving, show the user the resulting path and ask for explicit confirmation before proceeding to Step 3. This is the last exit before spending tokens on the resume writer.

## Step 3 — Tailor resume + cover letter

Invoke the **ats-resume-writer** agent pointing at the newly-saved JD file. Let it execute its full workflow — do not short-circuit any of its steps:

1. Stretch-fit assessment — if the agent warns the JD is a stretch, **surface the warning to the user and stop.** Do not proceed without explicit confirmation.
2. Anti-target check — if the agent refuses due to an MCD anti-target match, skip to Step 4 and log the application as `declined_anti_target` with the reason in the Notes column.
3. Lane selection, crown-jewel placement, keyword mapping, LaTeX compilation via `pdflatex` (run twice for cross-refs).

Output files land in `<output_dir>` following the naming convention:
`Resume-<Name>-<Company>-<Role>.{tex,pdf}` and `CoverLetter-<Name>-<Company>-<Role>.{tex,pdf}`.

## Step 4 — Log the application

Read `<applications_file>`. If it does not exist, create it by copying `applications.template.md` from the framework repo root, then proceed.

Append a new row to the table:

| Column | Value |
|---|---|
| Date Applied | today (YYYY-MM-DD) |
| Company | from the JD |
| Role | from the JD |
| Status | `applied` (or `declined_anti_target` if Step 3 refused) |
| Last Update | today |
| Score | from the digest entry if one existed, else blank |
| Files | `[resume](<path>) / [cover](<path>)` as markdown links, or blank if declined |
| URL | source URL |
| Notes | stretch-fit reason, refusal reason, or blank |

Preserve all existing rows untouched. Append at the top of the table (newest first) unless the existing file is clearly ordered oldest-first, in which case append at the bottom.

## Step 5 — Report

Return a concise summary:

```
JD saved:       <path>
Resume:         <path>
Cover letter:   <path>
Tracker:        row added to <applications_file>
Next step:      review the PDF, then submit via the company's portal
```

Keep it to those lines unless there was a warning or refusal — those get one extra line explaining what happened.

## Hard constraints

- **Zero fabrication in the JD file.** Only the text WebFetch actually retrieved. If parsing fails, stop and ask.
- **Never modify the MCD from `/apply`.** That is the `career-doc-builder` agent's job.
- **Confirm before LaTeX compilation.** Step 2 ends with an explicit pause.
- **Preserve tracker history.** Never rewrite old rows in Step 4 — only append.

---
description: Fetch a JD, tailor resume + cover letter, log the application
argument-hint: <url|company> [role hint]
---

# /apply - End-to-end application pipeline

Chain the three jobhunter-framework capabilities into one flow for a single role:
**fetch JD → tailor resume + cover letter → log to tracker.**

User input: `$ARGUMENTS`

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~` in all paths. Resolve:

- `mcd_path` - Master Career Document the resume writer will read
- `jd_dir` - where to save the fetched Job_Description-*.md file
- `output_dir` - where the resume writer will write `.tex` and compiled `.pdf`
- `applications_file` - tracker to append to
- `discovery.digest_dir` - needed only if input is a company name rather than a URL

If `config.yaml` is missing, warn the user and fall back to the project root for JDs, `./output` for output, and `./applications.md` for the tracker.

## Step 2 - Acquire the job description

The user's input is one of:

**A URL** - before WebFetch, check the board-descriptions cache at `<discovery.state_file parent>/board-descriptions.json` (default: `~/JobHunt/discovery/state/board-descriptions.json`). `/discover` persists JD bodies here for every LinkedIn / Indeed / Glassdoor posting it pulls via JobSpy. Compare the submitted URL's canonical form against cache keys. To canonicalize, run:

```bash
cd "$(git rev-parse --show-toplevel)/discovery" && source venv/bin/activate && python main.py --normalize-url "<url>"
```

If the normalized URL has a cache hit, use the cached `description`, `company`, `title`, `location`, and `posted_at` fields directly instead of WebFetch. This avoids LinkedIn 403s and rate limits on URLs the scanner has already fetched. Cached entries are pruned after 60 days, so very old digest URLs will still require WebFetch. Only fall back to WebFetch if the cache misses.

If the cache misses (or the input is an ATS URL not covered by JobSpy), use the WebFetch tool. Extract:
- Company name (from page title, metadata, or URL)
- Job title
- Location
- Posted date (if visible)
- Full description text, stripped of nav / footer / cookie banners

**Canonicalize the URL before recording.** If the submitted URL is on LinkedIn, Indeed, Glassdoor, or a similar aggregator AND the fetched page reveals a direct-apply URL on a supported ATS (`boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `jobs.smartrecruiters.com`, `apply.workable.com`), use the ATS URL as the URL-of-record from here on. ATS URLs are more stable, match what appears in company ack emails, and give `/triage` a clean key to correlate mail against. The user's submitted URL stays accurate in the JD file frontmatter ("Source:") but the tracker, JD filename components, and all downstream references use the canonical URL.

If WebFetch returns 403, an empty body, or obviously wrong content, **stop and ask the user to paste the JD text directly.** Do not fabricate.

**A company or role name** - read the most recent `<discovery.digest_dir>/digest-YYYY-MM-DD.md`. Find the matching entry (ask the user to disambiguate if there are multiple). Extract the URL from that entry, then proceed as URL case.

**An already-saved JD path** - if the user hands you a path to an existing `Job_Description-*.md`, skip straight to Step 3.

### Save the JD file

Write to `<jd_dir>/Job_Description-<Company>-<Role>.md` using this naming convention. Sanitize company + role for filesystem safety (replace spaces with `_`, strip `/`, `:`, `\`, quotes, etc.).

File format:

```markdown
# <Company> - <Role>

**Source:** <url>
**Fetched:** <YYYY-MM-DD>
**Location:** <location>

---

<full JD text>
```

After saving, show the user the resulting path and ask for explicit confirmation before proceeding to Step 3. This is the last exit before spending tokens on the resume writer.

## Step 3 - Tailor resume + cover letter

Invoke the **ats-resume-writer** agent pointing at the newly-saved JD file. Let it execute its full workflow - do not short-circuit any of its steps:

1. Stretch-fit assessment - if the agent warns the JD is a stretch, **surface the warning to the user and stop.** Do not proceed without explicit confirmation.
2. Anti-target check - if the agent refuses due to an MCD anti-target match, skip to Step 4 and log the application as `declined_anti_target` with the reason in the Notes column.
3. Lane selection, crown-jewel placement, keyword mapping, LaTeX compilation via `pdflatex` (run twice for cross-refs).

Output files land in `<output_dir>` following the naming convention:
`Resume-<Name>-<Company>-<Role>.{tex,pdf}` and `CoverLetter-<Name>-<Company>-<Role>.{tex,pdf}`.

## Step 3.5 - Duplicate check

Before logging, scan the existing `<applications_file>` for any row whose URL column matches the source URL. Compare using the canonical form - tracking parameters (`?utm_source=...`, `?trk=...`, `?refId=...`) vary between sessions and must not defeat the match. To get a canonical form for comparison, run:

```bash
cd "$(git rev-parse --show-toplevel)/discovery" && source venv/bin/activate && python main.py --normalize-url "<url>"
```

Do this for both the incoming URL (from Step 2) and each candidate row's URL (strip markdown auto-link wrappers `<url>` and `[text](url)` first). Also match on exact `Company + Role` even if the URL differs, since the same role can be re-posted at a new URL.

If a duplicate is found, behavior depends on which section the existing row is in:

- **`## Queued` or `## In Process`** (statuses `queued`/`applied`/`ack`/`screen`/`interview`/`offer`): **stop and surface the match**. Show the user: "Already in tracker as `<status>` from `<date>` (in `## <section>`). Apply again anyway?" Do not proceed without explicit confirmation. On confirm, proceed to Step 4 and append a new row (do not edit the existing row).
- **`## Rejected`** (status `rejected`): surface the prior status and reason, then ask: "Previously rejected: `<reason>`. Still want to apply?" On confirm, proceed.
- **`## Declined`** (status `withdrew` or `declined_anti_target`): surface the prior status and reason, then ask: "Previously `<status>`: `<reason>`. Still want to apply?" On confirm, proceed.

If no duplicate is found, proceed straight to Step 4.

## Step 4 - Log the application

Read `<applications_file>`. If it does not exist, create it by copying `applications.template.md` from the framework repo root, then proceed.

The tracker has four sections, each with identical columns:

- `## Queued` - status `queued` (generated, not yet submitted)
- `## In Process` - statuses `applied` / `ack` / `screen` / `interview` / `offer`
- `## Rejected` - status `rejected`
- `## Declined` - statuses `withdrew` / `declined_anti_target`

If the file was created before this split and is missing any of the four section headers, add the missing sections at the bottom using the headers from `applications.template.md` before appending.

Append a new row with these values:

| Column | Value |
|---|---|
| Date Applied | today (YYYY-MM-DD) |
| Company | from the JD |
| Role | from the JD |
| Status | `queued` (or `declined_anti_target` if Step 3 refused) - `/apply` only generates materials. The user still submits manually via each portal; `/triage` or `/submitted` flips the row to `applied` / `ack` later. |
| Last Update | today |
| Score | from the digest entry if one existed, else blank |
| Files | `[resume](<resume_path>) / [cover](<cover_path>) / [jd](<jd_path>)` as markdown links. For `declined_anti_target` rows no resume/cover was generated, so just `[jd](<jd_path>)`. |
| URL | source URL |
| Notes | stretch-fit reason, refusal reason, or blank |

**Row routing:**

- Status `queued` -> append to `## Queued`
- Status `declined_anti_target` -> append to `## Declined`
- (`/apply` never produces other statuses directly; `/submitted`, `/triage`, and `/decline` handle cross-section moves)

Preserve all existing rows untouched. Append at the top of the target section (newest first) unless the existing file is clearly ordered oldest-first, in which case append at the bottom. Never mix a row into the wrong section.

## Step 5 - Report

Return a concise summary:

```
JD saved: <path>
Resume: <path>
Cover letter: <path>
Tracker: row added to <applications_file>
Next step: review the PDF, submit via the company's portal, then run /submitted <company> (or wait for /triage to see the ack)
```

Keep it to those lines unless there was a warning or refusal - those get one extra line explaining what happened.

## Hard constraints

- **Zero fabrication in the JD file.** Only the text WebFetch actually retrieved. If parsing fails, stop and ask.
- **Never modify the MCD from `/apply`.** That is the `career-doc-builder` agent's job.
- **Confirm before LaTeX compilation.** Step 2 ends with an explicit pause.
- **Preserve tracker history.** Never rewrite old rows in Step 4 - only append.

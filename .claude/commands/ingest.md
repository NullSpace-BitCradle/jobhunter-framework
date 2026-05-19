---
description: Ingest manually-found job postings (URLs or pasted text) through the discovery pipeline
argument-hint: <urls inline | path to file with one URL per line>
---

# /ingest - Feed manually-found postings through the discovery pipeline

You found roles on LinkedIn, Indeed, a company's careers page, a recruiter email, or anywhere else. This command treats them as if they were discovered by `/discover`: same anti-target filter, same keyword and location rules, same scoring, same candidate-company tracking, same digest output. Use this to get a consistent signal on ad-hoc finds before deciding which ones to `/apply` for.

User input: `$ARGUMENTS`

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~` in all paths. Resolve:

- `discovery.state_file` - state dir where candidate-companies.json lives
- `discovery.digest_dir` - where the ingest digest will be written
- `applications_file` - the tracker (for previously-declined URL warnings)

If `config.yaml` is missing, warn the user and fall back to `discovery/state/seen-jobs.json` and `discovery/output/` relative to the repo.

## Step 2 - Collect URLs

Parse `$ARGUMENTS` into a list of URLs. Accept any of:

- **Inline URLs** - one or more URLs separated by whitespace or newlines in the command arguments
- **File path** - a path to a plain-text file with one URL per line (lines starting with `#` are comments; blank lines ignored)
- **Prose with URLs** - pull URLs out of free-form text, even if the user wraps them in sentences

If the user's input contains no recognizable URLs and no file path exists, ask them to paste the URLs one per line. Wait for their response before proceeding.

Deduplicate the URL list (keep first occurrence).

## Step 3 - Fetch and parse each URL

For each URL, use the WebFetch tool to retrieve the page. Extract:

- **Company** (from page title, Open Graph metadata, structured data, or URL)
- **Title** (the job title)
- **Location** (city, remote status - normalize to something like "Remote, USA" or "NYC, NY")
- **Posted date** (ISO-8601 if the page shows it, else omit)
- **Full description text** (strip navigation, footer, cookie banners, "apply now" boilerplate; keep enough of the JD body that anti-target and keyword matching has real signal to work with - aim for 2-5KB of body text)

**On fetch failure** (403, empty body, obviously wrong content, bot-detection):

- Tell the user: "WebFetch failed for `<url>`: `<short reason>`. Paste the JD body text here, or skip with `skip`."
- Wait for their response.
- If they paste, use that as the description and do a best-effort extraction of title/company from the pasted text. Ask them to confirm company + title if you can't extract cleanly.
- If they say `skip`, drop the URL and move to the next one.

**LinkedIn specifics:** LinkedIn routinely 403s on WebFetch. Expect to fall back to paste for LinkedIn URLs. Job postings on LinkedIn often include an "Apply on company website" link; if the user's paste includes a URL that resolves to a supported ATS (`boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `jobs.smartrecruiters.com`, `apply.workable.com`), **prefer that direct URL as the recorded URL** - it gives cleaner fetches, lets candidate tracking auto-detect the ATS slug, and matches what the company uses in ack emails (so `/triage` correlates mail correctly later).

**Canonicalization rule:** If WebFetch on the submitted LinkedIn/Indeed URL lands on (via redirect) or reveals (via "Apply at" link, Open Graph metadata, or structured data) an ATS URL, use the ATS URL as the `url` field in the posting JSON. Store the original aggregator URL nowhere - the ATS URL is the URL-of-record for this posting.

## Step 4 - Write postings JSON

Build a JSON array where each element has this schema:

```json
{
  "url": "<original URL as submitted>",
  "title": "<extracted job title>",
  "company": "<extracted company name>",
  "location": "<location string, empty if unknown>",
  "description": "<cleaned JD body text>",
  "posted_at": "<ISO date or null>",
  "remote": true
}
```

Include `remote: true/false` only if you can tell explicitly from the posting. Omit the field if unclear; the ingest script will infer from the location string.

Write the array to a temp file under `$TMPDIR`, e.g.:

```bash
TMPFILE="${TMPDIR:-/tmp}/jobhunter-ingest-$(date -u +%Y%m%d-%H%M%S).json"
```

Or let `mktemp` pick a path: `TMPFILE=$(mktemp --suffix=.json)`.

## Step 5 - Run the ingest pipeline

Execute from the discovery venv:

```bash
cd "$(git rev-parse --show-toplevel)/discovery" && source venv/bin/activate && python main.py --ingest "$TMPFILE"
```

Capture stdout. The script writes a timestamped `ingest-YYYY-MM-DD-HHMMSS.md` digest to `<discovery.digest_dir>` and prints a summary line like:

```
Matches: 2 | Anti-target: 0 | Filtered: 1 | Prev-declined: 0
Digest written: /path/to/ingest-2026-04-17-193045.md
```

## Step 6 - Report

Read the generated ingest digest and present a compact summary to the user:

```
Ingested N posting(s).

Matches (M):
  [score] Company - Title
    Location | URL
  ...

Anti-target (A):
  Company - Title -> Reason
  ...

Filtered (F):
  Company - Title -> "<rejection reason>"
  ...

Previously declined (P):
  Company - Title (was <status> on <date>)
  ...

Full digest: <path>
```

Omit any section that is empty. Keep it tight - no more than 3-5 lines per match.

End with a prompt:

```
Run /apply <url|company> for any of these? Reply with one or more URLs, or "none".
```

Do not invoke `/apply` automatically - wait for explicit user instruction.

## Step 7 - Cleanup

After the ingest script finishes, delete the temp postings JSON (`rm -f "$TMPFILE"`). The digest in the user's `digest_dir` is the durable record; the temp file is not needed.

## Hard constraints

- **Zero fabrication.** The fields passed to `--ingest` must come from WebFetch output or the user's explicit paste. If you cannot parse a field confidently, either ask the user or omit it rather than inventing.
- **Confirm before ambiguous extractions.** If WebFetch returns a page where title or company is genuinely unclear, ask the user to confirm rather than guessing.
- **Never update the applications tracker from /ingest.** That is `/apply`'s job. This command is read-only against `applications.md`.
- **Never invoke /apply implicitly.** Always wait for the user's explicit next instruction.
- **Dedupe URLs.** The same URL submitted twice in one run should only be processed once; list the earlier occurrence in the summary so the user knows.

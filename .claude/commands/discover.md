---
description: Run a discovery scan and show the top new matches
argument-hint: [--verify | --dry-run | -v]
---

# /discover - Scan ATS platforms and job boards for new matching roles

Thin wrapper around the Python discovery tool. Runs the scan (direct ATS APIs + job board aggregation via JobSpy if enabled in `keywords.yaml`), then parses the resulting digest and shows a compact summary so the user can quickly pick what to `/apply` for. Board-sourced results are tagged `_(via board)_` in the digest.

User arguments (if any): `$ARGUMENTS`

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~`. Need:

- `discovery.digest_dir` - where to find the latest digest after the scan

If missing, fall back to `discovery/output/` at the repo root.

## Step 2 - Run the scanner

Execute from the framework repo:

```bash
cd discovery && source venv/bin/activate && python main.py $ARGUMENTS
```

Stream stdout so the user sees progress. If the user passed `--verify` or `--dry-run`, those get forwarded through and you should NOT try to summarize a digest afterward (verify produces no digest; dry-run may still produce one but the user is just sanity-checking).

## Step 3 - Summarize the digest

Read `<discovery.digest_dir>/digest-YYYY-MM-DD.md` (today's date, or the most recent file if today's is missing). Show the user:

**Header stats** - one line each:
```
Companies scanned: N
Total jobs fetched: N
New since last run: N
Matches: N
Anti-target skipped: N
```

**Top 10 matches by score** - one line each, compact:
```
 [score] Company - Role (tier)
 → <url>
```

Order by score descending. If there are fewer than 10 matches, show all of them. If there are zero, say so plainly.

**Do not dump the full digest.** If the user wants it, tell them the file path.

## Step 4 - Prompt next action

End with:

```
Want me to /apply for any of these? Give me a number from the list above, a company name, or a URL.
```

Do not actually invoke `/apply` yourself - wait for the user's response.

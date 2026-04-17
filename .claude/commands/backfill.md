---
description: Surface matched jobs from past digests that were never logged to the tracker
argument-hint: [--days N] [--limit N] [--min-score N] [--max-score N]
---

# /backfill - Resurface missed opportunities from past digests

Walks back over recent `digest-YYYY-MM-DD.md` files, extracts every matched job, cross-references against the applications tracker, and surfaces anything that was NEVER acted on. Useful two ways:

- **Cast a wider net.** New postings light this week? Run `/backfill` to see what slipped through in the last month.
- **Fill gaps by score band.** Pair with `--max-score N` to focus on lower-scored matches you reasonably skipped at the time but would apply to now if you had the capacity.

Matches in the tracker at ANY status get excluded (queued / applied / ack / screen / interview / offer / rejected / withdrew / declined_anti_target) - a row means you already made a decision. Exclusion uses both URL and (company, title) so historical digests with LinkedIn URLs still correctly match tracker rows that hold the ATS URL for the same role.

User arguments (if any): `$ARGUMENTS`

## Argument parsing

Parse `$ARGUMENTS` for optional flags, all forwarded to the Python backend:

- `--days N` - window in days (default 30; `--days 0` = all time)
- `--limit N` - max candidates shown (default 30)
- `--min-score N` - only include highest-ever score >= N
- `--max-score N` - only include highest-ever score <= N (useful for the lower-scored gap-fill case)

Pass through any recognized flags verbatim. Ignore unrecognized flags with a one-line warning.

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~`. Resolve:

- `discovery.digest_dir` - where past digests live (and where the backfill digest gets written)
- `applications_file` - the tracker to cross-reference against

If `config.yaml` is missing, fall back to `discovery/output/` and `applications.md` relative to the repo.

## Step 2 - Run the backfill

Execute from the discovery venv:

```bash
cd <repo>/discovery && source venv/bin/activate && python main.py --backfill $ARGUMENTS
```

The script writes a timestamped `backfill-YYYY-MM-DD-HHMMSS.md` to `<discovery.digest_dir>` and prints a summary line like:

```
Backfill: 4 missed opportunity/ies across 4 digest(s) (last 30d). Top 4 written to <path>
```

## Step 3 - Report

Read the generated backfill digest and present a compact summary:

```
N missed opportunity/ies across D digest(s) (last Wd).

  [score]  Company - Title  (tier)
           seen: <date or range>  |  URL
  ...

Full digest: <path>
```

Top 10 in the summary; mention the remaining count if more exist. Include the appearance count and latest score parenthetically when a candidate appeared in multiple digests (the Python script emits that detail in the md; preserve the signal).

If zero candidates came back:

```
Nothing to backfill in the last W days. You're caught up.
```

End with a prompt:

```
Run /apply <url|company> for any of these? Reply with one or more URLs, or "none".
```

Do not invoke `/apply` automatically - wait for the user.

## Hard constraints

- **Read-only against applications.md.** This command never writes to the tracker.
- **Read-only against state files.** No updates to seen-jobs.json or candidate-companies.json.
- **Do not invoke /apply.** Show the user what's there; let them pick.
- **Respect the scoring signal.** Sort by highest-ever score descending (with recency as tiebreaker) so the top of the list is the best-fit missed opportunity. If the user wanted lower-scored only, they would have passed `--max-score`.

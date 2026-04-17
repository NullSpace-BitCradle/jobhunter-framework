---
description: Flip a queued application row to `applied` after you submit via the company portal
argument-hint: <company> [role hint]
---

# /submitted - Mark a queued application as actually submitted

`/apply` generates the resume + cover letter and stacks the row at `queued`. This command flips it to `applied` once the user has actually hit Submit on the company's portal. Use it when no ack email is expected (some portals don't send one) or when the ack will take days and the user wants the tracker accurate now.

User input: `$ARGUMENTS`

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~`. Need `applications_file`.

## Step 2 - Find the row

Read the tracker file. Match `$ARGUMENTS` against rows where Status is `queued`:

- Exact company name match → use it.
- Substring match on company or role → if exactly one, use it.
- Multiple matches → list them and ask the user to disambiguate (company + role + date).
- Zero matches among `queued` rows → check if a non-queued row exists for the same company. If one does, show its current status and ask whether the user wants to update notes instead or if this is a different role. If no match at all, say so plainly and stop.

## Step 3 - Update the row

For the chosen row:
- Status: `queued` → `applied`
- Last Update: today (YYYY-MM-DD)
- Leave Date Applied, Company, Role, Score, Files, URL, Notes untouched.

## Step 4 - Report

```
<Company> - <Role>: queued → applied (<date>)
```

One line. If multiple rows were updated (user ran `/submitted <company>` and it matched two queued roles at the same company), show one line per row.

## Hard constraints

- **Never regress status.** If the row is already `applied`/`ack`/`screen`/etc., stop and tell the user the current status - do not overwrite.
- **Never modify `declined_anti_target` rows.** Those are terminal.
- **One row per invocation unless the user explicitly asks for "all queued at X".**

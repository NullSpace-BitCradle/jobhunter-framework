---
description: Decline a queued application, move the row to Declined, delete generated materials
argument-hint: <company> [role hint] [reason]
---

# /decline - Withdraw a queued application before submission

`/apply` generates the resume + cover letter and stacks the row at `queued`. This command is the counterpart to `/submitted`: use it when the user has decided NOT to pursue a queued role. Common reasons:

- Posting closed before submission ("No Longer Accepting Applications")
- Role turned out to be part-time, contract-only, or otherwise outside scope after closer reading
- Compensation floor not met and recruiter confirmed
- User simply changed their mind

The command:

1. Moves the row from `## Queued` to `## Declined`
2. Sets Status to `withdrew`
3. Deletes the resume + cover letter files (`.tex` + `.pdf`). Keeps the JD file.
4. Updates the row's Notes with the decline reason.

User input: `$ARGUMENTS`

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~`. Need `applications_file` and `output_dir`.

## Step 2 - Parse arguments

`$ARGUMENTS` format: `<company> [role hint] [reason]`.

Simple heuristic: if `$ARGUMENTS` contains the word `because`, everything after it is the reason. Otherwise, treat the first token as company, optional next token(s) as role hint, and any trailing quoted string or clause as reason. If no reason is supplied, prompt the user briefly: "Reason for declining? (e.g., 'posting closed', 'part-time', 'comp below floor', 'changed mind')". Accept whatever they give.

## Step 3 - Find the row

Read the tracker. Match against rows in the `## Queued` section:

- Exact company name match -> use it.
- Substring match on company or role -> if exactly one, use it.
- Multiple matches -> list them (company + role + date) and ask the user to disambiguate.
- Zero matches in `## Queued` -> check other sections:
  - If the matching row is in `## In Process`: stop and tell the user. A submitted application cannot be declined via this command - they should either wait for the outcome or manually edit.
  - If the matching row is already in `## Declined` or `## Rejected`: stop and tell the user the row is already terminal.
  - If no match anywhere: say so plainly and stop.

## Step 4 - Delete generated materials

Parse the Files column of the chosen row. The format is markdown links like:

```
[resume](~/JobHunt/output/Resume-Sample_Candidate-Foo-Bar.pdf) / [cover](~/JobHunt/output/CoverLetter-Sample_Candidate-Foo-Bar.pdf) / [jd](~/JobHunt/jobs/Job_Description-Foo-Bar.md)
```

Extract the resume and cover letter paths (the `[resume](...)` and `[cover](...)` links). For each:

1. Resolve `~/` to the user's home.
2. Delete the `.pdf` file if it exists.
3. Delete the matching `.tex` file (same basename) if it exists.
4. If the file does not exist, log it and continue - do not error.

**Do NOT delete the JD file** (the `[jd](...)` link). The JD is the record of what the posting said and stays under `jobs/`.

If the Files column is blank or the row is `declined_anti_target` style (no files generated), skip this step entirely.

## Step 5 - Move the row

1. Update the row:
   - Status: `queued` -> `withdrew`
   - Last Update: today (YYYY-MM-DD)
   - Files: clear the resume and cover letter links. Keep the `[jd](...)` link if present. Result is either `[jd](path)` alone or blank.
   - Notes: prepend `Declined <YYYY-MM-DD> via /decline: <reason>. ` to any existing Notes content. Keep the rest.

2. Remove the row from `## Queued`.

3. Append the row to `## Declined`, newest first (same ordering as existing rows in that section).

Preserve all other rows untouched. Never modify rows in `## In Process`, `## Rejected`, or elsewhere in `## Declined`.

## Step 6 - Report

One-line summary:

```
<Company> - <Role>: queued -> withdrew (Declined). Deleted: <N file(s)>. Reason: <reason>.
```

If files were missing or the row had no generated materials:

```
<Company> - <Role>: queued -> withdrew (Declined). No files to delete. Reason: <reason>.
```

## Hard constraints

- **Queued rows only.** Never touch rows already in `## In Process`, `## Rejected`, or `## Declined`. A submitted application is not in scope for `/decline` - the user should hand-edit if they really want to withdraw a submitted one.
- **Never delete the JD file.** Only the resume and cover letter files are deleted. JD stays.
- **Never delete files listed in other rows.** If the same file name somehow appears in multiple rows (shouldn't happen under the canonical naming), only delete files bound to the declined row.
- **Preserve tracker history.** The row moves and gets an updated Note; it is not deleted from the tracker.

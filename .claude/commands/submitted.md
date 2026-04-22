---
description: Flip a queued application row to `applied` after you submit via the company portal
argument-hint: <company> [role hint]
---

# /submitted - Mark a queued application as actually submitted

`/apply` generates the resume + cover letter and stacks the row under `## Queued`. This command flips its status to `applied`, moves the row from `## Queued` to `## In Process`, and relocates the resume and cover letter PDFs (and `.tex`) into the `applied/` subdirectory. Use it when no ack email is expected (some portals don't send one) or when the ack will take days and the user wants the tracker accurate now.

User input: `$ARGUMENTS`

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~`. Need `applications_file` and `output_dir`.

## Step 2 - Find the row

Read the tracker file. Match `$ARGUMENTS` against rows in the `## Queued` section:

- Exact company name match -> use it.
- Substring match on company or role -> if exactly one, use it.
- Multiple matches -> list them and ask the user to disambiguate (company + role + date).
- Zero matches in `## Queued` -> check other sections. If a row for the same company exists in `## In Process`, `## Rejected`, or `## Declined`, show its current status and ask whether the user wants to update notes instead or if this is a different role. If no match anywhere, say so plainly and stop.

## Step 3 - Update the row

For the chosen row:
- Status: `queued` -> `applied`
- Last Update: today (YYYY-MM-DD)
- Leave Date Applied, Company, Role, Score, URL, Notes untouched. Files column is updated in Step 3.5.

## Step 3.5 - Move generated files to `applied/`

Parse the Files column of the chosen row. The format is markdown links like:

```
[resume](~/JobHunt/output/Resume-Sample_Candidate-Foo.pdf) / [cover](~/JobHunt/output/CoverLetter-Sample_Candidate-Foo.pdf) / [jd](~/JobHunt/jobs/Job_Description-Foo-Bar.md)
```

Extract the linked `[resume]` and `[cover]` PDF paths. **Leave the `[jd]` link alone** - the JD file stays under `jobs/` regardless of application state.

For the `[resume]` and `[cover]` PDFs:

1. Resolve `~/` to the user's home and normalize.
2. Compute the destination as `<output_dir>/applied/<filename>`. Create `<output_dir>/applied/` if it does not exist.
3. Move the `.pdf`. Also move the matching `.tex` if one exists at the same basename (the LaTeX source should travel with the compiled PDF).
4. If the source file is already under `applied/`, or the source does not exist (previously moved or manual cleanup), log it and continue - do not error.
5. Never move across filesystems blindly; `mv` within the same volume is fine. If the destination already exists, leave the source alone and flag it in the report.

After moving, rewrite the Files column so the resume and cover links point to the new `applied/` path. Preserve the existing link labels and the ` / ` separator. The `[jd]` link stays untouched.

If the Files column is empty or has only the `[jd]` link (no resume/cover generated), skip this step entirely - nothing to move.

## Step 4 - Move the row to `## In Process`

1. Remove the row from `## Queued`.
2. Append the row to `## In Process` at the top (newest first) unless the section is clearly oldest-first.

Preserve all other rows untouched.

## Step 5 - Report

```
<Company> - <Role>: queued -> applied (<date>). Row moved to ## In Process. Files moved to applied/.
```

One line. If multiple rows were updated (user ran `/submitted <company>` and it matched two queued roles at the same company), show one line per row.

## Hard constraints

- **Never regress status.** If the row is already `applied`/`ack`/`screen`/etc., stop and tell the user the current status - do not overwrite.
- **Never modify rows in `## Rejected` or `## Declined`.** Those are terminal.
- **Never delete the JD file.** Only the resume and cover letter files move; the JD stays under `jobs/`.
- **One row per invocation unless the user explicitly asks for "all queued at X".**

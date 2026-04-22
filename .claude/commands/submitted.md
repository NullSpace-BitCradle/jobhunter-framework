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
- Leave Date Applied, Company, Role, Score, URL, Notes untouched. Files column is updated in Step 3.5.

## Step 3.5 - Move generated files to `applied/`

Parse the Files column of the chosen row. The format is markdown links like `[resume](~/JobHunt/output/Resume-Sample_Candidate-Foo.pdf) / [cover](~/JobHunt/output/CoverLetter-Sample_Candidate-Foo.pdf)`. Extract each linked PDF path.

For every linked PDF:
1. Resolve `~/` to the user's home and normalize.
2. Compute the destination as `<output_dir>/applied/<filename>`. Create `<output_dir>/applied/` if it does not exist.
3. Move the `.pdf`. Also move the matching `.tex` if one exists at the same basename (the LaTeX source should travel with the compiled PDF).
4. If the source file is already under `applied/`, or the source does not exist (previously moved or manual cleanup), log it and continue - do not error.
5. Never move across filesystems blindly; `mv` within the same volume is fine. If the destination already exists, leave the source alone and flag it in the report.

After moving, rewrite the Files column so every link points to the new `applied/` path. Preserve the existing link labels (`resume`, `cover`, any others) and the ` / ` separator.

If the Files column is empty or `declined_anti_target`-style blank, skip this step entirely - nothing to move.

## Step 4 - Report

```
<Company> - <Role>: queued → applied (<date>)
```

One line. If multiple rows were updated (user ran `/submitted <company>` and it matched two queued roles at the same company), show one line per row.

## Hard constraints

- **Never regress status.** If the row is already `applied`/`ack`/`screen`/etc., stop and tell the user the current status - do not overwrite.
- **Never modify `declined_anti_target` rows.** Those are terminal.
- **One row per invocation unless the user explicitly asks for "all queued at X".**

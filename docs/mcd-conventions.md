# MCD conventions

The Master Career Document (MCD) is free-form markdown, but a few markers change what the resume writer and `/sync-filters` do. The `career-doc-builder` agent writes all of them when you build or update your MCD; this page is the reference for editing by hand.

[`examples/career-document.example.md`](../examples/career-document.example.md) is a short, fictional MCD for a nurse returning from a career break that uses every marker below, and [`examples/resume.example.tex`](../examples/resume.example.tex) is a resume built from it. `bun test tools/` checks that the resume passes the zero-fabrication validator.

## One rule behind all of them

The validator (`tools/validate.ts`) treats the MCD body as evidence and ignores agent notes: a note that says "never list CISSP" must not count as a CISSP. So:

- **Agent notes say what to do.** `> **Agent Note:** ...` lines are binding instructions for the writer.
- **Anything that should appear on a resume lives in the body.** A metric, a reason, or a phrase that exists only inside a note fails validation, and the writer leaves it out.

## Markers

| Marker | Where | What it does |
|---|---|---|
| `### Positioning Options` | under `## Notes for Resume Customization` | 2 to 8 lanes the writer chooses from. One bullet per lane: name, role types, what to lead with. |
| `> **Agent Note (Default Lane):** <lane>` | after the Positioning Options list | The lane the writer prefers when a job fits several equally. |
| `### Anti-Target Lanes` | under `## Notes for Resume Customization` | Roles not to apply to, one bullet each with a reason. The writer refuses to tailor for a match and `/apply` logs it as `declined_anti_target`; `/sync-filters` turns the list into discovery filters. |
| `### Target Companies by Lane` | under `## Notes for Resume Customization` | Optional. Employers or kinds of employers per lane. |
| `### Recency` | under `## Notes for Resume Customization` | Optional. `- Recency window: N years` and `- Role cutoff: N years` (or `none`). See below. |
| `> **Agent Note: CROWN JEWEL** ...` | right after the achievement it points at | Puts that achievement in the resume summary for the lanes it names. The note points; the facts stay in the bullet above it. |
| `- Distinctive Career Fact: ...` | under `## Professional Identity & Positioning` | Optional. One fact about your path few candidates share; the writer surfaces it at your usual level and above. |
| `### Career Break -- <reason>` | under `## Work Experience`, in date order | Shown on the resume as its own entry. See below. |
| `## Legacy & Historical Platforms` | its own section | Never used in any output. |

## Recency and older roles

The writer measures recency back from the **end of your most recent role** (today, if you are still in it), not from today. A career break never moves that anchor, so time away does not push your earlier roles out of the window.

- Roles within the recency window (default 7 years) get full treatment.
- Roles that ended before the cutoff (default 15 years) may be left out unless they are uniquely relevant.
- A role that sits between two roles shown on the resume is never dropped, only condensed, so the resume never shows a gap that wasn't there.

If your older experience is still your strongest, or you are returning after a long break, set a longer window or `Role cutoff: none`.

## Career breaks

```markdown
### Career Break -- Family caregiving

**Dates:** Sep 2022 - Present

- Maintained an active RN license through 30 contact hours of continuing education

> **Agent Note:** Show this break as its own entry, reason exactly as written in the heading.
```

- The text after `--` is what the resume shows, word for word: `\headingBf{Career Break}{Sep 2022 - Present}` then `\headingIt{Family caregiving}{}`. Choose the wording you would want a recruiter to read.
- Leave the reason off (`### Career Break`) if you would rather not give one; the resume then shows only "Career Break" and the dates.
- Bullets are optional and follow the same rules as any role: only what you actually did.
- Nothing about health, family, or other personal detail appears unless you put it in the heading or a bullet. The builder never asks for it.

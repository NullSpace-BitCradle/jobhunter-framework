---
description: Regenerate discovery anti-target filters from MCD Anti-Target Lanes section
---

# /sync-filters - MCD → filters.yaml auto-sync

Reads the Anti-Target Lanes section from the Master Career Document and generates (or updates) the discovery `filters.yaml` so the two can never drift.

Run this after editing the MCD's Anti-Target Lanes section, or periodically to catch drift.

## Step 1 - Load config

Read `config.yaml` at the project root. Resolve:

- `mcd_path` - where the MCD lives
- `discovery.config_dir` - where `filters.yaml` should be written

If config is absent, default to `Master_Career_Document.md` in the project root and `discovery/config/` respectively.

## Step 2 - Read the MCD Anti-Target Lanes

Read the MCD. Find the "Anti-Target Lanes" subsection (typically under "Notes for Resume Customization"). Extract every anti-target lane entry. Each entry describes a category of role the user should NOT apply to, with a brief explanation of why.

## Step 3 - Read the current filters.yaml (if present)

Read `<discovery.config_dir>/filters.yaml`. Note any existing patterns - the user may have hand-tuned match strings that should be preserved if they still correspond to an MCD anti-target.

## Step 4 - Generate updated filters

For each anti-target lane in the MCD, generate a YAML filter block under `anti_target_patterns:` with:

- **Key:** a snake_case slug derived from the lane name (e.g., "Hands-on Application Security Engineer" → `hands_on_appsec`)
- **`description:`** a short human-readable label ending with "(MCD anti-target)"
- **Match conditions** - use the condition types from the filter schema:
 - `title_contains_any:` - lowercase substrings to match in job titles
 - `description_contains_any:` - lowercase substrings to match in JD body text
 - `description_contains_all:` - ALL must match (for tighter patterns)
 - `location_contains_any:` - match in location field
 - `negates_if_location_also_contains:` - cancel the match if location also has these terms (e.g., "remote" negates "on-site")
 - `negates_if_description_also_contains:` - cancel the match if JD body also has these terms (e.g., "or equivalent experience" negates "bachelor's degree required")

When generating match strings:
- Use common ATS phrasing you'd expect to see in real job descriptions
- Be conservative - false negatives (missing a bad match) are acceptable; false positives (filtering a good match) are not
- For certification/degree requirements, match on "required" language only - not "preferred" or "nice to have"
- Keep strings lowercase (the matching engine lowercases inputs)

**If a current filters.yaml pattern corresponds to an MCD anti-target, preserve the user's existing match strings** - they may have been tuned from real-world scan results. Only add new strings, don't remove hand-tuned ones.

**If an MCD anti-target has no corresponding filter yet, create a new pattern block.**

**If a current filter has no corresponding MCD anti-target, flag it** - it may be orphaned. Don't delete it automatically; surface it for the user to decide.

## Step 5 - Show the diff and confirm

Present the user with:
- A summary of what changed: new patterns added, existing patterns updated, orphaned patterns flagged
- The complete generated `filters.yaml` content

Ask for explicit confirmation before writing. The user may want to adjust match strings before saving.

## Step 6 - Write

Write the confirmed content to `<discovery.config_dir>/filters.yaml`. Preserve the file header comment explaining the file's purpose and that it's MCD-derived.

## Constraints

- Never invent anti-target categories not in the MCD. The MCD is the source of truth.
- Never delete hand-tuned match strings from existing patterns unless the user explicitly confirms.
- Always show the full output before writing - no silent writes.

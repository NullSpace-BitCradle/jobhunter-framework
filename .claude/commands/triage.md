---
description: Triage the job-hunt inbox - classify mail, update tracker, schedule interviews
argument-hint: [--limit N] [--days N]
---

# /triage - Daily job-search inbox triage

Classify recent mail in the job-hunt Gmail account, cross-reference against the applications tracker, and surface anything that needs the user's attention. Interviews with proposed times get scheduled on Google Calendar automatically; everything else is read-only classification.

Previewing an email in Gmail shouldn't cause /triage to miss it, so the query does not filter on read/unread - tracker-state dedup handles repeat runs.

User arguments (if any): `$ARGUMENTS`

## Argument parsing

Before Step 0, parse `$ARGUMENTS` for optional flags:

- `--limit N` - override the default message fetch limit (default: 25). Useful after a weekend or a long gap where more than 25 messages may have landed.
- `--days N` - override the default lookback window (default: 2 days). `--days 7` catches a full week.

If neither flag is given, use the defaults. Both flags accept positive integers; ignore malformed values and fall back to defaults with a one-line warning to the user.

**Requires:** Gmail plugin and Google Calendar plugin configured for the job-hunt account specified in `config.yaml → gmail.account`.

## Step 0 - Verify MCP tools available

Before proceeding, confirm these MCP tools are accessible:
- `mcp__claude_ai_Gmail__gmail_search_messages`
- `mcp__claude_ai_Gmail__gmail_read_message`
- `mcp__claude_ai_Google_Calendar__gcal_create_event` (only if scheduling is needed)

If the Gmail tools are not available, stop immediately and tell the user: "Gmail MCP server not connected. Enable it in your Claude Code MCP configuration before running /triage."

## Step 1 - Load config

Read `config.yaml` at the framework repo root. Expand `~`. Need:

- `gmail.enabled` - must be `true`, otherwise stop and tell the user to enable it
- `gmail.account` - the job-hunt email address
- `applications_file` - the tracker to update

Read the tracker file now so you have its current state in memory for cross-referencing in Step 3.

## Step 2 - Fetch recent mail

Use `mcp__claude_ai_Gmail__gmail_search_messages` with:
- Query: `newer_than:<days>d -category:promotions -category:social` (default `<days>` is 2, overridable via `--days N`)
- Limit: `<limit>` (default 25, overridable via `--limit N`)

For each message in the result, read subject + sender + first ~500 chars of body via `mcp__claude_ai_Gmail__gmail_read_message`.

**Dedup against the tracker before acting in Step 4.** For each classified message tied to a tracker row, check whether the Status column already reflects the same or downstream state - e.g., if a message classifies as `application_ack` but the row is already `ack`/`screen`/`interview`/`offer`/`rejected`, skip the update. Status transitions should only move forward along: `queued -> applied -> ack -> screen -> interview -> offer | rejected | withdrew`. Never regress a status.

A `queued` row receiving an `application_ack`, `screen_invite`, `interview_invite`, or `rejection` is valid - the downstream event implicitly confirms submission, so fast-forward the status directly (e.g., `queued -> ack`) instead of going through `applied`. When this happens the row also moves sections: out of `## Queued` into `## In Process` (for ack/screen/interview) or `## Rejected` (for rejection). See Step 4 for section-move details.

## Step 3 - Classify each message

Assign exactly one of these labels per message:

| Label | Meaning |
|---|---|
| `recruiter_inbound` | Cold recruiter pitch for a role the user hasn't applied to |
| `application_ack` | Automated "we received your application" from an ATS |
| `screen_invite` | Request to schedule a recruiter / initial screen |
| `interview_invite` | Request to schedule a hiring-manager or technical interview |
| `rejection` | Explicit decline (polite no) |
| `offer` | Offer letter or verbal offer |
| `noise` | Newsletters, sales mail, unrelated |

Match sender domain or any company name mentioned against companies already in `applications_file`. Tie the message to its tracker row when possible.

**If classification confidence is below ~70%** for a message, do not act - surface it in the final report as "needs review".

## Step 4 - Apply updates

For each confidently-classified message tied to an existing tracker row:

- **`application_ack`** -> update Status to `ack`, set Last Update to today. Don't touch other fields. If the row is currently in `## Queued`, move it to `## In Process`.
- **`screen_invite`** -> update Status to `screen`, set Last Update. Add a Note with the proposed time if stated. If in `## Queued`, move to `## In Process`.
- **`interview_invite`** -> update Status to `interview`, set Last Update. If the message includes a proposed date/time, create a Calendar event (see below). If in `## Queued`, move to `## In Process`.
- **`rejection`** -> update Status to `rejected`, set Last Update, add the stated reason to Notes if present. **Move the row to `## Rejected`** (from `## Queued` or `## In Process`). Files stay - the user did apply, so resume and cover letter are retained per the retention rule.
- **`offer`** -> **do not auto-update.** Flag loudly in the report. Let the user confirm and run `/apply` or a manual edit.

**Section move rule:** when a status change crosses a section boundary per `applications.template.md` routing (e.g., `queued -> ack` crosses Queued -> In Process; any status -> `rejected` moves to Rejected), remove the row from its current section and append it to the top of the new section. Preserve all cell values exactly except Status and Last Update (and Notes, when appending a reason).

**File retention on rejection:** `rejected` rows keep their resume and cover letter files - the user did submit. Do NOT delete files on rejection. Only `/decline` (for `queued` roles never submitted) deletes generated materials.

**Calendar events for interview_invite:** use `mcp__claude_ai_Google_Calendar__gcal_create_event`. Event details:
- Summary: `<Company> - <Role> interview (<type>)`
- Description: link back to the applications tracker row + interviewer name if available
- Start/end: from the mail body
- Reminders: one 15-minute popup
- Calendar: the job-hunt account (`primary` from its perspective)

**For `recruiter_inbound`** → do **not** auto-apply. Extract the company name and the role and list it in the report for the user to decide.

**For `noise`** → ignore.

## Step 5 - Report

Return a compact summary organized by action:

```
Triaged: N messages in last 2 days

Updates made:
 - <Company> <Role>: <old-status> → <new-status>
 - ...

Interviews scheduled:
 - <Company> <Role> - <date> <time> (<interview_type>)
 - ...

New recruiter inbounds (no action taken):
 - <Company> - <Role> (from <sender>)
 - ...

Rejections:
 - <Company> <Role>
 - ...

⚠ Needs your attention:
 - <message subject> - <reason>
 - ...
```

Omit any section that is empty.

## Hard constraints

- **Read and classify only.** Never send, draft, delete, or archive messages from `/triage`. Drafts require explicit user instruction outside this command.
- **Low confidence → surface, don't act.** If you're not sure what a message is, put it in "Needs your attention" rather than guessing.
- **Preserve tracker history.** Status updates overwrite Status and Last Update columns only. Never modify Date Applied, Company, Role, Files, URL, or existing Notes - append to Notes only.
- **Calendar events go on the job-hunt account only.** Do not touch the user's personal calendar.

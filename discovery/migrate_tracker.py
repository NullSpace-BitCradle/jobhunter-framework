"""
One-shot migration script for the jobhunter applications tracker.

Transforms the legacy 2-section layout (## Applications + ## Declined) into the
new 4-section layout (## Queued, ## In Process, ## Rejected, ## Declined) per
the spec change of 2026-04-22, and:

1. Routes each row into the correct section based on current Status.
2. Adds `[jd](<path>)` link to the Files column where the matching JD file
   exists under jobs/.
3. Deletes resume + cover letter files for rows that land in ## Declined but
   still have generated PDFs/.tex lying on disk. JD files are never deleted.

Designed to be idempotent: re-running on an already-migrated file is a no-op
(rows already in the correct section stay put; Files column already containing
a [jd] link is untouched).

Usage:
    python3 discovery/migrate_tracker.py           # dry run: prints plan
    python3 discovery/migrate_tracker.py --apply   # write changes to disk
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_paths() -> tuple[Path, Path, Path]:
    """Resolve (applications_file, jd_dir, output_dir) from framework config.

    Order of resolution (same pattern as discovery/main.py):
      1. $JOBHUNTER_APPLICATIONS_FILE / $JOBHUNTER_JD_DIR / $JOBHUNTER_OUTPUT_DIR
      2. Keys in <repo>/config.yaml: applications_file, jd_dir, output_dir
      3. Fallback to <repo>/applications.md, <repo>/jobs, <repo>/output (keeps
         the script usable standalone before the user has created config.yaml).
    """
    cfg: dict = {}
    config_path = _REPO_ROOT / "config.yaml"
    if config_path.exists():
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is not None:
            try:
                cfg = yaml.safe_load(config_path.read_text()) or {}
            except Exception as e:  # pragma: no cover - malformed config
                print(f"WARN: could not parse {config_path}: {e}", file=sys.stderr)

    def _pick(env_key: str, cfg_key: str, default: Path) -> Path:
        env = os.environ.get(env_key)
        if env:
            return Path(env).expanduser()
        raw = cfg.get(cfg_key)
        if raw:
            return Path(raw).expanduser()
        return default

    app = _pick("JOBHUNTER_APPLICATIONS_FILE", "applications_file", _REPO_ROOT / "applications.md")
    jobs = _pick("JOBHUNTER_JD_DIR", "jd_dir", _REPO_ROOT / "jobs")
    output = _pick("JOBHUNTER_OUTPUT_DIR", "output_dir", _REPO_ROOT / "output")
    return app, jobs, output


APP_FILE, JOBS_DIR, OUTPUT_DIR = _resolve_paths()

# Status -> target section
SECTION_FOR_STATUS = {
    "queued": "Queued",
    "applied": "In Process",
    "ack": "In Process",
    "screen": "In Process",
    "interview": "In Process",
    "offer": "In Process",
    "rejected": "Rejected",
    "withdrew": "Declined",
    "withdrawn": "Declined",  # tolerate legacy spelling
    "declined_anti_target": "Declined",
}

SECTION_ORDER = ["Queued", "In Process", "Rejected", "Declined"]

SECTION_HEADER_TEMPLATE = {
    "Queued": (
        "## Queued\n\n"
        "Generated materials, not yet submitted. `/apply` appends here. "
        "`/submitted` promotes to `## In Process`. `/decline` removes rows "
        "and deletes files.\n\n"
        "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    ),
    "In Process": (
        "## In Process\n\n"
        "Active applications the user has submitted. Status progresses through "
        "`applied -> ack -> screen -> interview -> offer`. `/triage` "
        "fast-forwards status from inbox mail.\n\n"
        "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    ),
    "Rejected": (
        "## Rejected\n\n"
        "Applications the company declined, or that went silent for 30+ days. "
        "Files retained because the user did apply. Serves as a durable skip "
        "list so discovery does not resurface them.\n\n"
        "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    ),
    "Declined": (
        "## Declined\n\n"
        "Roles the user declined to pursue, or that the framework refused to "
        "tailor due to an anti-target match. Generated resume + cover letter "
        "files are deleted when a row lands here; JD file under `jobs/` is "
        "kept. Serves as a durable skip list so discovery does not resurface "
        "declined roles.\n\n"
        "| Date Applied | Company | Role | Status | Last Update | Score | Files | URL | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    ),
}

PREAMBLE = """# Applications Tracker

Living record of job applications. `/apply` generates resume + cover letter and
stacks the row under `## Queued`. `/submitted` flips `queued -> applied` and moves
the row to `## In Process` after the user submits via the company portal.
`/triage` updates status from inbox mail and moves rows across sections as state
changes. `/decline` removes a Queued row the user decided not to pursue and
deletes its generated materials. Hand edits are welcome - the format is plain
markdown so any editor works.

## Status legend

- `queued` - resume + cover letter generated by `/apply`, not yet submitted via the company portal
- `applied` - submitted via the company portal (set by `/submitted` or `/triage` on ack)
- `ack` - automated application acknowledgment received from the ATS
- `screen` - recruiter screen scheduled or completed
- `interview` - hiring-manager or technical interview in progress
- `offer` - offer extended
- `rejected` - explicit decline from the company, or ghosted for 30+ days
- `withdrew` - user pulled the application (pre- or post-submit)
- `declined_anti_target` - framework refused to tailor due to MCD anti-target match

## Section routing

Each row lives in exactly one section, determined by its Status:

| Section | Statuses |
|---|---|
| `## Queued` | `queued` |
| `## In Process` | `applied`, `ack`, `screen`, `interview`, `offer` |
| `## Rejected` | `rejected` |
| `## Declined` | `withdrew`, `declined_anti_target` |

When a row's status transitions across a section boundary (e.g. `queued -> applied`, `applied -> rejected`), the row moves to the new section.

## File retention rule

Generated resume + cover letter files (`.tex` + `.pdf`) are retained ONLY for roles the user actually submitted. When a row lands in `## Declined` (user-initiated decline or agent anti-target refusal), the matching resume and cover letter files are deleted. The JD file under `jobs/` is kept regardless - it is the record of what the posting said. Rows in `## Rejected` keep their files since the user did apply.

"""

ROW_START = re.compile(r"^\|\s*(?:\d{4}-\d{2}-\d{2}|unknown)\b")


@dataclass
class Row:
    """One tracker row, parsed from a single markdown table line."""
    date: str
    company: str
    role: str
    status: str
    last_update: str
    score: str
    files: str
    url: str
    notes: str
    raw_line: str  # the full original line (for re-emit if we choose)

    def target_section(self) -> str:
        return SECTION_FOR_STATUS.get(self.status.lower(), "Declined")

    def resume_paths(self) -> list[Path]:
        """Return absolute paths to resume .tex + .pdf referenced in Files column."""
        return _extract_label_paths(self.files, label="resume")

    def cover_paths(self) -> list[Path]:
        return _extract_label_paths(self.files, label="cover")

    def jd_link_present(self) -> bool:
        return "[jd](" in self.files

    def emit(self, files_override: str | None = None) -> str:
        files_col = files_override if files_override is not None else self.files
        return (
            f"| {self.date} | {self.company} | {self.role} | {self.status} "
            f"| {self.last_update} | {self.score} | {files_col} | {self.url} | {self.notes} |"
        )


def _extract_label_paths(files_col: str, label: str) -> list[Path]:
    """Pull `[label](<path>)` links out of the Files column and resolve them to
    absolute paths. Returns both the .pdf and the matching .tex at the same
    basename, regardless of which one the link points at.
    """
    pattern = re.compile(rf"\[{re.escape(label)}\]\(([^)]+)\)")
    out: list[Path] = []
    for match in pattern.finditer(files_col):
        raw = match.group(1).strip()
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            p = (OUTPUT_DIR / raw).resolve() if not raw.startswith("output/") else (
                OUTPUT_DIR.parent / raw
            ).resolve()
        out.append(p)
        # Companion .tex / .pdf at same basename
        if p.suffix == ".pdf":
            out.append(p.with_suffix(".tex"))
        elif p.suffix == ".tex":
            out.append(p.with_suffix(".pdf"))
    return out


def parse_rows(lines: list[str]) -> list[Row]:
    rows: list[Row] = []
    for line in lines:
        if not ROW_START.match(line):
            continue
        # Split on | and trim each cell; leading/trailing emptiness from the |...|
        # wrapper is expected. Need at least 9 data cells.
        cells = [c.strip() for c in line.rstrip("\n").split("|")]
        # Trim leading and trailing empty cells from outer pipes
        # Format: ['', date, company, role, status, last_update, score, files, url, notes, '']
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if len(cells) < 9:
            print(f"WARN: short row, skipping: {line[:120]!r}", file=sys.stderr)
            continue
        row = Row(
            date=cells[0],
            company=cells[1],
            role=cells[2],
            status=cells[3],
            last_update=cells[4],
            score=cells[5],
            files=cells[6],
            url=cells[7],
            notes=" | ".join(cells[8:]),  # notes may contain | chars
            raw_line=line,
        )
        rows.append(row)
    return rows


def sanitize(name: str) -> str:
    """Mirror the sanitization rule /apply uses for JD filenames."""
    out = name.replace(" ", "_")
    for ch in "/:\\\"'?*<>|,()":
        out = out.replace(ch, "")
    return out


def find_jd_path(row: Row, jd_index: dict[str, Path]) -> Path | None:
    """Best-effort match of a tracker row to a JD file on disk.

    Strategy:
      1. Build expected filename from sanitized Company + Role.
      2. If direct match, return it.
      3. Otherwise do a fuzzy contains-match on Company+first-few-words-of-role.
    """
    if not row.company or not row.role:
        return None

    direct = f"Job_Description-{sanitize(row.company)}-{sanitize(row.role)}.md"
    if direct in jd_index:
        return jd_index[direct]

    # Fuzzy: filename starts with Job_Description-<sanitized-company>-
    co = sanitize(row.company)
    candidates = [
        p for fn, p in jd_index.items()
        if fn.startswith(f"Job_Description-{co}-")
    ]
    if len(candidates) == 1:
        return candidates[0]

    # Multi-word role: try prefix matching on first 2-3 role tokens
    role_tokens = sanitize(row.role).split("_")
    if role_tokens and candidates:
        for n in (4, 3, 2):
            prefix = "_".join(role_tokens[:n])
            matches = [
                p for fn, p in jd_index.items()
                if fn.startswith(f"Job_Description-{co}-{prefix}")
            ]
            if len(matches) == 1:
                return matches[0]

    return None


def build_files_column(row: Row, jd_path: Path | None) -> str:
    """Reassemble the Files cell, preserving existing resume + cover links and
    appending a [jd](path) link if one is not already present.
    """
    base = row.files.strip()
    if jd_path is None:
        return base
    if row.jd_link_present():
        return base
    jd_link = f"[jd]({_to_tilde(jd_path)})"
    if not base:
        return jd_link
    return f"{base} / {jd_link}"


def _to_tilde(p: Path) -> str:
    """Render a path with ~ for the user's home dir so tracker entries stay
    portable across machines."""
    home = Path.home()
    try:
        rel = p.resolve().relative_to(home)
        return f"~/{rel}"
    except ValueError:
        return str(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes to disk")
    args = ap.parse_args()

    if not APP_FILE.exists():
        print(f"Tracker not found at {APP_FILE}", file=sys.stderr)
        return 2

    text = APP_FILE.read_text()
    lines = text.splitlines()
    rows = parse_rows(lines)
    print(f"Parsed {len(rows)} rows from tracker")

    # Build JD index
    jd_index: dict[str, Path] = {}
    if JOBS_DIR.is_dir():
        for f in JOBS_DIR.iterdir():
            if f.is_file() and f.suffix == ".md":
                jd_index[f.name] = f
    print(f"Found {len(jd_index)} JD files under {JOBS_DIR}")

    # Bucket rows by target section
    by_section: dict[str, list[Row]] = {s: [] for s in SECTION_ORDER}
    jd_added_count = 0
    jd_missing: list[str] = []
    for r in rows:
        target = r.target_section()
        jd = find_jd_path(r, jd_index)
        if jd is not None and not r.jd_link_present():
            jd_added_count += 1
        elif jd is None and not r.jd_link_present():
            jd_missing.append(f"{r.company} / {r.role}")
        r._resolved_jd = jd  # type: ignore[attr-defined]
        by_section[target].append(r)

    print(f"Adding [jd] link to {jd_added_count} row(s)")
    if jd_missing:
        print(f"  ({len(jd_missing)} row(s) have no matching JD file; leaving Files unchanged)")

    # Files to delete (rows going to ## Declined that still have resume/cover files)
    to_delete: list[Path] = []
    for r in by_section["Declined"]:
        for p in r.resume_paths():
            if p.exists():
                to_delete.append(p)
        for p in r.cover_paths():
            if p.exists():
                to_delete.append(p)
    # Dedupe
    to_delete_unique: list[Path] = []
    seen_set: set[Path] = set()
    for p in to_delete:
        if p in seen_set:
            continue
        seen_set.add(p)
        to_delete_unique.append(p)
    print(f"Will delete {len(to_delete_unique)} resume/cover file(s) from Declined rows")
    for p in to_delete_unique:
        print(f"  rm  {p}")

    # Build new tracker content
    out: list[str] = [PREAMBLE.rstrip("\n") + "\n"]
    for section in SECTION_ORDER:
        out.append(SECTION_HEADER_TEMPLATE[section])
        for r in by_section[section]:
            jd = getattr(r, "_resolved_jd", None)
            files_col = build_files_column(r, jd)
            out.append(r.emit(files_override=files_col) + "\n")
        out.append("\n")
    new_text = "".join(out).rstrip("\n") + "\n"

    print()
    print("Section counts after migration:")
    for s in SECTION_ORDER:
        print(f"  {s}: {len(by_section[s])} row(s)")

    if not args.apply:
        print()
        print("DRY RUN - no changes written. Re-run with --apply to commit.")
        return 0

    # Write new tracker
    backup = APP_FILE.with_suffix(".md.bak")
    backup.write_text(text)
    APP_FILE.write_text(new_text)
    print(f"Wrote {APP_FILE} (backup at {backup})")

    # Delete files
    deleted = 0
    for p in to_delete_unique:
        try:
            p.unlink()
            deleted += 1
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"  WARN: could not delete {p}: {e}", file=sys.stderr)
    print(f"Deleted {deleted} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

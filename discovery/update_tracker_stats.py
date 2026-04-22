"""
Keep the stats line at the top of applications.md in sync with the actual row
counts per section.

Idempotent: inserts the stats line if absent, updates it if present. Safe to
run from a PostToolUse hook after any Edit/Write on the tracker.

Stats line format:
    **Current state:** Queued N | In Process N | Rejected N | Declined N | **Total N** | Updated YYYY-MM-DD

Placement: immediately before the first `## Status legend` heading. Blank line
above and below.

Usage:
    python3 discovery/update_tracker_stats.py           # update in place
    python3 discovery/update_tracker_stats.py --check   # print current counts without writing
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_tracker_path() -> Path:
    """Honor the framework's config.yaml the same way discovery/main.py does.

    Order of resolution:
      1. $JOBHUNTER_APPLICATIONS_FILE (env override, useful for tests and CI)
      2. applications_file key in <repo>/config.yaml
      3. Fallback: <repo>/applications.md (keeps the script usable standalone
         when no config.yaml exists, e.g. fresh clones before setup)
    """
    env = os.environ.get("JOBHUNTER_APPLICATIONS_FILE")
    if env:
        return Path(env).expanduser()

    config_path = _REPO_ROOT / "config.yaml"
    if config_path.exists():
        try:
            import yaml
        except ImportError:
            yaml = None  # PyYAML may not be installed in a minimal env
        if yaml is not None:
            try:
                cfg = yaml.safe_load(config_path.read_text()) or {}
                raw = cfg.get("applications_file")
                if raw:
                    return Path(raw).expanduser()
            except Exception as e:  # pragma: no cover - malformed config
                print(f"WARN: could not parse {config_path}: {e}", file=sys.stderr)

    return _REPO_ROOT / "applications.md"


TRACKER_PATH = _resolve_tracker_path()

# Section header positions we care about
SECTION_NAMES = ("Queued", "In Process", "Rejected", "Declined")
SECTION_HEADER_RE = re.compile(r"^## (Queued|In Process|Rejected|Declined)\s*$")
DATA_ROW_RE = re.compile(r"^\|\s*(?:\d{4}-\d{2}-\d{2}|unknown)\b")
STATS_LINE_RE = re.compile(r"^\*\*Current state:\*\*")
STATUS_LEGEND_RE = re.compile(r"^## Status legend\s*$")


def count_by_section(lines: list[str]) -> dict[str, int]:
    """Walk the file, tracking which named section we are in, and count rows."""
    counts = {name: 0 for name in SECTION_NAMES}
    current: str | None = None
    for line in lines:
        m = SECTION_HEADER_RE.match(line)
        if m:
            current = m.group(1)
            continue
        if line.startswith("## "):
            # Entered some other section (e.g. Status legend, Section routing)
            current = None
            continue
        if current and DATA_ROW_RE.match(line):
            counts[current] += 1
    return counts


def format_stats_line(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    today = date.today().isoformat()
    return (
        f"**Current state:** "
        f"Queued {counts['Queued']} | "
        f"In Process {counts['In Process']} | "
        f"Rejected {counts['Rejected']} | "
        f"Declined {counts['Declined']} | "
        f"**Total {total}** | "
        f"Updated {today}"
    )


def splice_stats_line(lines: list[str], new_stats: str) -> list[str]:
    """Return a new list of lines with the stats line inserted or replaced.

    Rules:
      - If a stats line already exists anywhere before `## Status legend`,
        replace it in place.
      - Else, insert it immediately before the blank line preceding
        `## Status legend`, separated by one blank line above and below.
    """
    # Find `## Status legend` position
    legend_idx = next(
        (i for i, ln in enumerate(lines) if STATUS_LEGEND_RE.match(ln)),
        None,
    )
    if legend_idx is None:
        raise RuntimeError("Tracker has no `## Status legend` heading; cannot anchor stats line")

    # Look for an existing stats line anywhere before legend
    for i in range(legend_idx):
        if STATS_LINE_RE.match(lines[i]):
            out = list(lines)
            out[i] = new_stats
            return out

    # No existing stats line -> insert. Find the blank line immediately before legend.
    insert_idx = legend_idx
    while insert_idx > 0 and lines[insert_idx - 1].strip() == "":
        insert_idx -= 1
    # insert_idx now points at the first blank line before legend (or legend itself
    # if no blank line). Insert: stats line + blank line.
    out = list(lines)
    out.insert(insert_idx, "")
    out.insert(insert_idx, new_stats)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="print counts, do not write")
    args = ap.parse_args()

    if not TRACKER_PATH.exists():
        print(f"Tracker not found at {TRACKER_PATH}", file=sys.stderr)
        return 2

    text = TRACKER_PATH.read_text()
    lines = text.splitlines()
    counts = count_by_section(lines)

    if args.check:
        print(format_stats_line(counts))
        return 0

    new_stats = format_stats_line(counts)
    new_lines = splice_stats_line(lines, new_stats)

    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    if new_text == text:
        # No change (line already identical) - skip write to keep mtime clean
        return 0

    TRACKER_PATH.write_text(new_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

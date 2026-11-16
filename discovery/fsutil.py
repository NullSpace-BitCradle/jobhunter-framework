"""Cross-platform file helpers for the discovery scanner.

Why this exists: the scanner used `fcntl.flock` (POSIX only) around
`open("w")`, which truncates the file BEFORE the lock is taken, so the lock
never protected the write and a crash mid-write left an empty or half-written
state file. These helpers replace that with the portable pattern:

  * write to a temp file in the SAME directory, flush + fsync, then
    `os.replace()` it over the target. `os.replace` is atomic on POSIX and on
    Windows (NTFS), so a reader sees either the old file or the new one.
  * refuse to treat an unreadable state file as "empty", because starting
    fresh would mark every posting new and flood the digest. The bad file is
    copied aside first so nothing is lost.

All text is UTF-8 with LF newlines regardless of platform (Windows would
otherwise default to the ANSI code page and CRLF).
"""
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# os.replace() on Windows raises PermissionError when another process (an
# antivirus scanner, OneDrive sync, an editor) briefly holds the target open.
_REPLACE_ATTEMPTS = 6
_REPLACE_BASE_DELAY = 0.05


class StateFileError(RuntimeError):
    """A state file exists but cannot be used (corrupt, wrong shape, unreadable)."""


def _replace_with_retry(src: str, dst: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BASE_DELAY * (2 ** attempt))


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace `path` with `text` (UTF-8, LF newlines)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_name, mode)
        except OSError:
            pass
        _replace_with_retry(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, indent=2))


def write_new_text(directory: Path, stem: str, text: str, suffix: str = ".md") -> Path:
    """Write `text` to a NEW file `<directory>/<stem><suffix>`; never overwrite.

    If the name is taken (two runs in the same second), `_2`, `_3`, ... is
    appended to the stem. Creation is exclusive (O_EXCL), so two concurrent
    runs cannot pick the same name.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        name = f"{stem}{suffix}" if n == 1 else f"{stem}_{n}{suffix}"
        candidate = directory / name
        try:
            with open(candidate, "x", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return candidate
        except FileExistsError:
            n += 1


def load_json_object(path: Path) -> dict | None:
    """Read a JSON object from `path`.

    Returns None when the file does not exist (a legitimate first run).
    Raises StateFileError when it exists but is unreadable, not valid JSON, or
    not a JSON object. A copy is kept at `<name>.corrupt` (the original is left
    in place) so the caller can refuse to continue without destroying evidence.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:  # ValueError covers UnicodeDecodeError
        raise StateFileError(f"cannot read {path}: {e}") from e
    try:
        data = json.loads(text)
    except ValueError as e:
        _keep_corrupt_copy(path)
        raise StateFileError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        _keep_corrupt_copy(path)
        raise StateFileError(f"{path} does not contain a JSON object")
    return data


def _keep_corrupt_copy(path: Path) -> None:
    dest = path.with_name(path.name + ".corrupt")
    if dest.exists():
        return
    try:
        shutil.copy2(path, dest)
        logger.warning("Saved a copy of the unreadable file to %s", dest)
    except OSError as e:
        logger.warning("Could not save a copy of %s: %s", path, e)


def keep_backup(path: Path) -> None:
    """Copy the current `path` to `<name>.bak` (best effort) before replacing it.

    Only a file that still parses as JSON becomes the backup, so a corrupt file
    never overwrites the last good copy.
    """
    path = Path(path)
    if not path.exists():
        return
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    try:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    except OSError as e:
        logger.warning("Could not write backup of %s: %s", path, e)

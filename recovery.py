"""Autosave / crash recovery: atomic writes, recovery-file path mapping,
cursor-position sidecar files, and the recent-files list.

Reads config.RECOVERY_DIR, config.CONFIG_DIR, and config.MAX_RECENT_FILES
via qualified `config.NAME` access (not `from config import NAME`) since
all three are reassigned at runtime -- RECOVERY_DIR/CONFIG_DIR only ever
by tests (monkeypatch.setattr(config, ...)), MAX_RECENT_FILES also by
config.apply_runtime_config(). A bare import would copy the value at
import time and miss any later reassignment.
"""

import os
import re
import uuid
from pathlib import Path

import config

def atomic_write_text(path, text):
    """Write `text` to `path` without ever leaving a half-written file on
    disk if something interrupts the write (terminal killed, laptop loses
    power, disk fills up mid-write, ...).

    Path.write_text()/open(...).write() truncate the target file *before*
    writing the new content -- so a write that dies partway through can
    leave the real file shorter than either the old or new version, or
    empty. For the main .fountain file (written by save(), only on an
    explicit :w/:wq) that's the difference between "lost my last few
    keystrokes" and "the whole script is gone", which is what made an
    interrupted save look identical to :wq having silently done nothing --
    the very next open would read back a truncated/corrupt file.

    Writing to a sibling temp file first and then atomically renaming it
    over the real path (os.replace, a single filesystem operation on
    Linux) means the real path always contains either the fully-old or
    fully-new content, never a partial write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Autosave / crash recovery
# --------------------------------------------------------------------------

def recovery_key_for_path(path):
    """Stable, filesystem-safe recovery filename derived from a real
    screenplay path, so re-opening the same file always maps to the same
    recovery slot."""
    resolved = str(Path(path).expanduser().resolve())
    key = re.sub(r'[^A-Za-z0-9]+', '_', resolved).strip('_')
    return f"{key}.swp"


def recovery_path_for(path):
    return config.RECOVERY_DIR / recovery_key_for_path(path)


def load_cursor_pos(recovery_path, buffer):
    """Reads back the cy,cx written by Editor.save_cursor_pos(), clamped to
    the given buffer's actual bounds (the buffer may have changed size
    since the position was saved, e.g. someone hand-edited the .fountain
    file). Falls back to (0, 0) -- the very top -- if there's no sidecar,
    it's corrupt, or the recovery slot itself doesn't apply here."""
    try:
        raw = cursor_pos_path_for(recovery_path).read_text().strip()
        cy_str, cx_str = raw.split(",")
        cy, cx = int(cy_str), int(cx_str)
    except (OSError, ValueError):
        return 0, 0
    cy = max(0, min(cy, len(buffer) - 1))
    cx = max(0, min(cx, len(buffer[cy]["text"])))
    return cy, cx


def cursor_pos_path_for(recovery_path):
    """Sidecar file next to a .swp recovery slot that remembers where the
    cursor was, so reopening a script (whether via real crash recovery or
    just opening a file you worked on yesterday) picks up where you left
    off instead of always landing on line 1. Kept out of the actual
    .fountain content -- that file needs to stay clean/portable, openable
    in other Fountain tools -- so this rides alongside it in the same
    recovery slot instead."""
    return recovery_path.with_suffix(".pos")


def new_untitled_recovery_path():
    config.RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    return config.RECOVERY_DIR / f"untitled-{uuid.uuid4().hex[:8]}.swp"


RECENT_FILES_PATH = config.CONFIG_DIR / "recent.txt"
# config.MAX_RECENT_FILES itself is defined near the top of the file (with
# AUTOSAVE_INTERVAL) and overwritten from cfg["behavior"] by
# apply_runtime_config() -- not redefined here.


def load_recent_files():
    """Most-recently-opened screenplay paths, newest first. Used to widen
    the '[o] Open'/':o' picker beyond save_dir -- a file opened via
    `scriptee some/where/else.fountain` (or typed in with the picker's "e"
    prompt) doesn't necessarily live under save_dir, so the plain glob in
    open_file_screen() would never show it again."""
    try:
        lines = RECENT_FILES_PATH.read_text().splitlines()
    except OSError:
        return []
    return [ln for ln in lines if ln.strip()]


def record_recent_file(path):
    """Push `path` to the front of the recent-files list (see
    load_recent_files()), deduplicated, capped at config.MAX_RECENT_FILES. Best
    effort -- if this fails for any reason, the picker just falls back to
    save_dir only, same as before this existed."""
    try:
        resolved = str(Path(path).expanduser().resolve())
    except OSError:
        resolved = str(Path(path).expanduser())
    existing = [p for p in load_recent_files() if p != resolved]
    existing.insert(0, resolved)
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RECENT_FILES_PATH.write_text("\n".join(existing[:config.MAX_RECENT_FILES]) + "\n")
    except OSError:
        pass


def find_orphan_recoveries():
    """Recovery files for documents that were never saved to a real path
    before the session ended (crash, killed terminal, etc.) -- these can't
    be keyed to a real file, so they're offered separately at startup.
    Newest first."""
    if not config.RECOVERY_DIR.exists():
        return []
    orphans = list(config.RECOVERY_DIR.glob("untitled-*.swp"))
    orphans.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return orphans



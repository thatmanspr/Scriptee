"""Entry point: startup flow (start menu, recovery prompts, opening a
file) and main(), the curses.wrapper() target."""

import curses
import locale
import os
import sys
from pathlib import Path

from config import DEFAULT_CONFIG, load_config, write_default_config
from fountain import from_fountain
from recovery import (
    recovery_path_for, load_cursor_pos, record_recent_file,
    find_orphan_recoveries,
)
from screens import start_menu, confirm_recovery, new_file_metadata, open_file_screen
from editor import Editor

# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def open_and_run(stdscr, cfg, path, choice="open", readonly=False):
    """Shared open-a-real-path flow used both by the interactive [o] Open
    menu and by `scriptee some/file.fountain` on the command line: reads
    the file (or starts a blank buffer if it doesn't exist yet), offers to
    recover a newer autosave if one exists, restores the cursor to where
    it was last left, and runs the editor.

    `choice` mirrors the caller's start_menu() choice ("new" vs "open") --
    only relevant to the caller now for whether load_cursor_pos() applies,
    but kept as a parameter so the CLI path (which never went through
    start_menu at all) can pass "open" and get identical behavior to
    picking a file from the menu.
    """
    recovery_path = recovery_path_for(path)
    open_error = None
    recovered_note = None
    try:
        file_mtime = Path(path).stat().st_mtime
    except OSError:
        file_mtime = None
    try:
        text = Path(path).read_text()
        metadata, buffer = from_fountain(text)
        filepath = path
        if (recovery_path.exists() and file_mtime is not None
                and recovery_path.stat().st_mtime > file_mtime
                and confirm_recovery(stdscr, recovery_path)):
            metadata, buffer = from_fountain(recovery_path.read_text())
            recovered_note = "Recovered unsaved autosave changes. :w to keep them."
    except FileNotFoundError:
        # New file at this path -- start blank, will be created on :w,
        # unless there's autosaved content for it from a prior session
        # that never got that far. Never read-only: there's nothing yet
        # to protect from an accidental edit.
        metadata, buffer = {}, [{"type": "action", "text": ""}]
        filepath = path
        readonly = False
        if recovery_path.exists() and confirm_recovery(stdscr, recovery_path):
            try:
                metadata, buffer = from_fountain(recovery_path.read_text())
                recovered_note = "Recovered unsaved autosave changes. :w to keep them."
            except Exception:
                pass
    except Exception as e:
        # Directory given, permission denied, bad encoding, etc. Don't let
        # this crash out of curses -- start a blank buffer instead and
        # surface the error in the status line.
        metadata, buffer = {}, [{"type": "action", "text": ""}]
        filepath = None
        open_error = f"Could not open {path}: {e}"

    stdscr.clear()
    ed = Editor(stdscr, cfg, metadata, buffer, filepath, readonly=readonly)
    if filepath:
        ed.cy, ed.cx = load_cursor_pos(recovery_path_for(filepath), buffer)
    if recovered_note:
        ed.dirty = True
        ed.status = recovered_note
    if open_error:
        ed.status = open_error
    if filepath and not open_error:
        record_recent_file(filepath)
    return ed.run()


def main(stdscr):
    # ncurses does "line breakout optimization" by default: while a
    # refresh() is drawing to the physical terminal, it peeks at stdin
    # (fd 0) and, if more input has *already* arrived, it can defer or
    # coalesce part of that physical update rather than finish drawing it
    # right away -- the idea being to catch up with fast typing rather
    # than fall behind on redraws. In practice, for a per-keystroke editor
    # loop like this one (render() -> getch() -> render() -> ...), it's
    # exactly backwards: it's what made the display feel like it was
    # trailing behind what you'd actually typed -- a character (or a
    # backspace) applied to the buffer immediately, but visibly slow to
    # show up, especially typing at a normal clip. Disabling typeahead
    # checking (fd -1) makes every render() actually hit the terminal
    # before the next getch(), so the screen never lags behind the real
    # buffer state. Our renders are already cheap (styled_wrap() memoizes
    # per line, page_estimate() is throttled -- see their docstrings), so
    # there's no real redraw cost this optimization was ever saving us.
    curses.typeahead(-1)
    write_default_config()
    cfg = load_config()
    curses.curs_set(0)

    # `scriptee some/file.fountain` -- skip the start menu and open it
    # directly. If the file already exists, it opens read-only (press 'e'
    # to start editing) so a quick "let me check this scene" launch from
    # the shell can't turn into an accidental edit; a path that doesn't
    # exist yet behaves like picking [n] New and typing that path in as
    # the save location, fully editable from the start.
    #
    # This only fires once, on the very first pass through the loop below
    # -- after that editor session ends (quit, or ':o' hands off to a
    # different file), control falls through to the normal start-menu loop
    # rather than exiting the whole program. Previously this `return`ed
    # unconditionally, so `scriptee some/file.fountain` -> :q dropped
    # straight back to the shell with no way to open a second file short
    # of relaunching the whole program from the command line again.
    pending_path = None
    pending_readonly = False
    if len(sys.argv) > 1:
        pending_path = str(Path(sys.argv[1]).expanduser())
        pending_readonly = Path(pending_path).is_file()

    while True:
        if pending_path is not None:
            result = open_and_run(stdscr, cfg, pending_path,
                                   readonly=pending_readonly)
            pending_path, pending_readonly = None, False
            if isinstance(result, tuple) and result[0] == "OPEN":
                pending_path = result[1]
            continue

        orphan_recoveries = find_orphan_recoveries()
        choice = start_menu(stdscr, has_recovery=bool(orphan_recoveries))
        if choice == "quit":
            return

        if choice == "recover":
            # A session that was never saved to a real path before it ended
            # (crash, killed terminal, etc.). Re-open its recovery slot
            # directly; it keeps autosaving there until the user does a
            # real :w, at which point save() re-keys it and drops this
            # slot.
            recovery_path = orphan_recoveries[0]
            try:
                metadata, buffer = from_fountain(recovery_path.read_text())
            except Exception:
                metadata, buffer = {}, [{"type": "action", "text": ""}]
            stdscr.clear()
            ed = Editor(stdscr, cfg, metadata, buffer, None, recovery_path=recovery_path)
            ed.cy, ed.cx = load_cursor_pos(recovery_path, buffer)
            ed.dirty = True
            ed.status = f"Recovered unsaved session ({recovery_path.name}). :w to keep it."
            result = ed.run()
            if isinstance(result, tuple) and result[0] == "OPEN":
                pending_path = result[1]
            continue

        if choice == "new":
            metadata = new_file_metadata(stdscr, cfg)
            buffer = [{"type": "action", "text": ""}]
            stdscr.clear()
            ed = Editor(stdscr, cfg, metadata, buffer, None)
            result = ed.run()
            if isinstance(result, tuple) and result[0] == "OPEN":
                pending_path = result[1]
            continue

        path = open_file_screen(stdscr, cfg)
        if not path:
            # 'q' backing out of the picker -- go back to the home screen,
            # not out of the program. Previously this `return`ed straight
            # out of main(), so pressing 'q' to back out of [o] Open closed
            # Scriptee entirely instead of just returning to the menu it
            # was opened from.
            continue
        result = open_and_run(stdscr, cfg, path, choice="open")
        if isinstance(result, tuple) and result[0] == "OPEN":
            pending_path = result[1]


def run():
    """Real entry point, called by the scriptee.py launcher's own
    `if __name__ == "__main__":` guard. Kept as a function (rather than
    top-level module code, as in the original single-file scriptee.py)
    so importing app.py -- e.g. from tests, or from scriptee.py itself --
    never has the side effect of touching the terminal or the filesystem.
    """
    # ncurses waits ESCDELAY milliseconds after a lone ESC byte before
    # deciding it isn't the start of a longer escape sequence (arrow keys,
    # etc. all start with ESC on the wire). The ncurses default is 1000ms,
    # and some terminals/multiplexers push it even higher -- that's what
    # made Esc (leaving INSERT, closing :help, backing out of a prompt)
    # feel like it hung for a second-plus. Scriptee doesn't use any
    # Alt-modified or exotic escape sequences, so it's safe to shrink this
    # drastically by default -- configurable via behavior.esc_delay_ms for
    # anyone whose terminal/multiplexer needs more room. Must be set via
    # the environment before initscr() runs (which curses.wrapper() does
    # immediately), so it can't be done with curses.set_escdelay() from
    # inside main() -- meaning config has to be loaded here, before
    # main()'s own (otherwise-first) load_config() call.
    write_default_config()
    _startup_cfg = load_config()
    os.environ.setdefault(
        "ESCDELAY",
        str(_startup_cfg.get("behavior", {}).get(
            "esc_delay_ms", DEFAULT_CONFIG["behavior"]["esc_delay_ms"])))
    # Needed for both directions of Unicode support: get_wch() only decodes
    # multi-byte input correctly once the process locale is set from the
    # environment (Python starts in the "C" locale otherwise, which is
    # ASCII-only), and addstr() only *displays* non-ASCII characters
    # correctly under a UTF-8 locale too. Must happen before curses.wrapper
    # touches the terminal.
    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(main)

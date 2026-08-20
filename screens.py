"""Standalone (non-Editor) curses screens: the start menu, recovery
confirmation, new-file metadata prompt, sides-title prompt, and the
open-file picker (with fuzzy matching)."""

import curses
import os
import glob
import time
from pathlib import Path

from ui_helpers import read_key, safe_addstr, is_printable_char, prompt_line
from recovery import load_recent_files

# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

def start_menu(stdscr, has_recovery=False):
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    title = "S C R I P T E E"
    safe_addstr(stdscr, h // 2 - 3, (w - len(title)) // 2, title, curses.A_BOLD)
    opts = ["[n] New screenplay", "[o] Open existing"]
    if has_recovery:
        opts.append("[r] Recover unsaved session")
    opts.append("[q] Quit")
    for i, o in enumerate(opts):
        safe_addstr(stdscr, h // 2 - 1 + i, (w - len(o)) // 2, o)
    stdscr.refresh()
    while True:
        ch = read_key(stdscr)
        if ch in (ord("n"), ord("N")):
            return "new"
        if ch in (ord("o"), ord("O")):
            return "open"
        if has_recovery and ch in (ord("r"), ord("R")):
            return "recover"
        if ch in (ord("q"), ord("Q")):
            return "quit"


def confirm_recovery(stdscr, recovery_path):
    """Ask whether to load a newer autosaved recovery file instead of the
    file the user just picked. Returns True to recover, False to ignore.

    Enter defaults to [r] Recover -- this screen only ever appears because
    an autosave slot is *newer* than the last real save, i.e. there's
    editing that never made it into the saved file (crash, killed
    terminal, closed laptop lid, ...). Defaulting Enter to the safer
    choice (keep the newer content) means a reflexive Enter-press can't
    silently throw that work away. Previously Enter wasn't handled here at
    all, so the prompt just sat there ignoring it -- easy to mistake for a
    hang, and a user who force-kills the terminal at that point (thinking
    it's frozen) loses whatever this screen was offering to recover, which
    looks identical to ":wq not actually saving.\""""
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(recovery_path.stat().st_mtime))
    lines = [
        "An autosaved recovery file newer than the saved script was found.",
        f"(autosaved {ts}, likely from an unsaved exit)",
        "",
        "[r]/Enter Recover the autosaved version    [i] Ignore, open the saved file",
    ]
    for i, l in enumerate(lines):
        safe_addstr(stdscr, h // 2 - 2 + i, 2, l[: w - 3], curses.A_BOLD if i == 0 else 0)
    stdscr.refresh()
    while True:
        ch = read_key(stdscr)
        if ch in (ord("r"), ord("R"), curses.KEY_ENTER, 10, 13):
            return True
        if ch in (ord("i"), ord("I"), 27):
            return False


def confirm_yes_no(stdscr, lines, default=None):
    """Generic [y]es/[n]o confirmation prompt -- `lines` is the message
    shown above it (first line bold, rest plain). `default` ("y", "n",
    or None) is what plain Enter picks; Esc always counts as "n" (the
    safer, less-surprising choice when nothing's been picked)."""
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    top = max(1, h // 2 - len(lines) - 1)
    for i, l in enumerate(lines):
        safe_addstr(stdscr, top + i, 2, l[: w - 3], curses.A_BOLD if i == 0 else 0)
    footer = "[y] Yes    [n] No"
    if default in ("y", "n"):
        footer += f"    (Enter = {'Yes' if default == 'y' else 'No'})"
    safe_addstr(stdscr, top + len(lines) + 1, 2, footer[: w - 3])
    stdscr.refresh()
    while True:
        ch = read_key(stdscr)
        if ch in (ord("y"), ord("Y")):
            return True
        if ch in (ord("n"), ord("N")):
            return False
        if ch in (curses.KEY_ENTER, 10, 13) and default is not None:
            return default == "y"
        if ch == 27:
            return False


def new_file_metadata(stdscr, cfg,
                       heading="New Screenplay  (leave blank + Enter to skip)",
                       initial=None):
    """Prompt for each configured cover-page field and return the ones the
    user actually filled in.

    `initial` (dict, e.g. an existing Editor.metadata) prefills each field
    with its current value so the prompt doubles as an editor, not just a
    from-scratch form -- used by :cover. A field prefilled and left
    unedited round-trips as-is; a field prefilled and backspaced empty is
    dropped, same as never filling in a blank one.
    """
    stdscr.clear()
    fields = cfg["prompts"]["fields"]
    initial = initial or {}
    safe_addstr(stdscr, 1, 2, heading, curses.A_BOLD)
    metadata = {}
    for i, field_name in enumerate(fields):
        val = prompt_line(stdscr, 3 + i, 2, field_name,
                           initial=initial.get(field_name, ""))
        if val:
            metadata[field_name] = val
    return metadata


def prompt_sides_title(stdscr, heading):
    """Ask for a single custom Title for a scoped (sides/character) PDF
    export. Unlike new_file_metadata() -- which loops over every field in
    cfg["prompts"]["fields"] -- this only ever asks for the one Title
    field: a sides export keeps the rest of the cover page (Author,
    Contact, ...) as whatever's already set on the full script, see
    do_export_pdf(). Leaving it blank + Enter returns "", which
    do_export_pdf() takes as "auto-generate one instead."
    """
    stdscr.clear()
    safe_addstr(stdscr, 1, 2, heading, curses.A_BOLD)
    title = prompt_line(stdscr, 3, 2, "Title")
    stdscr.clear()
    return title.strip()


def fuzzy_match(query, text):
    """Case-insensitive subsequence match: every character of `query` must
    appear in `text` in order, though not necessarily contiguously (so
    "scnt" matches "scriptee_confidential"). Returns a score where lower is
    a better match (contiguous substrings score best), or None if `query`
    isn't a subsequence of `text` at all."""
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    search_from = 0
    first_pos = None
    last_pos = -1
    gap_penalty = 0
    for qc in q:
        pos = t.find(qc, search_from)
        if pos == -1:
            return None
        if first_pos is None:
            first_pos = pos
        elif last_pos != -1:
            gap_penalty += pos - last_pos - 1
        last_pos = pos
        search_from = pos + 1
    return first_pos + gap_penalty


def open_file_screen(stdscr, cfg):
    save_dir = Path(os.path.expanduser(cfg["general"]["save_dir"]))
    glob_files = sorted(glob.glob(str(save_dir / "**/*.fountain"), recursive=True) +
                         glob.glob(str(save_dir / "**/*.scriptee"), recursive=True))
    # Recently-opened files first (e.g. one launched via `scriptee
    # some/where/else.fountain`, which won't be under save_dir and so'd
    # otherwise never show up here again), then the rest of save_dir,
    # de-duplicated.
    recent_files = [p for p in load_recent_files() if Path(p).is_file()]
    seen = set()
    files = []
    for f in recent_files + glob_files:
        key = str(Path(f).resolve()) if Path(f).exists() else f
        if key in seen:
            continue
        seen.add(key)
        files.append(f)
    idx = 0
    filtering = False
    filter_text = ""
    curses.curs_set(0)

    def visible_files():
        if not filter_text:
            return files
        scored = []
        for f in files:
            score = fuzzy_match(filter_text, Path(f).name)
            if score is not None:
                scored.append((score, f))
        scored.sort(key=lambda pair: pair[0])
        return [f for _, f in scored]

    while True:
        shown = visible_files()
        idx = max(0, min(idx, len(shown) - 1)) if shown else 0
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        header = ("Open Screenplay  (typing filters, Enter select, "
                  "Esc clear/exit filter)" if filtering else
                  "Open Screenplay  (j/k move, Enter select, / filter, "
                  "e type path, q back)")
        safe_addstr(stdscr, 1, 2, header, curses.A_BOLD)
        if not files:
            safe_addstr(stdscr, 3, 2, f"(no .fountain files found in {save_dir})")
        elif not shown:
            safe_addstr(stdscr, 3, 2, f"(no matches for '{filter_text}')")
        for i, f in enumerate(shown[: h - 6]):
            attr = curses.A_REVERSE if i == idx else 0
            safe_addstr(stdscr, 3 + i, 2, Path(f).name, attr)
        if filtering:
            safe_addstr(stdscr, h - 2, 2, "/" + filter_text)
            curses.curs_set(1)
            stdscr.move(h - 2, 3 + len(filter_text))
        else:
            curses.curs_set(0)
        stdscr.refresh()
        ch = read_key(stdscr)

        if filtering:
            if ch == 27:  # Esc: drop the filter, back to full browsing
                filtering = False
                filter_text = ""
                idx = 0
            elif ch in (curses.KEY_ENTER, 10, 13):
                filtering = False
                if shown:
                    return shown[idx]
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                filter_text = filter_text[:-1]
                idx = 0
            elif ch == curses.KEY_DOWN:
                idx = min(idx + 1, max(0, len(shown) - 1))
            elif ch == curses.KEY_UP:
                idx = max(idx - 1, 0)
            elif is_printable_char(ch):
                filter_text += chr(ch)
                idx = 0
            continue

        if ch in (ord("j"), curses.KEY_DOWN):
            idx = min(idx + 1, max(0, len(shown) - 1))
        elif ch in (ord("k"), curses.KEY_UP):
            idx = max(idx - 1, 0)
        elif ch in (curses.KEY_ENTER, 10, 13) and shown:
            return shown[idx]
        elif ch == ord("/"):
            filtering = True
            filter_text = ""
            idx = 0
        elif ch == ord("e"):
            path = prompt_line(stdscr, h - 2, 2, "Path")
            curses.curs_set(0)
            if path:
                return os.path.expanduser(path)
        elif ch in (ord("q"), ord("Q")):
            return None



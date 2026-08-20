"""Small curses UI helpers: key reading, safe addstr, and single-line
prompts. No dependency on Editor or any other scriptee module."""

import curses

# --------------------------------------------------------------------------
# Small UI helpers
# --------------------------------------------------------------------------

def read_key(stdscr):
    """Read one keypress, Unicode-aware.

    Every input point in Scriptee used to call plain stdscr.getch(), which
    only ever hands back a single byte -- so any non-ASCII character (an
    accented name like "Jose", a typographic em dash "--" for interrupted
    dialogue, curly quotes, ...) arrived as a sequence of bytes each >127,
    none of which matched the "is this a printable character" check
    anywhere in the file. The keystroke was silently swallowed, byte by
    byte, with no error and no sign anything had happened -- typing a
    non-ASCII character just did nothing.

    stdscr.get_wch() decodes multi-byte input correctly and returns either
    a proper Unicode str (regular characters, of any script) or an int
    (recognized function/arrow/etc. keys) exactly like getch() already
    returned for those. Converting the str case to its codepoint via
    ord() keeps every existing `ch == ord("d")`-style comparison working
    unchanged, while letting real Unicode text all the way through instead
    of being dropped a byte at a time.
    """
    get_wch = getattr(stdscr, "get_wch", None)
    if get_wch is None:
        # Fall back to plain getch() if get_wch isn't available (e.g. a
        # test double standing in for a real curses window). Real curses
        # windows have had get_wch() since Python 3.3, so this is just a
        # safety net, not the normal path.
        return stdscr.getch()
    try:
        ch = get_wch()
    except curses.error:
        return -1
    return ord(ch) if isinstance(ch, str) else ch


def is_printable_char(ch):
    """True if `ch` (an int codepoint from read_key()) should be inserted
    as literal text, rather than treated as a control/navigation key.

    Excludes C0 controls, DEL, and the curses.KEY_MIN..KEY_MAX band that
    function/arrow/etc. keys occupy -- everything else (including the
    whole non-ASCII Unicode range) is treated as regular typed text. This
    replaces the old `32 <= ch < 127` ASCII-only check, which is what made
    non-ASCII input get silently dropped -- see read_key()."""
    if ch < 32 or ch == 127:
        return False
    if curses.KEY_MIN <= ch <= curses.KEY_MAX:
        return False
    return True


def safe_addstr(stdscr, y, x, s, attr=0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    s = s[: max(0, w - x - 1)]
    try:
        stdscr.addstr(y, x, s, attr)
    except curses.error:
        pass


def prompt_line(stdscr, y, x, label, initial=""):
    """Simple single-line text input with backspace support.

    `initial` seeds the field with existing text (cursor at the end) so a
    prompt can be used to *edit* a value, not just type one from scratch
    -- e.g. :cover re-editing an already-filled-in cover page. Plain Enter
    with no changes returns `initial` unmodified; backspacing it away and
    hitting Enter returns "" (clears the field), same as never typing
    anything for a blank prompt.
    """
    curses.curs_set(1)
    text = initial
    h, w = stdscr.getmaxyx()
    while True:
        safe_addstr(stdscr, y, x, " " * (w - x - 1))
        safe_addstr(stdscr, y, x, f"{label} -> {text}")
        stdscr.move(y, min(x + len(label) + 4 + len(text), w - 1))
        stdscr.refresh()
        ch = read_key(stdscr)
        if ch in (curses.KEY_ENTER, 10, 13):
            return text
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            text = text[:-1]
        elif ch == 27:
            return text
        elif is_printable_char(ch):
            text += chr(ch)



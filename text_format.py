"""Text formatting: wrap widths/indents per element type, inline
**bold**/*italic* tokenization, terminal-wrap word wrapping, and the
cursor <-> wrapped-row position mapping used by the editor.

WRAP_WIDTH/INDENT are mutated *in place* by config.apply_runtime_config()
(via .update()), never reassigned, so plain `from text_format import
WRAP_WIDTH` elsewhere stays correct -- every importer keeps seeing the
same dict object and its live values.
"""

import re

# --------------------------------------------------------------------------
# Formatting rules (approximating standard screenplay margins, in columns)
# --------------------------------------------------------------------------

INDENT = {
    "heading": 0, "action": 0, "shot": 0,
    "character": 22, "parenthetical": 18, "dialogue": 10,
    "transition": None,  # right-aligned
}
WRAP_WIDTH = {
    # Courier 12pt is exactly 10 chars/inch, so these are chosen to match
    # the PDF export's printed margins in export_pdf() -- e.g. action runs
    # left_edge (1.5in) to right_edge (7.5in) = 6.0in = 60 chars, not the
    # 70 this used to be, which let action/heading text run past the
    # printed right margin. Keeping these in lockstep with export_pdf()'s
    # margins is what makes the terminal view and the PDF agree on where
    # lines actually break.
    "heading": 60, "action": 60, "shot": 60,
    "character": 40, "parenthetical": 32, "dialogue": 40,
    "transition": 74,
}
UPPERCASE_TYPES = {"heading", "character", "transition", "shot"}
TYPE_LABELS = {
    "heading": "SCENE HEADING", "action": "ACTION", "character": "CHARACTER",
    "dialogue": "DIALOGUE", "parenthetical": "PARENTHETICAL",
    "shot": "SHOT", "transition": "TRANSITION",
}
NEXT_TYPE_ON_ENTER = {
    "character": "dialogue", "parenthetical": "dialogue",
    "dialogue": "dialogue", "heading": "action",
    # After a TRANSITION (e.g. "CUT TO:"), the next line is almost always
    # a new scene heading, not action -- so Enter defaults there instead.
    "transition": "heading", "shot": "action", "action": "action",
}

SCENE_RE = re.compile(r'^(INT|EXT|EST|INT\.?/EXT|I/E)[./ ]', re.IGNORECASE)


def tokenize_inline(text):
    """Split text on **bold** / *italic* markers -> [(substr, style), ...]"""
    parts = re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*)', text)
    out = []
    for p in parts:
        if not p:
            continue
        if p.startswith('**') and p.endswith('**') and len(p) > 3:
            out.append((p[2:-2], 'bold'))
        elif p.startswith('*') and p.endswith('*') and len(p) > 1:
            out.append((p[1:-1], 'italic'))
        else:
            out.append((p, 'normal'))
    return out


def styled_wrap(line, width):
    """Word-wrap a buffer line into rows while preserving bold/italic spans
    *across* wrap boundaries.

    Results are memoized on the line dict itself (keyed by the text/type/
    width that produced them), since this is the hottest path in the editor
    -- it used to re-run the tokenizer and wrap loop for every visible row
    on every single keystroke (and, via page_estimate(), for *every line in
    the whole document* on every keystroke too), which is what made long
    scripts feel laggy. A line's own cache is invalidated automatically the
    moment its text/type/width no longer match what's cached.
    """
    cache = line.get("_wrap_cache")
    key = (line["text"], line["type"], width)
    if cache is not None and cache[0] == key:
        return cache[1]
    rows = _styled_wrap_uncached(line, width)
    line["_wrap_cache"] = (key, rows)
    return rows


def _styled_wrap_uncached(line, width):
    """The actual wrap computation; see styled_wrap() for the caching
    wrapper that should normally be called instead.

    Word-wrap a buffer line into rows while preserving bold/italic spans
    *across* wrap boundaries.

    Returns a list of rows; each row is a list of (substr, style) tuples in
    left-to-right render order. Unlike the previous approach -- wrap the raw
    text with textwrap, then re-run tokenize_inline() on each wrapped
    sub-line -- this tokenizes styling first and wraps the resulting styled
    words, so a `**bold**`/`*italic*` span that happens to land on a wrap
    point keeps its styling on both sides of the break instead of losing it
    (the old approach could literally split a `**` marker pair across two
    rows, so neither row saw a complete marker and the span silently
    reverted to plain text).

    Wrapping behavior (greedy fill, whitespace runs collapsed to a single
    space, long unbreakable words left over-width) intentionally mirrors
    `textwrap.wrap(..., break_long_words=False)` so existing line-break
    points don't shift.
    """
    text = line["text"]
    if line["type"] in UPPERCASE_TYPES:
        text = text.upper()
    if not text.strip():
        return [[("", "normal")]]

    # Flatten into "words": a word is a maximal run of non-space text, kept
    # as a list of (chunk, style) sub-pieces since a single word can itself
    # straddle a style boundary (e.g. "un**believ**able").
    words = []
    current_word = []
    for chunk, style in tokenize_inline(text):
        for piece in re.split(r'(\s+)', chunk):
            if piece == "":
                continue
            if piece.isspace():
                if current_word:
                    words.append(current_word)
                    current_word = []
            else:
                current_word.append((piece, style))
    if current_word:
        words.append(current_word)

    def word_len(word):
        return sum(len(c) for c, _ in word)

    rows = []
    row = []
    row_len = 0
    for word in words:
        wlen = word_len(word)
        if not row:
            row = list(word)
            row_len = wlen
        elif row_len + 1 + wlen <= width:
            row.append((" ", "normal"))
            row.extend(word)
            row_len += 1 + wlen
        else:
            rows.append(row)
            row = list(word)
            row_len = wlen
    if row:
        rows.append(row)
    return rows or [[("", "normal")]]


def wrapped_lines_for(line, width):
    """Plain-text wrapped rows (no style info) -- used for cursor mapping,
    page estimation, etc. Delegates to styled_wrap() so the line breaks it
    reports always agree with what render() actually draws."""
    return ["".join(chunk for chunk, _ in row) for row in styled_wrap(line, width)]


def display_offset(text, cx):
    """Map a raw-text cursor offset `cx` (which counts the literal `*`/`**`
    marker characters) onto the corresponding offset in the *displayed*
    text, where those markers are stripped out by tokenize_inline().

    Without this, the cursor was mapped straight onto the marker-stripped
    display text using the raw offset -- correct only up until the first
    complete `*.../*` or `**...**` span, at which point the display text
    is shorter than the raw text by however many marker characters were
    stripped. In practice this meant the on-screen cursor visibly jumped
    backward the instant you finished typing a closing `*`/`**`, and then
    stayed out of sync (increasingly so with more styled spans) for the
    rest of that line -- every arrow-key move and every further keystroke
    landing one or more columns off from where it actually was. This
    walks the same tokenizer boundaries render() draws from, so raw and
    display positions are always computed from the same source of truth.

    A cursor that lands inside the marker characters themselves (e.g. the
    two `*`s of `**`) is pulled to the nearer edge of the styled content --
    there's no on-screen character for "between the two asterisks" once
    they're not rendered, so the nearest real position is the best fit.
    """
    raw_pos = 0
    disp_pos = 0
    for piece in re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*)', text):
        if not piece:
            continue
        if piece.startswith('**') and piece.endswith('**') and len(piece) > 3:
            content, delim = piece[2:-2], 2
        elif piece.startswith('*') and piece.endswith('*') and len(piece) > 1:
            content, delim = piece[1:-1], 1
        else:
            content, delim = piece, 0
        raw_len = len(piece)
        disp_len = len(content)
        if cx < raw_pos + raw_len:
            local = cx - raw_pos
            if delim == 0:
                return disp_pos + local
            if local <= delim:
                return disp_pos
            if local >= raw_len - delim:
                return disp_pos + disp_len
            return disp_pos + (local - delim)
        raw_pos += raw_len
        disp_pos += disp_len
    return disp_pos


def locate_cursor(wrapped, cx):
    """Map a *display-text* cursor offset `cx` onto (row, col) within
    `wrapped` lines (the output of wrapped_lines_for/styled_wrap).

    Callers with a raw-text offset (i.e. `Editor.cx`, which counts literal
    `*`/`**` markers) should use cursor_position() instead -- see that
    function's docstring for why a raw offset can't just be fed straight
    in here.

    textwrap collapses internal whitespace runs to single spaces, so this
    is an approximation for text with irregular spacing, but it is exact
    for normally single-spaced text -- which is the common case -- and is
    far better than always reporting the first wrapped line (the previous
    behavior), which put the terminal cursor in the wrong place for any
    line that wrapped past its column width.
    """
    if not wrapped:
        return 0, 0
    consumed = 0
    for row, wline in enumerate(wrapped):
        length = len(wline)
        is_last = row == len(wrapped) - 1
        if cx <= consumed + length or is_last:
            return row, max(0, min(cx - consumed, length))
        consumed += length + 1  # +1 for the space textwrap collapsed here
    return len(wrapped) - 1, len(wrapped[-1])


def _collapsed_offset(logical, cx):
    """Map a raw offset in `logical` text (markup already stripped, but
    whitespace intact) onto the offset it would have in the *collapsed*
    display text styled_wrap()'s word-list construction produces --
    where a run of whitespace between two words becomes exactly one
    rendered space, and a run before the first word or after the last
    one is dropped entirely.

    The naive version of this just clamps cx to the collapsed text's
    length, which made the cursor visibly freeze on every space you
    typed (leading, interior double-spaces, and trailing all had this
    problem, not just trailing) -- it only ever caught up once another
    real character gave the wrap algorithm a "word" to hang that
    whitespace off of. Since there's nothing to actually draw for a bare
    space, this instead lets the cursor keep advancing one column per
    raw whitespace character typed, from wherever that run of
    whitespace starts in the collapsed display -- so it never sits
    still while you're mid-keystroke, in any position on the line.

    Walks the exact same word/whitespace token split styled_wrap() uses,
    so the two can never disagree about where words start and end.
    """
    tokens = [t for t in re.split(r'(\s+)', logical) if t]
    raw_pos = 0
    disp_pos = 0
    for idx, tok in enumerate(tokens):
        tok_len = len(tok)
        at_last_token = idx == len(tokens) - 1
        if cx < raw_pos + tok_len or (at_last_token and cx == raw_pos + tok_len):
            return disp_pos + (cx - raw_pos)
        raw_pos += tok_len
        if tok.isspace():
            if not at_last_token:  # a word always follows in the alternating split
                disp_pos += 1
        else:
            disp_pos += tok_len
    return disp_pos


def cursor_position(line, width, cx):
    """Compute the (row, col) to draw the terminal cursor at for raw
    offset `cx` into `line["text"]`, given the wrapped rows `width`
    produces for it. This is what render() should call -- not
    locate_cursor() directly -- because two layers of raw-vs-display
    mismatch have to be corrected for, or the cursor visibly stops
    tracking what's actually being typed:

    1. `*`/`**` markup: the marker characters are stripped from the
       rendered text (they become underline/bold styling instead), so a
       raw offset past a completed span needs shifting back by however
       many marker characters preceded it. See display_offset().

    2. Collapsed whitespace: see _collapsed_offset() -- leading,
       interior-double-space, and trailing whitespace all render as
       fewer characters (often zero) than were actually typed.
    """
    raw_text = line["text"]
    if line["type"] in UPPERCASE_TYPES:
        raw_text = raw_text.upper()  # matches styled_wrap's own uppercasing; case never changes length/offsets
    logical = "".join(chunk for chunk, _style in tokenize_inline(raw_text))
    disp_cx = _collapsed_offset(logical, display_offset(raw_text, cx))
    wrapped = wrapped_lines_for(line, width)
    # Total length of everything actually rendered so far (all rows, plus
    # one collapsed separator per row break). If disp_cx lands beyond that
    # -- the common live-typing case, cursor sitting right where you're
    # typing, past the last real character -- let it keep advancing past
    # the last row's content instead of snapping back to sit on top of the
    # last character (which is what locate_cursor()'s normal clamping,
    # correct for in-bounds/navigated positions, would otherwise do here).
    total_rendered = sum(len(w) for w in wrapped) + (len(wrapped) - 1)
    if disp_cx > total_rendered:
        row, col = locate_cursor(wrapped, total_rendered)
        return row, col + (disp_cx - total_rendered)
    return locate_cursor(wrapped, disp_cx)


def _inverse_collapsed_offset(logical, disp_target):
    """Inverse of _collapsed_offset(): map a collapsed-whitespace display
    offset back onto an offset in `logical` (marker-stripped, whitespace
    still intact) text. Structurally mirrors _collapsed_offset()'s own
    token walk step for step, so the two stay exact inverses of one
    another instead of drifting out of sync with each other as either
    gets tweaked."""
    tokens = [t for t in re.split(r'(\s+)', logical) if t]
    raw_pos = 0
    disp_pos = 0
    for idx, tok in enumerate(tokens):
        tok_len = len(tok)
        at_last_token = idx == len(tokens) - 1
        if disp_target < disp_pos + tok_len or (at_last_token and disp_target == disp_pos + tok_len):
            return raw_pos + max(0, disp_target - disp_pos)
        raw_pos += tok_len
        if tok.isspace():
            if not at_last_token:
                disp_pos += 1
        else:
            disp_pos += tok_len
    return raw_pos


def _inverse_display_offset(text, logical_target):
    """Inverse of display_offset(): map an offset in the marker-stripped
    "logical" text back onto the corresponding raw offset in `text`
    (which still has the literal `*`/`**` markers in it)."""
    raw_pos = 0
    disp_pos = 0
    for piece in re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*)', text):
        if not piece:
            continue
        if piece.startswith('**') and piece.endswith('**') and len(piece) > 3:
            content, delim = piece[2:-2], 2
        elif piece.startswith('*') and piece.endswith('*') and len(piece) > 1:
            content, delim = piece[1:-1], 1
        else:
            content, delim = piece, 0
        raw_len = len(piece)
        disp_len = len(content)
        if logical_target < disp_pos + disp_len or (disp_len == 0 and logical_target == disp_pos):
            return raw_pos + delim + max(0, logical_target - disp_pos)
        raw_pos += raw_len
        disp_pos += disp_len
    return raw_pos


def raw_cx_for_visual(line, width, row, col):
    """Inverse of cursor_position(): given a target (row, col) in the
    wrapped *display* rows that `width` produces for `line`, return the
    raw offset into line["text"] that lands the cursor there.

    This is what makes visual-row movement (Editor._move_visual_row(),
    below) possible: without it, "down" from the first row of a
    two-row-wrapped ACTION/DIALOGUE/etc. line had nowhere to land inside
    that same logical line, so it always jumped straight to the *next*
    buffer element instead -- skipping the wrapped second row entirely,
    and (since the destination line is often much shorter, e.g. a
    CHARACTER cue) leaving the cursor and any further typing somewhere
    the person didn't intend.
    """
    raw_text = line["text"]
    if line["type"] in UPPERCASE_TYPES:
        raw_text = raw_text.upper()  # matches cursor_position()'s own uppercasing
    logical = "".join(chunk for chunk, _style in tokenize_inline(raw_text))
    wrapped = wrapped_lines_for(line, width)
    row = max(0, min(row, len(wrapped) - 1))
    col = max(0, min(col, len(wrapped[row])))
    disp_target = sum(len(w) + 1 for w in wrapped[:row]) + col
    logical_target = _inverse_collapsed_offset(logical, disp_target)
    raw_cx = _inverse_display_offset(raw_text, logical_target)
    return max(0, min(raw_cx, len(line["text"])))



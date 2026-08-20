"""PDF export: row drawing, pagination simulation, sides/character-scoped
export settings, and the top-level export_pdf() entry point.

Reads pdf_geometry's PDF_* geometry/font constants via qualified
`pdf_geometry.NAME` access (see pdf_geometry.py's module docstring for
why a bare import would be unsafe here).
"""

import re
import shlex

try:
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
except ImportError:
    pass

import pdf_geometry
from pdf_geometry import HAVE_REPORTLAB, _pdf_font_for_style, PDF_SEMIBOLD_OFFSET
import config
from text_format import WRAP_WIDTH, styled_wrap, wrapped_lines_for, _styled_wrap_uncached
from fountain import split_character_cue, DIALOGUE_CHAIN_TYPES

def _draw_styled_row(c, row, x, y, size, right_align=False):
    """Draw one styled_wrap() row (list of (substr, style) pieces),
    switching fonts per piece so bold/italic render as real Courier-Bold/
    Courier-Oblique instead of plain text. "semibold" pieces stay in the
    regular font but get struck twice with a sub-point horizontal offset --
    a standard faux-bold trick that thickens strokes just enough to read as
    heavier than plain text without the full weight of true Courier-Bold
    (used for CHARACTER cues, which are conventionally bolder than dialogue
    but shouldn't compete visually with scene headings/transitions, which
    stay true bold). Returns nothing; draws in place."""
    if right_align:
        total = sum(c.stringWidth(s, _pdf_font_for_style(st), size) for s, st in row)
        x = x - total
    cx = x
    for substr, style in row:
        font = _pdf_font_for_style(style)
        c.setFont(font, size)
        c.drawString(cx, y, substr)
        if style == "semibold":
            c.drawString(cx + PDF_SEMIBOLD_OFFSET, y, substr)
        cx += c.stringWidth(substr, font, size)


def _draw_styled_row_centered(c, row, center_x, y, size):
    """Like _draw_styled_row(), but centered on center_x -- used for a
    CHARACTER cue at the top of a dual-dialogue column, which is centered
    over its column rather than left-aligned like a normal cue."""
    total = sum(c.stringWidth(s, _pdf_font_for_style(st), size) for s, st in row)
    _draw_styled_row(c, row, center_x - total / 2, y, size)


def _forced_style(rows, style):
    """Return a copy of styled_wrap() rows with every piece's style forced
    to `style`, discarding whatever inline **bold**/*italic* markup (or
    lack of it) the source text had. Used for the screenplay-convention
    elements that are *always* a fixed style regardless of what the writer
    typed -- scene headings and character cues are always bold, transitions
    are always bold, and parenthetical asides are always italic."""
    return [[(substr, style) for substr, _ in row] for row in rows]


def _pdf_element_is_glued(buffer, idx):
    """True if buffer[idx] should be kept glued to the line right after it
    -- no blank line between them -- when exporting to PDF. Pulled out as
    its own function (mirroring the "glued" rule to_fountain() already
    applies, see its docstring) so it's testable without needing reportlab
    installed, and so export_pdf() can't silently drift from it again."""
    t = buffer[idx]["type"]
    next_ln = buffer[idx + 1] if idx + 1 < len(buffer) else None
    return (
        t in DIALOGUE_CHAIN_TYPES
        and next_ln is not None
        and next_ln["type"] in ("parenthetical", "dialogue")
    )


def _dialogue_block_end(buffer, i):
    """Given i pointing at a CHARACTER line, return the index of the last
    line of its glued CHARACTER/PARENTHETICAL/DIALOGUE chain (walking the
    same "glued" rule _pdf_element_is_glued() applies one pair at a time)."""
    k = i
    while _pdf_element_is_glued(buffer, k):
        k += 1
    return k


def _next_content_index(buffer, i):
    """Index of the next buffer line after i with non-blank text, or None
    if there isn't one. Blank buffer lines (a spacer the writer typed, not
    a Fountain structural separator -- see from_fountain()) don't normally
    appear between two dialogue blocks, but this tolerates it if they do."""
    k = i + 1
    while k < len(buffer) and not buffer[k]["text"].strip():
        k += 1
    return k if k < len(buffer) else None


def tag_revision_marks(buffer, marked_indices):
    """Return a new list of shallow-copied line dicts (the originals are
    never mutated -- same pattern buffer_for_scenes() already uses for
    its own "_export_scene_no" tag) with "_revised" set True on every
    index in `marked_indices`. export_pdf() looks for that key to draw
    the per-line "*" mark; see revised_line_indices()/do_export_pdf() in
    editor.py, which computes `marked_indices` and calls this before a
    scoped export's own buffer_for_scenes() filtering (so the flag
    survives into a sides/character-scoped PDF too)."""
    out = []
    for i, ln in enumerate(buffer):
        if i in marked_indices:
            ln = dict(ln)
            ln["_revised"] = True
        out.append(ln)
    return out


def find_dual_pair(buffer, i):
    """If buffer[i] starts a Fountain dual-dialogue pair -- a CHARACTER
    line whose paired second cue (the next non-blank line after this
    block) carries the "dual" flag (see toggle_dual_dialogue()) -- return
    (block1_end, second_start, block2_end): block1_end is the last index
    of the first CHARACTER/PARENTHETICAL/DIALOGUE chain, second_start is
    the paired CHARACTER line's index, and block2_end is the last index of
    its own chain. Returns None if buffer[i] isn't a dual-pair opener."""
    if buffer[i]["type"] != "character" or buffer[i].get("dual"):
        return None
    block1_end = _dialogue_block_end(buffer, i)
    m = _next_content_index(buffer, block1_end)
    if m is None or buffer[m]["type"] != "character" or not buffer[m].get("dual"):
        return None
    block2_end = _dialogue_block_end(buffer, m)
    return block1_end, m, block2_end


def _dual_pair_opener_above(buffer, i):
    """Search upward from buffer[i] (exclusive) for the CHARACTER line
    whose dialogue chain ends directly before buffer[i] -- tolerating
    blank buffer lines and walking back through a PARENTHETICAL/DIALOGUE
    chain -- so it's a valid dual-dialogue pairing target for buffer[i].
    Returns that CHARACTER line's index, or None if buffer[i] has nothing
    to pair with above it (used by toggle_dual_dialogue() to check *before*
    setting the "dual" flag, so the flag is never set to something that
    would silently do nothing on export -- see find_dual_pair(), which
    does the same walk but forward and requires the flag already set)."""
    k = i - 1
    while k >= 0:
        t, txt = buffer[k]["type"], buffer[k]["text"]
        if not txt.strip():
            k -= 1
            continue
        if t == "character":
            break
        if t in ("dialogue", "parenthetical"):
            k -= 1
            continue
        return None  # hit something that isn't a dialogue chain -- no target
    else:
        return None
    if buffer[k].get("dual"):
        return None  # that cue is itself already the second half of a pair
    block1_end = _dialogue_block_end(buffer, k)
    if _next_content_index(buffer, block1_end) != i:
        return None
    return k


def _dual_column_rows(buffer, i, end):
    """Wrapped rows for one column of a dual-dialogue block (buffer[i..end],
    a CHARACTER line plus its PARENTHETICAL/DIALOGUE chain), at the
    narrower pdf_geometry.PDF_DUAL_WRAP_WIDTH -- each row tagged with its draw style
    ('character'/'parenthetical'/'dialogue') so the caller can position
    and font each one correctly."""
    out = []
    for idx in range(i, end + 1):
        ln = buffer[idx]
        if ln["type"] == "character":
            out.append(("character", [(ln["text"].upper(),
                                        "semibold" if pdf_geometry.PDF_CHARACTER_BOLD else None)]))
        elif ln["type"] == "parenthetical":
            body = ln["text"] if ln["text"].startswith("(") else f"({ln['text']})"
            rows = _forced_style(
                _styled_wrap_uncached({"type": "parenthetical", "text": body},
                                      pdf_geometry.PDF_DUAL_WRAP_WIDTH),
                "italic") if pdf_geometry.PDF_PAREN_ITALIC else _styled_wrap_uncached(
                {"type": "parenthetical", "text": body}, pdf_geometry.PDF_DUAL_WRAP_WIDTH)
            out.extend(("parenthetical", r) for r in rows)
        else:  # dialogue
            rows = styled_wrap(ln, pdf_geometry.PDF_DUAL_WRAP_WIDTH)
            out.extend(("dialogue", r) for r in rows)
    return out


pdf_geometry.PDF_ROWS_PER_PAGE = 55
# export_pdf() prints single-spaced 12pt Courier (12pt leading) inside a
# US Letter page with 1in top/bottom margins, i.e. 9in = 648pt of usable
# height. A row is drawn whenever the *current* y position hasn't yet
# dropped below the bottom margin, so a page holds floor(648/12) + 1 = 55
# rows before the 56th row rolls onto a fresh page. Kept as one named
# constant (rather than re-deriving it independently in two places) so
# paginate_buffer() below and export_pdf() can't quietly drift apart. Its
# *value* is recomputed by _recompute_pdf_geometry() once config is
# loaded -- 55 here is only the pre-config startup default.


def paginate_buffer(buffer):
    """Simulate export_pdf()'s page-break placement across `buffer`,
    without needing reportlab installed.

    Returns (starts, total_pages): starts[i] is the (page, row) that
    buffer line i's first row lands on -- page is 1-indexed among
    *script* pages (i.e. not counting the title page export_pdf() adds
    separately when metadata is present), row is 0-indexed from the top
    of that page. total_pages is the final script page count.

    This replaces the old "total wrapped lines / 55" estimate, which
    counted a blank-line gap after *every* buffer line including glued
    CHARACTER/PARENTHETICAL/DIALOGUE blocks that export_pdf() actually
    prints with no gap between them -- on a dialogue-heavy script that
    phantom gap adds up to several extra pages of estimate that the real
    PDF never has. Mirroring export_pdf()'s exact row-by-row placement
    (including its "draw a blank row, then check for overflow" order for
    blank/gap rows, vs. "check for room, then draw" for real content
    rows) keeps the TUI's page count and the exported PDF's page count in
    agreement, page for page.
    """
    page, row = 1, 0
    starts = [(1, 0)] * len(buffer)
    # Mirrors export_pdf()'s current_character tracking -- a DIALOGUE line
    # with no CHARACTER cue above it (malformed buffer) gets no (MORE)/
    # (CONT'D) treatment there, so it must not get the extra page here
    # either, or the two would disagree on page count.
    has_character = False

    def blank_row():
        # Blank buffer lines and inter-element gaps are placed on the
        # current page even if that overflows it -- the overflow only
        # rolls onto a new page for whatever comes *next* (matches
        # export_pdf()'s "y -= leading; if y < bottom_y: new_page()").
        nonlocal page, row
        row += 1
        if row >= pdf_geometry.PDF_ROWS_PER_PAGE:
            page += 1
            row = 0

    idx = 0
    n = len(buffer)
    while idx < n:
        pair = find_dual_pair(buffer, idx)
        if pair is not None:
            # Mirrors export_pdf()'s row-by-row dual-pair drawing (see its
            # own comment) -- a pair that's taller than the room left on
            # the page splits mid-block, with a page bumped in for
            # whichever column(s) are still mid-DIALOGUE at the break
            # (same "(MORE)"/"(CONT'D)" rule single-column dialogue gets),
            # rather than the whole pair moving down as one atomic unit.
            #
            # NOTE on granularity: every buffer line in the pair (both
            # cues, both dialogue chains) is recorded at the pair's
            # *starting* (page, row) below, even lines that end up on the
            # far side of a mid-pair split -- the same "only the
            # element's first row is tracked" granularity every other
            # multi-row element already has here (e.g. a long ACTION
            # paragraph that itself wraps across a page break), not a
            # regression specific to splitting.
            block1_end, second_start, block2_end = pair
            rows1 = _dual_column_rows(buffer, idx, block1_end)
            rows2 = _dual_column_rows(buffer, second_start, block2_end)
            n_rows = max(len(rows1), len(rows2))
            has_character = True
            if row >= pdf_geometry.PDF_ROWS_PER_PAGE:
                page += 1
                row = 0
            for k in range(idx, block2_end + 1):
                starts[k] = (page, row)
            row_i = 0
            while row_i < n_rows:
                # Top-of-row check, same as every other multi-row element
                # above -- catches a "plain" overflow (the break falls
                # within a CHARACTER/PARENTHETICAL row of one or both
                # columns, so neither gets a (MORE)/(CONT'D) treatment)
                # by simply rolling onto a fresh page for the next row.
                if row >= pdf_geometry.PDF_ROWS_PER_PAGE:
                    page += 1
                    row = 0
                row += 1
                if row_i < n_rows - 1 and row >= pdf_geometry.PDF_ROWS_PER_PAGE:
                    cont1 = row_i + 1 < len(rows1) and rows1[row_i][0] == "dialogue"
                    cont2 = row_i + 1 < len(rows2) and rows2[row_i][0] == "dialogue"
                    if cont1 or cont2:
                        page += 1
                        row = 1  # the repeated (CONT'D) cue(s) take row 0
                row_i += 1
            blank_row()  # a dual pair is never glued to what follows
            idx = block2_end + 1
            continue

        ln = buffer[idx]
        t, txt = ln["type"], ln["text"]
        if not txt.strip():
            # A blank buffer line is spacing the *writer* typed for their
            # own on-screen readability, not a Fountain structural
            # separator -- the one-blank-line industry-standard gap
            # between elements is already added below via gap_after,
            # regardless of what's in the buffer. So a blank line (or a
            # run of several) contributes no rows of its own here: however
            # much whitespace you leave in the editor, the exported PDF
            # (and this page-count estimate, which must stay in lockstep
            # with export_pdf()) always uses exactly the standard gap.
            starts[idx] = (page, row)
            idx += 1
            continue

        width = WRAP_WIDTH.get(t, 60)
        n_rows = len(wrapped_lines_for(ln, width))
        gap_after = not _pdf_element_is_glued(buffer, idx)

        # A DIALOGUE block that splits across a page break gets an extra
        # page bumped in for it, mirroring export_pdf()'s "(MORE)" /
        # "(CONT'D)" handling: "(MORE)" is drawn off-grid at the overflow
        # position (so it doesn't itself consume a counted row), then the
        # repeated CHARACTER cue occupies row 0 of the fresh page before
        # the remaining rows continue. Only DIALOGUE splits this way --
        # see the same check in export_pdf().
        if t == "character":
            has_character = True
        is_dialogue = t == "dialogue" and has_character
        # Real content: room is checked *before* each row is drawn.
        for row_i in range(n_rows):
            if row >= pdf_geometry.PDF_ROWS_PER_PAGE:
                page += 1
                row = 0
            if row_i == 0:
                starts[idx] = (page, row)
            row += 1
            if is_dialogue and row_i < n_rows - 1 and row >= pdf_geometry.PDF_ROWS_PER_PAGE:
                page += 1
                row = 1  # the repeated CHARACTER (CONT'D) cue takes row 0
        if gap_after:
            blank_row()
        idx += 1
    return starts, page


def scene_bounds(buffer):
    """List of (start_idx, end_idx_exclusive) for every scene in `buffer`,
    in scene order (1-based externally, matching :scenes/scene_number_at).
    A scene is a HEADING line plus everything up to (not including) the
    next HEADING. Anything before the first heading has no scene number
    and is never included by scene/character PDF filtering below."""
    heads = [i for i, ln in enumerate(buffer) if ln["type"] == "heading"]
    bounds = []
    for n, start in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(buffer)
        bounds.append((start, end))
    return bounds


def sides_export_settings(cfg):
    """Resolve cfg["sides"] against the defaults, tolerating a partial or
    missing section (old configs on disk, or a bare {}/None passed from a
    test) exactly like character_export_settings() does. Returns a plain
    dict with every [sides] key present."""
    default = config.DEFAULT_CONFIG["sides"]
    sides = (cfg or {}).get("sides", {}) if cfg is not None else {}
    resolved = dict(default)
    resolved.update(sides or {})
    return resolved


def default_sides_title(sides_cfg, *, start=None, end=None, name=None):
    """Auto-generated Title for a scoped ":pdf" export, used when the
    title prompt is left blank (or skipped via sides.prompt_title =
    false). Pass either `name` (a ":pdf char NAME" export) or `start`/
    `end` (a scene-range export) -- never both.
    """
    if name is not None:
        return sides_cfg["character_title_format"].format(name=name)
    if start == end:
        return sides_cfg["title_format_single"].format(start=start, end=end)
    return sides_cfg["title_format"].format(start=start, end=end)


def sides_excerpt_subtitle(script_title, sides_cfg):
    """The small "(an excerpt from ...)" line drawn under a scoped
    export's title, or None if sides.excerpt_note is off or the script
    itself has no title to excerpt from -- e.g. an imported .fountain
    file that never had a title page, where self.metadata is {} and
    there's nothing to name (see do_export_pdf())."""
    if not sides_cfg.get("excerpt_note", True):
        return None
    script_title = (script_title or "").strip()
    if not script_title:
        return None
    return sides_cfg["excerpt_format"].format(title=script_title)


def character_export_settings(cfg):
    """Resolve cfg["character_export"] against the defaults, tolerating a
    partial or missing section (old configs on disk, or a bare {} passed
    from a test) exactly like the rest of apply_runtime_config() does.
    Returns (search_in: set[str], aliases: dict[str, list[str]]).
    """
    ce_default = config.DEFAULT_CONFIG["character_export"]
    ce = (cfg or {}).get("character_export", ce_default) if cfg is not None else ce_default
    search_in = set(ce.get("search_in", ce_default["search_in"]))
    aliases = ce.get("aliases", ce_default["aliases"]) or {}
    return search_in, aliases


def names_for_character(name, aliases):
    """The uppercased set of names to search for when resolving `name`
    against `character_export.aliases`: `name` itself, plus every other
    name in whichever alias group it belongs to. Lookup works from either
    side -- `:pdf char Danny` (the canonical CHARACTER-cue name, an alias
    key) and `:pdf char Dan` (one of its configured nicknames, an alias
    value) both resolve to the same {DANNY, DAN} group, so it doesn't
    matter which name you actually type at the prompt.
    """
    name_u = name.strip().upper()
    names = {name_u} if name_u else set()
    for key, alist in (aliases or {}).items():
        key_u = key.strip().upper()
        group = {key_u} | {a.strip().upper() for a in (alist or []) if a.strip()}
        if name_u in group:
            names.update(group)
    return names


def scenes_matching_character(buffer, name, cfg=None):
    """1-based scene numbers (see scene_bounds()) where `name` (or one of
    its configured aliases, see names_for_character()) speaks -- a
    CHARACTER cue whose base name (extension like "(V.O.)" stripped, see
    split_character_cue()) matches -- or is mentioned in one of the line
    types listed in cfg["character_export"]["search_in"] (ACTION and
    DIALOGUE by default). Matching is case-insensitive and whole-word
    only (a search for "AL" doesn't match "ALWAYS" or hit inside "ALEX"),
    so a short or common name doesn't pull in unrelated scenes.

    Searching DIALOGUE (on by default) is what gives a scoped export full
    context even when the requested character never appears in a scene
    themselves: if some *other* character's line mentions them by name
    (or by an alias, lowercase, mixed case, mid-sentence -- anything that
    still matches as a whole word), that scene is included too. Prose
    line types beyond CHARACTER cues are searched because a character can
    also appear in a scene with no dialogue of their own at all, and
    :rename deliberately does NOT touch prose (see its docstring /
    Limitations in REFERENCE.md), so this has to do its own scan rather
    than reusing that matcher.

    cfg=None uses the built-in defaults (search ACTION + DIALOGUE, no
    aliases) -- callers with a loaded config should pass it so
    user-configured search_in/aliases are honored.
    """
    search_in, aliases = character_export_settings(cfg)
    names = names_for_character(name, aliases)
    if not names:
        return set()
    word_res = [re.compile(r'\b' + re.escape(n) + r'\b') for n in names]
    bounds = scene_bounds(buffer)
    matches = set()
    for scene_no, (start, end) in enumerate(bounds, start=1):
        for i in range(start, end):
            ln = buffer[i]
            t = ln["type"]
            if t == "character":
                base, _ext = split_character_cue(ln["text"])
                if base.strip().upper() in names:
                    matches.add(scene_no)
                    break
            elif t in search_in:
                text_u = ln["text"].upper()
                if any(wr.search(text_u) for wr in word_res):
                    matches.add(scene_no)
                    break
    return matches


def buffer_for_scenes(buffer, scene_numbers):
    """A new buffer (line dicts shallow-copied, never the originals --
    export_pdf must not see mutations bleed back into the live document)
    containing only the given 1-based scene numbers, in scene order
    regardless of the order `scene_numbers` was given in. Each kept
    HEADING line carries its *original* scene number under
    "_export_scene_no" so export_pdf() can stamp that instead of a
    position-in-the-subset count -- a sides PDF for scenes 4, 9, and 12
    should still say "4", "9", "12" in the margin, not "1", "2", "3",
    since that's the number continuity/AD sheets and the rest of the cast
    already reference.
    """
    bounds = scene_bounds(buffer)
    out = []
    for scene_no in sorted(n for n in scene_numbers if 1 <= n <= len(bounds)):
        start, end = bounds[scene_no - 1]
        for i in range(start, end):
            ln = dict(buffer[i])
            ln.pop("_wrap_cache", None)
            if ln["type"] == "heading":
                ln["_export_scene_no"] = scene_no
            out.append(ln)
    return out


def parse_pdf_scope(arg):
    """Parse the optional scope prefix off a ':pdf' argument.

    Returns (scene_numbers, path_arg):
    - scene_numbers: a set of 1-based scene numbers to restrict the
      export to, or None for "no scene/character filter, export
      everything" (the plain ':pdf' / ':pdf PATH' case).
    - path_arg: whatever's left over to hand to the existing path
      resolution in do_export_pdf() -- None if nothing followed the
      scope.

    Recognized scopes (case-insensitive keywords, everything else is
    passed straight through as a path exactly like before so old
    ':pdf ~/Desktop/draft.pdf' usage is untouched):

        :pdf 5                    -> just scene 5
        :pdf 1-10                 -> scenes 1 through 10
        :pdf 1-10 ~/sides.pdf     -> scenes 1-10, to that path
        :pdf char VIKRANTH        -> every scene VIKRANTH is in
        :pdf char "OLD MAN" out.pdf
        :pdf character VIKRANTH   -> "character" also accepted

    `scene_numbers` is a set of scene numbers, not yet resolved against
    the actual document (a name/range that matches nothing is reported
    by the caller, not here -- this function only parses the grammar).
    """
    if not arg or not arg.strip():
        return None, None
    try:
        tokens = shlex.split(arg)
    except ValueError:
        tokens = arg.split()
    if not tokens:
        return None, None

    head = tokens[0].lower()
    if head in ("char", "character"):
        if len(tokens) < 2:
            return "char:usage", None
        name = tokens[1]
        path = tokens[2] if len(tokens) > 2 else None
        return ("char", name), path

    range_m = re.fullmatch(r'(\d+)-(\d+)', tokens[0])
    single_m = re.fullmatch(r'\d+', tokens[0])
    if range_m:
        lo, hi = int(range_m.group(1)), int(range_m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        path = tokens[1] if len(tokens) > 1 else None
        return set(range(lo, hi + 1)), path
    if single_m:
        path = tokens[1] if len(tokens) > 1 else None
        return {int(tokens[0])}, path

    # Doesn't match any scope keyword/pattern -- the whole arg is a path,
    # exactly like every ':pdf' call before scoping existed.
    return None, arg


def export_pdf(path, metadata, buffer, scene_numbers=True, cover_page=True,
                subtitle=None, revision_note=None):
    """Render `buffer` to an industry-formatted PDF at `path`.

    scene_numbers: stamp each scene heading's number (matching the
    editor's own left-gutter numbering, see scene_number_at()) in the
    left and right margins, mirroring how a production script marks scene
    numbers. On by default since the editor already computes and shows
    these numbers live -- carrying them into the PDF is what production
    ("locked") scripts actually look like, not just an aesthetic add-on.
    Pass False for a clean draft PDF with no margin numbers.

    cover_page: master on/off for the title page, independent of whether
    `metadata` has anything in it. Off means never draw one, full stop --
    for the "I never want a cover page, ever" case (general.cover_page in
    config), as distinct from prompt_missing_titlepage's "ask once if
    there's nothing to put on one" case, which is about the prompt, not
    the page itself.

    subtitle: an optional small-font line drawn just under the title --
    used by do_export_pdf()'s scoped (sides/character) exports for the
    "(an excerpt from ...)" note (see sides_excerpt_subtitle()). None
    (the default) draws nothing extra and leaves the title page laid out
    exactly as it always has been.

    revision_note: an optional small-font line (e.g. "BLUE REVISION --
    2026-08-20") stamped in the bottom-right corner of every script page
    -- the standard industry marker for which color-set draft a printed
    page belongs to, alongside the per-line "*" marks drawn next to any
    buffer line tagged "_revised" (see revised_line_indices()/
    lock_revision() in editor.py). None (the default, and every export
    before revision tracking existed) draws nothing extra.
    """
    if not HAVE_REPORTLAB:
        raise RuntimeError("reportlab is not installed (pip install reportlab)")

    c = canvas.Canvas(str(path), pagesize=(pdf_geometry.PDF_PAGE_W, pdf_geometry.PDF_PAGE_H))
    page_w, page_h = pdf_geometry.PDF_PAGE_W, pdf_geometry.PDF_PAGE_H
    # Standard screenplay format is single-spaced Courier 12 -- 12pt leading
    # (6 lines/inch), not 14.4. The old 14.4 leading plus the tiny 0.3x gap
    # between elements (below) is what made this look cramped/uneven next
    # to properly-formatted PDFs: within a block lines sat too far apart
    # relative to the *between-block* gap, so nothing looked proportional.
    # All geometry below comes from PDF_* globals -- set once by
    # _recompute_pdf_geometry() from cfg["format"]["pdf"] -- rather than
    # being hardcoded here, so config.toml controls both this and the
    # in-editor wrap widths without the two being able to silently drift
    # apart (paginate_buffer() reads the same globals).
    size, leading = pdf_geometry.PDF_FONT_SIZE, pdf_geometry.PDF_LEADING
    left_edge = pdf_geometry.PDF_LEFT_EDGE          # heading/action/shot
    dialogue_left = pdf_geometry.PDF_DIALOGUE_LEFT
    paren_left = pdf_geometry.PDF_PAREN_LEFT
    character_left = pdf_geometry.PDF_CHARACTER_LEFT
    right_edge = pdf_geometry.PDF_RIGHT_EDGE
    top_y = pdf_geometry.PDF_TOP_Y
    bottom_y = pdf_geometry.PDF_BOTTOM_Y

    # Title page
    if cover_page and metadata:
        c.setFont(pdf_geometry.PDF_FONT, size)
        title = metadata.get("Title", "Untitled")
        title_y = page_h * 0.55
        c.setFont(pdf_geometry.PDF_FONT, 16)
        c.drawCentredString(page_w / 2, title_y, title.upper())
        if subtitle:
            # Small italic note under the title (e.g. "(an excerpt from
            # ...)" on a sides/character export) -- kept in its own font
            # size well below the 16pt title so it reads as a caption,
            # not a second title.
            c.setFont(_pdf_font_for_style("italic"), 9)
            c.drawCentredString(page_w / 2, title_y - 20, subtitle)
            y = title_y - 20 - 24
        else:
            y = title_y - 40
        c.setFont(pdf_geometry.PDF_FONT, size)
        author = metadata.get("Author", "")
        if author:
            c.drawCentredString(page_w / 2, y, f"by {author}")
            y -= 20
        for key in ("Genre", "Year"):
            if metadata.get(key):
                c.drawCentredString(page_w / 2, y, f"{key}: {metadata[key]}")
                y -= 16
        contact_lines = [v for k, v in metadata.items() if k.startswith("Contact") and v]
        cy = bottom_y + 20 * len(contact_lines)
        for line in contact_lines:
            c.drawString(left_edge, cy, line)
            cy -= 16
        c.showPage()

    c.setFont(pdf_geometry.PDF_FONT, size)
    y = top_y
    # Industry convention: the title page and the first script page are
    # unnumbered; page numbers ("2.", "3.", ...) appear top-right starting
    # on the second script page.
    script_page_num = 1

    def stamp_page_number():
        if script_page_num > 1:
            c.setFont(pdf_geometry.PDF_FONT, size)
            c.drawRightString(right_edge, page_h - 0.75 * inch, f"{script_page_num}.")

    def stamp_revision_note():
        if revision_note:
            c.setFont(pdf_geometry.PDF_FONT, 8)
            c.drawRightString(right_edge, bottom_y - 0.3 * inch, revision_note)

    def new_page():
        nonlocal y, script_page_num
        c.showPage()
        script_page_num += 1
        c.setFont(pdf_geometry.PDF_FONT, size)
        y = top_y
        stamp_page_number()
        stamp_revision_note()

    stamp_page_number()
    stamp_revision_note()

    scene_count = 0
    # Tracks the most recent CHARACTER cue's raw text (extension and all,
    # e.g. "JOHN (V.O.)") so a dialogue block that splits across a page
    # break can repeat it with "(CONT'D)" appended -- see the dialogue
    # branch of the draw loop below.
    current_character = ""
    has_character = False

    idx = 0
    n = len(buffer)
    while idx < n:
        pair = find_dual_pair(buffer, idx)
        if pair is not None:
            # Two CHARACTER/PARENTHETICAL/DIALOGUE blocks marked as
            # Fountain dual dialogue (see toggle_dual_dialogue()) print
            # side by side in two columns, each cue centered above its
            # own column -- the standard convention for simultaneous
            # dialogue. Drawn row by row (both columns share one y per
            # row, since they sit side by side) rather than as one atomic
            # block, so a pair taller than the room left on the page
            # splits mid-block -- whichever column(s) are still
            # mid-DIALOGUE at the break get "(MORE)" at the foot of this
            # page and their cue repeated with "(CONT'D)" at the top of
            # the next, the same treatment single-column dialogue already
            # gets below. A break that instead falls within a CHARACTER/
            # PARENTHETICAL row (of either column) just rolls onto a
            # fresh page plainly, same restriction the single-column case
            # applies (only DIALOGUE rows ever get MORE/CONT'D).
            block1_end, second_start, block2_end = pair
            has_character = True
            rows1 = _dual_column_rows(buffer, idx, block1_end)
            rows2 = _dual_column_rows(buffer, second_start, block2_end)
            n_rows = max(len(rows1), len(rows2))
            char1_label = buffer[idx]["text"]
            char2_label = buffer[second_start]["text"]
            pair_revised = any(buffer[k].get("_revised") for k in range(idx, block2_end + 1))
            row_i = 0
            pair_mark_y = None
            while row_i < n_rows:
                if y < bottom_y:
                    new_page()
                if row_i == 0:
                    pair_mark_y = y
                for col_x, col_rows in ((pdf_geometry.PDF_DUAL_COL1_X, rows1),
                                         (pdf_geometry.PDF_DUAL_COL2_X, rows2)):
                    if row_i < len(col_rows):
                        kind, row = col_rows[row_i]
                        if kind == "character":
                            _draw_styled_row_centered(
                                c, row, col_x + pdf_geometry.PDF_DUAL_COL_WIDTH / 2, y, size)
                        else:
                            _draw_styled_row(c, row, col_x, y, size)
                y -= leading
                if row_i < n_rows - 1 and y < bottom_y:
                    cont1 = row_i + 1 < len(rows1) and rows1[row_i][0] == "dialogue"
                    cont2 = row_i + 1 < len(rows2) and rows2[row_i][0] == "dialogue"
                    if cont1:
                        c.setFont(pdf_geometry.PDF_FONT, size)
                        c.drawString(pdf_geometry.PDF_DUAL_COL1_X, y, "(MORE)")
                    if cont2:
                        c.setFont(pdf_geometry.PDF_FONT, size)
                        c.drawString(pdf_geometry.PDF_DUAL_COL2_X, y, "(MORE)")
                    if cont1 or cont2:
                        new_page()
                        if cont1:
                            cont_row = [(f"{char1_label.upper()} (CONT'D)",
                                         "semibold" if pdf_geometry.PDF_CHARACTER_BOLD else None)]
                            _draw_styled_row_centered(
                                c, cont_row, pdf_geometry.PDF_DUAL_COL1_X + pdf_geometry.PDF_DUAL_COL_WIDTH / 2,
                                y, size)
                        if cont2:
                            cont_row = [(f"{char2_label.upper()} (CONT'D)",
                                         "semibold" if pdf_geometry.PDF_CHARACTER_BOLD else None)]
                            _draw_styled_row_centered(
                                c, cont_row, pdf_geometry.PDF_DUAL_COL2_X + pdf_geometry.PDF_DUAL_COL_WIDTH / 2,
                                y, size)
                        y -= leading
                row_i += 1
            if pair_revised and pair_mark_y is not None:
                c.setFont(pdf_geometry.PDF_FONT, size)
                c.drawString(right_edge + 6, pair_mark_y, "*")
            # A dual pair is never glued to what follows it (find_dual_pair()
            # only matches on block2_end, whose own "glued" check already
            # came back False) -- one full blank line after it, same as any
            # other element.
            y -= leading
            if y < bottom_y:
                new_page()
            idx = block2_end + 1
            continue

        ln = buffer[idx]
        t, txt = ln["type"], ln["text"]
        if not txt.strip():
            # See the matching comment in paginate_buffer(): a blank
            # buffer line is the writer's own in-editor spacing, not a
            # Fountain separator, and the standard one-blank-line gap
            # between elements already comes from gap_after below -- so
            # blank lines (any number of them) draw nothing and consume
            # no vertical space here. This is what keeps the exported PDF
            # at industry-standard spacing no matter how much blank space
            # you leave yourself in the editor.
            idx += 1
            continue

        if t == "heading":
            scene_count += 1
            # NOTE: no extra pre-decrement here. Every element already gets
            # exactly one blank line *after* it (the "one full blank line
            # between elements" decrement at the bottom of this loop), which
            # is also the blank line *before* whatever comes next -- headings
            # included. An additional decrement here used to double that gap
            # to two blank lines before every scene heading, which on a
            # multi-scene script quietly added a meaningful number of extra
            # pages (this is what caused PDF exports to run several pages
            # longer than the same script in other screenwriting apps).
            rows = _forced_style(styled_wrap(ln, WRAP_WIDTH["heading"]),
                                  "bold") if pdf_geometry.PDF_HEADING_BOLD else styled_wrap(
                ln, WRAP_WIDTH["heading"])
            x = left_edge
            right_align = False
            # Page-check *before* stamping the scene number, not inside the
            # shared row loop below -- the number has to land at the same y
            # as the heading's own first row, so if that row is about to
            # roll onto a fresh page, the number needs to roll with it
            # rather than getting stamped at the old page's stale y.
            if y < bottom_y:
                new_page()
            if scene_numbers:
                c.setFont(pdf_geometry.PDF_FONT, size)
                # A scoped export (see buffer_for_scenes()) tags each kept
                # heading with the scene number it had in the *full*
                # script -- stamp that instead of scene_count (this
                # subset's own position), so a sides PDF for scenes
                # 4/9/12 is still labeled "4"/"9"/"12", matching what the
                # rest of the cast and crew are working from.
                label = str(ln.get("_export_scene_no", scene_count))
                # Locked/production scripts traditionally stamp the scene
                # number in both margins so it's visible whether you're
                # scanning from the left or right edge of a physical page.
                # On a PDF you never read from the right edge, so the
                # right-margin copy was pure duplication -- one number in
                # the left gutter is all it takes.
                c.drawString(left_edge - 0.5 * inch, y, label)
        elif t == "action":
            rows = styled_wrap(ln, WRAP_WIDTH["action"])
            x = left_edge
            right_align = False
        elif t == "shot":
            rows = styled_wrap(ln, WRAP_WIDTH["shot"])
            x = left_edge
            right_align = False
        elif t == "character":
            current_character = txt
            has_character = True
            rows = [[(txt.upper(), "semibold" if pdf_geometry.PDF_CHARACTER_BOLD else None)]]
            x = character_left
            right_align = False
        elif t == "parenthetical":
            body = txt if txt.startswith("(") else f"({txt})"
            rows = _forced_style(
                _styled_wrap_uncached({"type": "parenthetical", "text": body},
                                      WRAP_WIDTH["parenthetical"]),
                "italic") if pdf_geometry.PDF_PAREN_ITALIC else _styled_wrap_uncached(
                {"type": "parenthetical", "text": body}, WRAP_WIDTH["parenthetical"])
            x = paren_left
            right_align = False
        elif t == "dialogue":
            rows = styled_wrap(ln, WRAP_WIDTH["dialogue"])
            x = dialogue_left
            right_align = False
        elif t == "transition":
            rows = [[(txt.upper(), "bold" if pdf_geometry.PDF_TRANSITION_BOLD else None)]]
            x = right_edge
            right_align = True
        else:
            rows = styled_wrap(ln, 60)
            x = left_edge
            right_align = False

        mark_y = None
        if t == "dialogue" and len(rows) > 1 and has_character:
            # A multi-row speech that lands on a page break needs the
            # standard "(MORE)" / "(CONT'D)" markers -- otherwise it just
            # wraps mid-speech onto the next page with nothing telling the
            # reader it's the same character still talking. Checked once
            # per row (not just once for the whole element) since a very
            # long speech could in principle split more than once.
            for row_i, row in enumerate(rows):
                if y < bottom_y:
                    new_page()
                if row_i == 0:
                    mark_y = y
                _draw_styled_row(c, row, x, y, size, right_align=right_align)
                y -= leading
                if row_i < len(rows) - 1 and y < bottom_y:
                    c.setFont(pdf_geometry.PDF_FONT, size)
                    c.drawString(dialogue_left, y, "(MORE)")
                    new_page()
                    cont_row = [(f"{current_character.upper()} (CONT'D)",
                                 "semibold" if pdf_geometry.PDF_CHARACTER_BOLD else None)]
                    _draw_styled_row(c, cont_row, character_left, y, size)
                    y -= leading
        else:
            for row_i, row in enumerate(rows):
                if y < bottom_y:
                    new_page()
                if row_i == 0:
                    mark_y = y
                _draw_styled_row(c, row, x, y, size, right_align=right_align)
                y -= leading

        # Revision mark: a "*" in the right margin, level with the
        # element's first row -- see revised_line_indices() in editor.py
        # and lock_revision(). Lines are tagged "_revised" on a shallow
        # copy of the buffer built just for export (do_export_pdf()),
        # never on the live in-editor buffer itself.
        if ln.get("_revised") and mark_y is not None:
            c.setFont(pdf_geometry.PDF_FONT, size)
            c.drawString(right_edge + 6, mark_y, "*")

        # A CHARACTER/PARENTHETICAL/DIALOGUE speech block is kept glued
        # together with no blank line between its own lines -- same
        # "glued" rule to_fountain() already applies when writing .fountain
        # text (see its docstring), now mirrored here via
        # _pdf_element_is_glued(). Previously this unconditionally added
        # one full blank line after *every* element, including between a
        # CHARACTER cue and its own DIALOGUE, and between DIALOGUE and a
        # following PARENTHETICAL -- neither of which standard screenplay
        # format ever puts a blank line between. On a dialogue-heavy script
        # (this one is almost entirely two- and three-person exchanges)
        # that phantom blank line repeats hundreds of times and was the
        # actual cause of the export running several pages longer than the
        # same script from other screenwriting apps.
        if not _pdf_element_is_glued(buffer, idx):
            y -= leading  # one full blank line between elements (standard format)
            if y < bottom_y:
                new_page()
        idx += 1

    c.save()



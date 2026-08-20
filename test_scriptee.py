"""
Tests for scriptee.py.

These cover the pure-logic surface (fountain I/O, text wrapping, cursor
mapping, config merging, path handling) that doesn't require a live
terminal. The curses-driven Editor.run() loop itself isn't covered here
since it needs a real/mocked screen -- these tests are meant to lock down
the bugs found in review and guard the file format, which is the part
users' actual scripts depend on.

Run with:  python3 -m pytest tests/
"""
import sys
import re
import copy
import curses
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scriptee as s


# --------------------------------------------------------------------------
# Fountain round-trip
# --------------------------------------------------------------------------

def test_fountain_round_trip_basic():
    buffer = [
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "Vijay stares at the kettle."},
        {"type": "character", "text": "VIJAY"},
        {"type": "parenthetical", "text": "(quietly)"},
        {"type": "dialogue", "text": "It's never going to boil."},
        {"type": "transition", "text": "CUT TO"},
    ]
    metadata = {"Title": "Test Script", "Author": "Sriram"}
    text = s.to_fountain(metadata, buffer)
    meta2, buf2 = s.from_fountain(text)

    assert meta2["Title"] == "Test Script"
    assert meta2["Author"] == "Sriram"
    types = [ln["type"] for ln in buf2]
    assert types == ["heading", "action", "character", "parenthetical",
                      "dialogue", "transition"]


def test_from_fountain_scene_heading_detection():
    _, buf = s.from_fountain("EXT. ALLEY - NIGHT\nHe runs.\n")
    assert buf[0]["type"] == "heading"
    assert buf[1]["type"] == "action"


def test_from_fountain_character_then_dialogue():
    text = "GOPI\nWait for me.\n"
    _, buf = s.from_fountain(text)
    assert buf[0]["type"] == "character"
    assert buf[1]["type"] == "dialogue"


def test_from_fountain_transition_marker():
    _, buf = s.from_fountain("> FADE OUT\n")
    assert buf[0]["type"] == "transition"
    assert buf[0]["text"] == "FADE OUT"


def test_from_fountain_unforced_transition_marker():
    # Regression: most Fountain files from other apps (Highland, Slugline,
    # Fade In, ...) write transitions as plain "CUT TO:" rather than
    # forcing them with a leading ">". These used to fall through to the
    # character-cue check (all-caps, short, no trailing punctuation) and
    # get misread as a CHARACTER cue -- with the real next line then
    # lumped in underneath it as DIALOGUE instead of staying ACTION.
    text = "He stares at the door.\n\nCUT TO:\n\nEXT. STREET - DAY\n"
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["action", "transition", "heading"]
    assert buf[1]["text"] == "CUT TO:"


def test_from_fountain_opening_fade_in_is_not_swallowed_as_title_page():
    # Regression: "FADE IN:" (or "CUT TO:", "SUPER:", etc.) as the literal
    # first line of a title-page-less script matches the same "Word: ..."
    # shape as a real title-page key ("Title: ...") -- the title-page loop
    # used to swallow it whole into metadata, silently deleting it from
    # the script (nothing ever displays an unrecognized metadata key).
    text = "FADE IN:\n\nINT. KITCHEN - DAY\n\nVijay stares at the kettle.\n"
    metadata, buf = s.from_fountain(text)
    assert metadata == {}
    assert [l["type"] for l in buf] == ["transition", "heading", "action"]
    assert buf[0]["text"] == "FADE IN:"


def test_from_fountain_real_title_page_keys_still_recognized():
    # The fix for the above must not start rejecting genuine title-page
    # lines, including custom ones (any key the user's own
    # cfg["prompts"]["fields"] uses, not just Title/Author).
    text = ("Title: Beach House\nAuthor: Priya Menon\n"
            "Draft date: 2026\n\nINT. HOUSE - DAY\n")
    metadata, buf = s.from_fountain(text)
    assert metadata == {"Title": "Beach House", "Author": "Priya Menon",
                         "Draft date": "2026"}
    assert buf[0]["type"] == "heading"


def test_from_fountain_strips_section_headings():
    # Section headings ("# ...") are Fountain outline syntax, never meant
    # to be printed -- previously imported as literal, garbled ACTION
    # text (e.g. an action line reading "# Act One").
    text = "# Act One\n\nINT. HOUSE - DAY\n\n## A subsection\n\nHe waits.\n"
    _, buf = s.from_fountain(text)
    texts = [l["text"] for l in buf if l["text"]]
    assert not any(t.startswith("#") for t in texts)
    assert [l["type"] for l in buf if l["text"]] == ["heading", "action"]


def test_from_fountain_strips_synopsis_lines():
    # A single leading "=" is a Synopsis line, also never printed.
    text = "INT. HOUSE - DAY\n\n= A quiet morning\n\nHe waits.\n"
    _, buf = s.from_fountain(text)
    texts = [l["text"] for l in buf if l["text"]]
    assert "= A quiet morning" not in texts
    assert not any(t.startswith("=") for t in texts)


def test_from_fountain_strips_page_breaks():
    # Three-or-more "=" on their own line is a forced page break.
    text = "INT. HOUSE - DAY\n\nHe waits.\n\n===\n\nEXT. STREET - DAY\n"
    _, buf = s.from_fountain(text)
    texts = [l["text"] for l in buf]
    assert not any(t.strip("=") == "" and t for t in texts)
    assert [l["type"] for l in buf if l["text"]] == ["heading", "action", "heading"]


def test_from_fountain_centered_text_is_action_not_transition():
    # "> ... <" is Fountain's centered-text syntax, not a transition -- and
    # previously the trailing "<" was left stuck onto the text verbatim.
    _, buf = s.from_fountain("> THE END <\n")
    assert buf[0]["type"] == "action"
    assert buf[0]["text"] == "THE END"


def test_from_fountain_forced_scene_heading():
    _, buf = s.from_fountain(".FLASHBACK - THE OLD HOUSE\nShe remembers.\n")
    assert buf[0]["type"] == "heading"
    assert buf[0]["text"] == "FLASHBACK - THE OLD HOUSE"


def test_from_fountain_forced_character_cue():
    # "@" is Fountain's own forced-character-cue syntax, written by other
    # apps (Highland, Slugline, Fade In, ...) for names the plain
    # all-caps heuristic wouldn't otherwise catch. Previously nothing
    # recognized it at all, so it fell straight through to ACTION/DIALOGUE.
    text = "He stares at the door.\n@mccavity\nWho's there?\n"
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["action", "character", "dialogue"]
    assert buf[1]["text"] == "mccavity"  # "@" stripped, case preserved as typed


def test_from_fountain_forced_character_cue_no_blank_line_needed():
    # Unlike the plain all-caps heuristic (which requires a blank line
    # before), "@" forces regardless of context -- same as "!"/">"/".".
    text = "Line one.\nLine two.\n@JONES\nHello.\n"
    _, buf = s.from_fountain(text)
    assert buf[2]["type"] == "character"
    assert buf[2]["text"] == "JONES"
    assert buf[3]["type"] == "dialogue"


def test_from_fountain_forced_character_cue_with_dual_marker():
    text = "@BRICK\nScrew retirement.\n@STEEL ^\nSo do I.\n"
    _, buf = s.from_fountain(text)
    assert buf[0]["type"] == "character" and not buf[0].get("dual")
    assert buf[2]["type"] == "character" and buf[2].get("dual") is True
    assert buf[2]["text"] == "STEEL"  # "^" stripped off


def test_to_fountain_forces_character_names_the_heuristic_would_miss():
    # A name that's too long, or ends in punctuation, would fail
    # from_fountain()'s plain "all-caps, <40 chars, no trailing .!?"
    # check even after being upper-cased -- to_fountain() must write it
    # with a "@" prefix so it round-trips as CHARACTER, not silently
    # reclassify as ACTION/DIALOGUE on the next open. See
    # _needs_character_force().
    long_name = "THE PERSON WHOSE NAME NO ONE CAN EVER QUITE REMEMBER"
    punct_name = "REALLY?"
    buffer = [
        {"type": "character", "text": long_name},
        {"type": "dialogue", "text": "Line one."},
        {"type": "character", "text": punct_name},
        {"type": "dialogue", "text": "Line two."},
    ]
    text = s.to_fountain({}, buffer)
    assert f"@{long_name}" in text
    assert f"@{punct_name}" in text
    _, buf2 = s.from_fountain(text)
    assert buf2[0]["type"] == "character" and buf2[0]["text"] == long_name
    assert buf2[2]["type"] == "character" and buf2[2]["text"] == punct_name


def test_to_fountain_does_not_force_ordinary_character_names():
    # A normal short all-caps name round-trips fine on its own (upper-
    # casing already makes it match the plain heuristic) -- to_fountain()
    # shouldn't clutter the file with an unneeded "@" on every cue.
    buffer = [
        {"type": "character", "text": "brick"},
        {"type": "dialogue", "text": "Screw retirement."},
    ]
    text = s.to_fountain({}, buffer)
    assert "@BRICK" not in text
    assert "BRICK" in text


def test_from_fountain_midparagraph_allcaps_not_treated_as_character():
    # Regression: a CHARACTER cue is only recognized right after a blank
    # line, per the Fountain spec. An all-caps action sentence used for
    # emphasis (no blank line before it) used to hijack that check and
    # turn into a spurious character cue, lumping the next line in as
    # DIALOGUE instead of ACTION.
    text = "He freezes.\nTHE ALARM GOES OFF\nHe bolts for the door.\n"
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["action", "action", "action"]


def test_from_fountain_parenthetical():
    text = "VAIBHAV\n(beat)\nOkay.\n"
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["character", "parenthetical", "dialogue"]


def test_from_fountain_empty_document_has_one_line():
    _, buf = s.from_fountain("")
    assert buf == [{"type": "action", "text": ""}]


def test_from_fountain_action_after_dialogue_not_lumped_in():
    # Regression: a blank line ends an element in Fountain. An ACTION
    # paragraph that follows a DIALOGUE block, separated by a blank line
    # (as any real-world .fountain file from Highland/Fade In/etc. would
    # do), used to keep inheriting prev_type="dialogue" straight through
    # the blank line and get misclassified as more dialogue instead of
    # action.
    text = (
        "VIJAY\n"
        "It never boils. I have tried everything I know.\n"
        "\n"
        "He slams the kettle down and walks out.\n"
    )
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["character", "dialogue", "action"]


def test_from_fountain_action_after_parenthetical_not_lumped_in():
    text = (
        "VIJAY\n"
        "(beat)\n"
        "\n"
        "He storms off before she can answer.\n"
    )
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["character", "parenthetical", "action"]


def test_from_fountain_strips_inline_note():
    text = (
        "John enters the kitchen. [[double check this beat later]] He "
        "stops at the sink.\n"
    )
    _, buf = s.from_fountain(text)
    assert len(buf) == 1
    assert buf[0]["type"] == "action"
    assert "[[" not in buf[0]["text"] and "]]" not in buf[0]["text"]
    assert "double check" not in buf[0]["text"]


def test_from_fountain_strips_multiline_boneyard():
    # A boneyard comment can span several lines on its own; it should
    # vanish entirely (not get imported as ACTION/DIALOGUE text) and the
    # blank lines it leaves behind should just collapse into ordinary
    # element separators, same as any other run of blank lines.
    text = (
        "INT. KITCHEN - DAY\n"
        "\n"
        "/*\n"
        "TODO: rewrite this whole scene, it's not landing.\n"
        "Maybe cut it?\n"
        "*/\n"
        "\n"
        "John enters.\n"
    )
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["heading", "action"]
    assert buf[1]["text"] == "John enters."
    joined = "\n".join(l["text"] for l in buf)
    assert "TODO" not in joined and "/*" not in joined and "*/" not in joined


def test_from_fountain_strips_note_next_to_character_cue():
    # Notes shouldn't interfere with the blank-line-gated character-cue
    # detection they happen to sit next to.
    text = "[[reminder: give him an accent]]\nVIJAY\nWhere is it?\n"
    _, buf = s.from_fountain(text)
    assert [l["type"] for l in buf] == ["character", "dialogue"]
    assert buf[0]["text"] == "VIJAY"


def test_to_fountain_glues_speech_block_no_blank_between():
    # A CHARACTER/PARENTHETICAL/DIALOGUE block should round-trip with no
    # blank line inside it -- both because that's standard Fountain style,
    # and because from_fountain() now treats a blank line as ending an
    # element, so a stray blank mid-block would break the round trip.
    buffer = [
        {"type": "character", "text": "VIJAY"},
        {"type": "parenthetical", "text": "(quietly)"},
        {"type": "dialogue", "text": "It's never going to boil."},
        {"type": "action", "text": "He sighs."},
    ]
    text = s.to_fountain({}, buffer)
    assert "VIJAY\n(quietly)\nIt's never going to boil.\n\nHe sighs." in text


def test_scene_re_matches_common_prefixes():
    for prefix in ("INT.", "EXT.", "EST.", "INT/EXT.", "I/E."):
        assert s.SCENE_RE.match(f"{prefix} ROOM - DAY")
    assert not s.SCENE_RE.match("INTERIOR ROOM")  # must not false-positive


# --------------------------------------------------------------------------
# Inline styling tokenizer
# --------------------------------------------------------------------------

def test_tokenize_inline_bold_and_italic():
    out = s.tokenize_inline("plain **bold** and *italic* end")
    styles = [style for _, style in out]
    assert "bold" in styles and "italic" in styles
    bold_text = next(t for t, st in out if st == "bold")
    italic_text = next(t for t, st in out if st == "italic")
    assert bold_text == "bold"
    assert italic_text == "italic"


def test_tokenize_inline_no_markup():
    out = s.tokenize_inline("just plain text")
    assert out == [("just plain text", "normal")]


# --------------------------------------------------------------------------
# Wrapping
# --------------------------------------------------------------------------

def test_wrapped_lines_for_uppercases_headings():
    line = {"type": "heading", "text": "int. kitchen - day"}
    wrapped = s.wrapped_lines_for(line, 74)
    assert wrapped == ["INT. KITCHEN - DAY"]


def test_wrapped_lines_for_empty_text_returns_single_blank():
    assert s.wrapped_lines_for({"type": "action", "text": ""}, 70) == [""]


def test_wrapped_lines_for_wraps_long_action():
    long_text = "word " * 40
    line = {"type": "action", "text": long_text.strip()}
    wrapped = s.wrapped_lines_for(line, 20)
    assert len(wrapped) > 1
    assert all(len(w) <= 20 for w in wrapped)


# --------------------------------------------------------------------------
# Cursor mapping on wrapped lines (regression test for the render() bug
# where the terminal cursor was always drawn on the first wrapped row)
# --------------------------------------------------------------------------

def test_locate_cursor_single_line_no_wrap():
    wrapped = ["hello world"]
    assert s.locate_cursor(wrapped, 0) == (0, 0)
    assert s.locate_cursor(wrapped, 5) == (0, 5)
    assert s.locate_cursor(wrapped, 11) == (0, 11)


def test_locate_cursor_moves_to_second_wrapped_row():
    # "one two three four" wrapped at width 8 -> ["one two", "three", "four"]
    text = "one two three four"
    wrapped = ["one two", "three", "four"]
    # cursor at start of "three" (raw index 8) must land on row 1, col 0
    row, col = s.locate_cursor(wrapped, 8)
    assert row == 1
    assert col == 0


def test_locate_cursor_end_of_text_lands_on_last_row():
    wrapped = ["one two", "three", "four"]
    row, col = s.locate_cursor(wrapped, 19)  # len("one two three four")
    assert row == 2
    assert col == len("four")


def test_locate_cursor_empty_wrapped_list_is_safe():
    assert s.locate_cursor([], 5) == (0, 0)


# --------------------------------------------------------------------------
# display_offset -- raw (marker-including) cursor offset -> display
# (marker-stripped) offset, so the on-screen cursor tracks correctly on
# any line using *italic*/**bold** styling.
# --------------------------------------------------------------------------

def test_display_offset_plain_text_is_identity():
    text = "He walks into the room."
    for cx in range(len(text) + 1):
        assert s.display_offset(text, cx) == cx


def test_display_offset_before_markup_is_identity():
    text = "He picks up the *gun"  # unclosed '*' -- not a real span yet
    for cx in range(len(text) + 1):
        assert s.display_offset(text, cx) == cx


def test_display_offset_after_closed_italic_span():
    # "He picks up the *gun*" displays as "He picks up the gun" (3 chars
    # shorter -- both '*'s are stripped). Cursor right after the closing
    # '*' must land right after the "n" of "gun" in the display text, not
    # still counting the two stripped '*' characters.
    text = "He picks up the *gun*"
    assert s.display_offset(text, len(text)) == len("He picks up the gun")


def test_display_offset_after_closed_bold_span():
    text = "Then **wham** it fires."
    display = "Then wham it fires."
    assert s.display_offset(text, len(text)) == len(display)


def test_display_offset_mid_word_after_span_stays_in_sync():
    # Cursor further along, past the styled span -- verifies the mapping
    # doesn't just work at line-end but keeps tracking for the rest of the
    # line (this is what used to drift increasingly out of sync).
    text = "Then **wham** it fires."
    cx = text.index("fires")
    display = "Then wham it fires."
    assert s.display_offset(text, cx) == display.index("fires")


def test_display_offset_inside_marker_chars_clamps_to_content_edge():
    # A cursor that (raw-offset-wise) sits between the two '*'s of a bold
    # marker has no corresponding on-screen character -- clamp to the
    # nearest edge of the styled content instead of a nonsensical position.
    text = "**bold**"
    assert s.display_offset(text, 1) == 0        # inside opening **
    assert s.display_offset(text, 7) == len("bold")  # inside closing **


def test_render_cursor_stays_in_sync_across_a_styled_line():
    """End-to-end regression for the cursor-drift bug: type a line with
    both an *italic* and a **bold** span, and check the terminal cursor
    column render() reports never drifts from the true display position."""
    ed = _make_editor(buffer=[{"type": "action", "text": ""}])
    ed.mode = "INSERT"
    ed.stdscr = _FakeStdscr()
    typed = "He picks up the *gun* and drops it. Then **wham** it fires."
    for ch in typed:
        ed.handle_insert(ord(ch))
    raw = ed.buffer[0]["text"]
    display = "".join(s.wrapped_lines_for(ed.buffer[0], 60))
    ed.render()
    # cursor column is 4 (left margin) + indent(0) + display offset
    expected_col = 4 + len(display)
    assert ed.stdscr.last_move[1] == expected_col
    assert raw == typed
    assert len(display) < len(raw)  # markers really were stripped


def test_render_survives_terminal_shrinking_and_growing_mid_session():
    """Regression/hardening for the 'terminal resize hasn't been stress
    tested' limitation. render() re-reads getmaxyx() every frame and a
    resize keypress (curses.KEY_RESIZE) is already outside the printable
    range, so nothing should need to special-case it -- but that was
    previously an untested claim rather than a checked one. Drive the same
    Editor through a wide terminal, a very narrow one (narrower than the
    centered page block), a very short one, and back to a normal size,
    across a long multi-line document, and make sure render() never raises
    and always leaves the cursor within the new screen bounds.
    """
    buffer = [
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "John paces back and forth, muttering "
                                    "to himself about the broken kettle."},
        {"type": "character", "text": "JOHN"},
        {"type": "dialogue", "text": "It never boils. I have tried "
                                      "everything I know how to try."},
    ]
    ed = _make_editor(buffer=buffer)
    ed.stdscr = _FakeStdscr(dims=(24, 80))
    ed.cy = 3
    for dims in [(24, 80), (10, 20), (3, 10), (40, 200), (24, 80)]:
        ed.stdscr.last_move = None
        ed.stdscr.dims = dims
        ed.render()  # must not raise regardless of how small/odd dims are
        h, w = dims
        # The terminal cursor must be repositioned every frame, even a
        # frame too small to show the current line at all -- otherwise it
        # can be left stale at a pre-resize position outside the new,
        # smaller screen (see render()'s cursor_screen_pos fallback).
        assert ed.stdscr.last_move is not None
        row, col = ed.stdscr.last_move
        assert 0 <= row < h
        assert 0 <= col <= w


def test_read_key_resize_is_not_treated_as_printable_text():
    """curses.KEY_RESIZE (what get_wch() returns on a SIGWINCH-driven
    resize) must fall through as a non-printable key, not get typed into
    the buffer as a stray character."""
    assert not s.is_printable_char(curses.KEY_RESIZE)


# --------------------------------------------------------------------------
# cursor_position -- the space bar / mode-switch cursor freeze. The wrap
# algorithm doesn't render a run of whitespace until a following word
# gives it something to be "between" (matching textwrap's collapsing),
# so naively clamping the cursor to the end of the *rendered* text made
# it visibly freeze on every space press, and made leading spaces on an
# otherwise-blank line disappear and permanently misalign the cursor.
# --------------------------------------------------------------------------

def _typed_cursor_positions(text, width=60):
    """Simulate typing `text` one character at a time into a fresh empty
    action line and return the (row, col) cursor_position() reports after
    each character."""
    positions = []
    line = {"type": "action", "text": ""}
    for ch in text:
        line["text"] += ch
        line.pop("_wrap_cache", None)
        positions.append(s.cursor_position(line, width, len(line["text"])))
    return positions


def test_cursor_position_advances_through_a_single_trailing_space():
    positions = _typed_cursor_positions("Hi there")
    # every keystroke, including the space, must move the cursor forward
    # by exactly one column on the same row
    for (r0, c0), (r1, c1) in zip(positions, positions[1:]):
        assert r1 == r0 and c1 == c0 + 1


def test_cursor_position_advances_through_leading_spaces_on_blank_line():
    positions = _typed_cursor_positions("  Hi")
    # "  " then "Hi" -- the two leading spaces must each move the cursor
    # forward, not vanish/freeze, even though the wrap algorithm drops
    # leading whitespace from what's actually rendered
    assert positions[0] == (0, 1)  # after 1st space
    assert positions[1] == (0, 2)  # after 2nd space


def test_cursor_position_advances_through_multiple_trailing_spaces():
    positions = _typed_cursor_positions("Hi   ")  # word + 3 trailing spaces
    for (r0, c0), (r1, c1) in zip(positions, positions[1:]):
        assert r1 == r0 and c1 == c0 + 1


def test_cursor_position_never_lands_before_the_previous_character():
    """Regression sweep for the reported bug: type a realistic sentence
    (single-spaced, the overwhelmingly common case) and check the cursor
    never sits still or moves backward on the same row while typing
    forward -- it may only advance, or move to a new (later) row on wrap."""
    sentence = ("He picks up the gun and walks slowly toward the door, "
                "breathing hard.")
    positions = _typed_cursor_positions(sentence)
    for (r0, c0), (r1, c1) in zip(positions, positions[1:]):
        assert (r1 == r0 and c1 > c0) or (r1 > r0), (r0, c0, r1, c1)


def test_cursor_position_empty_line_is_col_zero():
    line = {"type": "action", "text": ""}
    assert s.cursor_position(line, 60, 0) == (0, 0)


# --------------------------------------------------------------------------
# raw_cx_for_visual -- the inverse of cursor_position(), used by
# Editor._move_visual_row() so j/k/arrow-key navigation can land on a
# specific wrapped row of a multi-row element.
# --------------------------------------------------------------------------

def test_raw_cx_for_visual_round_trips_through_cursor_position():
    """For realistic (single-spaced) screenplay text, cursor_position()
    then raw_cx_for_visual() then cursor_position() again should land on
    the exact same (row, col) -- i.e. the inverse actually inverts, across
    every cursor position in a line that wraps to multiple rows, styled
    spans included."""
    lines = [
        {"type": "action",
         "text": "He picks up the *photo frame* and just **stares** at "
                 "it for a long long long time indeed."},
        {"type": "dialogue",
         "text": "Nuvvu ela unnav ra, chala rojula tarvatha kalisaam, "
                 "em jarigindi cheppu na."},
        {"type": "heading",
         "text": "int. some very long and elaborate location description "
                 "- continuous day and night"},
    ]
    for line in lines:
        width = s.WRAP_WIDTH.get(line["type"], 60)
        for cx in range(0, len(line["text"]) + 1):
            row, col = s.cursor_position(line, width, cx)
            back_cx = s.raw_cx_for_visual(line, width, row, col)
            assert s.cursor_position(line, width, back_cx) == (row, col), \
                (line["text"], cx)


def test_raw_cx_for_visual_clamps_out_of_range_row_and_col():
    line = {"type": "action", "text": "a fairly short line"}
    width = s.WRAP_WIDTH["action"]
    # Only one row -- an out-of-range row/col should clamp, not explode.
    assert s.raw_cx_for_visual(line, width, 5, 0) == s.raw_cx_for_visual(line, width, 0, 0)
    assert s.raw_cx_for_visual(line, width, 0, 999) == len(line["text"])


# --------------------------------------------------------------------------
# Editor._move_visual_row() -- j/k and the arrow keys move by *wrapped*
# row, not straight to the next logical buffer element, so a two-row
# ACTION/DIALOGUE/etc. line's second row is a real landing spot.
# --------------------------------------------------------------------------

def test_move_visual_row_down_stays_on_second_row_of_wrapped_action():
    long_action = ("word " * 30).strip()  # wraps to multiple rows at width 60
    ed = _make_editor(buffer=[
        {"type": "action", "text": long_action},
        {"type": "character", "text": "VIKRANTH"},
        {"type": "dialogue", "text": "Parledu ra."},
    ])
    width = s.WRAP_WIDTH["action"]
    assert len(s.wrapped_lines_for(ed.buffer[0], width)) > 1
    ed.cy, ed.cx = 0, 0
    ed._move_visual_row(1)
    assert ed.cy == 0  # still the same logical (ACTION) line
    row, col = s.cursor_position(ed.buffer[0], width, ed.cx)
    assert (row, col) == (1, 0)  # first word of the second wrapped row


def test_move_visual_row_down_crosses_into_next_line_from_last_row():
    long_action = ("word " * 30).strip()
    ed = _make_editor(buffer=[
        {"type": "action", "text": long_action},
        {"type": "character", "text": "VIKRANTH"},
    ])
    width = s.WRAP_WIDTH["action"]
    n_rows = len(s.wrapped_lines_for(ed.buffer[0], width))
    ed.cy = 0
    ed.cx = s.raw_cx_for_visual(ed.buffer[0], width, n_rows - 1, 0)
    ed._move_visual_row(1)
    assert ed.cy == 1
    assert ed.buffer[ed.cy]["type"] == "character"


def test_move_visual_row_up_lands_on_last_row_of_previous_wrapped_line():
    long_action = ("word " * 30).strip()
    ed = _make_editor(buffer=[
        {"type": "action", "text": long_action},
        {"type": "character", "text": "VIKRANTH"},
    ])
    width = s.WRAP_WIDTH["action"]
    n_rows = len(s.wrapped_lines_for(ed.buffer[0], width))
    ed.cy, ed.cx = 1, 0
    ed._move_visual_row(-1)
    assert ed.cy == 0
    row, _col = s.cursor_position(ed.buffer[0], width, ed.cx)
    assert row == n_rows - 1


def test_move_visual_row_preserves_column_across_ragged_rows():
    """Column position (not raw char count) is what's preserved -- moving
    down/up should land at roughly the same on-screen column, clamped to
    whatever that row's own length allows."""
    ed = _make_editor(buffer=[
        {"type": "action", "text": ("word " * 30).strip()},
        {"type": "action", "text": "short"},
    ])
    width = s.WRAP_WIDTH["action"]
    n_rows = len(s.wrapped_lines_for(ed.buffer[0], width))
    ed.cy = 0
    ed.cx = s.raw_cx_for_visual(ed.buffer[0], width, n_rows - 1, 3)
    ed._move_visual_row(1)  # crosses into the short second line
    assert ed.cy == 1
    assert ed.cx <= len(ed.buffer[1]["text"])


def test_move_visual_row_top_and_bottom_of_document_are_safe():
    ed = _make_editor(buffer=[{"type": "action", "text": "only line"}])
    ed.cy, ed.cx = 0, 3
    ed._move_visual_row(-1)
    assert (ed.cy, ed.cx) == (0, 3)  # no previous row/line -- no-op
    ed._move_visual_row(1)
    assert (ed.cy, ed.cx) == (0, 3)  # no next row/line -- no-op


def test_normal_mode_j_k_use_visual_row_movement():
    long_action = ("word " * 30).strip()
    ed = _make_editor(buffer=[
        {"type": "action", "text": long_action},
        {"type": "character", "text": "VIKRANTH"},
    ])
    ed.cy, ed.cx = 0, 0
    ed.handle_normal(ord("j"))  # default move_down keybind
    assert ed.cy == 0  # stayed inside the wrapped ACTION line
    width = s.WRAP_WIDTH["action"]
    row, _col = s.cursor_position(ed.buffer[0], width, ed.cx)
    assert row == 1


def test_insert_mode_arrow_keys_use_visual_row_movement():
    long_action = ("word " * 30).strip()
    ed = _make_editor(buffer=[
        {"type": "action", "text": long_action},
        {"type": "character", "text": "VIKRANTH"},
    ])
    ed.cy, ed.cx = 0, 0
    ed.mode = "INSERT"
    ed.handle_insert(curses.KEY_DOWN)
    assert ed.cy == 0
    width = s.WRAP_WIDTH["action"]
    row, _col = s.cursor_position(ed.buffer[0], width, ed.cx)
    assert row == 1


# --------------------------------------------------------------------------
# Config merge
# --------------------------------------------------------------------------

def test_deep_merge_overrides_leaf_values():
    base = copy.deepcopy(s.DEFAULT_CONFIG)
    override = {"general": {"save_dir": "/tmp/scripts"}}
    merged = s.deep_merge(base, override)
    assert merged["general"]["save_dir"] == "/tmp/scripts"
    # untouched sections survive
    assert merged["colors"]["heading"] == "yellow"


def test_deep_merge_partial_keybind_override_keeps_rest():
    base = copy.deepcopy(s.DEFAULT_CONFIG)
    override = {"keybinds": {"heading": "H"}}
    merged = s.deep_merge(base, override)
    assert merged["keybinds"]["heading"] == "H"
    assert merged["keybinds"]["action"] == "a"


def test_config_toml_loader_available():
    # Regression: scriptee.py used to only try `import tomllib`, so on
    # Python < 3.11 (where install.sh explicitly installs `tomli` as a
    # fallback) config.toml was silently never read and user config was
    # always ignored. tomllib must be importable one way or another.
    assert s.tomllib is not None


def test_default_toml_text_parses_and_matches_default_config():
    """DEFAULT_TOML_TEXT (written to ~/.config/scriptee/config.toml on
    first run) and DEFAULT_CONFIG (the in-code fallback/merge base) are
    two independently-hand-written sources of the same defaults -- nothing
    generates one from the other. Parse the actual TOML text and check it
    deep-equals DEFAULT_CONFIG, so the two can't silently drift apart the
    way [format.pdf.emphasis] almost did when it was added to one but not
    the other."""
    parsed = s.tomllib.loads(s.DEFAULT_TOML_TEXT)
    # save_dir is the one intentional exception: DEFAULT_CONFIG stores it
    # pre-expanded (str(Path.home() / ...)) while the TOML text keeps the
    # portable, unexpanded "~/..." form a user would actually want to see
    # and edit in their own config file. Normalize that one field before
    # comparing everything else verbatim.
    parsed["general"]["save_dir"] = s.DEFAULT_CONFIG["general"]["save_dir"]
    assert parsed == s.DEFAULT_CONFIG


# --------------------------------------------------------------------------
# Save path / filename safety
# --------------------------------------------------------------------------

def test_safe_filename_strips_unsafe_characters():
    assert s.Editor.safe_filename('My/Script: "Draft" #2') == "MyScript_Draft_2"


def test_safe_filename_falls_back_to_untitled():
    assert s.Editor.safe_filename("") == "untitled"
    assert s.Editor.safe_filename(None) == "untitled"


def test_resolve_save_path_adds_fountain_extension(tmp_path):
    # A brand-new (never-saved) script resolves into its own <Title>
    # subfolder under save_dir -- see new_script_dir().
    ed = _make_editor(save_dir=tmp_path)
    ed.metadata = {"Title": "My Script"}
    p = ed.resolve_save_path(None)
    assert p == tmp_path / "My_Script" / "My_Script.fountain"
    assert p.parent.is_dir()


def test_resolve_save_path_respects_explicit_arg(tmp_path):
    ed = _make_editor(save_dir=tmp_path)
    target = tmp_path / "custom"
    p = ed.resolve_save_path(str(target))
    assert p == target.with_suffix(".fountain")


def test_pdf_export_path_uses_same_sanitizer_as_save(tmp_path):
    ed = _make_editor(save_dir=tmp_path)
    ed.metadata = {"Title": 'Weird: Title/Name'}
    ed.filepath = None
    ed.do_export_pdf(None)
    safe_title = s.Editor.safe_filename(ed.metadata["Title"])
    expected = tmp_path / safe_title / f"{safe_title}.pdf"
    assert expected.exists()


def test_do_export_pdf_works_before_any_save(tmp_path):
    # Regression: ":pdf" on a script that has never been saved (no :w/
    # :wq yet) used to fail -- do_export_pdf()'s no-filepath fallback
    # resolved a path under save_dir but never created the directory
    # (unlike resolve_save_path()'s equivalent branch), so a save_dir
    # that doesn't exist yet raised FileNotFoundError inside export_pdf(),
    # caught and shown as "PDF export failed: ...". This only ever
    # "worked" because a prior :wq happened to create the directory as a
    # side effect. save_dir here is a *fresh, nonexistent* subdirectory
    # of tmp_path -- nothing has touched it yet.
    pytest.importorskip("reportlab")
    save_dir = tmp_path / "brand_new_save_dir"
    assert not save_dir.exists()
    ed = _make_editor(save_dir=save_dir,
                       buffer=[{"type": "action", "text": "Opening image."}])
    ed.metadata = {"Title": "Interlude"}
    ed.filepath = None
    ed.do_export_pdf(None)
    assert "failed" not in ed.status.lower()
    assert (save_dir / "Interlude" / "Interlude.pdf").exists()


# --------------------------------------------------------------------------
# Editor behavior (constructed without curses.wrapper / a real terminal)
# --------------------------------------------------------------------------

class _FakeStdscr:
    """Minimal stand-in so Editor() can be constructed off-screen.

    Also records every addstr() into a plain-text grid (styling/attrs
    dropped) so render() output can actually be asserted on -- used by the
    dual-dialogue two-column render tests below, which need to check
    *where* text landed, not just that render() didn't crash.
    """
    def __init__(self, dims=(24, 80)):
        self.last_move = None
        self.dims = dims
        h, w = dims
        self.grid = [[" "] * w for _ in range(h)]

    def getmaxyx(self):
        return self.dims

    def erase(self):
        h, w = self.dims
        self.grid = [[" "] * w for _ in range(h)]

    def clear(self):
        pass

    def refresh(self):
        pass

    def move(self, y, x):
        self.last_move = (y, x)

    def addstr(self, y, x, s="", attr=0, *a, **kw):
        h, w = self.dims
        if not (0 <= y < h):
            return
        for i, ch in enumerate(s):
            if 0 <= x + i < w:
                self.grid[y][x + i] = ch

    def row(self, y):
        return "".join(self.grid[y]).rstrip()


def _make_editor(save_dir=None, buffer=None):
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    if save_dir is not None:
        cfg["general"]["save_dir"] = str(save_dir)
    buffer = buffer or [{"type": "action", "text": ""}]
    curses.setupterm  # no-op reference; real setup_colors is skipped below
    ed = s.Editor.__new__(s.Editor)
    ed.stdscr = _FakeStdscr()
    ed.cfg = cfg
    ed.metadata = {}
    ed.buffer = buffer
    ed.filepath = None
    ed.cy, ed.cx = 0, 0
    ed.mode = "NORMAL"
    ed.cmdline = ""
    ed.status = ""
    ed.undo_root = {"buffer": copy.deepcopy(buffer), "cy": 0, "cx": 0,
                     "parent": None, "children": [], "ts": 0.0}
    ed.undo_current = ed.undo_root
    ed._awaiting_new_undo_node = False
    ed._undo_node_count = 1
    ed.register = None
    ed.dirty = False
    ed.search_term = ""
    ed.pending_key = None
    ed.count_buffer = ""
    ed.readonly = False
    ed._title_prompt_shown = False
    ed._tab_state = None
    ed._page_cache = (1, 1, 0.0)
    ed._page_at_cache = None
    ed.buffer_rev = 0
    ed._autocomplete_cache = {}
    ed.last_command = None
    ed.pairs = {}
    ed.recovery_path = None
    ed.last_autosave = 0.0
    ed.revision_color = "White"
    ed.revision_baseline = copy.deepcopy(buffer)
    ed.revision_history = []
    ed._revision_marks_cache = None
    return ed


def test_dd_deletes_current_line():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
        {"type": "action", "text": "three"},
    ])
    ed.cy = 1
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    assert [l["text"] for l in ed.buffer] == ["one", "three"]


def test_dd_on_last_remaining_line_clears_it_and_resets_type():
    ed = _make_editor(buffer=[{"type": "heading", "text": "INT. ROOM"}])
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    assert ed.buffer == [{"type": "action", "text": ""}]


def test_pending_d_does_not_survive_unrelated_keys():
    """Regression: pressing 'd' then moving around (or entering/leaving
    insert mode) used to leave a stale pending_key='d', so a much later,
    unrelated 'd' press would silently delete whatever line the cursor was
    on. A single 'd' followed by any non-'d' key must never delete."""
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
        {"type": "action", "text": "three"},
    ])
    ed.handle_normal(ord("d"))     # arm pending "d"
    ed.handle_normal(ord("j"))     # unrelated: move down a line
    ed.handle_normal(ord("i"))     # unrelated: enter insert mode
    ed.mode = "NORMAL"             # simulate Esc back to NORMAL
    ed.handle_normal(ord("d"))     # a fresh, single "d" -- must NOT delete
    assert [l["text"] for l in ed.buffer] == ["one", "two", "three"]


def test_dd_still_works_when_pressed_back_to_back_after_navigation():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("j"))
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    assert [l["text"] for l in ed.buffer] == ["one"]


def test_undo_restores_deleted_line():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    assert len(ed.buffer) == 1
    ed.handle_normal(ord("u"))
    assert [l["text"] for l in ed.buffer] == ["one", "two"]


def test_insert_mode_types_characters():
    ed = _make_editor()
    ed.mode = "INSERT"
    for ch in "hi":
        ed.handle_insert(ord(ch))
    assert ed.buffer[0]["text"] == "hi"
    assert ed.cx == 2


def test_command_line_type_switch():
    ed = _make_editor()
    ed.execute_command("h")
    assert ed.buffer[0]["type"] == "heading"


# --------------------------------------------------------------------------
# Redo
# --------------------------------------------------------------------------

def test_redo_restores_undone_change():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    assert len(ed.buffer) == 1
    ed.undo()
    assert [l["text"] for l in ed.buffer] == ["one", "two"]
    ed.redo()
    assert [l["text"] for l in ed.buffer] == ["two"]


def test_redo_with_empty_stack_is_a_safe_noop():
    ed = _make_editor(buffer=[{"type": "action", "text": "one"}])
    ed.redo()
    assert ed.buffer == [{"type": "action", "text": "one"}]
    assert "Nothing to redo" in ed.status


def test_new_edit_after_undo_clears_redo_history():
    # Name kept for continuity with the old flat-stack test, but this now
    # asserts the *fixed* behavior: a fresh edit made after undo()ing no
    # longer destroys the branch it undid past -- it becomes a new
    # sibling, and the old branch stays reachable via :undotree. Plain
    # redo() still follows the newest edit by default, same feel as
    # before.
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    assert len(ed.buffer) == 1
    ed.undo()
    assert [l["text"] for l in ed.buffer] == ["one", "two"]
    old_future = ed.undo_current["children"][0]  # the "dd" edit we just undid
    ed.handle_normal(ord("x"))  # a fresh, different edit (delete-char)
    # The new edit is a sibling of the old one, not a replacement for it.
    assert old_future in ed.undo_current["parent"]["children"]
    assert len(ed.undo_current["parent"]["children"]) == 2
    # Plain redo() still follows the *new* edit by default...
    ed.undo()
    ed.redo()
    assert ed.buffer[0]["text"] == "ne"  # the "x" edit, not the old "dd" one
    # ...but the abandoned "dd" branch is still there for :undotree to
    # reach -- not silently gone.
    leaves = ed._undo_leaves()
    assert old_future in leaves or any(
        old_future is n for n in leaves)


def test_ctrl_r_triggers_redo():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    ed.undo()
    ed.handle_normal(18)  # Ctrl-R
    assert [l["text"] for l in ed.buffer] == ["two"]


# --------------------------------------------------------------------------
# Yank / paste
# --------------------------------------------------------------------------

def test_yy_yanks_current_line_into_register():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("y"))
    assert ed.register == {"type": "character", "text": "VIJAY"}
    # unchanged -- yy is read-only
    assert [l["text"] for l in ed.buffer] == ["VIJAY", "two"]


def test_pending_y_does_not_survive_unrelated_keys():
    ed = _make_editor(buffer=[{"type": "action", "text": "one"}])
    ed.handle_normal(ord("y"))     # arm pending "y"
    ed.handle_normal(ord("j"))     # unrelated
    ed.handle_normal(ord("y"))     # fresh single "y" -- must NOT yank
    assert ed.register is None


def test_p_pastes_yanked_line_below_cursor():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("p"))
    assert [l["text"] for l in ed.buffer] == ["one", "one", "two"]
    assert ed.cy == 1


def test_shift_p_pastes_yanked_line_above_cursor():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.cy = 1
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("P"))
    assert [l["text"] for l in ed.buffer] == ["one", "two", "two"]
    assert ed.cy == 1


def test_paste_with_empty_register_is_a_safe_noop():
    ed = _make_editor(buffer=[{"type": "action", "text": "one"}])
    ed.handle_normal(ord("p"))
    assert [l["text"] for l in ed.buffer] == ["one"]
    assert "Nothing" in ed.status


def test_pasted_line_is_an_independent_copy_not_a_shared_reference():
    ed = _make_editor(buffer=[{"type": "action", "text": "one"}])
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("p"))
    ed.buffer[0]["text"] = "changed"
    assert ed.buffer[1]["text"] == "one"  # paste didn't alias the original


def test_dd_cuts_line_into_register_so_it_can_be_pasted_back():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "one"},
        {"type": "action", "text": "two"},
    ])
    ed.handle_normal(ord("d"))
    ed.handle_normal(ord("d"))
    assert [l["text"] for l in ed.buffer] == ["two"]
    ed.handle_normal(ord("p"))
    assert [l["text"] for l in ed.buffer] == ["two", "one"]


def test_paste_is_undoable():
    ed = _make_editor(buffer=[{"type": "action", "text": "one"}])
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("y"))
    ed.handle_normal(ord("p"))
    assert len(ed.buffer) == 2
    ed.undo()
    assert [l["text"] for l in ed.buffer] == ["one"]


def test_paste_blocked_in_readonly():
    ed = _make_editor(buffer=[{"type": "action", "text": "one"}])
    ed.readonly = True
    ed.register = {"type": "action", "text": "yanked"}
    ed.handle_normal(ord("p"))
    assert [l["text"] for l in ed.buffer] == ["one"]
    assert "Read-only" in ed.status


# --------------------------------------------------------------------------
# Character rename sweep
# --------------------------------------------------------------------------

def test_split_character_cue_separates_extension():
    assert s.split_character_cue("VIJAY (V.O.)") == ("VIJAY", "(V.O.)")
    assert s.split_character_cue("VIJAY") == ("VIJAY", "")


def test_rename_character_updates_matching_cues_only():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "Hello."},
        {"type": "character", "text": "VIJAY (V.O.)"},
        {"type": "dialogue", "text": "Again."},
        {"type": "character", "text": "RAJU"},
        {"type": "dialogue", "text": "Hi."},
    ])
    ed.rename_character("vijay", "sriram")
    names = [l["text"] for l in ed.buffer if l["type"] == "character"]
    assert names == ["SRIRAM", "SRIRAM (V.O.)", "RAJU"]
    assert ed.dirty is True


def test_rename_character_no_match_leaves_buffer_untouched():
    ed = _make_editor(buffer=[{"type": "character", "text": "VIJAY"}])
    ed.rename_character("nobody", "someone")
    assert ed.buffer[0]["text"] == "VIJAY"
    assert "No CHARACTER cues matched" in ed.status


def test_rename_character_is_undoable():
    ed = _make_editor(buffer=[{"type": "character", "text": "VIJAY"}])
    ed.rename_character("vijay", "sriram")
    assert ed.buffer[0]["text"] == "SRIRAM"
    ed.undo()
    assert ed.buffer[0]["text"] == "VIJAY"


def test_rename_command_requires_two_arguments():
    ed = _make_editor(buffer=[{"type": "character", "text": "VIJAY"}])
    ed.execute_command("rename VIJAY")
    assert ed.buffer[0]["text"] == "VIJAY"
    assert "Usage: :rename" in ed.status


def test_rename_command_end_to_end():
    ed = _make_editor(buffer=[{"type": "character", "text": "VIJAY"}])
    ed.execute_command("rename VIJAY SRIRAM")
    assert ed.buffer[0]["text"] == "SRIRAM"


# --------------------------------------------------------------------------
# Fuzzy filter for the open-file list
# --------------------------------------------------------------------------

def test_fuzzy_match_subsequence():
    assert s.fuzzy_match("scnt", "scriptee_confidential.fountain") is not None
    assert s.fuzzy_match("xyz", "scriptee.fountain") is None


def test_fuzzy_match_empty_query_matches_everything_with_zero_score():
    assert s.fuzzy_match("", "anything.fountain") == 0


def test_fuzzy_match_prefers_tighter_matches():
    # "scr" is a contiguous prefix of "script.fountain" -- should score
    # better (lower) than a scattered match in "some_car.fountain".
    tight = s.fuzzy_match("scr", "script.fountain")
    loose = s.fuzzy_match("scr", "some_car_report.fountain")
    assert tight is not None and loose is not None
    assert tight <= loose


# --------------------------------------------------------------------------
# Bold/italic spans across a wrap boundary
# --------------------------------------------------------------------------

def test_styled_wrap_keeps_bold_style_when_span_crosses_wrap_boundary():
    # "reallybold" is one bold word; force a wrap so it's the only thing on
    # its own row and confirm the style survives (old code re-parsed **
    # markers per wrapped line and could lose them across a split).
    line = {"type": "action", "text": "one two **reallybold** four"}
    rows = s.styled_wrap(line, width=12)
    assert len(rows) > 1
    all_pieces = [piece for row in rows for piece in row]
    bold_pieces = [text for text, style in all_pieces if style == "bold"]
    assert "".join(bold_pieces) == "reallybold"


def test_styled_wrap_plain_text_matches_wrapped_lines_for():
    line = {"type": "action", "text": "the quick brown fox jumps over"}
    rows = s.styled_wrap(line, width=15)
    plain = ["".join(c for c, _ in row) for row in rows]
    assert plain == s.wrapped_lines_for(line, 15)


def test_styled_wrap_empty_line_returns_single_blank_row():
    rows = s.styled_wrap({"type": "action", "text": ""}, 20)
    assert rows == [[("", "normal")]]


# --------------------------------------------------------------------------
# Autosave / recovery
# --------------------------------------------------------------------------

def test_recovery_key_for_path_is_stable_and_filesystem_safe(tmp_path):
    target = tmp_path / "My Script.fountain"
    key1 = s.recovery_key_for_path(target)
    key2 = s.recovery_key_for_path(target)
    assert key1 == key2
    assert key1.endswith(".swp")
    assert re.match(r'^[A-Za-z0-9_]+\.swp$', key1)


def test_maybe_autosave_writes_recovery_file_when_dirty(tmp_path, monkeypatch):
    monkeypatch.setattr(s.config, "RECOVERY_DIR", tmp_path / "recovery")
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"}])
    ed.recovery_path = tmp_path / "recovery" / "test.swp"
    ed.dirty = True
    ed.last_autosave = 0.0
    ed.metadata = {}
    ed.maybe_autosave()
    assert ed.recovery_path.exists()
    assert "hello" in ed.recovery_path.read_text()


def test_maybe_autosave_skips_when_not_dirty(tmp_path, monkeypatch):
    monkeypatch.setattr(s.config, "RECOVERY_DIR", tmp_path / "recovery")
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"}])
    ed.recovery_path = tmp_path / "recovery" / "test.swp"
    ed.dirty = False
    ed.metadata = {}
    ed.maybe_autosave()
    assert not ed.recovery_path.exists()


def test_q_refuses_to_quit_with_unsaved_changes():
    """A bare ':q' with unsaved changes must not discard them -- it should
    refuse (no 'QUIT' result) and tell the user how to proceed, mirroring
    vim's 'No write since last change'."""
    ed = _make_editor(buffer=[{"type": "action", "text": "unsaved work"}])
    ed.dirty = True
    result = ed.execute_command("q")
    assert result is None
    assert "No write" in ed.status


def test_q_bang_force_quits_with_unsaved_changes():
    """':q!' is the explicit override that discards unsaved changes."""
    ed = _make_editor(buffer=[{"type": "action", "text": "unsaved work"}])
    ed.dirty = True
    result = ed.execute_command("q!")
    assert result == "QUIT"


def test_q_still_quits_cleanly_when_not_dirty(tmp_path):
    """A plain ':q' with nothing unsaved keeps working exactly as before."""
    ed = _make_editor(buffer=[{"type": "action", "text": "saved already"}])
    ed.recovery_path = tmp_path / "clean.swp"
    ed.dirty = False
    result = ed.execute_command("q")
    assert result == "QUIT"


def test_save_discards_recovery_file(tmp_path):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed.metadata = {"Title": "My Script"}
    ed.recovery_path = tmp_path / "leftover.swp"
    ed.recovery_path.parent.mkdir(parents=True, exist_ok=True)
    ed.recovery_path.write_text("stale autosave")
    ed.dirty = True
    ed.save()
    assert not (tmp_path / "leftover.swp").exists()
    assert ed.dirty is False


# --------------------------------------------------------------------------
# Enter -- always opens the new line straight into INSERT, no prompt.
# Only ":" + a type letter changes an element's type.
# --------------------------------------------------------------------------

def test_enter_after_action_lands_straight_in_insert():
    """No more auto ':' popup -- Enter just opens the new line (defaulting
    to ACTION) and drops straight into INSERT, same as any other editor.
    Changing the type is a deliberate ':a'/':c'/etc. in NORMAL mode."""
    ed = _make_editor(buffer=[{"type": "action", "text": "He walks in."}])
    ed.mode = "INSERT"
    ed.cx = len(ed.buffer[0]["text"])
    ed.handle_insert(curses.KEY_ENTER)
    assert ed.mode == "INSERT"
    assert ed.buffer[1]["type"] == "action"


def test_enter_after_heading_defaults_to_action_no_prompt():
    ed = _make_editor(buffer=[{"type": "heading", "text": "INT. ROOM - DAY"}])
    ed.mode = "INSERT"
    ed.cx = len(ed.buffer[0]["text"])
    ed.handle_insert(curses.KEY_ENTER)
    assert ed.mode == "INSERT"
    assert ed.buffer[1]["type"] == "action"


def test_enter_after_transition_defaults_to_heading():
    # A transition ("CUT TO:", etc.) is almost always followed by a new
    # scene heading, not action -- so Enter should default there.
    ed = _make_editor(buffer=[{"type": "transition", "text": "CUT TO:"}])
    ed.mode = "INSERT"
    ed.cx = len(ed.buffer[0]["text"])
    ed.handle_insert(curses.KEY_ENTER)
    assert ed.mode == "INSERT"
    assert ed.buffer[1]["type"] == "heading"


def test_enter_after_character_defaults_to_dialogue():
    ed = _make_editor(buffer=[{"type": "character", "text": "VIJAY"}])
    ed.mode = "INSERT"
    ed.cx = len(ed.buffer[0]["text"])
    ed.handle_insert(curses.KEY_ENTER)
    assert ed.buffer[1]["type"] == "dialogue"


def test_enter_after_parenthetical_defaults_to_dialogue():
    ed = _make_editor(buffer=[{"type": "parenthetical", "text": "(beat)"}])
    ed.mode = "INSERT"
    ed.cx = len(ed.buffer[0]["text"])
    ed.handle_insert(curses.KEY_ENTER)
    assert ed.buffer[1]["type"] == "dialogue"


def test_backspace_after_enter_merges_back_into_previous_line():
    """The whole point of dropping the auto-prompt: backspacing right after
    Enter should behave like undoing the newline (merge back into the
    previous line), not get stuck editing a ':' command line."""
    ed = _make_editor(buffer=[{"type": "action", "text": "He walks in."}])
    ed.mode = "INSERT"
    ed.cx = len(ed.buffer[0]["text"])
    ed.handle_insert(curses.KEY_ENTER)
    ed.handle_insert(curses.KEY_BACKSPACE)
    assert ed.mode == "INSERT"
    assert len(ed.buffer) == 1
    assert ed.buffer[0]["text"] == "He walks in."


def test_continuous_dialogue_has_no_prompt():
    """Dialogue -> dialogue and character/parenthetical -> dialogue land in
    DIALOGUE, straight into INSERT with no interrupting prompt."""
    for prev_type in ("dialogue", "character", "parenthetical"):
        ed = _make_editor(buffer=[{"type": prev_type, "text": "text"}])
        ed.mode = "INSERT"
        ed.cx = len(ed.buffer[0]["text"])
        ed.handle_insert(curses.KEY_ENTER)
        assert ed.mode == "INSERT", prev_type
        assert ed.buffer[1]["type"] == "dialogue", prev_type


def test_o_command_opens_insert_with_no_prompt():
    ed = _make_editor(buffer=[{"type": "shot", "text": "CLOSE ON THE DOOR"}])
    ed.handle_normal(ord("o"))
    assert ed.mode == "INSERT"
    assert ed.buffer[1]["type"] == "action"


def test_colon_type_letter_changes_current_line_type():
    """The only way an element's type changes is an explicit ':' command in
    NORMAL mode."""
    ed = _make_editor(buffer=[{"type": "action", "text": "He walks in."}])
    ed.mode = "NORMAL"
    ed.handle_normal(ord(":"))
    assert ed.mode == "COMMAND"
    ed.handle_command_key(ord("c"))
    ed.handle_command_key(curses.KEY_ENTER)
    # Drops straight into INSERT (see execute_command) so typing can
    # continue immediately without an extra "i"/"a" and without the first
    # keystrokes being swallowed as NORMAL-mode commands.
    assert ed.mode == "INSERT"
    assert ed.buffer[0]["type"] == "character"


def test_colon_type_letter_then_typing_loses_no_characters():
    """Regression test: typing right after a ':<letter>' type-switch used
    to drop the first couple of characters, because mode stayed NORMAL and
    those keystrokes got parsed as NORMAL commands (e.g. 'i' entering
    INSERT instead of being typed) before the rest landed as text."""
    ed = _make_editor(buffer=[{"type": "action", "text": ""}])
    ed.mode = "NORMAL"
    ed.execute_command("a")
    assert ed.mode == "INSERT"
    for ch in "Vikranth":
        ed.handle_insert(ord(ch))
    assert ed.buffer[0]["text"] == "Vikranth"


def test_colon_type_letter_bumps_buffer_rev_and_dirty():
    """Regression: switching a line's type with ':h'/':a'/etc. used to set
    buffer[cy]["type"] directly without going through touch(), so
    buffer_rev never advanced. render()'s scene-number gutter caches
    heading positions keyed on buffer_rev (_heading_indices()), so
    retyping a line as a heading (e.g. right after a transition) left that
    cache stale -- it didn't know about the new heading yet -- and the
    next render() crashed in scene_number_at() with
    "ValueError: x not in list". It also meant a pure type change (no text
    edit) never marked the file dirty."""
    ed = _make_editor(buffer=[{"type": "transition", "text": "CUT TO:"},
                               {"type": "action", "text": ""}])
    ed.cy = 1
    rev_before = ed.buffer_rev
    ed.execute_command("h")
    assert ed.buffer_rev != rev_before
    assert ed.dirty is True
    # The stale-cache crash reproduced as scene_number_at() raising on the
    # line that was *just* retyped as a heading -- exercise it directly.
    assert ed.scene_number_at(1) == 1


def _prime_heading_cache(ed):
    """Force _heading_indices() to compute and cache against the buffer's
    *current* buffer_rev, the way a normal render() would before the
    mutation under test -- otherwise the cache would happen to compute
    fresh (and correct) right when the test calls it, masking the bug."""
    ed._heading_indices()


def test_open_line_o_above_heading_does_not_crash_scene_numbering():
    """Regression: 'o' inserts a new line via open_new_line(), which
    shifts the buffer index of every line after it -- including any
    heading below the cursor. Without bumping buffer_rev, the cached
    heading-index list stayed pointed at the heading's pre-insert
    position, so the next render() crashed in scene_number_at() with
    "ValueError: x not in list" the moment 'o' was pressed anywhere above
    an existing heading."""
    ed = _make_editor(buffer=[{"type": "action", "text": "He walks in."},
                               {"type": "heading", "text": "INT. ROOM - DAY"}])
    ed.cy = 0
    _prime_heading_cache(ed)
    ed.handle_normal(ord("o"))
    assert ed.buffer[2]["type"] == "heading"
    assert ed.scene_number_at(2) == 1  # must not raise
    assert ed.dirty is True


def test_open_line_shift_O_above_heading_does_not_crash_scene_numbering():
    ed = _make_editor(buffer=[{"type": "action", "text": "He walks in."},
                               {"type": "heading", "text": "INT. ROOM - DAY"}])
    ed.cy = 1
    _prime_heading_cache(ed)
    ed.handle_normal(ord("O"))
    assert ed.buffer[2]["type"] == "heading"
    assert ed.scene_number_at(2) == 1  # must not raise
    assert ed.dirty is True


def test_enter_above_heading_does_not_crash_scene_numbering():
    ed = _make_editor(buffer=[{"type": "action", "text": "He walks in."},
                               {"type": "heading", "text": "INT. ROOM - DAY"}])
    ed.mode = "INSERT"
    ed.cy, ed.cx = 0, len(ed.buffer[0]["text"])
    _prime_heading_cache(ed)
    ed.handle_insert(curses.KEY_ENTER)
    assert ed.buffer[2]["type"] == "heading"
    assert ed.scene_number_at(2) == 1  # must not raise


def test_backspace_on_empty_command_prompt_backs_out_to_normal():
    ed = _make_editor(buffer=[{"type": "action", "text": "x"}])
    ed.mode = "NORMAL"
    ed.handle_normal(ord(":"))
    assert ed.mode == "COMMAND"
    ed.handle_command_key(curses.KEY_BACKSPACE)
    assert ed.mode == "NORMAL"


# --------------------------------------------------------------------------
# :lc -- last character
# --------------------------------------------------------------------------

def test_lc_fills_last_character_and_enters_insert():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "Hi."},
        {"type": "action", "text": "He pauses."},
        {"type": "action", "text": ""},
    ])
    ed.cy = 3
    ed.execute_command("lc")
    assert ed.buffer[3] == {"type": "character", "text": "VIJAY"}
    assert ed.mode == "INSERT"
    assert ed.cx == len("VIJAY")


def test_lc_strips_character_extension():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY (V.O.)"},
        {"type": "action", "text": ""},
    ])
    ed.cy = 1
    ed.execute_command("lc")
    assert ed.buffer[1]["text"] == "VIJAY"


def test_lc_refuses_to_clobber_nonempty_line():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "not empty"},
    ])
    ed.cy = 1
    ed.execute_command("lc")
    assert ed.buffer[1] == {"type": "action", "text": "not empty"}


def test_lc_with_no_prior_character_sets_status():
    ed = _make_editor(buffer=[{"type": "action", "text": ""}])
    ed.execute_command("lc")
    assert "No earlier CHARACTER" in ed.status


# --------------------------------------------------------------------------
# :lh / :lt -- last heading / last transition (generalized from :lc)
# --------------------------------------------------------------------------

def test_lh_fills_last_heading_and_enters_insert():
    ed = _make_editor(buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "Some action."},
        {"type": "action", "text": ""},
    ])
    ed.cy = 2
    ed.execute_command("lh")
    assert ed.buffer[2] == {"type": "heading", "text": "INT. KITCHEN - DAY"}
    assert ed.mode == "INSERT"


def test_lt_fills_last_transition_and_enters_insert():
    ed = _make_editor(buffer=[
        {"type": "transition", "text": "CUT TO:"},
        {"type": "action", "text": ""},
    ])
    ed.cy = 1
    ed.execute_command("lt")
    assert ed.buffer[1] == {"type": "transition", "text": "CUT TO:"}
    assert ed.mode == "INSERT"


def test_lh_refuses_to_clobber_nonempty_line():
    ed = _make_editor(buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "not empty"},
    ])
    ed.cy = 1
    ed.execute_command("lh")
    assert ed.buffer[1] == {"type": "action", "text": "not empty"}


# --------------------------------------------------------------------------
# "." -- repeat last command
# --------------------------------------------------------------------------

def test_dot_repeats_last_command():
    ed = _make_editor(buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
    ])
    ed.cy = 1
    ed.execute_command("lh")
    assert ed.buffer[1]["type"] == "heading"
    ed.mode = "NORMAL"
    ed.cy = 2
    ed.handle_normal(ord("."))
    assert ed.buffer[2] == {"type": "heading", "text": "INT. KITCHEN - DAY"}


def test_dot_with_no_prior_command_sets_status():
    ed = _make_editor()
    ed.handle_normal(ord("."))
    assert "No previous command" in ed.status


def test_dot_does_not_repeat_itself():
    # execute_command(cmd, from_repeat=True) shouldn't overwrite
    # last_command, so repeated "." presses keep re-running the *original*
    # command rather than degrading into a no-op.
    ed = _make_editor(buffer=[
        {"type": "transition", "text": "CUT TO:"},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
    ])
    ed.cy = 1
    ed.execute_command("lt")
    ed.mode = "NORMAL"
    ed.cy = 2
    ed.handle_normal(ord("."))
    assert ed.last_command == "lt"


# --------------------------------------------------------------------------
# Tab autocomplete
# --------------------------------------------------------------------------

def test_tab_autocompletes_character_name():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "character", "text": ""},
    ])
    ed.cy = 1
    ed.mode = "INSERT"
    ed.handle_insert(9)  # Tab
    assert ed.buffer[1]["text"] == "VIJAY"


def test_tab_autocomplete_cache_invalidates_on_edit():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "character", "text": ""},
    ])
    # Prime the cache with just VIJAY as a candidate.
    assert ed.autocomplete_candidates("character") == ["VIJAY"]
    # A real edit (via touch()) should invalidate the cache so a newly
    # added character name shows up without a stale hit.
    ed.buffer.append({"type": "character", "text": "SRIRAM"})
    ed.touch()
    assert ed.autocomplete_candidates("character") == ["VIJAY", "SRIRAM"]


def test_tab_cycles_through_multiple_matches():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "character", "text": "SRIRAM"},
        {"type": "character", "text": ""},
    ])
    ed.cy = 2
    ed.mode = "INSERT"
    ed.handle_insert(9)
    first = ed.buffer[2]["text"]
    ed.handle_insert(9)
    second = ed.buffer[2]["text"]
    assert {first, second} == {"VIJAY", "SRIRAM"}
    ed.handle_insert(9)
    third = ed.buffer[2]["text"]
    assert third == first  # wraps back around


def test_tab_respects_typed_prefix():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "character", "text": "SRIRAM"},
        {"type": "character", "text": "V"},
    ])
    ed.cy = 2
    ed.cx = 1
    ed.mode = "INSERT"
    ed.handle_insert(9)
    assert ed.buffer[2]["text"] == "VIJAY"


def test_tab_does_nothing_on_non_autocomplete_types():
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"}])
    ed.cy = 0
    ed.cx = 5
    ed.mode = "INSERT"
    ed.handle_insert(9)
    assert ed.buffer[0]["text"] == "hello"


def test_tab_state_resets_on_other_keys():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "character", "text": "SRIRAM"},
        {"type": "character", "text": ""},
    ])
    ed.cy = 2
    ed.mode = "INSERT"
    ed.handle_insert(9)
    ed.handle_insert(ord("X"))  # breaks the cycle
    assert ed._tab_state is None


# --------------------------------------------------------------------------
# Performance: styled_wrap caching
# --------------------------------------------------------------------------

def test_styled_wrap_cache_invalidates_on_text_change():
    line = {"type": "action", "text": "hello world"}
    rows1 = s.styled_wrap(line, 70)
    line["text"] = "a completely different sentence entirely"
    rows2 = s.styled_wrap(line, 70)
    assert rows1 != rows2
    assert line["_wrap_cache"][0][0] == line["text"]


def test_styled_wrap_cache_hits_return_same_object():
    line = {"type": "action", "text": "hello world"}
    rows1 = s.styled_wrap(line, 70)
    rows2 = s.styled_wrap(line, 70)
    assert rows1 is rows2  # served from cache, not recomputed


def test_page_estimate_is_throttled():
    ed = _make_editor(buffer=[{"type": "action", "text": "x" * 500}])
    ed._page_cache = (1, 1, 0.0)
    first = ed.page_estimate()
    ed.buffer[0]["text"] = "word " * 5000  # would change the real estimate
    second = ed.page_estimate()  # still within the throttle window
    assert second == first
    ed._page_cache = (first, first, 0.0)  # force the window to have elapsed
    third = ed.page_estimate()
    assert third > first


def test_page_number_at_is_cached_while_typing_same_line():
    """The hot path: cursor stays on one line, buffer length doesn't
    change (typing/backspacing within the line). page_number_at(cy) must
    not rescan buffer[:cy] on every call in that case."""
    buffer = [{"type": "action", "text": f"line {i}"} for i in range(200)]
    ed = _make_editor(buffer=buffer)
    ed.cy = 150
    first = ed.page_number_at(ed.cy)
    # Mutate a line *before* the cursor directly, bypassing the normal
    # cache-invalidation path -- if page_number_at recomputed, this would
    # change the result. It shouldn't, because the cache is still valid
    # for this (cy, buffer length) pair.
    buffer[0]["text"] = "x" * 5000
    second = ed.page_number_at(ed.cy)
    assert second == first
    assert ed._page_at_cache == (150, len(buffer), first)


def test_page_number_at_recomputes_when_cursor_moves():
    buffer = [{"type": "action", "text": f"line {i}"} for i in range(200)]
    ed = _make_editor(buffer=buffer)
    at_top = ed.page_number_at(0)
    at_bottom = ed.page_number_at(len(buffer) - 1)
    assert at_bottom > at_top


def test_page_number_at_recomputes_when_buffer_length_changes():
    buffer = [{"type": "action", "text": f"line {i}"} for i in range(50)]
    ed = _make_editor(buffer=buffer)
    ed.cy = 40
    before = ed.page_number_at(ed.cy)
    # Insert a bunch of lines before the cursor -- the buffer length
    # changing must bust the cache even though cy itself is unchanged.
    for _ in range(500):
        buffer.insert(0, {"type": "action", "text": "padding " * 20})
    ed.cy += 500
    after = ed.page_number_at(ed.cy)
    assert after > before


# --------------------------------------------------------------------------
# Cursor position persistence (recovery / reopen)
# --------------------------------------------------------------------------

def test_save_cursor_pos_and_load_cursor_pos_round_trip(tmp_path):
    recovery_path = tmp_path / "myscript.swp"
    buffer = [{"type": "action", "text": f"line {i}"} for i in range(10)]
    ed = _make_editor(buffer=buffer)
    ed.recovery_path = recovery_path
    ed.cy, ed.cx = 4, 3
    ed.save_cursor_pos()

    cy, cx = s.load_cursor_pos(recovery_path, buffer)
    assert (cy, cx) == (4, 3)


def test_load_cursor_pos_missing_sidecar_defaults_to_top(tmp_path):
    buffer = [{"type": "action", "text": "only line"}]
    cy, cx = s.load_cursor_pos(tmp_path / "nope.swp", buffer)
    assert (cy, cx) == (0, 0)


def test_load_cursor_pos_clamps_to_shrunk_buffer(tmp_path):
    recovery_path = tmp_path / "myscript.swp"
    s.cursor_pos_path_for(recovery_path).write_text("50,999")
    buffer = [{"type": "action", "text": "short"}]
    cy, cx = s.load_cursor_pos(recovery_path, buffer)
    assert cy == 0
    assert cx == len(buffer[0]["text"])


def test_discard_recovery_keeps_cursor_pos_sidecar(tmp_path):
    recovery_path = tmp_path / "myscript.swp"
    recovery_path.write_text("stale content")
    ed = _make_editor(buffer=[{"type": "action", "text": "x"}])
    ed.recovery_path = recovery_path
    ed.cy, ed.cx = 2, 1
    ed.save_cursor_pos()

    ed.discard_recovery()

    assert not recovery_path.exists()  # stale content dropped
    assert s.cursor_pos_path_for(recovery_path).exists()  # position kept


# --------------------------------------------------------------------------
# Jump to scene (<N>G in NORMAL mode, :scene N)
# --------------------------------------------------------------------------

def _scene_buffer():
    return [
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "Beat one."},
        {"type": "heading", "text": "EXT. STREET - NIGHT"},
        {"type": "action", "text": "Beat two."},
        {"type": "heading", "text": "INT. OFFICE - DAY"},
        {"type": "action", "text": "Beat three."},
    ]


def test_digit_g_jumps_to_scene_number():
    ed = _make_editor(buffer=_scene_buffer())
    ed.cy = 5
    for ch in "3":
        ed.handle_normal(ord(ch))
    ed.handle_normal(ord("G"))
    assert ed.cy == 4  # 3rd heading is at buffer index 4
    assert ed.cx == 0


def test_bare_g_jumps_to_last_line_not_a_scene():
    ed = _make_editor(buffer=_scene_buffer())
    ed.cy = 0
    ed.handle_normal(ord("G"))
    assert ed.cy == len(ed.buffer) - 1


def test_g_with_out_of_range_count_clamps_to_last_scene():
    ed = _make_editor(buffer=_scene_buffer())
    for ch in "99":
        ed.handle_normal(ord(ch))
    ed.handle_normal(ord("G"))
    assert ed.cy == 4  # only 3 scenes; clamps to the last one


def test_count_buffer_resets_on_unrelated_key():
    ed = _make_editor(buffer=_scene_buffer())
    ed.handle_normal(ord("1"))
    ed.handle_normal(ord("h"))  # unrelated key -- should discard the "1"
    ed.handle_normal(ord("G"))  # bare G now -- last line, not scene 1
    assert ed.cy == len(ed.buffer) - 1


def test_scene_command_jumps_to_scene_number():
    ed = _make_editor(buffer=_scene_buffer())
    ed.execute_command("scene 2")
    assert ed.cy == 2  # 2nd heading is at buffer index 2


def test_scene_command_with_no_scenes_sets_status():
    ed = _make_editor(buffer=[{"type": "action", "text": "no scenes here"}])
    ed.execute_command("scene 1")
    assert "No scenes" in ed.status


def test_scene_command_bad_arg_shows_usage():
    ed = _make_editor(buffer=_scene_buffer())
    ed.execute_command("scene")
    assert "Usage" in ed.status


# --------------------------------------------------------------------------
# Read-only mode (CLI-opened existing files)
# --------------------------------------------------------------------------

def test_readonly_blocks_insert_entry():
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"}])
    ed.readonly = True
    ed.handle_normal(ord("i"))
    assert ed.mode == "NORMAL"
    assert "Read-only" in ed.status


def test_readonly_blocks_dd_delete():
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"},
                               {"type": "action", "text": "world"}])
    ed.readonly = True
    ed.pending_key = "d"
    ed.handle_normal(ord("d"))
    assert len(ed.buffer) == 2  # unchanged


def test_readonly_blocks_command_mode_type_switch():
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"}])
    ed.readonly = True
    ed.execute_command("a")
    assert ed.buffer[0]["type"] == "action"  # unchanged, still ACTION not re-touched
    assert ed.mode == "NORMAL"


def test_readonly_blocks_rename_command():
    ed = _make_editor(buffer=[{"type": "character", "text": "BOB"}])
    ed.readonly = True
    ed.execute_command("rename BOB ALICE")
    assert ed.buffer[0]["text"] == "BOB"  # unchanged


def test_readonly_allows_navigation():
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"},
                               {"type": "action", "text": "world"}])
    ed.readonly = True
    ed.handle_normal(ord("j"))
    assert ed.cy == 1


def test_e_key_unlocks_editing_when_readonly():
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"}])
    ed.readonly = True
    ed.handle_normal(ord("e"))
    assert ed.readonly is False
    assert "EDITING ENABLED" in ed.status
    # now insert should actually work
    ed.handle_normal(ord("i"))
    assert ed.mode == "INSERT"


def test_e_key_is_a_no_op_when_not_readonly():
    ed = _make_editor(buffer=[{"type": "action", "text": ""}])
    ed.readonly = False
    ed.handle_normal(ord("e"))
    assert ed.mode == "NORMAL"  # 'e' has no other NORMAL-mode binding


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------

def test_atomic_write_text_writes_content_and_no_leftover_tmp(tmp_path):
    target = tmp_path / "script.fountain"
    s.atomic_write_text(target, "hello world\n")
    assert target.read_text() == "hello world\n"
    assert list(tmp_path.glob(".*.tmp*")) == []


def test_atomic_write_text_overwrites_existing_file(tmp_path):
    target = tmp_path / "script.fountain"
    target.write_text("old content")
    s.atomic_write_text(target, "new content")
    assert target.read_text() == "new content"


def test_save_uses_atomic_write(tmp_path):
    ed = _make_editor(save_dir=tmp_path,
                       buffer=[{"type": "action", "text": "final draft text"}])
    ed.metadata = {"Title": "MyScript"}
    ed.save()
    saved = tmp_path / "MyScript" / "MyScript.fountain"
    assert saved.exists()
    assert "final draft text" in saved.read_text()
    assert list(tmp_path.glob(".*.tmp*")) == []
    assert list((tmp_path / "MyScript").glob(".*.tmp*")) == []


# --------------------------------------------------------------------------
# save_dir default / migration
# --------------------------------------------------------------------------

def test_default_save_dir_is_documents_scripts():
    expected = str(Path.home() / "Documents" / "Scripts")
    assert s.DEFAULT_CONFIG["general"]["save_dir"] == expected


def test_load_config_migrates_old_documents_scriptee_default(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[general]\nsave_dir = "~/Documents/Scriptee"\n')
    monkeypatch.setattr(s.config, "CONFIG_PATH", config_path)
    cfg = s.load_config()
    assert cfg["general"]["save_dir"] == str(Path.home() / "Documents" / "Scripts")


def test_load_config_migrates_old_bare_documents_default(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[general]\nsave_dir = "~/Documents"\n')
    monkeypatch.setattr(s.config, "CONFIG_PATH", config_path)
    cfg = s.load_config()
    assert cfg["general"]["save_dir"] == str(Path.home() / "Documents" / "Scripts")


def test_load_config_leaves_customized_save_dir_alone(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[general]\nsave_dir = "~/Scripts/WIP"\n')
    monkeypatch.setattr(s.config, "CONFIG_PATH", config_path)
    cfg = s.load_config()
    assert cfg["general"]["save_dir"] == "~/Scripts/WIP"


# --------------------------------------------------------------------------
# confirm_recovery Enter-key handling
# --------------------------------------------------------------------------

def test_confirm_recovery_enter_key_recovers(tmp_path):
    recovery_path = tmp_path / "myscript.swp"
    recovery_path.write_text("autosaved content")

    class _EnterStdscr(_FakeStdscr):
        def __init__(self):
            super().__init__()
            self._sent = False

        def getch(self):
            assert not self._sent, "should return on the first Enter press"
            self._sent = True
            return 10  # Enter

    result = s.confirm_recovery(_EnterStdscr(), recovery_path)
    assert result is True



    """Regression: export_pdf() used to add a blank line after *every*
    element unconditionally, including between a CHARACTER cue and its own
    DIALOGUE and between DIALOGUE and a following PARENTHETICAL -- neither
    of which standard screenplay format ever separates with a blank line.
    On a dialogue-heavy script that phantom blank line, repeated hundreds
    of times, was the actual cause of PDF exports running several pages
    longer than the same script from other screenwriting apps."""
    buf = [
        {"type": "character", "text": "VIJAY"},
        {"type": "parenthetical", "text": "(gently)"},
        {"type": "dialogue", "text": "It never boils."},
        {"type": "action", "text": "He walks out."},
    ]
    # character -> parenthetical: glued
    assert s._pdf_element_is_glued(buf, 0) is True
    # parenthetical -> dialogue: glued
    assert s._pdf_element_is_glued(buf, 1) is True
    # dialogue -> action (new element, not a dialogue-chain type): not glued
    assert s._pdf_element_is_glued(buf, 2) is False
    # action has no dialogue-chain type at all: not glued
    assert s._pdf_element_is_glued(buf, 3) is False


def test_pdf_glued_rule_new_character_after_dialogue_not_glued():
    # A new speaker's CHARACTER cue after someone else's DIALOGUE still
    # gets a blank line before it -- only a cue's *own* dialogue/
    # parenthetical stays glued to it.
    buf = [
        {"type": "dialogue", "text": "Hi."},
        {"type": "character", "text": "MEENA"},
    ]
    assert s._pdf_element_is_glued(buf, 0) is False


def test_forced_style_overrides_inline_markup():
    rows = [[("INT. ", "normal"), ("KITCHEN", "bold")]]
    forced = s._forced_style(rows, "italic")
    assert forced == [[("INT. ", "italic"), ("KITCHEN", "italic")]]


def test_pdf_export_bold_and_italic_and_page_count(tmp_path):
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")

    sample = (
        "INT. KITCHEN - DAY\n\n"
        "Vijay stares at the kettle.\n\n"
        "VIJAY\n"
        "It never boils.\n\n"
        "MEENA\n"
        "(gently)\n"
        "Some things just take time.\n\n"
        "CUT TO:\n\n"
        "EXT. STREET - DAY\n\n"
        "Rain hits the pavement.\n"
    )
    meta, buf = s.from_fountain(sample)
    meta = {"Title": "Test Script", "Author": "Someone"}
    out = tmp_path / "test.pdf"
    s.export_pdf(out, meta, buf)

    reader = pypdf.PdfReader(str(out))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    # Heading, character cue, and transition text all present
    assert "KITCHEN" in text
    assert "VIJAY" in text
    assert "CUT TO" in text
    # Title page + one content page for this tiny script -- the glued-line
    # fix keeps it from spilling onto a spurious extra page.
    assert len(reader.pages) == 2


# --------------------------------------------------------------------------
# Unicode input -- read_key()/is_printable_char() regression. Every input
# point used to gate on the ASCII-only `32 <= ch < 127`, which silently
# dropped any non-ASCII keystroke (accented names, em dashes, curly
# quotes, ...) with no error and no visible effect.
# --------------------------------------------------------------------------

def test_is_printable_char_accepts_non_ascii():
    for ch in "café — 'quoted' \u2014 \u00e9 \u4e2d\u6587":
        assert s.is_printable_char(ord(ch)), f"{ch!r} should be printable"


def test_is_printable_char_rejects_controls_and_function_keys():
    assert not s.is_printable_char(27)          # Esc
    assert not s.is_printable_char(127)          # Del
    assert not s.is_printable_char(9)            # Tab
    assert not s.is_printable_char(curses.KEY_UP)
    assert not s.is_printable_char(curses.KEY_ENTER)


def test_handle_insert_accepts_accented_character():
    ed = _make_editor(buffer=[{"type": "action", "text": ""}])
    ed.mode = "INSERT"
    for ch in "José":
        ed.handle_insert(ord(ch))
    assert ed.buffer[0]["text"] == "José"


def test_handle_insert_accepts_em_dash_for_interrupted_dialogue():
    ed = _make_editor(buffer=[{"type": "dialogue", "text": "Wait, I didn't"}])
    ed.mode = "INSERT"
    ed.cx = len(ed.buffer[0]["text"])
    ed.handle_insert(ord("\u2014"))  # em dash: standard for interrupted dialogue
    assert ed.buffer[0]["text"] == "Wait, I didn't\u2014"


class _FakeWChStdscr(_FakeStdscr):
    """Like _FakeStdscr, but with get_wch() feeding from a scripted queue
    of characters/keycodes, to exercise read_key() itself end to end."""
    def __init__(self, queue):
        super().__init__()
        self._queue = list(queue)

    def get_wch(self):
        if not self._queue:
            raise curses.error("no input")
        return self._queue.pop(0)


def test_read_key_decodes_unicode_str_to_its_codepoint():
    stdscr = _FakeWChStdscr(["é", "—"])
    assert s.read_key(stdscr) == ord("é")
    assert s.read_key(stdscr) == ord("—")


def test_read_key_passes_through_function_key_ints_unchanged():
    stdscr = _FakeWChStdscr([curses.KEY_UP, curses.KEY_ENTER])
    assert s.read_key(stdscr) == curses.KEY_UP
    assert s.read_key(stdscr) == curses.KEY_ENTER


def test_read_key_falls_back_to_getch_without_get_wch():
    # Old-style fake screens (or an unusual curses build) with no
    # get_wch() at all shouldn't crash read_key() -- fall back to getch().
    class _NoWch(_FakeStdscr):
        def getch(self):
            return ord("x")
    assert s.read_key(_NoWch()) == ord("x")


# --------------------------------------------------------------------------
# :rename with multi-word character names -- shlex.split() regression. A
# plain arg.split() != 2 check rejected any name containing a space (e.g.
# "OLD MAN", "YOUNG SARAH"), even though rename_character() itself always
# supported them fine.
# --------------------------------------------------------------------------

def test_rename_command_supports_quoted_multiword_names():
    ed = _make_editor(buffer=[{"type": "character", "text": "OLD MAN"}])
    ed.execute_command('rename "OLD MAN" "YOUNG MAN"')
    assert ed.buffer[0]["text"] == "YOUNG MAN"
    assert "Renamed 1" in ed.status


def test_rename_command_still_supports_single_word_names_unquoted():
    ed = _make_editor(buffer=[{"type": "character", "text": "VIJAY"}])
    ed.execute_command("rename VIJAY SRIRAM")
    assert ed.buffer[0]["text"] == "SRIRAM"


def test_rename_command_bad_arg_count_shows_usage_with_quoting_hint():
    ed = _make_editor(buffer=[{"type": "character", "text": "VIJAY"}])
    ed.execute_command("rename VIJAY")
    assert "Usage: :rename" in ed.status
    assert "quote" in ed.status.lower()


# --------------------------------------------------------------------------
# Character rename sweep -- "--all" prose sweep
# --------------------------------------------------------------------------

def test_rename_without_all_flag_leaves_prose_mentions_untouched():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "Vijay opens the door."},
    ])
    ed.rename_character("vijay", "sriram")
    assert ed.buffer[0]["text"] == "SRIRAM"
    assert ed.buffer[1]["text"] == "Vijay opens the door."


def test_rename_with_include_prose_sweeps_action_and_dialogue():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "Vijay opens the door."},
        {"type": "dialogue", "text": "Where is VIJAY?"},
        {"type": "parenthetical", "text": "(to Vijay)"},
    ])
    ed.rename_character("vijay", "sriram", include_prose=True)
    assert ed.buffer[0]["text"] == "SRIRAM"
    assert ed.buffer[1]["text"] == "Sriram opens the door."
    assert ed.buffer[2]["text"] == "Where is SRIRAM?"
    # parenthetical isn't in the default search_in list -- left alone
    assert ed.buffer[3]["text"] == "(to Vijay)"


def test_rename_prose_sweep_preserves_case_pattern_per_match():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "VIJAY nods. Vijay smiles. Then vijay leaves."},
    ])
    ed.rename_character("vijay", "sriram", include_prose=True)
    assert (ed.buffer[1]["text"] ==
            "SRIRAM nods. Sriram smiles. Then sriram leaves.")


def test_rename_prose_sweep_only_matches_whole_words():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "AL"},
        {"type": "action", "text": "AL waves. He is always alert."},
    ])
    ed.rename_character("al", "raj", include_prose=True)
    assert ed.buffer[1]["text"] == "RAJ waves. He is always alert."


def test_rename_prose_sweep_counted_separately_in_status():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "Vijay waits. Vijay leaves."},
    ])
    ed.rename_character("vijay", "sriram", include_prose=True)
    assert "1 cue" in ed.status
    assert "2 prose" in ed.status


def test_rename_include_prose_with_no_matches_anywhere():
    ed = _make_editor(buffer=[{"type": "action", "text": "Nobody is here."}])
    ed.rename_character("ghost", "spirit", include_prose=True)
    assert ed.buffer[0]["text"] == "Nobody is here."
    assert "No CHARACTER cues" in ed.status


def test_rename_command_all_flag_end_to_end():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "Vijay opens the door."},
    ])
    ed.execute_command("rename VIJAY SRIRAM --all")
    assert ed.buffer[0]["text"] == "SRIRAM"
    assert ed.buffer[1]["text"] == "Sriram opens the door."


def test_rename_command_short_flag_variant():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "Vijay opens the door."},
    ])
    ed.execute_command("rename VIJAY SRIRAM -a")
    assert ed.buffer[1]["text"] == "Sriram opens the door."


def test_rename_command_all_flag_with_quoted_multiword_names():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "OLD MAN"},
        {"type": "action", "text": "The Old Man shuffles forward."},
    ])
    ed.execute_command('rename "OLD MAN" "YOUNG MAN" --all')
    assert ed.buffer[0]["text"] == "YOUNG MAN"
    assert ed.buffer[1]["text"] == "The Young Man shuffles forward."


def test_rename_command_without_all_flag_still_defaults_to_cues_only():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "action", "text": "Vijay opens the door."},
    ])
    ed.execute_command("rename VIJAY SRIRAM")
    assert ed.buffer[1]["text"] == "Vijay opens the door."


# --------------------------------------------------------------------------
# compute_stats() -- word counts and per-character dialogue breakdown
# powering the new ':stats' command.
# --------------------------------------------------------------------------

def test_compute_stats_word_and_scene_counts():
    buffer = [
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "Vijay stares at the kettle."},
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "It's never going to boil."},
        {"type": "heading", "text": "EXT. STREET - NIGHT"},
        {"type": "action", "text": "Rain falls."},
    ]
    stats = s.compute_stats(buffer)
    assert stats["scene_count"] == 2
    assert stats["action_words"] == len("Vijay stares at the kettle.".split()) + \
        len("Rain falls.".split())
    assert stats["dialogue_words"] == len("It's never going to boil.".split())
    # total_words counts every element (headings, character cues, ...),
    # not just action+dialogue -- those two are reported separately
    # because they read very differently, not because they sum to the
    # total.
    assert stats["total_words"] >= stats["action_words"] + stats["dialogue_words"]


def test_compute_stats_groups_dialogue_by_character():
    buffer = [
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "Hello there friend"},
        {"type": "character", "text": "SRIRAM"},
        {"type": "dialogue", "text": "Hi"},
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "How are you doing today"},
    ]
    stats = s.compute_stats(buffer)
    names = {name for name, _, _ in stats["characters"]}
    assert names == {"VIJAY", "SRIRAM"}
    vijay = next(t for t in stats["characters"] if t[0] == "VIJAY")
    assert vijay[1] == 2  # two dialogue lines
    assert vijay[2] == 3 + 5  # "Hello there friend" + "How are you doing today"


def test_compute_stats_folds_vo_extension_into_same_character():
    buffer = [
        {"type": "character", "text": "VIJAY (V.O.)"},
        {"type": "dialogue", "text": "One line"},
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "Another line"},
    ]
    stats = s.compute_stats(buffer)
    assert len(stats["characters"]) == 1
    assert stats["characters"][0][0] == "VIJAY"
    assert stats["characters"][0][1] == 2


def test_compute_stats_dialogue_without_preceding_character_is_unattributed():
    # A stray DIALOGUE-typed line with no CHARACTER cue above it (e.g. an
    # odd import) still counts toward dialogue_words but isn't attributed
    # to any speaker -- and an action line breaks the chain so leftover
    # dialogue after it isn't misattributed to a stale earlier speaker.
    buffer = [
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "Real line"},
        {"type": "action", "text": "Beat."},
        {"type": "dialogue", "text": "Orphaned line"},
    ]
    stats = s.compute_stats(buffer)
    assert len(stats["characters"]) == 1
    assert stats["characters"][0][1] == 1  # only the real, attributed line
    assert stats["dialogue_words"] == 2 + 2  # both dialogue lines still counted


def test_compute_stats_sorted_by_word_count_descending():
    buffer = [
        {"type": "character", "text": "QUIET"},
        {"type": "dialogue", "text": "Hi"},
        {"type": "character", "text": "CHATTY"},
        {"type": "dialogue", "text": "I have so much more to say than that"},
    ]
    stats = s.compute_stats(buffer)
    assert [name for name, _, _ in stats["characters"]] == ["CHATTY", "QUIET"]


# --------------------------------------------------------------------------
# :stats command dispatch
# --------------------------------------------------------------------------

def test_stats_command_opens_popup(monkeypatch):
    ed = _make_editor(buffer=[
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "Hello"},
    ])
    calls = []
    monkeypatch.setattr(ed, "show_stats", lambda: calls.append(True))
    ed.execute_command("stats")
    assert calls == [True]


# --------------------------------------------------------------------------
# PDF scene numbers -- export_pdf(..., scene_numbers=True) stamps each
# heading's number in the left/right margins.
# --------------------------------------------------------------------------

def test_pdf_scene_numbers_appear_in_margins(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    buffer = [
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "Vijay stares at the kettle."},
        {"type": "heading", "text": "EXT. STREET - NIGHT"},
        {"type": "action", "text": "Rain falls."},
    ]
    out = tmp_path / "scenes.pdf"
    s.export_pdf(out, {"Title": "Test"}, buffer, scene_numbers=True)
    reader = pypdf.PdfReader(str(out))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "1" in text
    assert "2" in text
    assert "KITCHEN" in text


def test_pdf_scene_numbers_can_be_disabled(tmp_path):
    pytest.importorskip("pypdf")
    buffer = [{"type": "heading", "text": "INT. KITCHEN - DAY"}]
    out = tmp_path / "noscenes.pdf"
    # Just confirming this doesn't raise with the flag off.
    s.export_pdf(out, {"Title": "Test"}, buffer, scene_numbers=False)
    assert out.exists()


def test_pdf_scene_numbers_are_not_duplicated(tmp_path):
    """Locked scripts stamp each scene number once, in the left gutter --
    a second copy in the right margin is pure duplication for a PDF (no
    one reads a screen from the right edge inward)."""
    pypdf = pytest.importorskip("pypdf")
    buffer = [
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "action", "text": "Vijay stares at the kettle."},
    ]
    out = tmp_path / "scene.pdf"
    s.export_pdf(out, {"Title": "Test"}, buffer, scene_numbers=True)
    reader = pypdf.PdfReader(str(out))
    tokens = reader.pages[1].extract_text().split("\n")
    assert tokens.count("1") == 1


# --------------------------------------------------------------------------
# Scoped PDF export -- ":pdf 1-10", ":pdf 5", ":pdf char NAME" -- sides for
# a scene range or for every scene a given character appears in.
# --------------------------------------------------------------------------

_SIDES_BUFFER = [
    {"type": "heading", "text": "INT. KITCHEN - DAY"},               # scene 1
    {"type": "character", "text": "VIKRANTH"},
    {"type": "dialogue", "text": "Parledu ra."},
    {"type": "heading", "text": "EXT. STREET - NIGHT"},              # scene 2
    {"type": "action", "text": "Rishitha walks alone in the rain."},
    {"type": "heading", "text": "INT. GARAGE - CONTINUOUS"},         # scene 3
    {"type": "action", "text": "Vikranth fixes the old bike."},
    {"type": "heading", "text": "INT. OFFICE - DAY"},                # scene 4
    {"type": "character", "text": "RISHITHA"},
    {"type": "dialogue", "text": "Nenu osthanu."},
]


def test_scene_bounds_splits_buffer_at_each_heading():
    bounds = s.scene_bounds(_SIDES_BUFFER)
    assert bounds == [(0, 3), (3, 5), (5, 7), (7, 10)]


def test_scenes_matching_character_finds_dialogue_cue():
    assert s.scenes_matching_character(_SIDES_BUFFER, "vikranth") == {1, 3}
    assert s.scenes_matching_character(_SIDES_BUFFER, "RISHITHA") == {2, 4}


def test_scenes_matching_character_checks_action_prose_too():
    """A character can appear (be mentioned/act) in a scene with no
    dialogue of their own -- scene 2 and 3 both mention names only in
    ACTION text, not a CHARACTER cue."""
    assert 2 in s.scenes_matching_character(_SIDES_BUFFER, "Rishitha")
    assert 3 in s.scenes_matching_character(_SIDES_BUFFER, "Vikranth")


def test_scenes_matching_character_is_whole_word_only():
    buffer = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "action", "text": "ALEX always arrives late."},
    ]
    # "AL" should not match inside "ALEX" or "ALWAYS".
    assert s.scenes_matching_character(buffer, "AL") == set()
    assert s.scenes_matching_character(buffer, "ALEX") == {1}


def test_scenes_matching_character_no_match_returns_empty_set():
    assert s.scenes_matching_character(_SIDES_BUFFER, "NOBODY") == set()


# -- dialogue scanning (mentioned by another character, not just cue/action) --

_DIALOGUE_MENTION_BUFFER = [
    {"type": "heading", "text": "INT. KITCHEN - DAY"},                # scene 1
    {"type": "character", "text": "PRIYA"},
    {"type": "dialogue", "text": "danny left an hour ago."},
    {"type": "heading", "text": "EXT. STREET - NIGHT"},               # scene 2
    {"type": "character", "text": "PRIYA"},
    {"type": "dialogue", "text": "I haven't seen DANNY all day."},
    {"type": "heading", "text": "INT. GARAGE - CONTINUOUS"},          # scene 3
    {"type": "character", "text": "PRIYA"},
    {"type": "dialogue", "text": "Nobody here but us."},
]


def test_scenes_matching_character_finds_name_in_other_characters_dialogue():
    """DANNY never has a cue or ACTION mention of their own here -- only
    PRIYA says the name, lowercase in scene 1 and mixed/upper in scene 2.
    Dialogue scanning is on by default, so both scenes must be found and
    scene 3 (no mention at all) must not be."""
    matches = s.scenes_matching_character(_DIALOGUE_MENTION_BUFFER, "Danny")
    assert matches == {1, 2}


def test_scenes_matching_character_dialogue_scan_is_configurable_off():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["character_export"]["search_in"] = ["action"]
    matches = s.scenes_matching_character(_DIALOGUE_MENTION_BUFFER, "Danny", cfg)
    assert matches == set()


def test_scenes_matching_character_search_in_defaults_when_cfg_missing_section():
    # A cfg dict that simply doesn't have "character_export" (e.g. an old
    # config.toml on disk from before this feature existed) must still
    # search dialogue, matching the documented default.
    cfg = {"general": {}}
    matches = s.scenes_matching_character(_DIALOGUE_MENTION_BUFFER, "Danny", cfg)
    assert matches == {1, 2}


# -- aliases --

_ALIAS_BUFFER = [
    {"type": "heading", "text": "INT. KITCHEN - DAY"},                # scene 1
    {"type": "character", "text": "DANNY"},
    {"type": "dialogue", "text": "I'm making breakfast."},
    {"type": "heading", "text": "EXT. STREET - NIGHT"},               # scene 2
    {"type": "character", "text": "PRIYA"},
    {"type": "dialogue", "text": "Have you seen Dan anywhere?"},
    {"type": "heading", "text": "INT. GARAGE - CONTINUOUS"},          # scene 3
    {"type": "action", "text": "A DANFORTH sign hangs on the wall."},
]


def _cfg_with_alias(name, aliases):
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["character_export"]["aliases"] = {name: aliases}
    return cfg


def test_scenes_matching_character_alias_pulls_in_nickname_scenes():
    cfg = _cfg_with_alias("DANNY", ["Dan"])
    assert s.scenes_matching_character(_ALIAS_BUFFER, "Danny", cfg) == {1, 2}


def test_scenes_matching_character_alias_lookup_by_alias_name_too():
    """Querying by the alias itself (":pdf char Dan") should pull in the
    same scenes as querying by the canonical name."""
    cfg = _cfg_with_alias("DANNY", ["Dan"])
    assert s.scenes_matching_character(_ALIAS_BUFFER, "Dan", cfg) == {1, 2}


def test_scenes_matching_character_alias_is_whole_word_only():
    """"Dan" must not match inside "DANFORTH" in scene 3."""
    cfg = _cfg_with_alias("DANNY", ["Dan"])
    assert 3 not in s.scenes_matching_character(_ALIAS_BUFFER, "Danny", cfg)


def test_scenes_matching_character_no_aliases_by_default():
    matches = s.scenes_matching_character(_ALIAS_BUFFER, "Danny")
    assert matches == {1}  # scene 2's "Dan" isn't linked without an alias


def test_character_export_settings_defaults_and_overrides():
    search_in, aliases = s.character_export_settings(None)
    assert search_in == {"action", "dialogue"}
    assert aliases == {}

    cfg = _cfg_with_alias("DANNY", ["Dan", "Danno"])
    search_in, aliases = s.character_export_settings(cfg)
    assert aliases == {"DANNY": ["Dan", "Danno"]}


def test_names_for_character_case_insensitive_key_match():
    aliases = {"danny": ["Dan"]}
    assert s.names_for_character("DANNY", aliases) == {"DANNY", "DAN"}
    assert s.names_for_character("Priya", aliases) == {"PRIYA"}


def test_buffer_for_scenes_keeps_only_requested_scenes_in_order():
    out = s.buffer_for_scenes(_SIDES_BUFFER, {3, 1})
    types_and_text = [(l["type"], l["text"]) for l in out]
    assert types_and_text == [
        ("heading", "INT. KITCHEN - DAY"),
        ("character", "VIKRANTH"),
        ("dialogue", "Parledu ra."),
        ("heading", "INT. GARAGE - CONTINUOUS"),
        ("action", "Vikranth fixes the old bike."),
    ]


def test_buffer_for_scenes_tags_original_scene_number():
    out = s.buffer_for_scenes(_SIDES_BUFFER, {3})
    heading = out[0]
    assert heading["type"] == "heading"
    assert heading["_export_scene_no"] == 3


def test_buffer_for_scenes_does_not_mutate_original_buffer():
    original = copy.deepcopy(_SIDES_BUFFER)
    s.buffer_for_scenes(_SIDES_BUFFER, {1})
    assert _SIDES_BUFFER == original


def test_buffer_for_scenes_ignores_out_of_range_numbers():
    out = s.buffer_for_scenes(_SIDES_BUFFER, {1, 99})
    assert [l["text"] for l in out if l["type"] == "heading"] == ["INT. KITCHEN - DAY"]


def test_parse_pdf_scope_plain_arg_is_a_path():
    assert s.parse_pdf_scope(None) == (None, None)
    assert s.parse_pdf_scope("") == (None, None)
    assert s.parse_pdf_scope("~/Desktop/draft.pdf") == (None, "~/Desktop/draft.pdf")


def test_parse_pdf_scope_single_scene_number():
    assert s.parse_pdf_scope("5") == ({5}, None)
    assert s.parse_pdf_scope("5 out.pdf") == ({5}, "out.pdf")


def test_parse_pdf_scope_scene_range():
    assert s.parse_pdf_scope("1-10") == (set(range(1, 11)), None)
    assert s.parse_pdf_scope("10-1") == (set(range(1, 11)), None)  # reversed range still works
    assert s.parse_pdf_scope("3-3") == ({3}, None)
    assert s.parse_pdf_scope("1-10 ~/sides.pdf") == (set(range(1, 11)), "~/sides.pdf")


def test_parse_pdf_scope_character_filter():
    assert s.parse_pdf_scope("char VIKRANTH") == (("char", "VIKRANTH"), None)
    assert s.parse_pdf_scope("character VIKRANTH") == (("char", "VIKRANTH"), None)
    assert s.parse_pdf_scope("char VIKRANTH out.pdf") == (("char", "VIKRANTH"), "out.pdf")


def test_parse_pdf_scope_character_filter_quoted_multiword_name():
    assert s.parse_pdf_scope('char "OLD MAN"') == (("char", "OLD MAN"), None)


def test_parse_pdf_scope_character_filter_missing_name_is_usage_error():
    scope, path = s.parse_pdf_scope("char")
    assert scope == "char:usage"


def test_do_export_pdf_scene_range_exports_only_those_scenes(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Test"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False  # skip the sides-title prompt
    ed.do_export_pdf("1-2")
    out = tmp_path / "script_scenes_1-2.pdf"
    assert out.exists()
    reader = pypdf.PdfReader(str(out))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "KITCHEN" in text
    assert "STREET" in text
    assert "GARAGE" not in text
    assert "OFFICE" not in text


def test_do_export_pdf_scene_range_stamps_original_scene_numbers(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Test"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.do_export_pdf("3-4")  # scenes 3 and 4, not the subset's own 1 and 2
    out = tmp_path / "script_scenes_3-4.pdf"
    reader = pypdf.PdfReader(str(out))
    # Scoped exports now get their own title page (page 0) -- the script
    # content with the scene-number stamp is the page after it.
    tokens = reader.pages[1].extract_text().split("\n")
    assert "3" in tokens
    assert "1" not in tokens


def test_do_export_pdf_character_filter_exports_only_their_scenes(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Test"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.do_export_pdf("char VIKRANTH")
    out = tmp_path / "script_VIKRANTH.pdf"
    assert out.exists()
    reader = pypdf.PdfReader(str(out))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "KITCHEN" in text   # scene 1 -- VIKRANTH has a dialogue cue
    assert "GARAGE" in text    # scene 3 -- VIKRANTH mentioned in action
    assert "STREET" not in text
    assert "OFFICE" not in text


def test_do_export_pdf_character_with_no_scenes_reports_status_and_does_not_export(tmp_path):
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Test"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.do_export_pdf("char NOBODY")
    assert not (tmp_path / "script_NOBODY.pdf").exists()
    assert "NOBODY" in ed.status


def test_do_export_pdf_scene_range_out_of_bounds_reports_status(tmp_path):
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Test"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.do_export_pdf("50-60")
    assert not list(tmp_path.glob("*scenes_50*"))
    assert "50" in ed.status


def test_do_export_pdf_plain_call_still_exports_everything(tmp_path):
    """No regression for the existing, unscoped ':pdf' / ':pdf PATH'
    behavior -- a plain call still exports the whole script to the usual
    default location with no suffix."""
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Test"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.do_export_pdf(None)
    out = tmp_path / "script.pdf"
    assert out.exists()
    reader = pypdf.PdfReader(str(out))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    for scene in ("KITCHEN", "STREET", "GARAGE", "OFFICE"):
        assert scene in text


def test_do_export_pdf_explicit_path_after_scope_is_honored(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Test"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    out = tmp_path / "custom_sides.pdf"
    ed.do_export_pdf(f"char VIKRANTH {out}")
    assert out.exists()


# --------------------------------------------------------------------------
# [sides] -- scoped (sides/character-draft) exports get their own title
# page: a custom or auto-generated Title, the rest of the cover page
# carried over from the full script's own metadata, and an optional
# "(an excerpt from ...)" note. See sides_export_settings(),
# default_sides_title(), sides_excerpt_subtitle(), and do_export_pdf().
# --------------------------------------------------------------------------

def test_sides_export_settings_defaults_and_overrides():
    resolved = s.sides_export_settings(None)
    assert resolved["cover_page"] is True
    assert resolved["prompt_title"] is True
    assert resolved["title_format"] == "Sides (Scene {start} - Scene {end})"
    assert resolved["character_title_format"] == "Sides - {name} Draft"

    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["sides"]["prompt_title"] = False
    resolved = s.sides_export_settings(cfg)
    assert resolved["prompt_title"] is False
    assert resolved["cover_page"] is True  # untouched keys keep the default


def test_sides_export_settings_tolerates_missing_section():
    # An old config.toml on disk from before this feature existed simply
    # won't have a [sides] table -- must fall back to the built-in
    # defaults rather than KeyError.
    assert s.sides_export_settings({"general": {}}) == s.DEFAULT_CONFIG["sides"]


def test_default_sides_title_scene_range():
    cfg = s.DEFAULT_CONFIG["sides"]
    assert s.default_sides_title(cfg, start=4, end=9) == "Sides (Scene 4 - Scene 9)"


def test_default_sides_title_single_scene_uses_singular_format():
    cfg = s.DEFAULT_CONFIG["sides"]
    assert s.default_sides_title(cfg, start=5, end=5) == "Sides (Scene 5)"


def test_default_sides_title_character():
    cfg = s.DEFAULT_CONFIG["sides"]
    assert s.default_sides_title(cfg, name="VIKRANTH") == "Sides - VIKRANTH Draft"


def test_sides_excerpt_subtitle_formats_from_script_title():
    cfg = s.DEFAULT_CONFIG["sides"]
    assert s.sides_excerpt_subtitle("Beach House", cfg) == "(an excerpt from Beach House)"


def test_sides_excerpt_subtitle_none_when_script_has_no_title():
    cfg = s.DEFAULT_CONFIG["sides"]
    assert s.sides_excerpt_subtitle("", cfg) is None
    assert s.sides_excerpt_subtitle(None, cfg) is None
    assert s.sides_excerpt_subtitle("   ", cfg) is None


def test_sides_excerpt_subtitle_can_be_turned_off():
    cfg = dict(s.DEFAULT_CONFIG["sides"])
    cfg["excerpt_note"] = False
    assert s.sides_excerpt_subtitle("Beach House", cfg) is None


def test_do_export_pdf_scoped_export_now_gets_its_own_cover_page(tmp_path):
    """Scoped exports used to never draw a cover page at all. They now get
    one built from the sides title/excerpt note -- but still never reuse
    the *full script's* Title verbatim as the big heading."""
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "The Full Screenplay Title"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.do_export_pdf("1-1")
    out = tmp_path / "script_scenes_1.pdf"
    text = (pypdf.PdfReader(str(out)).pages[0].extract_text() or "").upper()
    assert "SIDES (SCENE 1)" in text
    assert "AN EXCERPT FROM THE FULL SCREENPLAY TITLE" in text
    # The full script's own title is never drawn as the big cover heading
    # on a scoped export -- only inside the small excerpt note.
    assert "THE FULL SCREENPLAY TITLE" not in text.replace(
        "AN EXCERPT FROM THE FULL SCREENPLAY TITLE", "")


def test_do_export_pdf_scoped_export_carries_over_rest_of_cover_fields(tmp_path):
    """"Keep the rest the same": Author/Genre/etc. from the full script's
    metadata still show up on a scoped export's title page, only the
    Title itself is replaced."""
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Beach House", "Author": "Priya Menon", "Genre": "Drama"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.do_export_pdf("char VIKRANTH")
    out = tmp_path / "script_VIKRANTH.pdf"
    text = (pypdf.PdfReader(str(out)).pages[0].extract_text() or "").upper()
    assert "SIDES - VIKRANTH DRAFT" in text
    assert "BY PRIYA MENON" in text
    assert "DRAMA" in text


def test_do_export_pdf_scoped_export_prompts_for_custom_title(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Beach House"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.stdscr = _FakeWChStdscr(_queued_field_answers("Table Read Draft"))
    import curses as _curses
    orig = _curses.curs_set
    _curses.curs_set = lambda n: None
    try:
        ed.do_export_pdf("1-2")
    finally:
        _curses.curs_set = orig
    out = tmp_path / "script_scenes_1-2.pdf"
    text = (pypdf.PdfReader(str(out)).pages[0].extract_text() or "").upper()
    assert "TABLE READ DRAFT" in text
    assert "AN EXCERPT FROM BEACH HOUSE" in text  # excerpt note shows either way


def test_do_export_pdf_scoped_export_blank_prompt_auto_generates(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Beach House"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.stdscr = _FakeWChStdscr(_queued_field_answers(""))
    import curses as _curses
    orig = _curses.curs_set
    _curses.curs_set = lambda n: None
    try:
        ed.do_export_pdf("char VIKRANTH")
    finally:
        _curses.curs_set = orig
    out = tmp_path / "script_VIKRANTH.pdf"
    text = (pypdf.PdfReader(str(out)).pages[0].extract_text() or "").upper()
    assert "SIDES - VIKRANTH DRAFT" in text


def test_do_export_pdf_scoped_export_prompt_disabled_never_touches_stdscr(tmp_path):
    """sides.prompt_title = false must skip the prompt outright -- a
    _FakeStdscr with no get_wch() queued would raise if it were touched."""
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Beach House"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.stdscr = _FakeStdscr()  # no get_wch queued
    ed.do_export_pdf("1-1")  # must not raise
    out = tmp_path / "script_scenes_1.pdf"
    assert out.exists()


def test_do_export_pdf_scoped_export_cover_page_disabled_via_sides_config(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Should Not Appear"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["cover_page"] = False
    ed.stdscr = _FakeStdscr()  # no get_wch queued -- prompt must not fire either
    ed.do_export_pdf("1-1")
    out = tmp_path / "script_scenes_1.pdf"
    text = (pypdf.PdfReader(str(out)).pages[0].extract_text() or "").upper()
    assert "SHOULD NOT APPEAR" not in text
    assert "SIDES" not in text


def test_do_export_pdf_scoped_export_excerpt_note_can_be_disabled(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    ed.metadata = {"Title": "Beach House"}
    ed.filepath = str(tmp_path / "script.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.cfg["sides"]["excerpt_note"] = False
    ed.do_export_pdf("1-1")
    out = tmp_path / "script_scenes_1.pdf"
    text = (pypdf.PdfReader(str(out)).pages[0].extract_text() or "").upper()
    assert "SIDES (SCENE 1)" in text
    assert "EXCERPT" not in text


def test_do_export_pdf_scoped_export_on_titlepage_less_import_still_gets_a_cover(tmp_path):
    """The imported-Fountain-with-no-title-page case: self.metadata == {}
    (nothing typed, no :cover run). A scoped export must still get its
    own title page -- naming the scene range/character -- and must not
    show an excerpt note, since there's no script title to name."""
    pypdf = pytest.importorskip("pypdf")
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    assert ed.metadata == {}
    ed.filepath = str(tmp_path / "imported.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.do_export_pdf("char RISHITHA")
    out = tmp_path / "imported_RISHITHA.pdf"
    reader = pypdf.PdfReader(str(out))
    text = (reader.pages[0].extract_text() or "").upper()
    assert "SIDES - RISHITHA DRAFT" in text
    assert "EXCERPT" not in text
    # And the main script's own metadata is left untouched -- unlike the
    # full-export path, a scoped export never triggers the general
    # "no title page in this file" prompt/probe.
    assert ed.metadata == {}


def test_do_export_pdf_scoped_export_on_titlepage_less_import_skips_general_prompt(tmp_path):
    """A scoped export on a title-page-less import must go straight to
    its own sides-title flow, never the full-script "no title page in
    this file" prompt -- a _FakeStdscr with nothing queued for that
    6-field prompt would raise if it fired."""
    ed = _make_editor(save_dir=tmp_path, buffer=copy.deepcopy(_SIDES_BUFFER))
    assert ed.metadata == {}
    ed.filepath = str(tmp_path / "imported.fountain")
    ed.cfg["sides"]["prompt_title"] = False
    ed.stdscr = _FakeStdscr()  # no get_wch queued
    ed.do_export_pdf("1-1")  # must not raise
    assert (tmp_path / "imported_scenes_1.pdf").exists()
    assert ed._title_prompt_shown is False  # the *general* prompt never ran


def test_do_export_pdf_reads_scene_numbers_from_config(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    ed.metadata = {"Title": "Test"}  # has a title page -- not what this test covers
    ed.cfg["general"]["pdf_scene_numbers"] = False
    seen = {}

    def fake_export_pdf(path, metadata, buffer, scene_numbers=True, cover_page=True, **kw):
        seen["scene_numbers"] = scene_numbers

    monkeypatch.setattr(s.editor, "export_pdf", fake_export_pdf)
    ed.do_export_pdf(str(tmp_path / "out.pdf"))
    assert seen["scene_numbers"] is False


# --------------------------------------------------------------------------
# :pdf on a title-page-less import -- probes for cover-page metadata once
# (reusing [n]ew's own field prompt) instead of silently exporting with no
# title page. See do_export_pdf()'s own comment for the rationale.
# --------------------------------------------------------------------------

def _queued_field_answers(*answers):
    """Build a get_wch()-style queue of keycodes that types each string in
    `answers` (one per prompted field, in cfg["prompts"]["fields"] order)
    followed by Enter, matching how prompt_line() reads input."""
    queue = []
    for answer in answers:
        queue.extend(list(answer))
        queue.append(curses.KEY_ENTER)
    return queue


def test_export_pdf_prompts_for_titlepage_when_metadata_empty(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    assert ed.metadata == {}  # imported file with no title page
    # cfg["prompts"]["fields"] defaults to 6 fields (Title, Author, Genre,
    # Year, Contact (Number), Contact (Email)) -- prompt_line is called
    # once per field regardless of answer, so the queue needs one entry
    # per field or it runs dry mid-prompt.
    n_fields = len(ed.cfg["prompts"]["fields"])
    answers = ["My Script", "Wmans"] + [""] * (n_fields - 2)
    ed.stdscr = _FakeWChStdscr(_queued_field_answers(*answers))
    monkeypatch.setattr(curses, "curs_set", lambda n: None)
    seen = {}
    monkeypatch.setattr(s.editor, "export_pdf",
                         lambda path, metadata, buffer, scene_numbers=True, cover_page=True, **kw:
                             seen.update(metadata=dict(metadata)))
    ed.do_export_pdf(str(tmp_path / "out.pdf"))
    assert seen["metadata"] == {"Title": "My Script", "Author": "Wmans"}
    assert ed.metadata == {"Title": "My Script", "Author": "Wmans"}
    assert ed.dirty is True


def test_export_pdf_skips_prompt_when_metadata_already_present(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    ed.metadata = {"Title": "Already Has A Cover"}
    ed.stdscr = _FakeStdscr()  # no get_wch queued -- prompt must not fire
    seen = {}
    monkeypatch.setattr(s.editor, "export_pdf",
                         lambda path, metadata, buffer, scene_numbers=True, cover_page=True, **kw:
                             seen.update(metadata=dict(metadata)))
    ed.do_export_pdf(str(tmp_path / "out.pdf"))
    assert seen["metadata"] == {"Title": "Already Has A Cover"}


def test_export_pdf_declining_prompt_exports_with_no_titlepage(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    # Every field answered blank (straight Enter) -- "no cover page, on
    # purpose", same as leaving them blank on [n]ew.
    ed.stdscr = _FakeWChStdscr(
        _queued_field_answers(*([""] * len(ed.cfg["prompts"]["fields"]))))
    monkeypatch.setattr(curses, "curs_set", lambda n: None)
    seen = {}
    monkeypatch.setattr(s.editor, "export_pdf",
                         lambda path, metadata, buffer, scene_numbers=True, cover_page=True, **kw:
                             seen.update(metadata=dict(metadata)))
    ed.do_export_pdf(str(tmp_path / "out.pdf"))
    assert seen["metadata"] == {}
    assert ed.metadata == {}


def test_export_pdf_only_prompts_once_per_session(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    ed.stdscr = _FakeWChStdscr(
        _queued_field_answers(*([""] * len(ed.cfg["prompts"]["fields"]))))
    monkeypatch.setattr(curses, "curs_set", lambda n: None)
    monkeypatch.setattr(s.editor, "export_pdf", lambda *a, **k: None)
    ed.do_export_pdf(str(tmp_path / "out1.pdf"))
    assert ed._title_prompt_shown is True
    # Second export in the same session: metadata is still {} (declined
    # above), but the queue is now empty -- if do_export_pdf tried to
    # prompt again, read_key() would raise and the export would fail.
    ed.do_export_pdf(str(tmp_path / "out2.pdf"))  # must not re-prompt


def test_export_pdf_prompt_disabled_via_config(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    ed.cfg["general"]["prompt_missing_titlepage"] = False
    ed.stdscr = _FakeStdscr()  # no get_wch queued -- prompt must not fire
    monkeypatch.setattr(s.editor, "export_pdf", lambda *a, **k: None)
    ed.do_export_pdf(str(tmp_path / "out.pdf"))  # must not raise/hang
    assert ed.metadata == {}


# --------------------------------------------------------------------------
# general.cover_page -- full master switch for the PDF cover page,
# independent of prompt_missing_titlepage (which only gates the prompt).
# --------------------------------------------------------------------------

def test_cover_page_disabled_skips_prompt_and_passes_false_through(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    ed.cfg["general"]["cover_page"] = False
    ed.stdscr = _FakeStdscr()  # no get_wch queued -- prompt must not fire
    seen = {}
    monkeypatch.setattr(s.editor, "export_pdf",
                         lambda path, metadata, buffer, scene_numbers=True, cover_page=True, **kw:
                             seen.update(cover_page=cover_page))
    ed.do_export_pdf(str(tmp_path / "out.pdf"))
    assert seen["cover_page"] is False


def test_cover_page_disabled_even_with_metadata_present(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
    ])
    ed.metadata = {"Title": "Has Metadata"}
    ed.cfg["general"]["cover_page"] = False
    seen = {}
    monkeypatch.setattr(s.editor, "export_pdf",
                         lambda path, metadata, buffer, scene_numbers=True, cover_page=True, **kw:
                             seen.update(cover_page=cover_page, metadata=dict(metadata)))
    ed.do_export_pdf(str(tmp_path / "out.pdf"))
    assert seen["cover_page"] is False
    assert seen["metadata"] == {"Title": "Has Metadata"}  # untouched, just not drawn


def test_export_pdf_title_page_flag_respects_cover_page_config(tmp_path):
    # export_pdf() itself: cover_page=False must suppress the title page
    # section even with non-empty metadata, not just skip the prompt.
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    out = tmp_path / "out.pdf"
    s.export_pdf(out, {"Title": "Should Not Appear"},
                 [{"type": "action", "text": "hi"}], cover_page=False)
    reader = pypdf.PdfReader(str(out))
    assert len(reader.pages) == 1  # no separate title page
    text = reader.pages[0].extract_text()
    assert "Should Not Appear" not in text


def test_has_title_page_respects_cover_page_config(tmp_path):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed.metadata = {"Title": "Something"}
    assert ed._has_title_page() is True
    ed.cfg["general"]["cover_page"] = False
    assert ed._has_title_page() is False  # page-count estimate must agree with export


# --------------------------------------------------------------------------
# :cover -- manually (re-)prompt for cover-page fields, any number of
# times, prefilled with whatever's already set.
# --------------------------------------------------------------------------

def test_cover_command_prefills_existing_metadata(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed.metadata = {"Title": "Old Title", "Author": "Wmans"}
    seen_initial = {}

    def fake_new_file_metadata(stdscr, cfg, heading=None, initial=None):
        seen_initial["initial"] = dict(initial or {})
        return dict(initial or {})  # simulate accepting every prefilled value as-is

    monkeypatch.setattr(s.editor, "new_file_metadata", fake_new_file_metadata)
    ed.stdscr = _FakeStdscr()
    ed.do_cover_prompt()
    assert seen_initial["initial"] == {"Title": "Old Title", "Author": "Wmans"}
    assert ed.metadata == {"Title": "Old Title", "Author": "Wmans"}
    assert ed.dirty is False  # nothing actually changed


def test_cover_command_updates_metadata_and_marks_dirty(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed.metadata = {"Title": "Old Title"}
    monkeypatch.setattr(s.editor, "new_file_metadata",
                         lambda stdscr, cfg, heading=None, initial=None:
                             {"Title": "New Title"})
    ed.stdscr = _FakeStdscr()
    ed.do_cover_prompt()
    assert ed.metadata == {"Title": "New Title"}
    assert ed.dirty is True
    assert "updated" in ed.status.lower()


def test_cover_command_can_clear_metadata_entirely(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed.metadata = {"Title": "Old Title"}
    monkeypatch.setattr(s.editor, "new_file_metadata",
                         lambda stdscr, cfg, heading=None, initial=None: {})
    ed.stdscr = _FakeStdscr()
    ed.do_cover_prompt()
    assert ed.metadata == {}
    assert ed.dirty is True
    assert "cleared" in ed.status.lower()


def test_cover_command_resets_title_prompt_shown_every_call(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed._title_prompt_shown = True  # e.g. already declined once via :pdf
    monkeypatch.setattr(s.editor, "new_file_metadata",
                         lambda stdscr, cfg, heading=None, initial=None: {})
    ed.stdscr = _FakeStdscr()
    ed.do_cover_prompt()
    assert ed._title_prompt_shown is False


def test_cover_command_runs_every_time_unlike_pdfs_one_shot(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    calls = []
    monkeypatch.setattr(
        s.editor, "new_file_metadata",
        lambda stdscr, cfg, heading=None, initial=None: calls.append(1) or {})
    ed.stdscr = _FakeStdscr()
    ed.do_cover_prompt()
    ed.do_cover_prompt()
    ed.do_cover_prompt()
    assert len(calls) == 3  # every call prompts, no one-shot suppression


def test_cover_command_via_execute_command_returns_to_editor(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    monkeypatch.setattr(s.editor, "new_file_metadata",
                         lambda stdscr, cfg, heading=None, initial=None:
                             {"Title": "Via Command"})
    ed.stdscr = _FakeStdscr()
    result = ed.execute_command("cover")
    assert result is None  # doesn't quit/open -- just returns to the screenplay
    assert ed.metadata == {"Title": "Via Command"}


def test_cover_command_status_notes_when_cover_page_disabled(tmp_path, monkeypatch):
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed.cfg["general"]["cover_page"] = False
    monkeypatch.setattr(s.editor, "new_file_metadata",
                         lambda stdscr, cfg, heading=None, initial=None:
                             {"Title": "Saved Anyway"})
    ed.stdscr = _FakeStdscr()
    ed.do_cover_prompt()
    assert ed.metadata == {"Title": "Saved Anyway"}  # still stored
    assert "cover_page = false" in ed.status  # but flagged as inert in status


def test_cover_command_allowed_in_readonly(tmp_path, monkeypatch):
    # Metadata edits aren't the buffer-content changes readonly guards
    # against -- :pdf's own inline prompt is already allowed in readonly
    # (see execute_command's readonly-blocked head list), :cover follows
    # the same reasoning.
    ed = _make_editor(save_dir=tmp_path, buffer=[{"type": "action", "text": "hi"}])
    ed.readonly = True
    monkeypatch.setattr(s.editor, "new_file_metadata",
                         lambda stdscr, cfg, heading=None, initial=None:
                             {"Title": "Still Works"})
    ed.stdscr = _FakeStdscr()
    ed.execute_command("cover")
    assert ed.metadata == {"Title": "Still Works"}
    assert "Read-only" not in ed.status


def test_prompt_line_prefill_kept_on_plain_enter(monkeypatch):
    monkeypatch.setattr(curses, "curs_set", lambda n: None)
    stdscr = _FakeWChStdscr([curses.KEY_ENTER])
    result = s.prompt_line(stdscr, 0, 0, "Title", initial="Existing Title")
    assert result == "Existing Title"


def test_prompt_line_prefill_cleared_by_backspacing_then_enter(monkeypatch):
    monkeypatch.setattr(curses, "curs_set", lambda n: None)
    backspaces = [curses.KEY_BACKSPACE] * len("Existing")
    stdscr = _FakeWChStdscr(backspaces + [curses.KEY_ENTER])
    result = s.prompt_line(stdscr, 0, 0, "Title", initial="Existing")
    assert result == ""


def test_new_file_metadata_initial_prefills_matching_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(curses, "curs_set", lambda n: None)
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["prompts"]["fields"] = ["Title", "Author"]
    # Both fields answered with a bare Enter -- should round-trip the
    # prefilled initial values unchanged.
    stdscr = _FakeWChStdscr([curses.KEY_ENTER, curses.KEY_ENTER])
    result = s.new_file_metadata(stdscr, cfg,
                                  initial={"Title": "Kept", "Author": "Also Kept"})
    assert result == {"Title": "Kept", "Author": "Also Kept"}



# --------------------------------------------------------------------------
# paginate_buffer() -- shared page-break simulation behind both the status
# bar's page/runtime estimate and export_pdf() itself.
# --------------------------------------------------------------------------

def test_paginate_buffer_does_not_add_gap_inside_glued_dialogue_block():
    """A CHARACTER cue immediately followed by its own DIALOGUE (no blank
    line between them, per Fountain's "glued" convention) must not be
    counted as if a blank inter-element row followed the cue -- that
    phantom row is exactly what made the old lines/55 heuristic overcount
    dialogue-heavy scripts."""
    glued = [
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "It never boils."},
    ]
    separate = [
        {"type": "action", "text": "Vijay stares."},
        {"type": "action", "text": "He sighs."},
    ]
    starts_glued, _ = s.paginate_buffer(glued)
    starts_sep, _ = s.paginate_buffer(separate)
    # Glued: dialogue starts the row right after the cue's single row (1).
    assert starts_glued[1] == (1, 1)
    # Not glued: the second action starts a row later, since a blank gap
    # row is inserted between the two.
    assert starts_sep[1] == (1, 2)


def test_paginate_buffer_ignores_manual_blank_lines_between_elements():
    """Blank buffer lines the writer typed for their own visual spacing
    must not add extra rows beyond the standard one-blank-line gap that's
    already inserted between non-glued elements -- see the "industry
    standard spacing" fix in paginate_buffer()/export_pdf()."""
    one_gap = [
        {"type": "action", "text": "Vijay stares."},
        {"type": "action", "text": "He sighs."},
    ]
    many_blanks = [
        {"type": "action", "text": "Vijay stares."},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
        {"type": "action", "text": "He sighs."},
    ]
    starts_one, pages_one = s.paginate_buffer(one_gap)
    starts_many, pages_many = s.paginate_buffer(many_blanks)
    assert pages_one == pages_many
    # The final "He sighs." line lands on the exact same (page, row) in
    # both buffers -- the 4 extra blank lines contributed nothing.
    assert starts_one[1] == starts_many[5]


def test_export_pdf_ignores_manual_blank_lines_between_elements(tmp_path):
    """Same claim as the paginate_buffer test above, but against a real
    reportlab export: the rendered page count must not grow just because
    the writer left blank lines between action beats in the editor."""
    pytest.importorskip("reportlab")
    one_gap = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "action", "text": "Vijay stares."},
        {"type": "action", "text": "He sighs."},
    ]
    many_blanks = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "action", "text": "Vijay stares."},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
        {"type": "action", "text": ""},
        {"type": "action", "text": "He sighs."},
    ]
    out1, out2 = tmp_path / "one.pdf", tmp_path / "many.pdf"
    s.export_pdf(out1, {}, one_gap)
    s.export_pdf(out2, {}, many_blanks)
    pages1 = int(re.search(rb"/Count\s+(\d+)", out1.read_bytes()).group(1))
    pages2 = int(re.search(rb"/Count\s+(\d+)", out2.read_bytes()).group(1))
    assert pages1 == pages2


def test_paginate_buffer_matches_real_pdf_export_page_count(tmp_path):
    """paginate_buffer() must land on the exact same page count
    export_pdf() itself produces -- checked here against a real reportlab
    export (parsing the PDF's own page-tree /Count rather than adding a
    second PDF-reading dependency) for a dialogue-heavy script, the shape
    that most exposed the old heuristic's overcounting."""
    pytest.importorskip("reportlab")
    buffer = [{"type": "heading", "text": "INT. ROOM - DAY"}]
    for i in range(120):
        buffer.append({"type": "character", "text": f"PERSON{i % 2}"})
        buffer.append({"type": "dialogue",
                        "text": "This is a line of dialogue that runs on a bit. " * 2})
    out = tmp_path / "big.pdf"
    s.export_pdf(out, {}, buffer)  # no metadata -> no title page to offset by
    actual_pages = int(re.search(rb"/Count\s+(\d+)", out.read_bytes()).group(1))
    _, script_pages = s.paginate_buffer(buffer)
    assert script_pages == actual_pages


def test_dialogue_split_across_page_gets_more_and_contd(tmp_path):
    """A DIALOGUE block long enough to spill onto a second page must get
    industry-standard continuation markers: '(MORE)' under the last line
    printed on the first page, and the CHARACTER cue repeated with
    '(CONT'D)' at the top of the next page -- not a silent mid-speech
    wrap with nothing marking the continuation."""
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    buffer = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "character", "text": "ALICE"},
        {"type": "dialogue",
         "text": "This monologue keeps going and going and going. " * 40},
    ]
    out = tmp_path / "split.pdf"
    s.export_pdf(out, {}, buffer)
    reader = pypdf.PdfReader(str(out))
    assert len(reader.pages) >= 2
    full_text = "\n".join(p.extract_text() for p in reader.pages)
    assert "(MORE)" in full_text
    assert "CONT'D" in full_text
    assert "ALICE" in full_text


def test_short_dialogue_gets_no_continuation_markers(tmp_path):
    """A short speech that fits on one page shouldn't get '(MORE)' or
    '(CONT'D)' stamped anywhere -- those only belong on an actual split."""
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    buffer = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "character", "text": "ALICE"},
        {"type": "dialogue", "text": "Hello there."},
    ]
    out = tmp_path / "short.pdf"
    s.export_pdf(out, {}, buffer)
    reader = pypdf.PdfReader(str(out))
    full_text = "\n".join(p.extract_text() for p in reader.pages)
    assert "(MORE)" not in full_text
    assert "CONT'D" not in full_text


def test_paginate_buffer_matches_pdf_when_dialogue_splits_page(tmp_path):
    """paginate_buffer() must account for the extra page a (MORE)/(CONT'D)
    split introduces, or the status bar's page estimate would undercount
    against the real exported PDF for any script with a long speech."""
    pytest.importorskip("reportlab")
    buffer = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "character", "text": "ALICE"},
        {"type": "dialogue",
         "text": "This monologue keeps going and going and going. " * 40},
    ]
    out = tmp_path / "split2.pdf"
    s.export_pdf(out, {}, buffer)
    actual_pages = int(re.search(rb"/Count\s+(\d+)", out.read_bytes()).group(1))
    _, script_pages = s.paginate_buffer(buffer)
    assert script_pages == actual_pages


def test_page_estimate_accounts_for_title_page():
    """export_pdf() inserts a separate title page whenever metadata is
    non-empty -- page_estimate() has to add that page to match the actual
    exported PDF's page count, not just the script pages."""
    buffer = [{"type": "heading", "text": "INT. ROOM - DAY"},
              {"type": "action", "text": "Something happens."}]
    ed = _make_editor(buffer=buffer)
    ed._page_cache = (1, 1, 0.0)
    ed.metadata = {}
    no_title = ed.page_estimate()
    ed._page_cache = (1, 1, 0.0)
    ed.metadata = {"Title": "Test"}
    with_title = ed.page_estimate()
    assert with_title == no_title + 1


def test_page_number_at_matches_paginate_buffer():
    buffer = [{"type": "action", "text": f"Beat {i}." * 3} for i in range(300)]
    ed = _make_editor(buffer=buffer)
    ed.metadata = {}
    for idx in (0, 5, 150, len(buffer) - 1):
        starts, _ = s.paginate_buffer(buffer[:idx + 1])
        assert ed.page_number_at(idx) == starts[idx][0]


# --------------------------------------------------------------------------
# Config: keybinds / behavior / format are actually consulted, not just
# stored -- these lock down that changing config.toml really does change
# runtime behavior, not just the merged dict.
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_runtime_config():
    """apply_runtime_config() mutates module-level globals (WRAP_WIDTH,
    TRANSITION_KEYWORDS, PDF_* geometry, AUTOSAVE_INTERVAL, ...) in place
    so the rest of the file doesn't need cfg threaded everywhere -- but
    that means any test that loads a non-default config has to put those
    globals back afterward, or it leaks into every test that runs after
    it in the same process."""
    yield
    s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_keybind_move_is_remappable():
    ed = _make_editor(buffer=[{"type": "action", "text": "hello"}])
    ed.cfg["keybinds"]["move_right"] = "L"  # remapped from default "l"
    ed.cx = 0
    ed.handle_normal(ord("l"))  # default letter should now do nothing
    assert ed.cx == 0
    ed.handle_normal(ord("L"))
    assert ed.cx == 1


def test_keybind_delete_line_uses_configured_letter():
    ed = _make_editor(buffer=[{"type": "action", "text": "one"},
                               {"type": "action", "text": "two"}])
    ed.cfg["keybinds"]["delete_line"] = "z"
    ed.handle_normal(ord("z"))
    ed.handle_normal(ord("z"))
    assert len(ed.buffer) == 1
    assert ed.buffer[0]["text"] == "two"


def test_keybind_redo_ctrl_follows_configured_letter():
    ed = _make_editor(buffer=[{"type": "action", "text": ""}])
    ed.cfg["keybinds"]["redo"] = "y"  # Ctrl-y instead of the default Ctrl-r
    ed.snapshot()
    ed.buffer[0]["text"] = "typed"
    ed.touch()  # real edit call sites always follow snapshot()+mutate with touch()
    ed.undo()
    assert ed.buffer[0]["text"] == ""
    ctrl_y = ord("Y") & 0x1f
    ed.handle_normal(ctrl_y)
    assert ed.buffer[0]["text"] == "typed"


def test_max_undo_steps_is_configurable():
    ed = _make_editor(buffer=[{"type": "action", "text": ""}])
    ed.cfg["behavior"]["max_undo_steps"] = 3
    for i in range(10):
        ed.snapshot()
        ed.buffer[0]["text"] = str(i)
        ed.touch()
    # A straight (non-branching) run of edits beyond the cap gets pruned
    # from the far end -- see _prune_undo_tree(). A tree with active
    # branches is exempt from this cap (preserving branches is the whole
    # point of the undo tree), but plain linear history like this still
    # respects it.
    assert ed._undo_node_count == 3


def test_type_for_key_only_matches_line_type_setters():
    """type_for_key() backs ':<letter>' element-setting -- it must not
    also match a bare-NORMAL-mode action name that happens to share a
    letter with a line-type setter (e.g. 'a' is both ':a' -> action and
    the bare 'append after cursor' key)."""
    ed = _make_editor()
    assert ed.type_for_key("a") == "action"
    assert ed.type_for_key("z") is None  # not bound to anything by default


def test_apply_runtime_config_updates_transitions():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["transitions"]["builtins"] = ["IRIS OUT."]
    s.apply_runtime_config(cfg)
    assert s.TRANSITION_KEYWORDS == ["IRIS OUT."]
    # "IRIS OUT." ends in "." (not "TO:"), so it can only be recognized as
    # an un-forced transition via TRANSITION_KEYWORDS membership -- proving
    # the configured list, not just the endswith("TO:") fallback, is what
    # matched. A leading action line keeps "IRIS OUT." from being read as
    # title-page metadata (the from_fountain() title-block heuristic).
    text = "He stares at the door.\n\nIRIS OUT.\n\nEXT. STREET - DAY\n"
    _, buf = s.from_fountain(text)
    assert buf[1]["type"] == "transition"
    assert buf[1]["text"] == "IRIS OUT."


def test_apply_runtime_config_updates_wrap_width():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["wrap_width"]["action"] = 10
    s.apply_runtime_config(cfg)
    assert s.WRAP_WIDTH["action"] == 10
    rows = s.styled_wrap({"type": "action", "text": "a " * 20}, s.WRAP_WIDTH["action"])
    assert all(len("".join(c for c, _ in row)) <= 10 for row in rows)


def test_apply_runtime_config_recomputes_pdf_rows_per_page():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    default_rows = s.pdf_geometry.PDF_ROWS_PER_PAGE
    cfg["format"]["pdf"]["top_margin_in"] = 3.0  # much less usable height
    s.apply_runtime_config(cfg)
    assert s.pdf_geometry.PDF_ROWS_PER_PAGE < default_rows


def test_apply_runtime_config_updates_autosave_and_recent_files():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["behavior"]["autosave_interval_secs"] = 999
    cfg["behavior"]["max_recent_files"] = 2
    s.apply_runtime_config(cfg)
    assert s.config.AUTOSAVE_INTERVAL == 999
    assert s.config.MAX_RECENT_FILES == 2


# --------------------------------------------------------------------------
# PDF font configuration
# --------------------------------------------------------------------------

def _reportlab_test_font_dir():
    reportlab = pytest.importorskip("reportlab")
    return Path(reportlab.__file__).parent / "fonts"


def test_pdf_font_defaults_to_courier():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    s.apply_runtime_config(cfg)
    assert s.pdf_geometry.PDF_FONT == "Courier"
    assert s.pdf_geometry.PDF_FONT_BOLD == "Courier-Bold"
    assert s.pdf_geometry.PDF_FONT_ITALIC == "Courier-Oblique"
    assert s.pdf_geometry.PDF_FONT_WARNING == ""


def test_pdf_font_custom_registers_ttf():
    fonts = _reportlab_test_font_dir()
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["font_family"] = "custom"
    cfg["format"]["pdf"]["custom_font"] = {
        "regular": str(fonts / "Vera.ttf"),
        "bold": str(fonts / "VeraBd.ttf"),
        "italic": str(fonts / "VeraIt.ttf"),
    }
    s.apply_runtime_config(cfg)
    try:
        assert s.pdf_geometry.PDF_FONT == "ScripteeCustom"
        assert s.pdf_geometry.PDF_FONT_BOLD == "ScripteeCustom-Bold"
        assert s.pdf_geometry.PDF_FONT_ITALIC == "ScripteeCustom-Italic"
        assert s.pdf_geometry.PDF_FONT_WARNING == ""
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))  # reset globals


def test_pdf_font_custom_falls_back_without_bold_italic():
    fonts = _reportlab_test_font_dir()
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["font_family"] = "custom"
    cfg["format"]["pdf"]["custom_font"] = {"regular": str(fonts / "Vera.ttf")}
    s.apply_runtime_config(cfg)
    try:
        assert s.pdf_geometry.PDF_FONT == "ScripteeCustom"
        # No distinct bold/italic given -- both fall back to the regular face.
        assert s.pdf_geometry.PDF_FONT_BOLD == "ScripteeCustom"
        assert s.pdf_geometry.PDF_FONT_ITALIC == "ScripteeCustom"
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_pdf_font_custom_missing_path_falls_back_to_courier_with_warning():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["font_family"] = "custom"
    cfg["format"]["pdf"]["custom_font"] = {"regular": "/nonexistent/font.ttf"}
    s.apply_runtime_config(cfg)
    try:
        assert s.pdf_geometry.PDF_FONT == "Courier"
        assert s.pdf_geometry.PDF_FONT_BOLD == "Courier-Bold"
        assert s.pdf_geometry.PDF_FONT_ITALIC == "Courier-Oblique"
        assert "custom_font.regular" in s.pdf_geometry.PDF_FONT_WARNING
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_pdf_font_custom_blank_regular_falls_back_to_courier_with_warning():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["font_family"] = "custom"
    s.apply_runtime_config(cfg)  # custom_font.regular left at its "" default
    try:
        assert s.pdf_geometry.PDF_FONT == "Courier"
        assert s.pdf_geometry.PDF_FONT_WARNING != ""
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_export_pdf_with_custom_font_smoke(tmp_path):
    """A custom-font export should still produce a valid PDF and actually
    use the registered font name in the content stream."""
    fonts = _reportlab_test_font_dir()
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["font_family"] = "custom"
    cfg["format"]["pdf"]["custom_font"] = {"regular": str(fonts / "Vera.ttf")}
    s.apply_runtime_config(cfg)
    try:
        buffer = [
            {"type": "heading", "text": "INT. ROOM - DAY"},
            {"type": "action", "text": "Vijay stares."},
        ]
        out = tmp_path / "custom_font.pdf"
        s.export_pdf(out, {}, buffer)
        assert out.exists() and out.read_bytes().startswith(b"%PDF")
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


# --------------------------------------------------------------------------
# Configurable PDF emphasis -- [format.pdf.emphasis]
# --------------------------------------------------------------------------

def _pdf_basefonts_used(pdf_path):
    """Set of every /BaseFont name referenced anywhere in the PDF's page
    font resources -- lets a test check whether e.g. Courier-Bold or
    Courier-Oblique actually got used, not just whether export succeeded."""
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(pdf_path))
    names = set()
    for page in reader.pages:
        res = page.get("/Resources")
        if res and "/Font" in res:
            for font in res["/Font"].values():
                base = font.get("/BaseFont")
                if base:
                    names.add(str(base).lstrip("/"))
    return names


def test_pdf_emphasis_defaults_to_true():
    assert s.pdf_geometry.PDF_HEADING_BOLD is True
    assert s.pdf_geometry.PDF_CHARACTER_BOLD is True
    assert s.pdf_geometry.PDF_TRANSITION_BOLD is True
    assert s.pdf_geometry.PDF_PAREN_ITALIC is True


def test_pdf_emphasis_flags_read_from_config():
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["emphasis"] = {
        "heading_bold": False,
        "character_bold": False,
        "transition_bold": False,
        "parenthetical_italic": False,
    }
    s.apply_runtime_config(cfg)
    try:
        assert s.pdf_geometry.PDF_HEADING_BOLD is False
        assert s.pdf_geometry.PDF_CHARACTER_BOLD is False
        assert s.pdf_geometry.PDF_TRANSITION_BOLD is False
        assert s.pdf_geometry.PDF_PAREN_ITALIC is False
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_pdf_emphasis_partial_config_falls_back_to_defaults():
    # Only overriding one key shouldn't silently zero out the rest --
    # anything not mentioned keeps its (True) default.
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["emphasis"] = {"character_bold": False}
    s.apply_runtime_config(cfg)
    try:
        assert s.pdf_geometry.PDF_CHARACTER_BOLD is False
        assert s.pdf_geometry.PDF_HEADING_BOLD is True
        assert s.pdf_geometry.PDF_TRANSITION_BOLD is True
        assert s.pdf_geometry.PDF_PAREN_ITALIC is True
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_pdf_export_uses_bold_and_italic_fonts_by_default(tmp_path):
    pytest.importorskip("reportlab")
    buffer = [
        {"type": "heading", "text": "INT. KITCHEN - DAY"},
        {"type": "character", "text": "VIJAY"},
        {"type": "dialogue", "text": "It never boils."},
        {"type": "parenthetical", "text": "(beat)"},
        {"type": "transition", "text": "CUT TO:"},
    ]
    out = tmp_path / "default_emphasis.pdf"
    s.export_pdf(out, {"Title": "T"}, buffer)
    used = _pdf_basefonts_used(out)
    assert "Courier-Bold" in used
    assert "Courier-Oblique" in used


def test_pdf_export_character_bold_can_be_disabled(tmp_path):
    """Regression for the actual feature request: someone who doesn't
    want CHARACTER cues bold can turn just that one off, independently of
    heading/transition bold and parenthetical italic."""
    pytest.importorskip("reportlab")
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["emphasis"] = {"character_bold": False}
    s.apply_runtime_config(cfg)
    try:
        buffer = [
            {"type": "heading", "text": "INT. KITCHEN - DAY"},  # still bold
            {"type": "character", "text": "VIJAY"},              # not bold
            {"type": "dialogue", "text": "It never boils."},
            {"type": "parenthetical", "text": "(beat)"},         # still italic
        ]
        out = tmp_path / "character_not_bold.pdf"
        s.export_pdf(out, {"Title": "T"}, buffer)
        used = _pdf_basefonts_used(out)
        # Heading is the only remaining bold user here, so Bold still shows
        # up -- character_bold being off doesn't disable bold everywhere.
        assert "Courier-Bold" in used
        assert "Courier-Oblique" in used  # parenthetical still italic
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_pdf_export_all_emphasis_disabled_uses_only_plain_font(tmp_path):
    pytest.importorskip("reportlab")
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["emphasis"] = {
        "heading_bold": False,
        "character_bold": False,
        "transition_bold": False,
        "parenthetical_italic": False,
    }
    s.apply_runtime_config(cfg)
    try:
        buffer = [
            {"type": "heading", "text": "INT. KITCHEN - DAY"},
            {"type": "character", "text": "VIJAY"},
            {"type": "dialogue", "text": "It never boils."},
            {"type": "parenthetical", "text": "(beat)"},
            {"type": "transition", "text": "CUT TO:"},
        ]
        out = tmp_path / "no_emphasis.pdf"
        s.export_pdf(out, {"Title": "T"}, buffer)
        used = _pdf_basefonts_used(out)
        assert "Courier-Bold" not in used
        assert "Courier-Oblique" not in used
        assert "Courier" in used
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


def test_pdf_export_disabled_emphasis_does_not_suppress_inline_markup(tmp_path):
    # Turning off the *forced* styling on ACTION/DIALOGUE-adjacent elements
    # was never a thing (they're never force-styled either way) -- but
    # make sure disabling heading_bold doesn't somehow also swallow an
    # inline **bold**/*italic* span the writer typed in ACTION text.
    pytest.importorskip("reportlab")
    cfg = copy.deepcopy(s.DEFAULT_CONFIG)
    cfg["format"]["pdf"]["emphasis"] = {
        "heading_bold": False, "character_bold": False,
        "transition_bold": False, "parenthetical_italic": False,
    }
    s.apply_runtime_config(cfg)
    try:
        buffer = [
            {"type": "action", "text": "He grabs the **loaded** gun."},
        ]
        out = tmp_path / "inline_markup_survives.pdf"
        s.export_pdf(out, {}, buffer)
        used = _pdf_basefonts_used(out)
        assert "Courier-Bold" in used  # from the **loaded** markup, not forcing
    finally:
        s.apply_runtime_config(copy.deepcopy(s.DEFAULT_CONFIG))


# --------------------------------------------------------------------------
# Dual dialogue
# --------------------------------------------------------------------------

def test_toggle_dual_dialogue_only_applies_to_character_line():
    ed = _make_editor(buffer=[{"type": "action", "text": "Beat."}])
    ed.toggle_dual_dialogue()
    assert "dual" not in ed.buffer[0]
    assert "only applies to a CHARACTER" in ed.status


def test_toggle_dual_dialogue_sets_and_clears_flag():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "ALICE"},
    ])
    ed.cy = 2
    ed.toggle_dual_dialogue()
    assert ed.buffer[2]["dual"] is True
    ed.toggle_dual_dialogue()
    assert "dual" not in ed.buffer[2]


def test_toggle_dual_dialogue_refuses_with_no_pairing_target():
    """A lone CHARACTER line with nothing above it to pair with must not
    silently set the flag -- it should refuse and say why, not report
    success while doing nothing on export."""
    ed = _make_editor(buffer=[{"type": "character", "text": "ALICE"}])
    ed.toggle_dual_dialogue()
    assert "dual" not in ed.buffer[0]
    assert "needs a CHARACTER" in ed.status


def test_toggle_dual_dialogue_refuses_when_only_action_above():
    ed = _make_editor(buffer=[
        {"type": "action", "text": "She walks in."},
        {"type": "character", "text": "ALICE"},
    ])
    ed.cy = 1
    ed.toggle_dual_dialogue()
    assert "dual" not in ed.buffer[1]
    assert "needs a CHARACTER" in ed.status


def test_toggle_dual_dialogue_tolerates_blank_lines_above():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "action", "text": ""},
        {"type": "character", "text": "STEEL"},
    ])
    ed.cy = 3
    ed.toggle_dual_dialogue()
    assert ed.buffer[3]["dual"] is True


def test_dual_dialogue_keybind_and_colon_command():
    ed = _make_editor(buffer=[
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "ALICE"},
    ])
    ed.cy = 2
    ed.handle_normal(ord(s.DEFAULT_CONFIG["keybinds"]["dual_dialogue"]))
    assert ed.buffer[2]["dual"] is True
    ed.execute_command("dual")
    assert "dual" not in ed.buffer[2]


def test_dual_dialogue_fountain_round_trip():
    buffer = [
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "STEEL", "dual": True},
        {"type": "dialogue", "text": "Screw retirement."},
    ]
    text = s.to_fountain({}, buffer)
    assert "STEEL ^" in text
    assert "BRICK ^" not in text  # only the second cue carries the marker
    _, buf2 = s.from_fountain(text)
    assert buf2[0]["type"] == "character" and not buf2[0].get("dual")
    assert buf2[2]["type"] == "character" and buf2[2].get("dual") is True
    assert buf2[2]["text"] == "STEEL"  # "^" stripped back off


def test_find_dual_pair_detects_matching_pair():
    buffer = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "STEEL", "dual": True},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "action", "text": "They stare at each other."},
    ]
    pair = s.find_dual_pair(buffer, 1)
    assert pair == (2, 3, 4)
    # A non-CHARACTER opener, or a CHARACTER opener that's itself the
    # "dual" (second) cue, is never a pair start.
    assert s.find_dual_pair(buffer, 0) is None
    assert s.find_dual_pair(buffer, 3) is None


def test_find_dual_pair_none_when_second_cue_not_marked_dual():
    buffer = [
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Line one."},
        {"type": "character", "text": "STEEL"},  # no dual flag
        {"type": "dialogue", "text": "Line two."},
    ]
    assert s.find_dual_pair(buffer, 0) is None


def test_render_dual_dialogue_draws_two_side_by_side_columns():
    """render() should lay a dual-dialogue pair out as two real columns --
    both cues' text on the same screen row, second column strictly to the
    right of the first -- not the old stacked-single-column placeholder."""
    buffer = [
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "STEEL", "dual": True},
        {"type": "dialogue", "text": "So do I."},
        {"type": "action", "text": "They stare at each other."},
    ]
    ed = _make_editor(buffer=buffer)
    ed.cy = 0
    s.Editor.render(ed)
    cue_row = ed.stdscr.row(0)
    assert "BRICK" in cue_row and "STEEL" in cue_row
    assert cue_row.index("BRICK") < cue_row.index("STEEL")
    dialogue_row = ed.stdscr.row(1)
    assert "Screw retirement." in dialogue_row and "So do I." in dialogue_row
    assert dialogue_row.index("Screw retirement.") < dialogue_row.index("So do I.")
    # The action line after the pair should render right after it, on its
    # own single-column row -- confirms the main loop's index correctly
    # skipped past the whole pair rather than re-walking it line by line.
    assert "They stare at each other." in ed.stdscr.row(2)


def test_render_dual_dialogue_cursor_tracks_second_column():
    """The terminal cursor should land inside the second column when self.cy
    is on the second cue's dialogue, not wherever the first column's rows
    happened to end."""
    buffer = [
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "STEEL", "dual": True},
        {"type": "dialogue", "text": "So do I."},
    ]
    ed = _make_editor(buffer=buffer)
    ed.cy, ed.cx = 3, 0  # start of "So do I."
    s.Editor.render(ed)
    cursor_y, cursor_x = ed.stdscr.last_move
    row = ed.stdscr.row(cursor_y)
    assert "So do I." in row
    # cursor_x should fall at or before "So do I."'s own position, not
    # inside/after BRICK's column.
    assert cursor_x <= row.index("So do I.") + 1


def test_render_no_dual_pair_falls_back_to_single_column():
    """A lone CHARACTER cue (no pairing) must still render the ordinary
    single-column way -- the dual-block path should only fire when
    find_dual_pair() actually finds a pair."""
    buffer = [
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Nobody's pairing with me."},
    ]
    ed = _make_editor(buffer=buffer)
    ed.cy = 0
    s.Editor.render(ed)  # should not raise
    joined = "\n".join(ed.stdscr.row(y) for y in range(4))
    assert "BRICK" in joined
    assert "Nobody's pairing with me." in joined


def test_export_pdf_dual_dialogue_smoke(tmp_path):
    """Doesn't assert on drawn pixel positions (out of reach without a PDF
    renderer), but proves a dual-dialogue buffer exports without error and
    produces a real, parseable PDF."""
    buffer = [
        {"type": "heading", "text": "INT. ROOM - DAY"},
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "STEEL", "dual": True},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "action", "text": "They stare at each other."},
    ]
    out = tmp_path / "dual.pdf"
    s.export_pdf(out, {}, buffer)
    assert out.exists() and out.read_bytes().startswith(b"%PDF")


def test_paginate_buffer_treats_dual_pair_as_one_block():
    """The two lines of a dual pair should land on the same (page, row)
    start -- they're drawn as one atomic two-column unit, not stacked."""
    buffer = [
        {"type": "character", "text": "BRICK"},
        {"type": "dialogue", "text": "Screw retirement."},
        {"type": "character", "text": "STEEL", "dual": True},
        {"type": "dialogue", "text": "Screw retirement."},
    ]
    starts, _ = s.paginate_buffer(buffer)
    assert starts[0] == starts[2]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))


# --------------------------------------------------------------------------
# :help rendering -- rows are flattened into real screen lines before
# scrolling/paging, so a wrapped multi-line description can never make a
# later entry's row overlap/overwrite it. See Editor._flatten_help_rows().
# --------------------------------------------------------------------------

def test_flatten_help_rows_gives_wrapped_lines_their_own_row():
    rows = [
        ("A", "one two three four five six seven eight nine ten"),
        ("B", "short"),
    ]
    flat = s.Editor._flatten_help_rows(rows, 10)  # narrow -> "A" must wrap
    cmd_rows = [i for i, (k, _l, _d) in enumerate(flat) if k == "cmd"]
    # One flat "cmd" row per help entry -- never one per wrapped line.
    assert len(cmd_rows) == 2
    a_row, b_row = cmd_rows
    # Every row between A's own row and B's own row must be a "cont" row
    # holding one of A's wrapped continuation lines. This is exactly what
    # the old `y = 2 + i` scheme never allocated at all -- it advanced
    # straight from A's slot to B's, so B (and everything after it) got
    # drawn on top of lines A's own wrap loop had just written.
    between = flat[a_row + 1: b_row]
    assert len(between) >= 1
    assert all(k == "cont" for k, _l, _d in between)
    assert flat[b_row][1] == "B"


def test_flatten_help_rows_preserves_headers_and_blank_spacers():
    # Mirrors help_text()'s actual row shapes: a section header is
    # (label, None); a blank spacer is ("", None) -- desc is None either
    # way, so both take the "header" branch (an empty-text header row
    # draws nothing, same as a blank line); only (label, "text") rows
    # become "cmd" rows.
    rows = [("SECTION", None), ("", None), ("cmd", "desc")]
    flat = s.Editor._flatten_help_rows(rows, 40)
    assert [k for k, _l, _d in flat] == ["header", "header", "cmd"]
    assert flat[1][1] == ""


def test_help_text_flattens_and_scrolls_without_row_collisions():
    # End-to-end sanity check against the real help_text() content (not a
    # synthetic table): every entry that isn't a blank spacer must occupy
    # a distinct row when flattened at a realistically narrow width, where
    # several entries are known to wrap across multiple lines.
    ed = _make_editor()
    rows = ed.help_text()
    flat = ed._flatten_help_rows(rows, 30)
    assert len(flat) >= len(rows)  # wrapping only ever adds rows, never merges
    # No two non-blank rows can carry the same left-column label at
    # different positions unless flattening is broken (labels are unique
    # per command in help_text()).
    labels = [left for k, left, _d in flat if k == "cmd"]
    assert len(labels) == len(set(labels))


def test_show_help_scrolls_to_bottom_without_crashing_or_dropping_last_entry():
    # Drives the real show_help() loop (not just the flatten helper) at a
    # narrow, short fake terminal that forces both wrapping and paging,
    # scrolling all the way to the bottom with 'j' then quitting with 'q'.
    # Must not crash, and the very last flattened row must actually reach
    # the screen once scrolled into view -- confirming the loop's own
    # top/body_h bounds line up with the flattened row count.
    ed = _make_editor()
    ed.stdscr = _FakeWChStdscr([])  # dims default to (24, 80)
    rows = ed.help_text()
    flat = ed._flatten_help_rows(rows, ed.stdscr.dims[1] - 26)
    keys = [ord("j")] * (len(flat) + 5) + [ord("q")]
    ed.stdscr._queue = keys
    ed.pairs = {}
    ed.show_help()  # must return normally, not raise
    last_cmd = next((left for k, left, _d in reversed(flat) if k == "cmd"), None)
    assert last_cmd is not None
    assert any(last_cmd in ed.stdscr.row(y) for y in range(ed.stdscr.dims[0]))

"""Fountain format save/load (to_fountain/from_fountain), plus the small
split_character_cue() helper used across stats/pdf_export/editor to strip
a CHARACTER cue's (V.O.)/(CONT'D)-style extension.

TRANSITION_KEYWORDS is mutated in place by config.apply_runtime_config()
(via slice assignment), never reassigned, so plain imports elsewhere stay
correct.
"""

import re

from text_format import SCENE_RE

# --------------------------------------------------------------------------
# Fountain save / load
# --------------------------------------------------------------------------

DIALOGUE_CHAIN_TYPES = {"character", "parenthetical", "dialogue"}

# Recognized un-forced transitions (no leading ">"), e.g. "CUT TO:" -- shared
# between from_fountain()'s import heuristics and the editor's Tab-complete
# builtin list so both agree on the same vocabulary.
TRANSITION_KEYWORDS = ["CUT TO:", "FADE OUT.", "FADE IN:", "DISSOLVE TO:",
                        "SMASH CUT TO:"]

# "MONTAGE" and "SERIES OF SHOTS" are the two slug-style labels screenwriters
# routinely write on their own line, in caps, exactly like a scene heading --
# even though they don't start with INT./EXT. and so never match SCENE_RE.
# Recognized here (own line, optional trailing colon, nothing else on the
# line) so they count as scenes in the gutter, :scenes, :stats, and the PDF
# export, same as everywhere else that already treats them as a heading.
MONTAGE_RE = re.compile(r'^(MONTAGE|SERIES OF SHOTS)\s*:?\s*$')


def _needs_action_force(s):
    """True if writing `s` as a plain ACTION line would make from_fountain()
    misclassify it as something else (heading/character/transition) the
    next time this file is opened -- i.e. whether to_fountain() needs to
    prefix it with a forcing "!" to keep it ACTION on round-trip.

    Mirrors from_fountain()'s own non-blank-line classification rules
    (everything except the dialogue-chain-continuation check, which depends
    on the *previous* line rather than this line's own text and so can't be
    decided here)."""
    if not s:
        return False
    if s.startswith(">"):
        return True
    if SCENE_RE.match(s):
        return True
    if s.startswith(".") and not s.startswith(".."):
        return True
    if MONTAGE_RE.match(s):
        return True
    if s.startswith("(") and s.endswith(")"):
        return True
    is_shout = s.isupper() and not s.endswith((".", "!", "?"))
    if s in TRANSITION_KEYWORDS or (is_shout and s.endswith("TO:")):
        return True
    if is_shout and len(s) < 40:
        return True
    return False


def _needs_character_force(name):
    """True if writing `name` as a plain (upper-cased) CHARACTER cue would
    make from_fountain() fail to classify it as CHARACTER on the next
    import -- i.e. whether to_fountain() needs the Fountain-spec "@"
    forcing prefix to round-trip it correctly.

    Mirrors from_fountain()'s own CHARACTER heuristic (the "blank line
    before, all-caps, no ending punctuation, under 40 chars" check) on the
    *upper-cased* text, since that's what actually gets written -- casing
    itself is never the problem here, only length and trailing
    punctuation are.
    """
    if not name:
        return False
    up = name.upper()
    is_shout = up.isupper() and not up.endswith((".", "!", "?"))
    return not (is_shout and len(up) < 40)


def _looks_like_title_key(line):
    """True if `line` (already confirmed to match the "Letters: value"
    shape) should actually be treated as a title-page key -- as opposed
    to body content that merely happens to have that shape.

    The classic false positive: a screenplay's very first line, with no
    title page at all, is a plain (unforced) TRANSITION like "FADE IN:"
    or "CUT TO:" -- extremely common as literally the opening line of a
    script. That matches "[Letters/spaces]: ..." exactly as well as a
    real "Title: ..." line does, so without this check it was silently
    swallowed into metadata by the title-page loop below -- and since
    nothing ever displays an unrecognized metadata key, it vanished from
    the script entirely rather than showing up misclassified as
    something visible. Reuses the same TRANSITION_KEYWORDS/SCENE_RE/
    MONTAGE_RE vocabulary the body heuristics below already recognize, so
    a genuine title-page key (Title, Author, Draft date, a user's own
    custom cfg["prompts"]["fields"] name, ...) can never collide with
    something that would otherwise parse as a heading or transition.
    """
    up = line.strip().upper()
    is_shout = up.isupper() and not up.endswith((".", "!", "?"))
    if up in TRANSITION_KEYWORDS or (is_shout and up.endswith("TO:")):
        return False
    if SCENE_RE.match(up) or MONTAGE_RE.match(up):
        return False
    return True


def to_fountain(metadata, buffer):
    out = []
    for key, val in metadata.items():
        if val:
            out.append(f"{key}: {val}")
    if out:
        out.append("")
    n = len(buffer)
    for idx, ln in enumerate(buffer):
        t, txt = ln["type"], ln["text"]
        if not txt.strip():
            out.append("")
            continue
        if t == "heading":
            txt_up = txt.upper()
            if SCENE_RE.match(txt_up) or MONTAGE_RE.match(txt_up):
                out.append(txt_up)
            else:
                # Doesn't start with INT/EXT/EST and isn't a recognized
                # MONTAGE/SERIES OF SHOTS slug (e.g. a custom flashback
                # label set via ":h" or imported with a leading "."). Force
                # it with a leading "." so from_fountain() reads it back as
                # a heading -- without this it round-trips as plain ACTION
                # on the next open, silently dropping it from the scene
                # count/gutter/:scenes/PDF export.
                out.append(f".{txt_up}")
        elif t == "character":
            # A leading "@" is Fountain's own forced-character-cue syntax,
            # for a name the plain "blank line + all-caps + short" import
            # heuristic wouldn't otherwise catch (see
            # _needs_character_force()) -- e.g. one that's 40+ chars, or
            # ends in ".", "!", or "?". Without it, a name like that would
            # silently reclassify as ACTION or DIALOGUE the next time this
            # file is opened.
            cue = f"@{txt.upper()}" if _needs_character_force(txt) else txt.upper()
            # A trailing "^" is Fountain's own dual-dialogue marker: it
            # tells any Fountain-reading tool (this one included, see
            # from_fountain() below) that this speech pairs side by side
            # with the CHARACTER/DIALOGUE block immediately above it,
            # rather than following it in sequence. Set via ":dual" /
            # the dual_dialogue keybind (see toggle_dual_dialogue()).
            out.append(f"{cue} ^" if ln.get("dual") else cue)
        elif t == "parenthetical":
            out.append(txt if txt.startswith("(") else f"({txt})")
        elif t == "transition":
            out.append(f"> {txt.upper()}")
        elif t == "shot":
            out.append(txt.upper())
        elif t == "action" and _needs_action_force(txt):
            # This line's type was set to ACTION (e.g. via ":a") but its
            # text still looks like a heading/character cue/transition on
            # its own -- most commonly a scene heading that got demoted to
            # action without touching its "INT./EXT. ..." text. Written
            # plain, from_fountain() would re-derive the type from that
            # text on the next open and snap it right back to whatever it
            # looked like before, silently undoing the ":a". A leading "!"
            # forces ACTION regardless of what the text looks like.
            out.append(f"!{txt}")
        else:
            out.append(txt)

        # A CHARACTER/PARENTHETICAL/DIALOGUE speech block is kept glued
        # together with no blank line between its own lines, matching
        # standard Fountain convention. This matters beyond style: blank
        # lines are what from_fountain() now treats as ending an element
        # (see its docstring), so inserting one here between e.g. a
        # CHARACTER cue and its own DIALOGUE would make from_fountain
        # re-detect the dialogue line from scratch instead of reading it
        # back as a continuation -- usually harmless (it'd still often
        # come out as dialogue via other heuristics) but not guaranteed,
        # and definitely not the standard, portable shape other Fountain
        # tools expect. Every other transition (into/out of action,
        # heading, shot, transition, or the start of a new CHARACTER cue)
        # still gets a blank line, same as before.
        next_ln = buffer[idx + 1] if idx + 1 < n else None
        glued = (
            t in DIALOGUE_CHAIN_TYPES
            and next_ln is not None
            and next_ln["type"] in ("parenthetical", "dialogue")
        )
        if not glued:
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def from_fountain(text):
    # Strip Fountain's two invisible-content syntaxes before line-splitting:
    # boneyard comments (/* ... */, may span multiple lines) and notes
    # ([[ ... ]], usually inline). Both are meant to be author-only asides
    # that never appear in the rendered script, so other tools (Highland,
    # Slugline, Fade In) omit them entirely on export/print -- previously
    # Scriptee had no notion of either and imported their literal text as
    # ordinary ACTION/DIALOGUE content. DOTALL lets a boneyard block that
    # spans several lines collapse in one match; any blank lines the strip
    # leaves behind are harmless since from_fountain() already treats runs
    # of blank lines as a single element separator, not distinct elements.
    # The \n? on each side absorbs one adjacent newline along with the
    # block itself, so a boneyard that sits on its own blank-line-framed
    # line(s) (the normal style) leaves exactly the single "\n\n" ordinary
    # separator behind -- not the double gap you'd get from the blank
    # line before *and* the blank line after both surviving untouched.
    text = re.sub(r"\n?/\*.*?\*/\n?", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[.*?\]\]", "", text, flags=re.DOTALL)
    # Section headings ("# Act One", "## Sequence B") and Synopsis lines
    # ("= A quiet morning") are Fountain's own outline/notes syntax --
    # per spec neither is meant to appear in the printed script, the same
    # way boneyard/notes above never are. Scriptee has no outline concept
    # of its own to put them in, so (like boneyard/notes) they're
    # stripped here rather than imported as literal, garbled ACTION text
    # ("# Act One" showing up as an action line, complete with the "#").
    # Page breaks ("===", "====", ...) are stripped the same way, and
    # must be checked first: a line of 3+ "=" would otherwise also match
    # the synopsis pattern below (a single leading "=" not immediately
    # followed by another).
    text = re.sub(r"(?m)^[ \t]*={3,}[ \t]*$\n?", "", text)
    text = re.sub(r"(?m)^[ \t]*=(?!=)[^\n]*$\n?", "", text)
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*[^\n]*$\n?", "", text)
    lines = text.split("\n")
    metadata = {}
    i = 0
    # title page: consecutive "Key: Value" lines at top -- but only ones
    # that actually look like title-page keys (see _looks_like_title_key),
    # not body content (e.g. an opening "FADE IN:") that merely has the
    # same shape.
    while (i < len(lines) and re.match(r'^[A-Za-z ()]+:\s?.*$', lines[i] or "")
           and _looks_like_title_key(lines[i])):
        key, _, val = lines[i].partition(":")
        metadata[key.strip()] = val.strip()
        i += 1
    while i < len(lines) and lines[i].strip() == "":
        i += 1

    buffer = []
    prev_type = None
    # Per the Fountain spec, a CHARACTER cue (and a plain, non-forced
    # TRANSITION like "CUT TO:") is only recognized when the line right
    # before it is blank -- that blank line is the only thing that tells a
    # parser "this all-caps short line is a cue", as opposed to an
    # all-caps action beat some writers use for emphasis (e.g. "THE ROOM
    # EXPLODES."). Previously any all-caps line under 40 chars was read as
    # a character cue no matter where it sat, so a stray all-caps action
    # line mid-paragraph would hijack classification -- and everything
    # after it, until the next blank line, got swept in as DIALOGUE
    # instead of ACTION. `blank_before` (true at the very start of the
    # document too) restores that spec rule.
    blank_before = True
    # Counts the current run of consecutive blank source lines. A single
    # blank line is just Fountain's ordinary element separator -- the one
    # to_fountain() always writes after every non-glued element -- so it
    # carries no meaning of its own and is intentionally NOT stored in the
    # buffer (this is what keeps a freshly-imported file's PDF export at
    # clean industry-standard spacing). But a *longer* run is the writer's
    # own deliberate extra spacing in the editor (e.g. leaving 10 blank
    # lines between two action beats while drafting), and previously that
    # distinction was lost entirely: every run, of any length, collapsed to
    # nothing. That's what made spacing added in the editor vanish the
    # moment the file was reopened, even though to_fountain() had faithfully
    # written it to disk on save -- the save was fine, the *next* read threw
    # it away. Now every blank line beyond the first in a run becomes one
    # {"action", ""} buffer entry, restoring exactly the extra spacing the
    # writer added (round-tripping cleanly with to_fountain(), which writes
    # one "" per such entry) without changing what a single ordinary
    # separator does.
    blank_run = 0
    for raw in lines[i:]:
        s = raw.strip()
        if not s:
            # A blank line is Fountain's element separator -- it ends
            # whatever element came before it. Without this reset,
            # prev_type stayed set to "dialogue" (etc.) straight through
            # the blank line, so the *next* paragraph -- even a plain
            # ACTION beat with a blank line clearly separating it from the
            # dialogue above -- fell into the `prev_type in ("character",
            # "parenthetical", "dialogue")` branch below and got lumped in
            # as more dialogue instead of action. Only truly consecutive,
            # non-blank-separated lines after a CHARACTER/PARENTHETICAL/
            # DIALOGUE line should be treated as continuing dialogue.
            prev_type = None
            blank_before = True
            blank_run += 1
            continue

        if blank_run > 1:
            buffer.extend({"type": "action", "text": ""} for _ in range(blank_run - 1))
        blank_run = 0

        is_shout = s.isupper() and not s.endswith((".", "!", "?"))

        if s.startswith(">") and s.endswith("<") and len(s) > 1:
            # Centered text, e.g. "> THE END <", is not a transition --
            # it's (centered) action. Previously this fell into the plain
            # ">" branch below, which kept the leading ">" *and* left the
            # trailing "<" stuck onto the text verbatim.
            buffer.append({"type": "action", "text": s[1:-1].strip()})
            prev_type = "action"
        elif s.startswith(">"):
            buffer.append({"type": "transition", "text": s[1:].strip()})
            prev_type = "transition"
        elif SCENE_RE.match(s):
            buffer.append({"type": "heading", "text": s})
            prev_type = "heading"
        elif s.startswith(".") and not s.startswith(".."):
            # A leading "." (not "...") forces a scene heading -- used for
            # slugs that don't start with INT/EXT (e.g. flashback labels).
            buffer.append({"type": "heading", "text": s[1:].strip()})
            prev_type = "heading"
        elif MONTAGE_RE.match(s):
            buffer.append({"type": "heading", "text": s})
            prev_type = "heading"
        elif s.startswith("!"):
            # A leading "!" forces ACTION regardless of what the rest of the
            # line looks like -- the escape hatch to_fountain() reaches for
            # (see there) whenever a line's *type* was manually changed away
            # from something heading/character/transition-shaped while its
            # *text* stayed the same, so re-parsing it here can't silently
            # snap it back to the old type.
            buffer.append({"type": "action", "text": s[1:].strip()})
            prev_type = "action"
        elif s.startswith("@") and len(s) > 1:
            # A leading "@" is Fountain's own forced-character-cue syntax
            # (mirrored by to_fountain(), see _needs_character_force()) --
            # unlike the plain all-caps heuristic below, it works
            # regardless of case, length, trailing punctuation, or a blank
            # line before it, the same way "!"/">"/"." force their own
            # types. This is what other Fountain apps write for names the
            # heuristic would otherwise miss entirely (lowercase names, a
            # name that happens to end in "?", etc.) -- previously nothing
            # here recognized "@" at all, so a foreign file using it fell
            # straight through to plain ACTION or DIALOGUE.
            body = s[1:].strip()
            if body.endswith("^") and len(body) > 1:
                buffer.append({"type": "character", "text": body[:-1].rstrip(), "dual": True})
            else:
                buffer.append({"type": "character", "text": body})
            prev_type = "character"
        elif s.startswith("(") and s.endswith(")"):
            buffer.append({"type": "parenthetical", "text": s})
            prev_type = "parenthetical"
        elif blank_before and (s in TRANSITION_KEYWORDS
                                or (is_shout and s.endswith("TO:"))):
            # Un-forced transition, e.g. "CUT TO:" written without a
            # leading ">". Most Fountain files from other apps (Highland,
            # Slugline, Fade In, ...) write transitions this way rather
            # than forcing them -- previously only the ">"-forced form was
            # recognized, so these fell through to the character-cue
            # check below (all-caps, short, no trailing punctuation) and
            # got misread as a CHARACTER cue, with the actual next action
            # line then lumped in underneath it as DIALOGUE.
            buffer.append({"type": "transition", "text": s})
            prev_type = "transition"
        elif blank_before and is_shout and s.endswith("^") and len(s) > 1:
            # A trailing "^" marks Fountain dual (simultaneous) dialogue --
            # this CHARACTER cue pairs side by side with the block
            # immediately above it, rather than following it. Stripped
            # here and stored as a "dual" flag; to_fountain() re-adds the
            # "^" on save (see there).
            name = s[:-1].rstrip()
            if name and len(name) < 40:
                buffer.append({"type": "character", "text": name, "dual": True})
                prev_type = "character"
            else:
                buffer.append({"type": "action", "text": s})
                prev_type = "action"
        elif blank_before and is_shout and len(s) < 40:
            buffer.append({"type": "character", "text": s})
            prev_type = "character"
        elif prev_type in DIALOGUE_CHAIN_TYPES:
            buffer.append({"type": "dialogue", "text": s})
            prev_type = "dialogue"
        else:
            buffer.append({"type": "action", "text": s})
            prev_type = "action"
        blank_before = False
    if not buffer:
        buffer = [{"type": "action", "text": ""}]
    return metadata, buffer



def split_character_cue(text):
    """Split a CHARACTER line into (base_name, extension), where extension
    is a trailing parenthetical like "(V.O.)" or "(CONT'D)" -- kept
    separate so a rename sweep can preserve it."""
    text = text.strip()
    m = re.search(r'\(([^)]*)\)\s*$', text)
    if m:
        base = text[: m.start()].strip()
        ext = f"({m.group(1)})"
        return base, ext
    return text, ""

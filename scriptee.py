#!/usr/bin/env python3
"""
Scriptee — a vim-motion TUI screenwriter for Linux.

Files are stored in Fountain format (plain-text, industry standard),
so they're git-diffable and open in other screenwriting tools too.

Run:  python3 scriptee.py
"""

import curses
import locale
import os
import re
import copy
import glob
import shlex
import sys
import textwrap
import time
import uuid
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # Python < 3.11 fallback (installed by install.sh)
    except ImportError:
        tomllib = None

try:
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    HAVE_REPORTLAB = True
except ImportError:
    HAVE_REPORTLAB = False

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config" / "scriptee"
CONFIG_PATH = CONFIG_DIR / "config.toml"
RECOVERY_DIR = CONFIG_DIR / "recovery"
# Fallback values only -- once a config is loaded, apply_runtime_config()
# below overwrites these module-level names from cfg["behavior"], so every
# reader of AUTOSAVE_INTERVAL/MAX_RECENT_FILES sees the configured value
# without needing cfg threaded into every call site.
AUTOSAVE_INTERVAL = 15  # seconds of dirty, idle-tolerant editing between autosaves
MAX_RECENT_FILES = 15

DEFAULT_CONFIG = {
    "general": {
        "save_dir": str(Path.home() / "Documents" / "Scriptee"),
        "pdf_scene_numbers": True,
        # Master on/off for the PDF cover page, independent of whatever's
        # in metadata. False means :pdf never draws one, full stop -- for
        # "I never want a cover page" (as opposed to
        # prompt_missing_titlepage below, which is only about the prompt,
        # not the page itself). :cover still works either way, so
        # metadata can be filled in ahead of flipping this back on.
        "cover_page": True,
        # If a script has no title page at all (e.g. a Fountain file
        # imported from elsewhere that never had one) and hits :pdf, ask
        # once for the same fields [n]ew screenplays get -- otherwise the
        # export silently comes out with no cover page. Set to false to
        # never ask and just export without one. Has no effect if
        # cover_page above is false.
        "prompt_missing_titlepage": True,
    },
    "prompts": {
        "fields": ["Title", "Author", "Genre", "Year",
                   "Contact (Number)", "Contact (Email)"],
    },
    "sides": {
        # Title page for a *scoped* ":pdf" export -- a scene range
        # ("sides") or ":pdf char NAME" (a character's draft). Entirely
        # independent of [general] cover_page/prompt_missing_titlepage,
        # which only ever govern the full-script export.
        #
        # Master on/off: whether a scoped export gets a title page at
        # all. false means scene-range and character exports never draw
        # one, no matter what's set below.
        "cover_page": True,
        # Ask for a custom title before each scoped export -- just the
        # one field, not the full [prompts] fields list, since a sides
        # PDF keeps the rest of the cover page (Author, Contact, ...) as
        # whatever's already set for the full script. Leaving the prompt
        # blank + Enter auto-generates one from the *_format strings
        # below. false skips the prompt entirely and always auto-generates.
        "prompt_title": True,
        # Auto-generated title for a scene-range export when the prompt
        # above is left blank (or skipped). {start}/{end} are the
        # first/last scene numbers in the exported range.
        "title_format": "Sides (Scene {start} - Scene {end})",
        # Used instead of title_format when the range is a single scene,
        # so it reads "Sides (Scene 4)" rather than "Sides (Scene 4 -
        # Scene 4)". {start} and {end} are both that one scene number.
        "title_format_single": "Sides (Scene {start})",
        # Auto-generated title for a ":pdf char NAME" export. {name} is
        # the character name exactly as typed at the prompt.
        "character_title_format": "Sides - {name} Draft",
        # Small line under the sides title naming which script it's
        # excerpted from, e.g. "(an excerpt from Beach House)" -- drawn
        # whether the title above was typed or auto-generated. Uses
        # whatever's already in the full script's own Title field (see
        # :cover); skipped automatically when that's empty (e.g. an
        # imported .fountain with no title page at all) since there's no
        # script title to name. Set to false to never draw this line.
        "excerpt_note": True,
        "excerpt_format": "(an excerpt from {title})",
    },
    "character_export": {
        # Which non-cue line types are also scanned for a character's name
        # (or alias) when resolving ":pdf char NAME" -- a scene is pulled
        # in if the name shows up as a whole word, case-insensitively, in
        # any of these, *in addition to* a matching CHARACTER cue (that
        # exact-name check always happens and isn't gated by this list).
        # "dialogue" here is what makes a scene get included just because
        # some *other* character mentions the name in their line -- so a
        # sides PDF still has full context even for scenes where the
        # requested character never appears themselves. Valid values:
        # "action", "dialogue", "parenthetical", "shot", "transition".
        # On by default; trim the list to narrow what counts as a match.
        "search_in": ["action", "dialogue"],
        # Per-character alternate names/nicknames -- e.g. a character
        # CHARACTER-cued as DANNY but called "Dan" by other characters in
        # dialogue/action. Keys are matched case-insensitively against the
        # name given to ":pdf char", so { "DANNY": ["Dan"] } means both
        # ":pdf char Danny" and ":pdf char Dan" pull in every scene that
        # mentions either name. Empty by default -- add entries for
        # whichever characters actually get nicknamed in your script.
        "aliases": {},
    },
    "behavior": {
        # Seconds of dirty, idle-tolerant editing between autosave snapshots.
        "autosave_interval_secs": 15,
        # Flat undo/redo stack depth (see Editor.snapshot()).
        "max_undo_steps": 50,
        # How many paths the "recent files" list at the open-file screen keeps.
        "max_recent_files": 15,
        # ncurses' ESCDELAY, in ms -- how long a lone Esc byte waits to see if
        # it's the start of a longer escape sequence. Low by design (see the
        # startup comment near ESCDELAY); raise it if your terminal/multiplexer
        # sends multi-byte Alt-sequences scriptee doesn't otherwise expect.
        "esc_delay_ms": 25,
    },
    "keybinds": {
        # Line-type setters: ":" + this letter, in NORMAL mode.
        "heading": "h", "action": "a", "character": "c",
        "dialogue": "d", "parenthetical": "p", "shot": "s",
        "transition": "t",
        # Bare NORMAL-mode keys (no leading ":"). These share letters with
        # the line-type setters above on purpose -- e.g. "a" is both
        # "append after cursor" bare and ":a" (set line to ACTION) -- since
        # they're only ever read in different modes and never collide.
        "insert_before": "i", "insert_after": "a",
        "open_below": "o", "open_above": "O",
        "move_left": "h", "move_down": "j", "move_up": "k", "move_right": "l",
        "delete_char": "x", "delete_line": "d",
        "undo": "u",
        "redo": "r",  # held with Ctrl automatically, i.e. Ctrl-r
        "search": "/", "next_match": "n",
        "command": ":", "repeat": ".",
        "jump_end": "G", "toggle_readonly": "e",
        # Toggle whether the current CHARACTER line pairs with the dialogue
        # block above it as Fountain dual (simultaneous) dialogue.
        "dual_dialogue": "D",
    },
    "colors": {
        "heading": "yellow", "character": "cyan", "dialogue": "white",
        "transition": "magenta", "action": "white", "shot": "green",
        "accent": "blue", "parenthetical": "white",
    },
    "transitions": {
        # Un-forced (no leading ">") transition text recognized on import
        # and offered by Tab-complete, e.g. "CUT TO:" -- see
        # TRANSITION_KEYWORDS. Add house-style transitions here.
        "builtins": ["CUT TO:", "FADE OUT.", "FADE IN:", "DISSOLVE TO:",
                     "SMASH CUT TO:"],
    },
    "format": {
        # Terminal column widths and left-indents per element -- the
        # in-editor equivalent of the [format.pdf] margins below. See the
        # comment on WRAP_WIDTH/INDENT: these are independently editable
        # (not derived from format.pdf) so any character width you want is
        # possible, but changing one side without the other means the
        # terminal view and the exported PDF will wrap differently.
        "wrap_width": {
            "heading": 60, "action": 60, "shot": 60,
            "character": 40, "parenthetical": 32, "dialogue": 40,
            "transition": 74,
        },
        "indent": {
            "heading": 0, "action": 0, "shot": 0,
            "character": 22, "parenthetical": 18, "dialogue": 10,
            # transition has no indent entry -- it's right-aligned instead.
        },
        "pdf": {
            "font_size": 12,
            "page_size": "letter",  # "letter" or "a4"
            # "courier" uses the PDF spec's built-in base-14 Courier family
            # (zero embedding, works everywhere). "custom" registers the
            # TrueType files named under custom_font below instead -- e.g.
            # to use Courier Prime, a free font designed to match Courier's
            # own metrics but with cleaner, less cramped-looking letterforms
            # (especially bold). write_default_config() auto-fills this with
            # Scriptee's bundled Courier Prime when it finds one -- see
            # _bundled_courier_prime().
            "font_family": "courier",  # "courier" or "custom"
            "custom_font": {
                "regular": "",      # required if font_family = "custom"
                "bold": "",         # optional -- falls back to regular
                "italic": "",       # optional -- falls back to regular
            },
            "left_edge_in": 1.5,           # heading / action / shot
            "dialogue_left_in": 2.5,
            "parenthetical_left_in": 2.8,
            "character_left_in": 3.5,
            "right_margin_in": 1.0,
            "top_margin_in": 1.0,
            "bottom_margin_in": 1.0,
            # Gap between the two columns when printing dual dialogue.
            "dual_dialogue_gutter_in": 0.3,
            # Which elements are force-styled on :pdf export, regardless of
            # any inline **bold**/*italic* markup (or lack of it) typed in
            # the source text -- these are the standard screenplay-format
            # conventions (scene headings/character cues/transitions in
            # bold, parentheticals in italic). Turn any of these off if
            # your house style differs; ACTION and DIALOGUE are never
            # force-styled either way -- they only ever get bold/italic
            # from what you actually typed with **/* markers.
            "emphasis": {
                "heading_bold": True,
                "character_bold": True,
                "transition_bold": True,
                "parenthetical_italic": True,
            },
        },
    },
}

COLOR_MAP = {
    "black": curses.COLOR_BLACK, "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE, "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN, "white": curses.COLOR_WHITE,
    "default": -1,
}


def deep_merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


_OLD_DEFAULT_SAVE_DIRS = {"~/Documents", str(Path.home() / "Documents")}


def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if tomllib and CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                user_cfg = tomllib.load(f)
            deep_merge(cfg, user_cfg)
        except Exception:
            pass  # fall back silently to defaults
        else:
            # save_dir used to default to bare ~/Documents. If the config on
            # disk still has exactly that untouched value, this is almost
            # certainly a config nobody hand-edited (just the old default
            # written out by an earlier version of write_default_config()),
            # so it's safe to carry it forward to the new default of
            # ~/Documents/Scriptee rather than leaving scripts scattered
            # loose in ~/Documents. A save_dir the user actually customized
            # to something else entirely is left untouched.
            if cfg.get("general", {}).get("save_dir") in _OLD_DEFAULT_SAVE_DIRS:
                cfg["general"]["save_dir"] = DEFAULT_CONFIG["general"]["save_dir"]
    apply_runtime_config(cfg)
    return cfg


def apply_runtime_config(cfg):
    """Push cfg's [behavior]/[transitions]/[format] values into the small
    number of module-level globals that pure functions (from_fountain(),
    styled_wrap() callers, PDF export, ...) read directly, so those
    functions don't all need cfg threaded through their signatures.

    Called once by load_config(); safe to call again (e.g. from tests) to
    re-apply a different cfg. Dicts/lists are updated *in place* (not
    reassigned) so every module-level reference to e.g. WRAP_WIDTH keeps
    seeing the same object and picks up the new values automatically.
    """
    global AUTOSAVE_INTERVAL, MAX_RECENT_FILES

    behavior = cfg.get("behavior", DEFAULT_CONFIG["behavior"])
    AUTOSAVE_INTERVAL = behavior.get("autosave_interval_secs",
                                      DEFAULT_CONFIG["behavior"]["autosave_interval_secs"])
    MAX_RECENT_FILES = behavior.get("max_recent_files",
                                     DEFAULT_CONFIG["behavior"]["max_recent_files"])

    transitions = cfg.get("transitions", DEFAULT_CONFIG["transitions"])
    TRANSITION_KEYWORDS[:] = transitions.get(
        "builtins", DEFAULT_CONFIG["transitions"]["builtins"])

    fmt = cfg.get("format", DEFAULT_CONFIG["format"])
    WRAP_WIDTH.update(fmt.get("wrap_width", DEFAULT_CONFIG["format"]["wrap_width"]))
    INDENT.update(fmt.get("indent", DEFAULT_CONFIG["format"]["indent"]))
    _recompute_pdf_geometry(cfg)


def _bundled_courier_prime():
    """Locate Scriptee's bundled Courier Prime TTFs, if present, so a
    freshly-written config.toml can default to them instead of the PDF
    spec's built-in base-14 Courier.

    Base-14 Courier is what export_pdf() falls back to when
    format.pdf.font_family is left at "courier" -- it needs no file and
    works in every viewer, but at 12pt its bold weight in particular
    renders noticeably heavier and more cramped-looking than Courier
    Prime (the font actually designed for screenplays, and what most
    other screenwriting apps embed). Same margins, same 12pt leading,
    same wrap widths -- just a denser-looking typeface -- is exactly the
    kind of "why does this look cramped at the same page count" gap this
    resolves.

    Checked in order: next to this script (running from a repo/dev
    checkout), then the fonts/ directory install.sh copies into
    ~/.local/share/scriptee/. Returns {} if neither location has a full
    Regular/Bold/Italic set -- callers must treat that as "no bundled
    font found" and leave font_family at its safe "courier" default,
    never write a "custom" config pointing at files that don't exist.
    """
    candidates = [
        Path(__file__).resolve().parent / "fonts",
        Path.home() / ".local" / "share" / "scriptee" / "fonts",
    ]
    for d in candidates:
        reg = d / "CourierPrime-Regular.ttf"
        bold = d / "CourierPrime-Bold.ttf"
        italic = d / "CourierPrime-Italic.ttf"
        if reg.is_file() and bold.is_file() and italic.is_file():
            return {"regular": str(reg), "bold": str(bold), "italic": str(italic)}
    return {}


def write_default_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return
    text = DEFAULT_TOML_TEXT
    fonts = _bundled_courier_prime()
    if fonts:
        # Only flip the default when the files are actually there --
        # otherwise leave the template's safe "courier" / empty-paths
        # default untouched, so _apply_pdf_font_config() never has
        # anything to fall back from on a bare/manual install.
        text = (
            text
            .replace('font_family = "courier"    # "courier" or "custom"',
                      'font_family = "custom"     # "courier" or "custom"')
            .replace('regular = ""', f'regular = "{fonts["regular"]}"')
            .replace('bold = ""', f'bold = "{fonts["bold"]}"')
            .replace('italic = ""', f'italic = "{fonts["italic"]}"')
        )
    CONFIG_PATH.write_text(text)


DEFAULT_TOML_TEXT = """\
[general]
save_dir = "~/Documents/Scriptee"
# Stamp each scene's number in the left/right PDF margins on :pdf export,
# matching the numbers already shown in the editor's left gutter. Set to
# false for a clean draft PDF with no margin numbers.
pdf_scene_numbers = true
# Master on/off for the PDF cover page, independent of whatever's in the
# title-page fields below. false means :pdf never draws one, full stop.
# :cover (in-app) still works either way, so fields can be filled in
# ahead of flipping this back on.
cover_page = true
# If a script has no title page at all (e.g. imported from a Fountain file
# that never had one) and you hit :pdf, ask once for the same fields [n]ew
# screenplays get -- otherwise the export silently has no cover page. Set
# to false to never ask and just export without one. Has no effect if
# cover_page above is false.
prompt_missing_titlepage = true

[prompts]
# Fields asked when creating a new screenplay. Add, remove, reorder freely.
# Any field left blank when prompted is simply skipped.
fields = ["Title", "Author", "Genre", "Year", "Contact (Number)", "Contact (Email)"]

[sides]
# Title page for a *scoped* ":pdf" export -- a scene range ("sides") or
# ":pdf char NAME" (a character's draft). Entirely independent of
# [general]'s cover_page/prompt_missing_titlepage, which only ever
# govern the full-script export.
#
# Master on/off: whether a scoped export gets a title page at all. false
# means scene-range and character exports never draw one, no matter what
# else is set below.
cover_page = true
# Ask for a custom title before each scoped export -- just the one
# field, not the full [prompts] fields list above, since a sides PDF
# keeps the rest of the cover page (Author, Contact, ...) as whatever's
# already set for the full script. Leaving the prompt blank + Enter
# auto-generates one from the *_format strings below. Set to false to
# skip the prompt entirely and always auto-generate.
prompt_title = true
# Auto-generated title for a scene-range export when the prompt above is
# left blank (or skipped). {start}/{end} are the first/last scene
# numbers in the exported range.
title_format = "Sides (Scene {start} - Scene {end})"
# Used instead of title_format when the range is a single scene, so it
# reads "Sides (Scene 4)" rather than "Sides (Scene 4 - Scene 4)".
# {start} and {end} are both that one scene number.
title_format_single = "Sides (Scene {start})"
# Auto-generated title for a ":pdf char NAME" export. {name} is the
# character name exactly as typed at the prompt.
character_title_format = "Sides - {name} Draft"
# Small line under the sides title naming which script it's excerpted
# from, e.g. "(an excerpt from Beach House)" -- drawn whether the title
# above was typed in or auto-generated. Uses whatever's already in the
# full script's own Title field (see :cover); skipped automatically when
# that's empty (e.g. an imported .fountain with no title page at all)
# since there's no script title to name. Set to false to never draw
# this line.
excerpt_note = true
excerpt_format = "(an excerpt from {title})"

[character_export]
# Controls ":pdf char NAME" (a.k.a. ":pdf character NAME") -- the scoped
# export that pulls out every scene one character is in. A scene always
# counts if the name matches a CHARACTER cue exactly; this list adds
# other line types to also search, so a name just *mentioned* in the
# scene (not necessarily spoken by that character) still pulls the scene
# in, for full context. Matching is always case-insensitive and
# whole-word, so "AL" won't match "ALEX" or "ALWAYS".
#
# "dialogue" is what makes another character saying "...tell Danny..."
# in their own line count -- on by default, along with "action" (a
# character doing something with no dialogue of their own). Add
# "parenthetical", "shot", or "transition" too, or trim the list down to
# just ["action"] (or even []) to match only exact CHARACTER cues.
search_in = ["action", "dialogue"]

[character_export.aliases]
# Nicknames/alternate names for a character, so scenes that only use the
# nickname still get pulled into that character's sides export. Keys are
# matched case-insensitively against whatever name you pass to
# ":pdf char" -- values are a list of alternates to also search for
# (also case-insensitive, whole-word). Example, commented out:
# DANNY = ["Dan"]
# means ":pdf char Danny" (or ":pdf char Dan") includes every scene
# mentioning either "Danny" or "Dan". Empty by default -- add a line per
# character that actually gets nicknamed in your script.

[behavior]
# Seconds of dirty (unsaved), idle-tolerant editing between autosave
# snapshots to the crash-recovery slot. Never touches your real file.
autosave_interval_secs = 15
# How many undo/redo steps are kept on the flat undo stack.
max_undo_steps = 50
# How many paths the "recent files" list at [o]pen remembers.
max_recent_files = 15
# ncurses ESCDELAY in milliseconds -- how long a lone Esc byte waits before
# scriptee treats it as a real Esc rather than the start of a longer escape
# sequence (arrow keys etc.). Kept low so Esc feels instant; raise it only
# if your terminal/multiplexer needs more time.
esc_delay_ms = 25

[keybinds]
# Command-mode line-type setters, e.g. typing ":h<Enter>" sets the
# current line to a scene heading. Change the letters to remap them.
heading = "h"
action = "a"
character = "c"
dialogue = "d"
parenthetical = "p"
shot = "s"
transition = "t"

# Bare NORMAL-mode keys (pressed without a leading ":"). These can safely
# share a letter with a line-type setter above -- "a" here is "append
# after cursor", unrelated to ":a" setting the current line to ACTION --
# since one only applies bare in NORMAL mode and the other only after ":".
insert_before = "i"      # enter INSERT at the cursor
insert_after = "a"       # enter INSERT just past the cursor
open_below = "o"         # open a new line below, enter INSERT
open_above = "O"         # open a new line above, enter INSERT
move_left = "h"
move_down = "j"
move_up = "k"
move_right = "l"
delete_char = "x"
delete_line = "d"        # pressed twice, vim-style ("dd")
undo = "u"
redo = "r"               # held with Ctrl automatically -- i.e. Ctrl-r
search = "/"
next_match = "n"
command = ":"
repeat = "."              # re-run the last ":" command
jump_end = "G"            # bare G: jump to last line; "<N>G": jump to scene N
toggle_readonly = "e"     # unlock a file opened read-only from the shell
dual_dialogue = "D"       # mark/unmark current CHARACTER as dual dialogue

[colors]
# Any of: black, red, green, yellow, blue, magenta, cyan, white, default
heading       = "yellow"
character     = "cyan"
dialogue      = "white"
transition    = "magenta"
action        = "white"
shot          = "green"
parenthetical = "white"
accent        = "blue"

[transitions]
# Un-forced transition text (no leading ">") recognized when importing a
# .fountain file and offered by Tab-complete on TRANSITION lines. Add your
# own house-style transitions here, e.g. "WIPE TO:".
builtins = ["CUT TO:", "FADE OUT.", "FADE IN:", "DISSOLVE TO:", "SMASH CUT TO:"]

[format]
# Terminal column widths (characters) and left-indents (columns) per
# element. These are independent of [format.pdf]'s margins below, not
# derived from them -- change wrap_width.action and left_edge_in together
# if you want the terminal view and the exported PDF to keep agreeing on
# where lines wrap (they're only in lockstep by default, not by formula).

[format.wrap_width]
heading       = 60
action        = 60
shot          = 60
character     = 40
parenthetical = 32
dialogue      = 40
transition    = 74

[format.indent]
heading       = 0
action        = 0
shot          = 0
character     = 22
parenthetical = 18
dialogue      = 10
# transition has no indent -- it's right-aligned instead.

[format.pdf]
font_size = 12
page_size = "letter"       # "letter" or "a4"
# "courier" uses the PDF spec's built-in base-14 Courier family (zero
# embedding, works everywhere, and is what makes an exported PDF look
# right in any viewer with no extra install). Set to "custom" to instead
# embed your own TrueType font -- e.g. Courier Prime, a free font made to
# match Courier's own character metrics but with cleaner letterforms --
# via [format.pdf.custom_font] below. A bad or missing path silently
# falls back to Courier with a note in the status bar after :pdf, so a
# typo here can never break an export.
#
# If Scriptee's bundled Courier Prime was found at install time (see
# fonts/ and install.sh), write_default_config() flips this to "custom"
# and fills in custom_font below automatically -- what you see here is
# only the safe zero-dependency fallback. Base-14 Courier's bold weight
# in particular renders noticeably heavier/denser at 12pt than Courier
# Prime, which is what can make a same-margins, same-leading export look
# "cramped" next to another app's PDF even at the same page count.
font_family = "courier"    # "courier" or "custom"
left_edge_in = 1.5         # heading / action / shot start column
dialogue_left_in = 2.5
parenthetical_left_in = 2.8
character_left_in = 3.5
right_margin_in = 1.0
top_margin_in = 1.0
bottom_margin_in = 1.0
# Gap between the two columns when printing dual (simultaneous) dialogue.
dual_dialogue_gutter_in = 0.3

[format.pdf.custom_font]
# Absolute paths to .ttf files. Only used when font_family = "custom"
# above. "regular" is required; "bold"/"italic" fall back to "regular"
# if left blank (so a font with no distinct bold/italic weight still
# works, just without the visual distinction bold/italic markup gives).
# Note: column math (wrap width, dual-dialogue columns) still assumes a
# Courier-like fixed 0.6em character advance -- an actual monospaced font
# at that metric (Courier Prime included) lines up correctly; a
# proportional or unusually-spaced font may not.
regular = ""
bold = ""
italic = ""

[format.pdf.emphasis]
# Which elements are force-styled on :pdf export, regardless of any inline
# **bold**/*italic* markup (or lack of it) you typed. These are the
# standard screenplay-format conventions; turn any of them off if your
# house style differs. ACTION and DIALOGUE are never force-styled either
# way -- they only ever get bold/italic from **/* markers you actually type.
heading_bold          = true
character_bold        = true
transition_bold       = true
parenthetical_italic  = true
"""

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
            # A trailing "^" is Fountain's own dual-dialogue marker: it
            # tells any Fountain-reading tool (this one included, see
            # from_fountain() below) that this speech pairs side by side
            # with the CHARACTER/DIALOGUE block immediately above it,
            # rather than following it in sequence. Set via ":dual" /
            # the dual_dialogue keybind (see toggle_dual_dialogue()).
            out.append(f"{txt.upper()} ^" if ln.get("dual") else txt.upper())
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
    lines = text.split("\n")
    metadata = {}
    i = 0
    # title page: consecutive "Key: Value" lines at top
    while i < len(lines) and re.match(r'^[A-Za-z ()]+:\s?.*$', lines[i] or ""):
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


def compute_stats(buffer):
    """Word/scene/character breakdown for the ':stats' popup. A pure
    function of `buffer` (no Editor/curses dependency), so it's directly
    unit-testable without a fake screen.

    Returns a dict:
      - total_words: word count across every element type.
      - dialogue_words / action_words: split out separately since they
        read very differently -- dialogue words roughly track spoken
        runtime, action words roughly track visual/page real estate.
      - scene_count: number of HEADING lines.
      - characters: (name, line_count, word_count) for every distinct
        speaker's DIALOGUE, sorted by word count descending. A speaker's
        (V.O.)/(CONT'D)-style extension is stripped via
        split_character_cue() so e.g. "SRIRAM" and "SRIRAM (V.O.)" count
        as the same character, matching how :rename already treats
        character identity.
    """
    total_words = dialogue_words = action_words = scene_count = 0
    char_stats = {}  # base_name -> [line_count, word_count]
    current_speaker = None
    for ln in buffer:
        t, txt = ln["type"], ln["text"]
        words = len(txt.split())
        total_words += words
        if t not in DIALOGUE_CHAIN_TYPES:
            current_speaker = None
        if t == "heading":
            scene_count += 1
        elif t == "action":
            action_words += words
        elif t == "character":
            base, _ = split_character_cue(txt)
            current_speaker = base.strip().upper() or None
        elif t == "dialogue":
            dialogue_words += words
            if current_speaker:
                lc, wc = char_stats.get(current_speaker, (0, 0))
                char_stats[current_speaker] = (lc + 1, wc + words)
    characters = sorted(
        ((name, lc, wc) for name, (lc, wc) in char_stats.items()),
        key=lambda tup: tup[2], reverse=True,
    )
    return {
        "total_words": total_words,
        "dialogue_words": dialogue_words,
        "action_words": action_words,
        "scene_count": scene_count,
        "characters": characters,
    }


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
    return RECOVERY_DIR / recovery_key_for_path(path)


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
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    return RECOVERY_DIR / f"untitled-{uuid.uuid4().hex[:8]}.swp"


RECENT_FILES_PATH = CONFIG_DIR / "recent.txt"
# MAX_RECENT_FILES itself is defined near the top of the file (with
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
    load_recent_files()), deduplicated, capped at MAX_RECENT_FILES. Best
    effort -- if this fails for any reason, the picker just falls back to
    save_dir only, same as before this existed."""
    try:
        resolved = str(Path(path).expanduser().resolve())
    except OSError:
        resolved = str(Path(path).expanduser())
    existing = [p for p in load_recent_files() if p != resolved]
    existing.insert(0, resolved)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RECENT_FILES_PATH.write_text("\n".join(existing[:MAX_RECENT_FILES]) + "\n")
    except OSError:
        pass


def find_orphan_recoveries():
    """Recovery files for documents that were never saved to a real path
    before the session ended (crash, killed terminal, etc.) -- these can't
    be keyed to a real file, so they're offered separately at startup.
    Newest first."""
    if not RECOVERY_DIR.exists():
        return []
    orphans = list(RECOVERY_DIR.glob("untitled-*.swp"))
    orphans.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return orphans


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


# --------------------------------------------------------------------------
# PDF export
# --------------------------------------------------------------------------
#
# Fonts: Courier / Courier-Bold / Courier-Oblique / Courier-BoldOblique are
# four of the PDF spec's 14 "base" fonts -- every conformant PDF viewer has
# them built in, so reportlab can reference them with zero embedding and
# nothing extra to install. That also means **bold**/*italic* markers now
# render as actual bold/oblique Courier instead of literal asterisks (the
# previous export ignored inline styling entirely and printed the raw
# markers). True Courier-Oblique (real slant) reads better in a PDF than
# the underline substitution the in-terminal view uses, since terminals
# generally can't render italics but a PDF viewer always can.
#
# Margins/indents/leading below follow the standard US industry format:
# Courier 12pt, 1" top/bottom/right margins, 1.5" left margin for action/
# heading/shot, dialogue starting ~2.5" from the left edge, character cues
# ~3.7", 55 lines/page -- the same convention Final Draft/Highland/Fade In
# target, and the same WRAP_WIDTH columns the in-editor view itself wraps
# at, so the PDF's line breaks match what you saw while writing.

PDF_FONT = "Courier"
PDF_FONT_BOLD = "Courier-Bold"
PDF_FONT_ITALIC = "Courier-Oblique"
# Set by _apply_pdf_font_config() whenever a "custom" font_family falls
# back to Courier (missing reportlab, missing/bad path, registration
# error) -- surfaced in the status bar after :pdf so a config typo is
# visible instead of silently changing nothing.
PDF_FONT_WARNING = ""

# Plain (width_pt, height_pt) page sizes, defined directly rather than
# imported from reportlab.lib.pagesizes -- pagination (paginate_buffer(),
# used for the live page/runtime estimate in the status bar) has to work
# even when reportlab isn't installed (:pdf export is the only thing that
# actually needs it), so nothing pagination-related may import reportlab.
PAGE_SIZES_PT = {
    "letter": (8.5 * 72.0, 11.0 * 72.0),
    "a4": (595.28, 841.89),
}

# All of the following are geometry derived from cfg["format"]["pdf"] by
# _recompute_pdf_geometry() (called from apply_runtime_config()) -- the
# values here are just startup defaults so the module works before a
# config is ever loaded (e.g. under test). Both export_pdf() and
# paginate_buffer() read these instead of their own hardcoded numbers, so
# a margin/font-size change in config.toml can't leave the two disagreeing
# about where pages break -- the exact "kept in lockstep" property the
# in-code comments below already describe for WRAP_WIDTH.
PDF_PAGE_W, PDF_PAGE_H = PAGE_SIZES_PT["letter"]
PDF_FONT_SIZE = 12
PDF_LEADING = 12  # tied to font size, not separately configurable
PDF_LEFT_EDGE = 1.5 * 72.0
PDF_DIALOGUE_LEFT = 2.5 * 72.0
PDF_PAREN_LEFT = 2.8 * 72.0
PDF_CHARACTER_LEFT = 3.5 * 72.0
PDF_RIGHT_EDGE = PDF_PAGE_W - 1.0 * 72.0
PDF_TOP_Y = PDF_PAGE_H - 1.0 * 72.0
PDF_BOTTOM_Y = 1.0 * 72.0
PDF_DUAL_GUTTER = 0.3 * 72.0
# Dual-dialogue column geometry, also recomputed by _recompute_pdf_geometry():
# two equal columns spanning left_edge..right_edge with a gutter between.
PDF_DUAL_COL_WIDTH = (PDF_RIGHT_EDGE - PDF_LEFT_EDGE - PDF_DUAL_GUTTER) / 2
PDF_DUAL_COL1_X = PDF_LEFT_EDGE
PDF_DUAL_COL2_X = PDF_LEFT_EDGE + PDF_DUAL_COL_WIDTH + PDF_DUAL_GUTTER
PDF_DUAL_WRAP_WIDTH = 30  # chars -- recomputed below
PDF_HEADING_BOLD = True
PDF_CHARACTER_BOLD = True
PDF_TRANSITION_BOLD = True
PDF_PAREN_ITALIC = True


def _recompute_pdf_geometry(cfg):
    """Derive every PDF layout constant above from cfg["format"]["pdf"].
    Called once by apply_runtime_config(); pure arithmetic, no reportlab
    dependency, so it's safe to run even when reportlab isn't installed."""
    global PDF_PAGE_W, PDF_PAGE_H, PDF_FONT_SIZE, PDF_LEADING
    global PDF_LEFT_EDGE, PDF_DIALOGUE_LEFT, PDF_PAREN_LEFT, PDF_CHARACTER_LEFT
    global PDF_RIGHT_EDGE, PDF_TOP_Y, PDF_BOTTOM_Y, PDF_DUAL_GUTTER
    global PDF_DUAL_COL_WIDTH, PDF_DUAL_COL1_X, PDF_DUAL_COL2_X, PDF_DUAL_WRAP_WIDTH
    global PDF_ROWS_PER_PAGE
    global PDF_HEADING_BOLD, PDF_CHARACTER_BOLD, PDF_TRANSITION_BOLD, PDF_PAREN_ITALIC

    pdf_cfg = cfg.get("format", DEFAULT_CONFIG["format"]).get(
        "pdf", DEFAULT_CONFIG["format"]["pdf"])
    d = DEFAULT_CONFIG["format"]["pdf"]

    page_size_name = pdf_cfg.get("page_size", d["page_size"])
    PDF_PAGE_W, PDF_PAGE_H = PAGE_SIZES_PT.get(page_size_name, PAGE_SIZES_PT["letter"])
    PDF_FONT_SIZE = pdf_cfg.get("font_size", d["font_size"])
    PDF_LEADING = PDF_FONT_SIZE

    PDF_LEFT_EDGE = pdf_cfg.get("left_edge_in", d["left_edge_in"]) * 72.0
    PDF_DIALOGUE_LEFT = pdf_cfg.get("dialogue_left_in", d["dialogue_left_in"]) * 72.0
    PDF_PAREN_LEFT = pdf_cfg.get("parenthetical_left_in", d["parenthetical_left_in"]) * 72.0
    PDF_CHARACTER_LEFT = pdf_cfg.get("character_left_in", d["character_left_in"]) * 72.0
    right_margin = pdf_cfg.get("right_margin_in", d["right_margin_in"]) * 72.0
    top_margin = pdf_cfg.get("top_margin_in", d["top_margin_in"]) * 72.0
    bottom_margin = pdf_cfg.get("bottom_margin_in", d["bottom_margin_in"]) * 72.0
    PDF_RIGHT_EDGE = PDF_PAGE_W - right_margin
    PDF_TOP_Y = PDF_PAGE_H - top_margin
    PDF_BOTTOM_Y = bottom_margin
    PDF_DUAL_GUTTER = pdf_cfg.get("dual_dialogue_gutter_in", d["dual_dialogue_gutter_in"]) * 72.0

    PDF_DUAL_COL_WIDTH = max(1.0, (PDF_RIGHT_EDGE - PDF_LEFT_EDGE - PDF_DUAL_GUTTER) / 2)
    PDF_DUAL_COL1_X = PDF_LEFT_EDGE
    PDF_DUAL_COL2_X = PDF_LEFT_EDGE + PDF_DUAL_COL_WIDTH + PDF_DUAL_GUTTER
    # Courier's fixed advance is 0.6em -- 72/(0.6*size) chars fit per inch
    # at the configured font size (10 chars/inch at the standard 12pt).
    chars_per_inch = 72.0 / (0.6 * PDF_FONT_SIZE)
    PDF_DUAL_WRAP_WIDTH = max(4, int((PDF_DUAL_COL_WIDTH / 72.0) * chars_per_inch))

    # See the original PDF_ROWS_PER_PAGE comment: a row is drawn whenever
    # the current y hasn't yet dropped below the bottom margin, so a page
    # holds floor(usable_height / leading) + 1 rows.
    PDF_ROWS_PER_PAGE = int((PDF_TOP_Y - PDF_BOTTOM_Y) // PDF_LEADING) + 1

    emphasis = pdf_cfg.get("emphasis", d.get("emphasis", {}))
    d_emphasis = d.get("emphasis", {})
    PDF_HEADING_BOLD = emphasis.get("heading_bold", d_emphasis.get("heading_bold", True))
    PDF_CHARACTER_BOLD = emphasis.get("character_bold", d_emphasis.get("character_bold", True))
    PDF_TRANSITION_BOLD = emphasis.get("transition_bold", d_emphasis.get("transition_bold", True))
    PDF_PAREN_ITALIC = emphasis.get("parenthetical_italic", d_emphasis.get("parenthetical_italic", True))

    _apply_pdf_font_config(pdf_cfg, d)


def _apply_pdf_font_config(pdf_cfg, d):
    """Set PDF_FONT/PDF_FONT_BOLD/PDF_FONT_ITALIC from
    cfg["format"]["pdf"]["font_family"]. "courier" (the default) uses the
    PDF spec's built-in base-14 Courier family -- no file, no reportlab
    call needed. "custom" registers TrueType files named under
    [format.pdf.custom_font] instead, so a house font (e.g. Courier Prime)
    can be used. Any failure here (reportlab missing, path missing/bad,
    a corrupt font file) falls back to Courier rather than raising --
    a font typo should never be able to break :pdf export -- and records
    why in PDF_FONT_WARNING so the caller can surface it."""
    global PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC, PDF_FONT_WARNING
    PDF_FONT_WARNING = ""
    family = pdf_cfg.get("font_family", d.get("font_family", "courier"))
    if family != "custom":
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")
        return
    if not HAVE_REPORTLAB:
        PDF_FONT_WARNING = ("format.pdf.font_family is \"custom\" but reportlab "
                             "isn't installed -- using Courier instead.")
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")
        return
    paths = pdf_cfg.get("custom_font", d.get("custom_font", {}))
    regular = paths.get("regular", "")
    if not regular or not Path(regular).expanduser().is_file():
        PDF_FONT_WARNING = ("format.pdf.font_family is \"custom\" but "
                             "custom_font.regular is missing or not found -- "
                             "using Courier instead.")
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")
        return
    bold = paths.get("bold", "")
    italic = paths.get("italic", "")
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont("ScripteeCustom", str(Path(regular).expanduser())))
        PDF_FONT = "ScripteeCustom"
        if bold and Path(bold).expanduser().is_file():
            pdfmetrics.registerFont(TTFont("ScripteeCustom-Bold", str(Path(bold).expanduser())))
            PDF_FONT_BOLD = "ScripteeCustom-Bold"
        else:
            PDF_FONT_BOLD = PDF_FONT  # no distinct bold weight given
        if italic and Path(italic).expanduser().is_file():
            pdfmetrics.registerFont(TTFont("ScripteeCustom-Italic", str(Path(italic).expanduser())))
            PDF_FONT_ITALIC = "ScripteeCustom-Italic"
        else:
            PDF_FONT_ITALIC = PDF_FONT  # no distinct italic weight given
    except Exception as e:
        PDF_FONT_WARNING = f"Couldn't load custom PDF font ({e}) -- using Courier instead."
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")


PDF_SEMIBOLD_OFFSET = 0.3  # pt -- hairline double-strike offset, see _draw_styled_row()


def _pdf_font_for_style(style):
    if style == "bold":
        return PDF_FONT_BOLD
    if style == "semibold":
        # No dedicated semibold weight ships with Courier Prime (or base-14
        # Courier), so "semibold" text is still measured/drawn in the
        # regular weight -- _draw_styled_row() fakes the extra heft with a
        # hairline double-strike rather than switching fonts.
        return PDF_FONT
    if style == "italic":
        return PDF_FONT_ITALIC
    return PDF_FONT


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
    narrower PDF_DUAL_WRAP_WIDTH -- each row tagged with its draw style
    ('character'/'parenthetical'/'dialogue') so the caller can position
    and font each one correctly."""
    out = []
    for idx in range(i, end + 1):
        ln = buffer[idx]
        if ln["type"] == "character":
            out.append(("character", [(ln["text"].upper(),
                                        "semibold" if PDF_CHARACTER_BOLD else None)]))
        elif ln["type"] == "parenthetical":
            body = ln["text"] if ln["text"].startswith("(") else f"({ln['text']})"
            rows = _forced_style(
                _styled_wrap_uncached({"type": "parenthetical", "text": body},
                                      PDF_DUAL_WRAP_WIDTH),
                "italic") if PDF_PAREN_ITALIC else _styled_wrap_uncached(
                {"type": "parenthetical", "text": body}, PDF_DUAL_WRAP_WIDTH)
            out.extend(("parenthetical", r) for r in rows)
        else:  # dialogue
            rows = styled_wrap(ln, PDF_DUAL_WRAP_WIDTH)
            out.extend(("dialogue", r) for r in rows)
    return out


PDF_ROWS_PER_PAGE = 55
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
        if row >= PDF_ROWS_PER_PAGE:
            page += 1
            row = 0

    idx = 0
    n = len(buffer)
    while idx < n:
        pair = find_dual_pair(buffer, idx)
        if pair is not None:
            # A dual-dialogue pair is drawn as one atomic two-column unit
            # by export_pdf() (see its own comment for why it doesn't
            # split a pair across a page break) -- mirrored here as one
            # block whose height is whichever column runs longer, rather
            # than walking its lines individually.
            block1_end, second_start, block2_end = pair
            rows1 = len(_dual_column_rows(buffer, idx, block1_end))
            rows2 = len(_dual_column_rows(buffer, second_start, block2_end))
            n_rows = max(rows1, rows2)
            has_character = True
            if row + n_rows > PDF_ROWS_PER_PAGE and row > 0:
                page += 1
                row = 0
            for k in range(idx, block2_end + 1):
                starts[k] = (page, row)
            row += n_rows
            if row >= PDF_ROWS_PER_PAGE:
                page += 1
                row = 0
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
            if row >= PDF_ROWS_PER_PAGE:
                page += 1
                row = 0
            if row_i == 0:
                starts[idx] = (page, row)
            row += 1
            if is_dialogue and row_i < n_rows - 1 and row >= PDF_ROWS_PER_PAGE:
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
    default = DEFAULT_CONFIG["sides"]
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
    ce_default = DEFAULT_CONFIG["character_export"]
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
                subtitle=None):
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
    """
    if not HAVE_REPORTLAB:
        raise RuntimeError("reportlab is not installed (pip install reportlab)")

    c = canvas.Canvas(str(path), pagesize=(PDF_PAGE_W, PDF_PAGE_H))
    page_w, page_h = PDF_PAGE_W, PDF_PAGE_H
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
    size, leading = PDF_FONT_SIZE, PDF_LEADING
    left_edge = PDF_LEFT_EDGE          # heading/action/shot
    dialogue_left = PDF_DIALOGUE_LEFT
    paren_left = PDF_PAREN_LEFT
    character_left = PDF_CHARACTER_LEFT
    right_edge = PDF_RIGHT_EDGE
    top_y = PDF_TOP_Y
    bottom_y = PDF_BOTTOM_Y

    # Title page
    if cover_page and metadata:
        c.setFont(PDF_FONT, size)
        title = metadata.get("Title", "Untitled")
        title_y = page_h * 0.55
        c.setFont(PDF_FONT, 16)
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
        c.setFont(PDF_FONT, size)
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

    c.setFont(PDF_FONT, size)
    y = top_y
    # Industry convention: the title page and the first script page are
    # unnumbered; page numbers ("2.", "3.", ...) appear top-right starting
    # on the second script page.
    script_page_num = 1

    def stamp_page_number():
        if script_page_num > 1:
            c.setFont(PDF_FONT, size)
            c.drawRightString(right_edge, page_h - 0.75 * inch, f"{script_page_num}.")

    def new_page():
        nonlocal y, script_page_num
        c.showPage()
        script_page_num += 1
        c.setFont(PDF_FONT, size)
        y = top_y
        stamp_page_number()

    stamp_page_number()

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
            # dialogue. Drawn as one atomic unit: unlike a single-column
            # speech, a two-column block has no clean (MORE)/(CONT'D)
            # equivalent, so rather than splitting it mid-block, the whole
            # pair moves to a fresh page first if it won't fit on this one.
            block1_end, second_start, block2_end = pair
            has_character = True
            rows1 = _dual_column_rows(buffer, idx, block1_end)
            rows2 = _dual_column_rows(buffer, second_start, block2_end)
            block_height = max(len(rows1), len(rows2)) * leading
            if y - block_height < bottom_y and y != top_y:
                new_page()
            pair_top_y = y
            for col_x, col_rows in ((PDF_DUAL_COL1_X, rows1), (PDF_DUAL_COL2_X, rows2)):
                cy = pair_top_y
                for kind, row in col_rows:
                    if kind == "character":
                        _draw_styled_row_centered(c, row, col_x + PDF_DUAL_COL_WIDTH / 2, cy, size)
                    else:
                        _draw_styled_row(c, row, col_x, cy, size)
                    cy -= leading
            y = pair_top_y - block_height
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
                                  "bold") if PDF_HEADING_BOLD else styled_wrap(
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
                c.setFont(PDF_FONT, size)
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
            rows = [[(txt.upper(), "semibold" if PDF_CHARACTER_BOLD else None)]]
            x = character_left
            right_align = False
        elif t == "parenthetical":
            body = txt if txt.startswith("(") else f"({txt})"
            rows = _forced_style(
                _styled_wrap_uncached({"type": "parenthetical", "text": body},
                                      WRAP_WIDTH["parenthetical"]),
                "italic") if PDF_PAREN_ITALIC else _styled_wrap_uncached(
                {"type": "parenthetical", "text": body}, WRAP_WIDTH["parenthetical"])
            x = paren_left
            right_align = False
        elif t == "dialogue":
            rows = styled_wrap(ln, WRAP_WIDTH["dialogue"])
            x = dialogue_left
            right_align = False
        elif t == "transition":
            rows = [[(txt.upper(), "bold" if PDF_TRANSITION_BOLD else None)]]
            x = right_edge
            right_align = True
        else:
            rows = styled_wrap(ln, 60)
            x = left_edge
            right_align = False

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
                _draw_styled_row(c, row, x, y, size, right_align=right_align)
                y -= leading
                if row_i < len(rows) - 1 and y < bottom_y:
                    c.setFont(PDF_FONT, size)
                    c.drawString(dialogue_left, y, "(MORE)")
                    new_page()
                    cont_row = [(f"{current_character.upper()} (CONT'D)",
                                 "semibold" if PDF_CHARACTER_BOLD else None)]
                    _draw_styled_row(c, cont_row, character_left, y, size)
                    y -= leading
        else:
            for row in rows:
                if y < bottom_y:
                    new_page()
                _draw_styled_row(c, row, x, y, size, right_align=right_align)
                y -= leading

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


# --------------------------------------------------------------------------
# Editor
# --------------------------------------------------------------------------

class Editor:
    def __init__(self, stdscr, cfg, metadata, buffer, filepath, recovery_path=None,
                 readonly=False):
        self.stdscr = stdscr
        self.cfg = cfg
        self.metadata = metadata
        self.buffer = buffer if buffer else [{"type": "action", "text": ""}]
        self.filepath = filepath
        self.cy, self.cx = 0, 0          # cursor: line index, col in raw text
        self.mode = "NORMAL"             # NORMAL, INSERT, COMMAND, SEARCH
        self.cmdline = ""
        # Opened via `scriptee some/file.fountain` on the command line --
        # starts locked against edits so a quick "let me check something"
        # launch can't turn into an accidental edit; 'e' in NORMAL mode
        # unlocks it. Never true for files opened through the in-app
        # menus, or for a brand-new/not-yet-existing path.
        self.readonly = readonly
        if readonly:
            self.status = "-- READ-ONLY -- press 'e' to enable editing"
        else:
            self.status = ("Ready.  :help for all commands   u/Ctrl-R undo/redo   "
                            ":wq save+quit   :o open   :pdf export   Tab autocompletes")
        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False
        self.search_term = ""
        self.pending_key = None          # for two-key sequences like dd
        self.count_buffer = ""           # digits typed before "G" -- jump-to-scene count
        self._tab_state = None           # Tab-cycle state, see handle_tab_complete()
        # Whether do_export_pdf() has already offered the missing-title-page
        # prompt this session -- so declining it (leaving every field blank)
        # means "export without a cover page" for the *rest* of this session
        # instead of re-asking on every single :pdf.
        self._title_prompt_shown = False
        self._page_cache = (1, 1, 0.0)   # (total pdf pages, script pages, monotonic time)
        self._page_at_cache = None       # (line_index, buf_len, result) -- see page_number_at()
        self.buffer_rev = 0              # bumped on every buffer mutation; lets
                                          # per-buffer caches (autocomplete, ...)
                                          # know when they're stale
        self._autocomplete_cache = {}    # {type_name: (buffer_rev, candidates)}
        self.last_command = None         # last ":" command run, for "." repeat
        # Where autosaved recovery data is written. Keyed to the real path
        # when one is known, so re-opening the same file finds its own
        # recovery slot; otherwise a fresh throwaway slot for this
        # never-yet-saved session (see find_orphan_recoveries()).
        if recovery_path is not None:
            self.recovery_path = recovery_path
        elif filepath:
            self.recovery_path = recovery_path_for(filepath)
        else:
            self.recovery_path = new_untitled_recovery_path()
        self.last_autosave = 0.0
        self.setup_colors()

    # -- colors --------------------------------------------------------
    def setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        colors = self.cfg["colors"]
        self.pairs = {}
        names = ["heading", "character", "dialogue", "transition",
                 "action", "shot", "parenthetical", "accent"]
        for i, name in enumerate(names, start=1):
            fg = COLOR_MAP.get(colors.get(name, "white"), curses.COLOR_WHITE)
            curses.init_pair(i, fg, -1)
            self.pairs[name] = curses.color_pair(i)

    def attr_for_type(self, t):
        return self.pairs.get(t, 0)

    # -- undo / redo -------------------------------------------------------
    def max_undo_steps(self):
        return self.cfg.get("behavior", {}).get("max_undo_steps", 50)

    def snapshot(self):
        self.undo_stack.append((copy.deepcopy(self.buffer), self.cy, self.cx))
        if len(self.undo_stack) > self.max_undo_steps():
            self.undo_stack.pop(0)
        # A fresh edit invalidates any redo history -- standard editor
        # semantics (undo, then type something new, and the old "future"
        # you undid past is gone).
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            self.status = "Already at oldest change."
            return
        self.redo_stack.append((copy.deepcopy(self.buffer), self.cy, self.cx))
        if len(self.redo_stack) > self.max_undo_steps():
            self.redo_stack.pop(0)
        self.buffer, self.cy, self.cx = self.undo_stack.pop()
        self.clamp_cursor()
        self.touch()
        self.status = "Undo."

    def redo(self):
        if not self.redo_stack:
            self.status = "Nothing to redo."
            return
        self.undo_stack.append((copy.deepcopy(self.buffer), self.cy, self.cx))
        self.buffer, self.cy, self.cx = self.redo_stack.pop()
        self.clamp_cursor()
        self.touch()
        self.status = "Redo."

    def clamp_cursor(self):
        self.cy = max(0, min(self.cy, len(self.buffer) - 1))
        self.cx = max(0, min(self.cx, len(self.buffer[self.cy]["text"])))

    def touch(self):
        """Mark the buffer dirty *and* bump buffer_rev so revision-keyed
        caches (e.g. Tab autocomplete's candidate list) know to recompute.
        Use this instead of setting self.dirty directly whenever the
        buffer's actual content changes."""
        self.dirty = True
        self.buffer_rev += 1

    # -- keybind lookup ----------------------------------------------------
    def type_for_key(self, key_char):
        kb = self.cfg["keybinds"]
        # Only the seven line-type setters are eligible for a ":" + letter
        # command -- excluding the bare NORMAL-mode action names (which
        # live in the same [keybinds] table) so e.g. typing ":D" can't
        # accidentally be read as trying to set a line-type via the
        # "dual_dialogue" binding.
        for type_name in TYPE_LABELS:
            if kb.get(type_name) == key_char:
                return type_name
        return None

    def key(self, name):
        """Codepoint for a configured bare NORMAL-mode key, e.g.
        self.key("move_left") -> ord('h') by default. Falls back to the
        built-in default if the user's config is missing/malformed for
        this entry, so a typo'd config.toml can't crash the app."""
        letter = self.cfg.get("keybinds", {}).get(
            name, DEFAULT_CONFIG["keybinds"].get(name, ""))
        return ord(letter) if letter else -1

    def ctrl_key(self, name):
        """Codepoint for a configured key held with Ctrl, e.g.
        self.ctrl_key("redo") -> 18 (Ctrl-r) by default."""
        letter = self.cfg.get("keybinds", {}).get(
            name, DEFAULT_CONFIG["keybinds"].get(name, ""))
        return (ord(letter.upper()) & 0x1f) if letter else -1

    # -- dual dialogue -------------------------------------------------
    def toggle_dual_dialogue(self):
        """Mark/unmark the current CHARACTER line as Fountain dual
        (simultaneous) dialogue -- paired with the CHARACTER/DIALOGUE
        block immediately above it, matching Fountain's own "^" cue-line
        convention (see to_fountain()/from_fountain()). Only meaningful on
        a CHARACTER line, since that's the only element the flag is ever
        read from -- there's nothing for it to pair on any other type."""
        line = self.buffer[self.cy]
        if line["type"] != "character":
            self.status = "Dual dialogue only applies to a CHARACTER line (:c first)."
            return
        if line.get("dual"):
            self.snapshot()
            del line["dual"]
            self.status = "Dual dialogue: off for this cue."
            self.touch()
            return
        # Only set the flag if there's an actual CHARACTER+DIALOGUE block
        # directly above (blank lines OK) for it to pair with -- otherwise
        # the flag would set with no error and no visible effect on export,
        # which is exactly the "did that even do anything?" confusion this
        # guards against. See _dual_pair_opener_above().
        if _dual_pair_opener_above(self.buffer, self.cy) is None:
            self.status = ("Dual dialogue needs a CHARACTER + DIALOGUE block "
                            "directly above this cue to pair with (blank lines "
                            "are OK, but nothing else in between) -- write that "
                            "first, then mark this cue.")
            return
        self.snapshot()
        line["dual"] = True
        self.status = "Dual dialogue: on -- pairs with the block above."
        self.touch()

    # -- autosave / recovery ---------------------------------------------
    def maybe_autosave(self):
        """Best-effort periodic write to the recovery slot -- never touches
        the real file (only :w/:wq do that) and never raises, so a
        permissions hiccup or full disk can't interrupt editing."""
        if not self.dirty:
            return
        now = time.monotonic()
        if now - self.last_autosave < AUTOSAVE_INTERVAL:
            return
        self.last_autosave = now
        try:
            atomic_write_text(self.recovery_path, to_fountain(self.metadata, self.buffer))
            self.save_cursor_pos()
        except OSError:
            pass

    def save_cursor_pos(self):
        """Best-effort write of the current cursor line/col next to
        self.recovery_path, so the next open of this file (recovery *or*
        a normal reopen -- see main()) can restore it. Never raises."""
        try:
            cursor_pos_path_for(self.recovery_path).write_text(f"{self.cy},{self.cx}")
        except OSError:
            pass

    def discard_recovery(self):
        """Drops the stale autosaved *content* now that the real file
        matches the buffer. Cursor position is intentionally left alone --
        callers that want it (re)persisted call save_cursor_pos()
        separately, e.g. right after this on a clean :q or right after
        re-keying self.recovery_path in save()."""
        if self.recovery_path is not None and self.recovery_path.exists():
            try:
                self.recovery_path.unlink()
            except OSError:
                pass

    # -- character rename sweep -------------------------------------------
    def rename_character(self, old, new):
        old_u = old.strip().upper()
        new_u = new.strip().upper()
        if not old_u or not new_u:
            self.status = "Usage: :rename OLD NEW"
            return
        changed = copy.deepcopy(self.buffer)
        count = 0
        for ln in changed:
            if ln["type"] != "character":
                continue
            base, ext = split_character_cue(ln["text"])
            if base.upper() == old_u:
                ln["text"] = new_u + (f" {ext}" if ext else "")
                count += 1
        if count == 0:
            self.status = f"No CHARACTER cues matched '{old_u}'."
            return
        self.snapshot()
        self.buffer = changed
        self.touch()
        self.status = f"Renamed {count} cue(s): {old_u} -> {new_u}"

    # -- last-of-type resume (":lc" / ":lh" / ":lt") ------------------------
    def insert_last_of_type(self, type_name):
        """Set the current (empty) line to `type_name`, filled with
        whichever line of that type was most recently used above the
        cursor -- quick way to resume the same speaker/location/transition
        without retyping it. Only acts on an empty line, so it never
        clobbers text you've already typed; use "o" (or accept the auto
        ":" prompt's default) to get an empty line first.

        - CHARACTER (":lc"): last speaker, stripped of any (V.O.)-style
          extension -- resuming dialogue after an action beat almost never
          wants to repeat the old extension verbatim.
        - HEADING (":lh") / TRANSITION (":lt"): last heading/transition
          text used verbatim -- handy for a quick INT./EXT. re-establish
          or a repeated "CUT TO:" without retyping or Tab-cycling.
        """
        label = TYPE_LABELS[type_name]
        text = None
        for i in range(self.cy - 1, -1, -1):
            if self.buffer[i]["type"] == type_name:
                raw = self.buffer[i]["text"]
                text = split_character_cue(raw)[0] if type_name == "character" else raw
                break
        if not text:
            self.status = f"No earlier {label} found."
            return
        line = self.buffer[self.cy]
        if line["text"].strip():
            self.status = "Line isn't empty -- open a new line (o) first, then retry."
            return
        self.snapshot()
        line["type"] = type_name
        line["text"] = text
        self.cx = len(text)
        self.touch()
        self.mode = "INSERT"
        self.status = f"-- INSERT --  [{label}]  ({text})"

    # -- help screen ---------------------------------------------------
    def help_text(self):
        kb = self.cfg["keybinds"]
        return [
            ("MODES", None),
            ("NORMAL", "Move around and issue commands. Esc always returns here."),
            ("INSERT", "Type screenplay text. i / a / o / O enter it (see below)."),
            ("COMMAND", "Prompt starting with ':' -- element types, :w, :help, etc."),
            ("SEARCH", "Prompt starting with '/' -- text search."),
            ("", None),
            ("NORMAL MODE", None),
            (kb['insert_before'], "Insert before cursor"),
            (kb['insert_after'], "Insert after cursor"),
            (kb['open_below'], "Open a new line below and insert"),
            (kb['open_above'], "Open a new line above and insert"),
            (f"{kb['move_left']} {kb['move_down']} {kb['move_up']} {kb['move_right']}",
             "Move left / down / up / right"),
            (kb['delete_char'], "Delete character under cursor"),
            (kb['delete_line'] * 2, "Delete current line"),
            (kb['undo'], "Undo"),
            (f"Ctrl-{kb['redo'].upper()}", "Redo"),
            (kb['command'], "Enter COMMAND mode"),
            (kb['search'], f"Enter SEARCH mode; {kb['next_match']} repeats the last search"),
            (kb['repeat'], "Repeat the last ':' command (e.g. last :lc, :rename, :w)"),
            (kb['jump_end'], "Jump to the last line. With a count, e.g. "
                              f"12{kb['jump_end']}, jump to scene 12 instead "
                              "(same numbering as the gutter/:scenes)."),
            (kb['dual_dialogue'], "On a CHARACTER line, toggle dual "
                                   "(simultaneous) dialogue -- pairs it with "
                                   "the CHARACTER/DIALOGUE block right above "
                                   "it, printed as two side-by-side columns "
                                   "in the exported PDF. Write that block "
                                   "first, then use this on the *next* "
                                   "cue -- there must already be a "
                                   "CHARACTER+DIALOGUE block directly above "
                                   "to pair with, or nothing happens. Both "
                                   "cues of a paired set show a small '^' "
                                   "in the gutter. Same as ':dual'."),
            (kb['toggle_readonly'], "In a read-only file (opened via "
                                     "`scriptee file.fountain` from the "
                                     "shell), enable editing."),
            ("", None),
            ("INSERT MODE", None),
            ("Enter", "New line. Continuing dialogue keeps flowing straight "
                      "into INSERT. Landing on an ambiguous element (after "
                      "action/heading/shot/transition) instead opens a quick "
                      "':' prompt -- type a letter below to pick the element, "
                      "or just press Enter again to accept the default; "
                      "either way you're back in INSERT with no extra 'i'."),
            ("Tab", "Autocomplete. On a CHARACTER line, cycles matching "
                    "character names used elsewhere in the script. On a "
                    "HEADING line, cycles matching scene headings. On a "
                    "TRANSITION line, cycles built-ins (CUT TO:, FADE OUT.) "
                    "plus ones you've used. Press Tab again to cycle."),
            ("Esc", "Return to NORMAL mode"),
            ("", None),
            ("INLINE STYLING (typed directly into ACTION/DIALOGUE/etc. text)", None),
            ("*text*", "Italic (real Courier-Oblique in the exported PDF)"),
            ("**text**", "Bold (real Courier-Bold in the exported PDF)"),
            ("", None),
            ("SET ELEMENT TYPE (COMMAND mode, e.g. type ':a' + Enter)", None),
            (f":{kb['heading']}", "Scene heading (INT./EXT. ...)"),
            (f":{kb['action']}", "Action"),
            (f":{kb['character']}", "Character cue"),
            (f":{kb['dialogue']}", "Dialogue"),
            (f":{kb['parenthetical']}", "Parenthetical, e.g. (quietly)"),
            (f":{kb['shot']}", "Shot"),
            (f":{kb['transition']}", "Transition, e.g. CUT TO:"),
            ("", None),
            ("OTHER COMMANDS", None),
            (":lc", "Fill an empty line with the last CHARACTER used above "
                     "the cursor -- quick way to resume the same speaker "
                     "after an action beat."),
            (":lh", "Fill an empty line with the last SCENE HEADING used "
                     "above the cursor."),
            (":lt", "Fill an empty line with the last TRANSITION used "
                     "above the cursor."),
            (":rename OLD NEW", 'Rename a character across every CHARACTER '
                                  'cue. Quote multi-word names: :rename '
                                  '"OLD MAN" "YOUNG MAN"'),
            (":dual", f"Same as the {kb['dual_dialogue']} key -- toggle dual "
                       "dialogue on the current CHARACTER line."),
            (":scenes", "Scene list -- j/k move, Enter jump, q/Esc back"),
            (":stats", "Word counts + per-character dialogue breakdown "
                        "(scroll with j/k)"),
            (":scene N", "Jump to scene N (see also <N>G in NORMAL mode)"),
            (":N", "Jump to line N"),
            (":w [path]", "Save (Fountain format)"),
            (":wq", "Save and quit"),
            (":q", "Quit (refuses if there are unsaved changes)"),
            (":q!", "Quit, discarding unsaved changes"),
            (":o", "Open a different screenplay (browse/filter/type a "
                    "path, same picker as [o] at the start menu)"),
            (":pdf [path]", "Export to PDF"),
            (":pdf N", "Sides: export just scene N"),
            (":pdf N-M", "Sides: export scenes N through M"),
            (":pdf char NAME", "Sides: export every scene NAME appears in "
                                "(quote multi-word names). 'character' also "
                                "works. Add a path after NAME/range to save "
                                "somewhere specific."),
            (":cover", "(Re-)fill in the cover page fields (Title, Author, "
                        "...) prefilled with whatever's already set. Runs "
                        "again every time -- unlike the one-off prompt "
                        ":pdf offers when a script has none at all."),
            (":help", "This screen"),
        ]

    def show_help(self):
        rows = self.help_text()
        top = 0
        while True:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            body_h = h - 3
            safe_addstr(self.stdscr, 0, 2,
                        "SCRIPTEE HELP  (j/k, arrows, or PgUp/PgDn to scroll, Esc/q to close)",
                        curses.A_BOLD | self.pairs.get("accent", 0))
            top = max(0, min(top, max(0, len(rows) - body_h)))
            visible = rows[top: top + body_h]
            for i, (left, desc) in enumerate(visible):
                y = 2 + i
                if desc is None:
                    safe_addstr(self.stdscr, y, 2, left, curses.A_BOLD)
                elif left == "":
                    continue
                else:
                    safe_addstr(self.stdscr, y, 2, left, curses.A_BOLD)
                    for line in textwrap.wrap(desc, max(10, w - 26)) or [""]:
                        safe_addstr(self.stdscr, y, 20, line)
                        y += 1
            self.stdscr.refresh()
            ch = read_key(self.stdscr)
            if ch in (27, ord("q")):
                return
            elif ch in (ord("j"), curses.KEY_DOWN):
                top += 1
            elif ch in (ord("k"), curses.KEY_UP):
                top = max(0, top - 1)
            elif ch == curses.KEY_NPAGE:
                top += body_h
            elif ch == curses.KEY_PPAGE:
                top = max(0, top - body_h)

    # -- main loop -----------------------------------------------------
    def run(self):
        curses.curs_set(1)
        self.stdscr.timeout(1000)  # wake periodically (getch -> -1) for autosave
        while True:
            self.render()
            ch = read_key(self.stdscr)
            if ch == -1:
                self.maybe_autosave()
                continue
            if self.mode == "NORMAL":
                result = self.handle_normal(ch)
            elif self.mode == "INSERT":
                result = self.handle_insert(ch)
            elif self.mode == "COMMAND":
                result = self.handle_command_key(ch)
            elif self.mode == "SEARCH":
                result = self.handle_search_key(ch)
            else:
                result = None
            if result == "QUIT":
                return None
            if isinstance(result, tuple) and result[0] == "OPEN":
                # ':o' picked a different file -- hand it back up to
                # main()'s loop, which re-runs open_and_run() on it. Don't
                # try to swap self.buffer/self.filepath in place here: a
                # fresh Editor (and a fresh open_and_run(), which also
                # handles the newer-autosave-than-saved-file prompt) is
                # simpler and less error-prone than resetting every bit of
                # this instance's state by hand.
                return result

    # -- normal mode -----------------------------------------------------
    def handle_normal(self, ch):
        line = self.buffer[self.cy]
        # Any keypress consumes/resets a pending multi-key sequence (e.g. the
        # first "d" of "dd") unless the branch below explicitly re-arms it.
        # Previously this reset only happened on an *unhandled* key, so a
        # stray "d" followed by unrelated navigation/insert keys could leave
        # pending_key stuck at "d" and cause a later, unrelated "d" press to
        # silently delete the current line. See tests/test_scriptee.py.
        pending = self.pending_key
        self.pending_key = None

        # Digits (outside of a ':' command) accumulate as a count prefix
        # for "G" -- e.g. "12G" jumps to scene 12. Anything else clears a
        # half-typed count instead of leaving it to silently apply to some
        # later, unrelated "G" press.
        if ord("0") <= ch <= ord("9"):
            self.count_buffer += chr(ch)
            return
        count_str, self.count_buffer = self.count_buffer, ""

        if ch == 27:  # ESC
            self.status = "-- NORMAL --"
            return
        if ch == self.key("command"):
            self.mode = "COMMAND"
            self.cmdline = ""
            return
        if ch == self.key("search"):
            self.mode = "SEARCH"
            self.cmdline = ""
            return
        if ch == self.key("next_match") and self.search_term:
            self.jump_to_next_match()
            return
        if ch == self.key("jump_end"):
            # Jump-to-scene-number: "<N>" + this key jumps to scene N
            # (gutter numbering); the bare key with no count jumps to the
            # last line, matching vim's usual meaning for an uncounted G.
            if count_str:
                self.jump_to_scene(int(count_str))
            else:
                self.cy = len(self.buffer) - 1
                self.cx = 0
            return
        if ch == self.key("toggle_readonly") and self.readonly:
            self.readonly = False
            self.status = "-- EDITING ENABLED --"
            return
        if self.readonly and ch in (
                self.key("insert_before"), self.key("insert_after"),
                self.key("open_below"), self.key("open_above"),
                self.key("delete_char"), self.key("delete_line"),
                self.key("undo"), self.ctrl_key("redo"),
                self.key("dual_dialogue")):
            self.status = "Read-only -- press 'e' to enable editing."
            return
        if ch == self.key("repeat"):
            if self.last_command is None:
                self.status = "No previous command to repeat."
            else:
                self.execute_command(self.last_command, from_repeat=True)
            return
        if ch == self.key("dual_dialogue"):
            self.toggle_dual_dialogue()
            return
        if ch == self.key("insert_before"):
            self.snapshot()
            self.mode = "INSERT"
            self.status = "-- INSERT --"
            return
        if ch == self.key("insert_after"):
            self.snapshot()
            self.cx = min(self.cx + 1, len(line["text"]))
            self.mode = "INSERT"
            self.status = "-- INSERT --"
            return
        if ch == self.key("open_below"):
            self.snapshot()
            self.open_new_line(self.cy + 1, line["type"], "")
            return
        if ch == self.key("open_above"):
            self.snapshot()
            self.buffer.insert(self.cy, {"type": "action", "text": ""})
            self.cx = 0
            self.mode = "INSERT"
            self.status = "-- INSERT --"
            # Same cache-invalidation requirement as open_new_line() -- "O"
            # inserts above the cursor, which was the other crash path
            # (open_new_line() covers "o" and Enter, but "O" builds its
            # buffer entry inline and was missing this).
            self.touch()
            return
        if ch == self.key("move_left"):
            self.cx = max(0, self.cx - 1)
            return
        if ch == self.key("move_right"):
            self.cx = min(len(line["text"]), self.cx + 1)
            return
        if ch == self.key("move_down"):
            self._move_visual_row(1)
            return
        if ch == self.key("move_up"):
            self._move_visual_row(-1)
            return
        if ch == self.key("delete_char"):
            self.snapshot()
            t = line["text"]
            if self.cx < len(t):
                line["text"] = t[: self.cx] + t[self.cx + 1:]
            self.touch()
            return
        if ch == self.key("undo"):
            self.undo()
            return
        if ch == self.ctrl_key("redo"):
            self.redo()
            return
        if ch == self.key("delete_line"):
            if pending == "delete_line":
                self.snapshot()
                if len(self.buffer) > 1:
                    del self.buffer[self.cy]
                    self.cy = min(self.cy, len(self.buffer) - 1)
                else:
                    self.buffer[0] = {"type": "action", "text": ""}
                self.cx = 0
                self.touch()
            else:
                self.pending_key = "delete_line"
            return

    def _move_visual_row(self, delta):
        """Move the cursor one *visual* (wrapped) row up (delta=-1) or
        down (delta=+1) -- staying inside the current logical line if it
        has another wrapped row in that direction, and only crossing into
        the next/previous logical line once the current one's rows are
        exhausted.

        Before this, "down"/"up" just did cy += delta -- always jumping
        straight to the next *logical* buffer entry regardless of how
        many rows the current line wrapped to. A two-row ACTION line's
        second row was never a landing spot for j/k or the arrow keys at
        all: pressing down from the first row jumped clean over it into
        whatever came next (often a much shorter CHARACTER/DIALOGUE
        line), which is also what made further typing right after land
        somewhere other than where it visually looked like the cursor
        was.

        Horizontal position is preserved by *visual column* (using
        cursor_position()/raw_cx_for_visual(), the same row/col math
        render() itself uses to draw the terminal cursor), not raw
        character offset -- so moving down from column 12 of a wrapped
        row lands on column 12 of the next row (clamped to its length),
        the way any normal text editor's up/down does.
        """
        line = self.buffer[self.cy]
        width = WRAP_WIDTH.get(line["type"], 60)
        row, col = cursor_position(line, width, self.cx)
        n_rows = len(wrapped_lines_for(line, width))
        target_row = row + delta

        if 0 <= target_row < n_rows:
            # Another wrapped row of this same logical line -- land there.
            self.cx = raw_cx_for_visual(line, width, target_row, col)
            return

        new_cy = self.cy + delta
        if new_cy < 0 or new_cy >= len(self.buffer):
            return  # already at the very top/bottom of the document
        self.cy = new_cy
        new_line = self.buffer[self.cy]
        new_width = WRAP_WIDTH.get(new_line["type"], 60)
        new_rows = wrapped_lines_for(new_line, new_width)
        # Moving down lands on the new line's *first* row; moving up
        # lands on its *last* row -- exactly like moving up/down through
        # a wrapped paragraph in any other editor, rather than always
        # landing on row 0 regardless of direction.
        landing_row = 0 if delta > 0 else len(new_rows) - 1
        self.cx = raw_cx_for_visual(new_line, new_width, landing_row, col)

    # -- insert mode -----------------------------------------------------
    def handle_insert(self, ch):
        line = self.buffer[self.cy]
        t = line["text"]
        if ch != 9:
            # Any key other than Tab breaks a Tab-cycle in progress, so the
            # next Tab press starts a fresh completion from scratch.
            self._tab_state = None
        if ch == 27:
            self.mode = "NORMAL"
            self.status = "-- NORMAL --"
            return
        if ch == 9:
            self.handle_tab_complete()
            return
        if ch in (curses.KEY_ENTER, 10, 13):
            self.snapshot()
            before, after = t[: self.cx], t[self.cx:]
            line["text"] = before
            self.open_new_line(self.cy + 1, line["type"], after)
            # open_new_line() itself calls touch() now (it has to, for the
            # "o"/"O" NORMAL-mode paths too) -- also touch() here since the
            # current line's text was just split/shortened, a real edit in
            # its own right.
            self.touch()
            return
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            if self.cx > 0:
                line["text"] = t[: self.cx - 1] + t[self.cx:]
                self.cx -= 1
            elif self.cy > 0:
                prev = self.buffer[self.cy - 1]
                self.cx = len(prev["text"])
                prev["text"] += t
                del self.buffer[self.cy]
                self.cy -= 1
            self.touch()
            return
        if ch == curses.KEY_LEFT:
            self.cx = max(0, self.cx - 1)
            return
        if ch == curses.KEY_RIGHT:
            self.cx = min(len(t), self.cx + 1)
            return
        if ch == curses.KEY_UP:
            self._move_visual_row(-1)
            return
        if ch == curses.KEY_DOWN:
            self._move_visual_row(1)
            return
        if is_printable_char(ch):
            line["text"] = t[: self.cx] + chr(ch) + t[self.cx:]
            self.cx += 1
            self.touch()
            return

    # -- new-line creation (Enter / "o") -----------------------------------
    # Enter/`o` always just opens the new line and drops straight into
    # INSERT -- no popup, no guessing prompt. Continuing dialogue
    # (character/parenthetical/dialogue -> dialogue) picks DIALOGUE;
    # anything else defaults to ACTION. If that default isn't what you
    # want, `:` + a type letter (h/a/c/d/p/s/t) in NORMAL mode changes the
    # current line's type -- that's the only place element type changes
    # happen, so Enter never surprises you or interrupts typing.
    def open_new_line(self, index, prev_type, text):
        new_type = NEXT_TYPE_ON_ENTER.get(prev_type, "action")
        self.buffer.insert(index, {"type": new_type, "text": text})
        self.cy = index
        self.cx = len(text)
        self.mode = "INSERT"
        self.status = "-- INSERT --"
        # touch() here (not left to callers) is what caught the crash below
        # -- inserting a line shifts the buffer index of everything after
        # it, which invalidates any buffer_rev-keyed cache (scene-heading
        # positions, autocomplete candidates, page numbers) even though no
        # *text* changed. Skipping this is exactly what let "o"/"O" above
        # an existing heading crash render() with "ValueError: x not in
        # list" -- the heading-index cache still pointed at the heading's
        # old (pre-insert) position.
        self.touch()

    # -- tab autocomplete ----------------------------------------------
    AUTOCOMPLETE_TYPES = ("character", "heading", "transition")
    BUILTIN_TRANSITIONS = TRANSITION_KEYWORDS

    def autocomplete_candidates(self, t):
        # Cached per element type, invalidated whenever the buffer actually
        # changes (buffer_rev). Tab-cycling itself doesn't recompute (see
        # handle_tab_complete's _tab_state short-circuit); this cache is what
        # keeps a *fresh* Tab press cheap on long scripts too, since it
        # otherwise re-scanned every line in the document from scratch.
        cached = self._autocomplete_cache.get(t)
        if cached is not None and cached[0] == self.buffer_rev:
            return cached[1]
        seen = []
        if t == "character":
            for ln in self.buffer:
                if ln["type"] == "character":
                    base, _ = split_character_cue(ln["text"])
                    if base and base not in seen:
                        seen.append(base)
        elif t == "heading":
            for ln in self.buffer:
                if ln["type"] == "heading" and ln["text"] and ln["text"] not in seen:
                    seen.append(ln["text"])
        elif t == "transition":
            for b in self.BUILTIN_TRANSITIONS:
                seen.append(b)
            for ln in self.buffer:
                if ln["type"] == "transition" and ln["text"] and ln["text"].upper() not in seen:
                    seen.append(ln["text"].upper())
        self._autocomplete_cache[t] = (self.buffer_rev, seen)
        return seen

    def handle_tab_complete(self):
        line = self.buffer[self.cy]
        t = line["type"]
        if t not in self.AUTOCOMPLETE_TYPES:
            return
        if self._tab_state and self._tab_state["cy"] == self.cy:
            state = self._tab_state
        else:
            prefix = line["text"][: self.cx].strip().upper()
            candidates = self.autocomplete_candidates(t)
            matches = [c for c in candidates if c.upper().startswith(prefix)] if prefix \
                else candidates
            if not matches:
                self.status = "No autocomplete matches."
                return
            state = {"cy": self.cy, "prefix": line["text"][: self.cx],
                      "suffix": line["text"][self.cx:], "matches": matches, "idx": -1}
        state["idx"] = (state["idx"] + 1) % len(state["matches"])
        chosen = state["matches"][state["idx"]]
        line["text"] = chosen + state["suffix"]
        self.cx = len(chosen)
        self.touch()
        self._tab_state = state
        self.status = f"[{state['idx']+1}/{len(state['matches'])}] Tab to cycle"

    # -- command mode ------------------------------------------------------
    def handle_command_key(self, ch):
        if ch == 27:
            self.mode = "NORMAL"
            return
        if ch in (curses.KEY_ENTER, 10, 13):
            self.mode = "NORMAL"
            cmd = self.cmdline.strip()
            return self.execute_command(cmd)
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            if self.cmdline:
                self.cmdline = self.cmdline[:-1]
            else:
                # Backspacing on an empty ":" prompt backs all the way out
                # to NORMAL instead of sitting there doing nothing -- so a
                # stray/accidental ":" is as easy to back out of as it was
                # to get into.
                self.mode = "NORMAL"
            return
        if is_printable_char(ch):
            self.cmdline += chr(ch)

    def execute_command(self, cmd, from_repeat=False):
        if cmd == "":
            return
        if not from_repeat:
            # Remember this for "." (repeat last command) in NORMAL mode.
            # Recorded here rather than at the call site so every path that
            # runs a command -- typed, or the auto ":" prompt after Enter --
            # updates it uniformly.
            self.last_command = cmd
        parts = cmd.split(maxsplit=1)
        head = parts[0]
        arg = parts[1] if len(parts) > 1 else None

        if self.readonly and (self.type_for_key(head) or
                               head in ("rename", "lc", "lastchar", "lh", "lt", "dual")):
            self.status = "Read-only -- press 'e' to enable editing."
            return

        type_name = self.type_for_key(head)
        if type_name:
            self.snapshot()
            self.buffer[self.cy]["type"] = type_name
            # touch() bumps buffer_rev (not just self.dirty) -- required
            # because scene_number_at()/_heading_indices() and the Tab
            # autocomplete cache are keyed on buffer_rev to avoid rescanning
            # the whole buffer every render/keystroke. Previously this set
            # the type in place without touching buffer_rev, so e.g. typing
            # ":h" on a line right after a transition left the heading-index
            # cache stale (it didn't know about the new heading yet) --
            # render() then called scene_number_at() on a line that
            # "_heading_indices().index(line_index)" couldn't find,
            # crashing with ValueError. It also meant a type-only edit
            # (with no text change) never marked the file dirty, so :h/:a/
            # etc. on existing text could silently go unsaved.
            self.touch()
            # Drop straight into INSERT, cursor at end of whatever text is
            # already on the line -- same as Enter/"o" opening a new line.
            # Previously this left mode at NORMAL (set by handle_command_key
            # just before calling us), so the very next keystrokes were
            # parsed as NORMAL-mode commands instead of typed text: e.g.
            # typing "Vikranth" right after ":a" fed 'V' to NORMAL (no
            # binding, silently dropped) then 'i' to NORMAL (which *enters*
            # INSERT rather than inserting the letter), and only "kranth"
            # actually landed in the buffer. Auto-entering INSERT here
            # closes that gap -- no dropped keys, and no extra "i"/"a"
            # press needed to keep typing after a type-switch.
            self.cx = len(self.buffer[self.cy]["text"])
            self.mode = "INSERT"
            self.status = f"-- INSERT -- ({TYPE_LABELS[type_name]})"
            return

        if head in ("q", "q!"):
            if self.dirty and head == "q":
                # vim-style refusal: a bare ":q" with unsaved changes does
                # nothing but tell you so -- ":q!" (below) is the explicit
                # override. Autosave already covers the crash/kill case
                # (see AUTOSAVE_INTERVAL), but a screenplay editor whose
                # whole premise is "never lose the user's words" shouldn't
                # let a reflexive ":q" throw away in-progress edits with
                # zero friction, especially since the buffer may hold up
                # to ~15s of changes newer than the last autosave snapshot.
                self.status = ("No write since last change "
                                "(:w to save, or :q! to discard changes)")
                return
            if not self.dirty:
                # No unsaved changes -- any lingering recovery *content*
                # (e.g. from a previous crashed session on this same file)
                # is stale now that the real file already matches the
                # buffer. Cursor position is still worth keeping, though,
                # so the next open of this file resumes here.
                self.discard_recovery()
                self.save_cursor_pos()
            return "QUIT"
        if head == "wq":
            self.save(arg)
            return "QUIT"
        if head == "w":
            self.save(arg)
            return
        if head == "o":
            # Browse for another screenplay to open without leaving the
            # app. Delegates to the same file-picker used at startup --
            # 'q' there cancels back into *this* running editor (run()
            # just keeps looping and re-renders it) rather than quitting
            # Scriptee, since we're mid-session here, not at the start
            # menu.
            path = open_file_screen(self.stdscr, self.cfg)
            self.stdscr.clear()
            if not path:
                self.status = "Ready."
                return
            return ("OPEN", path)
        if head == "pdf":
            self.do_export_pdf(arg)
            return
        if head == "cover":
            self.do_cover_prompt()
            return
        if head == "rename":
            # shlex.split() (not a plain str.split()) so multi-word names
            # can be quoted, e.g. :rename "OLD MAN" "YOUNG MAN" -- a bare
            # arg.split() != 2 check rejected any character name with a
            # space in it (very common: "OLD MAN", "YOUNG SARAH", "AGENT
            # SMITH", ...), even though rename_character() itself has
            # always been able to match and replace a multi-word cue fine.
            try:
                parts = shlex.split(arg) if arg else []
            except ValueError:
                parts = []  # unbalanced quotes
            if len(parts) != 2:
                self.status = ('Usage: :rename OLD NEW  '
                                '(quote multi-word names, e.g. '
                                ':rename "OLD MAN" "YOUNG MAN")')
                return
            old, new = parts
            self.rename_character(old, new)
            return
        if head == "dual":
            self.toggle_dual_dialogue()
            return
        if head == "scenes":
            self.show_scene_list()
            return
        if head == "stats":
            self.show_stats()
            return
        if head == "scene":
            if arg and arg.strip().isdigit():
                self.jump_to_scene(int(arg.strip()))
            else:
                self.status = "Usage: :scene N  (or <N>G in NORMAL mode)"
            return
        if head == "help":
            self.show_help()
            return
        if head in ("lc", "lastchar"):
            self.insert_last_of_type("character")
            return
        if head == "lh":
            self.insert_last_of_type("heading")
            return
        if head == "lt":
            self.insert_last_of_type("transition")
            return
        if head.isdigit():
            self.cy = max(0, min(int(head) - 1, len(self.buffer) - 1))
            self.cx = 0
            return
        self.status = f"Unknown command: {cmd}"

    # -- search --------------------------------------------------------
    def handle_search_key(self, ch):
        if ch == 27:
            self.mode = "NORMAL"
            return
        if ch in (curses.KEY_ENTER, 10, 13):
            self.mode = "NORMAL"
            self.search_term = self.cmdline.strip().lower()
            self.jump_to_next_match()
            return
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            self.cmdline = self.cmdline[:-1]
            return
        if is_printable_char(ch):
            self.cmdline += chr(ch)

    def jump_to_next_match(self):
        if not self.search_term:
            return
        n = len(self.buffer)
        for offset in range(1, n + 1):
            idx = (self.cy + offset) % n
            if self.search_term in self.buffer[idx]["text"].lower():
                self.cy = idx
                self.cx = 0
                self.status = f"Found: '{self.search_term}'"
                return
        self.status = f"No match for '{self.search_term}'"

    # -- scene list popup ------------------------------------------------
    def show_scene_list(self):
        scenes = [(i, l["text"]) for i, l in enumerate(self.buffer) if l["type"] == "heading"]
        if not scenes:
            self.status = "No scene headings yet."
            return
        idx = 0
        while True:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            safe_addstr(self.stdscr, 1, 2, "Scenes  (j/k move, Enter jump, q back)", curses.A_BOLD)
            for i, (line_no, text) in enumerate(scenes[: h - 4]):
                attr = curses.A_REVERSE if i == idx else 0
                safe_addstr(self.stdscr, 3 + i, 2, f"{line_no+1:>4}  {text.upper()[:w-10]}", attr)
            self.stdscr.refresh()
            ch = read_key(self.stdscr)
            if ch in (ord("j"), curses.KEY_DOWN):
                idx = min(idx + 1, len(scenes) - 1)
            elif ch in (ord("k"), curses.KEY_UP):
                idx = max(idx - 1, 0)
            elif ch in (curses.KEY_ENTER, 10, 13):
                self.cy = scenes[idx][0]
                self.cx = 0
                return
            elif ch in (ord("q"), 27):
                return

    # -- stats popup -------------------------------------------------------
    def show_stats(self):
        """':stats' -- word counts and a per-character dialogue breakdown,
        scrollable like :help/:scenes. Built from compute_stats(), which
        does the actual counting so it stays testable without a screen."""
        stats = compute_stats(self.buffer)
        pages = self.page_estimate()
        rows = [
            f"~{pages} estimated page(s)   {stats['scene_count']} scene(s)",
            f"{stats['total_words']} words total  "
            f"({stats['dialogue_words']} dialogue / {stats['action_words']} action)",
            "",
        ]
        if stats["characters"]:
            rows.append("CHARACTER            LINES   WORDS")
            for name, lc, wc in stats["characters"]:
                rows.append(f"{name[:20]:<20}  {lc:>5}   {wc:>5}")
        else:
            rows.append("(no dialogue yet)")
        top = 0
        while True:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            body_h = h - 4
            safe_addstr(self.stdscr, 1, 2,
                        "SCRIPT STATS  (j/k to scroll, Esc/q to close)",
                        curses.A_BOLD | self.pairs.get("accent", 0))
            top = max(0, min(top, max(0, len(rows) - body_h)))
            for i, line in enumerate(rows[top: top + body_h]):
                safe_addstr(self.stdscr, 3 + i, 2, line[: w - 3])
            self.stdscr.refresh()
            ch = read_key(self.stdscr)
            if ch in (27, ord("q")):
                return
            elif ch in (ord("j"), curses.KEY_DOWN):
                top += 1
            elif ch in (ord("k"), curses.KEY_UP):
                top = max(0, top - 1)

    # -- save / export -----------------------------------------------------
    @staticmethod
    def safe_filename(title):
        return re.sub(r'[^A-Za-z0-9_\- ]', '', title or "").strip().replace(" ", "_") or "untitled"

    def resolve_save_path(self, arg):
        if arg:
            p = Path(os.path.expanduser(arg))
        elif self.filepath:
            p = Path(self.filepath)
        else:
            save_dir = Path(os.path.expanduser(self.cfg["general"]["save_dir"]))
            save_dir.mkdir(parents=True, exist_ok=True)
            safe_title = self.safe_filename(self.metadata.get("Title", "untitled"))
            p = save_dir / f"{safe_title}.fountain"
        if p.suffix == "":
            p = p.with_suffix(".fountain")
        return p

    def save(self, arg=None):
        p = self.resolve_save_path(arg)
        atomic_write_text(p, to_fountain(self.metadata, self.buffer))
        # The real file now reflects the buffer, so whatever recovery data
        # was tracking this session (possibly under an "untitled-*" slot,
        # if this is the first save) is redundant -- drop it, then re-key
        # future autosaves to this now-known path.
        self.discard_recovery()
        self.filepath = str(p)
        self.recovery_path = recovery_path_for(p)
        self.save_cursor_pos()
        self.dirty = False
        self.status = f"Saved: {p}"
        record_recent_file(p)

    def do_export_pdf(self, arg=None):
        cover_enabled = self.cfg["general"].get("cover_page", True)

        # Scoped exports -- ":pdf 1-10", ":pdf 5", ":pdf char NAME" -- see
        # parse_pdf_scope()'s docstring for the full grammar. `scope` is
        # either None (no filter, the plain ':pdf'/':pdf PATH' case),
        # "char:usage" (a malformed "char" scope), or the resolved data
        # needed to build the filtered buffer below. Parsed up front (and
        # not just the None-check) so the "no title page in this file"
        # prompt right below can tell a scoped export apart from a plain
        # one -- a sides/character export gets its own dedicated title
        # prompt further down instead, so it isn't asked twice.
        scope, path_arg = parse_pdf_scope(arg)
        if scope == "char:usage":
            self.status = 'Usage: :pdf char NAME  (quote multi-word names, e.g. :pdf char "OLD MAN")'
            return

        # export_pdf() only adds a title page when metadata is non-empty
        # (see its own "if cover_page and metadata:" check) -- a Fountain
        # file imported from elsewhere that never had a title page loads
        # with metadata == {}, so without this the PDF would silently come
        # out with no cover page at all. Ask once, using the same
        # fields/prompt [n]ew screenplays already get; leaving every field
        # blank is treated as "no cover page, on purpose" and isn't asked
        # again this session (see _title_prompt_shown). Skipped entirely
        # if cover pages are turned off in config -- no point prompting
        # for a page that won't be drawn either way -- and skipped for a
        # scoped export, which never uses this prompt/these fields (see
        # the sides-title prompt below instead).
        if (scope is None and cover_enabled and not self.metadata
                and not self._title_prompt_shown
                and self.cfg["general"].get("prompt_missing_titlepage", True)):
            self._title_prompt_shown = True
            probed = new_file_metadata(
                self.stdscr, self.cfg,
                heading="No title page in this file -- add one for the PDF "
                        "cover page?  (leave blank + Enter to skip)")
            self.stdscr.clear()
            if probed:
                self.metadata = probed
                self.dirty = True

        export_buffer = self.buffer
        scope_suffix = ""
        sides_kind = None   # None | "range" | "char" -- which title to build below
        char_name = None
        lo = hi = None
        if scope is not None:
            if isinstance(scope, tuple) and scope[0] == "char":
                name = scope[1]
                wanted = scenes_matching_character(self.buffer, name, self.cfg)
                if not wanted:
                    search_in, aliases = character_export_settings(self.cfg)
                    names = names_for_character(name, aliases)
                    checked = ", ".join(["CHARACTER cues"] +
                                         [t.upper() for t in sorted(search_in)])
                    alias_note = ""
                    if len(names) > 1:
                        others = sorted(names - {name.strip().upper()})
                        alias_note = f" (aliases checked: {', '.join(others)})"
                    self.status = (f"No scenes found for '{name.strip().upper()}'"
                                    f"{alias_note} (checked {checked}).")
                    return
                export_buffer = buffer_for_scenes(self.buffer, wanted)
                scope_suffix = f"_{self.safe_filename(name)}"
                sides_kind = "char"
                char_name = name.strip()
            else:
                total_scenes = len(scene_bounds(self.buffer))
                in_range = {n for n in scope if 1 <= n <= total_scenes}
                if not in_range:
                    lo, hi = min(scope), max(scope)
                    label = str(lo) if lo == hi else f"{lo}-{hi}"
                    self.status = (f"No scene(s) {label} -- this script only has "
                                    f"{total_scenes} scene(s).")
                    return
                export_buffer = buffer_for_scenes(self.buffer, in_range)
                lo, hi = min(in_range), max(in_range)
                scope_suffix = f"_scenes_{lo}" if lo == hi else f"_scenes_{lo}-{hi}"
                sides_kind = "range"

        # A scoped export's own title page -- see [sides] in the config
        # reference. Independent of general.cover_page/metadata: a sides
        # or character-draft PDF still gets a title page (naming the
        # scene range or character, plus an "(an excerpt from ...)" note
        # pointing back at the full script) even for a title-page-less
        # import, since sides_metadata always ends up with a Title of its
        # own regardless of what self.metadata has.
        sides_metadata = None
        sides_subtitle = None
        sides_cover_enabled = False
        if sides_kind is not None:
            sides_cfg = sides_export_settings(self.cfg)
            sides_cover_enabled = bool(sides_cfg.get("cover_page", True))
            if sides_cover_enabled:
                sides_title = ""
                if sides_cfg.get("prompt_title", True):
                    if sides_kind == "char":
                        heading = (f"Sides title for {char_name.upper()}  "
                                   "(leave blank + Enter to auto-generate)")
                    else:
                        label = str(lo) if lo == hi else f"{lo}-{hi}"
                        heading = (f"Sides title for scene(s) {label}  "
                                   "(leave blank + Enter to auto-generate)")
                    sides_title = prompt_sides_title(self.stdscr, heading)
                if not sides_title:
                    sides_title = default_sides_title(
                        sides_cfg, start=lo, end=hi,
                        name=char_name if sides_kind == "char" else None)
                sides_metadata = dict(self.metadata)  # keep the rest (Author, ...) as-is
                sides_metadata["Title"] = sides_title
                sides_subtitle = sides_excerpt_subtitle(
                    self.metadata.get("Title", ""), sides_cfg)

        try:
            if path_arg:
                p = Path(os.path.expanduser(path_arg))
            elif self.filepath:
                p = Path(self.filepath).with_suffix("")
                p = p.with_name(p.name + scope_suffix + ".pdf")
            else:
                save_dir = Path(os.path.expanduser(self.cfg["general"]["save_dir"]))
                safe_title = self.safe_filename(self.metadata.get("Title", "untitled"))
                p = save_dir / f"{safe_title}{scope_suffix}.pdf"
            scene_numbers = self.cfg["general"].get("pdf_scene_numbers", True)
            if sides_kind is not None:
                export_pdf(p, sides_metadata or {}, export_buffer,
                           scene_numbers=scene_numbers,
                           cover_page=sides_cover_enabled,
                           subtitle=sides_subtitle)
            else:
                export_pdf(p, self.metadata, export_buffer,
                           scene_numbers=scene_numbers,
                           cover_page=cover_enabled)
            self.status = f"Exported PDF: {p}"
            if PDF_FONT_WARNING:
                self.status += f" ({PDF_FONT_WARNING})"
        except Exception as e:
            self.status = f"PDF export failed: {e}"

    def do_cover_prompt(self):
        """:cover -- (re-)ask the cover-page fields on demand, prefilled
        with whatever's already set, then drop back into the screenplay.

        Unlike do_export_pdf()'s own one-shot auto-prompt (which only
        fires once per session, see _title_prompt_shown), this is a
        manual command: it resets that one-shot state and shows the
        prompt every single time it's run, however many times that is.
        """
        self._title_prompt_shown = False
        probed = new_file_metadata(
            self.stdscr, self.cfg,
            heading="Cover Page  (Enter keeps a value, clear it + Enter removes it)",
            initial=self.metadata)
        self.stdscr.clear()
        if probed != self.metadata:
            self.metadata = probed
            self.dirty = True
        if not self.cfg["general"].get("cover_page", True):
            self.status = ("Cover page saved, but general.cover_page = false "
                            "in config -- :pdf exports won't include one.")
        elif probed:
            self.status = "Cover page updated."
        else:
            self.status = "Cover page cleared -- :pdf will export with no cover page."

    # -- word / page estimate --------------------------------------------
    PAGE_ESTIMATE_REFRESH = 0.5  # seconds

    def _has_title_page(self):
        """Whether export_pdf() will insert a separate title page ahead of
        the script -- mirrors its own `if cover_page and metadata:` check
        exactly, so the TUI's page count and the exported PDF's page count
        always agree on whether that extra page exists."""
        return bool(self.metadata) and self.cfg["general"].get("cover_page", True)

    def page_estimate(self):
        """Page count for the status bar, matching the exported PDF
        page-for-page (title page included, if there is one).

        This scans the *whole* document via paginate_buffer() -- the same
        row-by-row simulation export_pdf() itself follows -- so recomputing
        it on every keystroke (render() used to call this unconditionally,
        every frame) is the main thing that made long screenplays feel
        laggy -- a status bar estimate doesn't need to be perfectly
        real-time, so it's throttled to recompute at most twice a second.
        styled_wrap()'s own per-line cache (see above) means even a full
        rescan is cheap when nothing but the current line has changed.
        """
        total_pdf_pages, _, cached_at = self._page_cache
        now = time.monotonic()
        if now - cached_at < self.PAGE_ESTIMATE_REFRESH:
            return total_pdf_pages
        _, script_pages = paginate_buffer(self.buffer)
        total_pdf_pages = script_pages + (1 if self._has_title_page() else 0)
        self._page_cache = (total_pdf_pages, script_pages, now)
        return total_pdf_pages

    def _script_pages(self):
        """Script-only page count (no title page) behind the cached
        page_estimate() value -- used for the runtime estimate, where a
        title page isn't screen time. Always freshly consistent with
        page_estimate() since they share the same throttled cache."""
        self.page_estimate()  # refreshes self._page_cache if stale
        return self._page_cache[1]

    def page_number_at(self, line_index):
        """Which page the given buffer line falls on, matching the
        exported PDF exactly (via paginate_buffer(), see its docstring),
        including the title-page offset if export_pdf() would add one.

        Cached on (line_index, len(buffer)): page_number_at() only depends
        on buffer[:line_index + 1], and the overwhelmingly common call
        pattern is page_number_at(self.cy) on every render while the
        cursor sits on one line and the user just types -- buffer[:cy] is
        untouched by edits to line cy itself, so the cached result is
        still correct and this becomes an O(1) cache hit instead of an
        O(cy) rescan on every keystroke. Any navigation (cy changes) or
        structural edit that changes the line count invalidates it and
        forces a fresh scan; a same-length edit to a line *before* the
        cursor (e.g. a global substitution) is the one case this can go
        briefly stale on, which is an acceptable trade for a status-bar
        estimate."""
        cache = self._page_at_cache
        buf_len = len(self.buffer)
        if cache is not None and cache[0] == line_index and cache[1] == buf_len:
            return cache[2]
        starts, _ = paginate_buffer(self.buffer[:line_index + 1])
        script_page = starts[line_index][0]
        result = script_page + (1 if self._has_title_page() else 0)
        self._page_at_cache = (line_index, buf_len, result)
        return result

    def _heading_indices(self):
        """Buffer indices of every HEADING line, in order -- cached against
        buffer_rev so scene_number_at() (called per visible heading, every
        render) doesn't rescan the whole document each time."""
        cached = getattr(self, "_heading_index_cache", None)
        if cached is not None and cached[0] == self.buffer_rev:
            return cached[1]
        indices = [i for i, ln in enumerate(self.buffer) if ln["type"] == "heading"]
        self._heading_index_cache = (self.buffer_rev, indices)
        return indices

    def scene_number_at(self, line_index):
        """1-based scene number for the heading at `line_index` -- None if
        that line isn't a heading. Matches the order :scenes lists them
        in."""
        if self.buffer[line_index]["type"] != "heading":
            return None
        return self._heading_indices().index(line_index) + 1

    def jump_to_scene(self, n):
        """Move the cursor to the Nth scene heading (1-based, same
        numbering as the gutter and :scenes). Bound to <N>G in NORMAL mode
        and to the :scene N command."""
        heads = self._heading_indices()
        if not heads:
            self.status = "No scenes yet."
            return
        n = max(1, min(n, len(heads)))
        self.cy = heads[n - 1]
        self.cx = 0
        self.status = f"Scene {n}/{len(heads)}."

    def runtime_estimate_minutes(self):
        """Rough screen-time estimate using the standard industry rule of
        thumb that one properly-formatted page runs about one minute of
        screen time. Uses script pages only (not the title page, which
        isn't screen time). It's exactly as approximate as that rule is
        (action-heavy pages run faster, dialogue-heavy pages slower) --
        good enough for a status-bar ballpark, not a substitute for a
        table read."""
        return self._script_pages()

    # -- render -----------------------------------------------------------
    # The widest column any element actually uses: CHARACTER (indent 22 +
    # width 40 = 62) and TRANSITION (right-aligned within a 74-wide column)
    # are the outer edges; everything else (heading/action/shot at 0+60,
    # dialogue at 10+40, parenthetical at 18+32) sits inside that. Plus a
    # left gutter for the scene-number column and a matching right pad, so
    # the whole block reads as one deliberate "page" rather than text
    # jammed against the left edge with the rest of a wide terminal empty.
    PAGE_LEFT_GUTTER = 4
    PAGE_CONTENT_WIDTH = 74
    PAGE_RIGHT_PAD = 4
    PAGE_BLOCK_WIDTH = PAGE_LEFT_GUTTER + PAGE_CONTENT_WIDTH + PAGE_RIGHT_PAD

    def render(self):
        stdscr = self.stdscr
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        content_h = h - 3
        # Centers the page block in wide terminals; on anything narrower
        # than the block itself (e.g. a stock 80-col terminal) block_x0 is
        # 0 and origin falls back to the old fixed left margin, so nothing
        # regresses for people already running it at a normal width.
        block_x0 = max(0, (w - self.PAGE_BLOCK_WIDTH) // 2)
        origin = block_x0 + self.PAGE_LEFT_GUTTER
        page_right = origin + self.PAGE_CONTENT_WIDTH
        # Faint page-edge rules so the centered block reads as an
        # intentional page rather than an unexplained gap on both sides --
        # only worth drawing once there's actually margin to frame.
        if block_x0 > 1:
            frame_attr = self.pairs.get("accent", 0) | curses.A_DIM
            left_rule = block_x0
            right_rule = min(w - 1, block_x0 + self.PAGE_BLOCK_WIDTH - 1)
            for row in range(content_h):
                safe_addstr(stdscr, row, left_rule, "\u2502", frame_attr)
                safe_addstr(stdscr, row, right_rule, "\u2502", frame_attr)
        # figure out which buffer line to start rendering from (simple scroll)
        start = max(0, self.cy - content_h // 2)

        screen_row = 0
        cursor_screen_pos = None
        i = start
        while i < len(self.buffer) and screen_row < content_h:
            line = self.buffer[i]
            t = line["type"]
            width = WRAP_WIDTH.get(t, 60)
            indent = INDENT.get(t)
            rows = styled_wrap(line, width)
            plain_rows = ["".join(c for c, _ in row) for row in rows]
            for wi, row in enumerate(rows):
                if screen_row >= content_h:
                    break
                plain = plain_rows[wi]
                if indent is None:  # right aligned (transition)
                    x = max(origin, page_right - len(plain))
                else:
                    x = origin + indent
                # Scene number gutter: only on a heading's first wrapped
                # row, so a long heading that wraps doesn't repeat it.
                if t == "heading" and wi == 0:
                    scene_no = str(self.scene_number_at(i))
                    safe_addstr(stdscr, screen_row, max(0, x - len(scene_no) - 1),
                                scene_no, self.pairs.get("accent", 0))
                # A small "^" gutter marker on both CHARACTER cues of a
                # dual-dialogue pair's first row -- the TUI stays
                # single-column (see toggle_dual_dialogue()'s docstring for
                # why), so this is just a reminder the pairing exists; the
                # two-column layout itself only happens in the exported
                # PDF. Marking *both* cues (not just the flagged second
                # one) makes it possible to spot a pair while scrolling
                # past its first half, which used to show no indicator at
                # all.
                if t == "character" and wi == 0 and (
                        line.get("dual") or find_dual_pair(self.buffer, i) is not None):
                    safe_addstr(stdscr, screen_row, max(0, x - 2),
                                "^", self.pairs.get("accent", 0) | curses.A_BOLD)
                col = x
                for substr, style in row:
                    attr = self.attr_for_type(t)
                    if style == "bold":
                        attr |= curses.A_BOLD
                    elif style == "italic":
                        attr |= curses.A_UNDERLINE
                    safe_addstr(stdscr, screen_row, col, substr, attr)
                    col += len(substr)
                screen_row += 1
            if i == self.cy:
                cursor_row, cursor_col = cursor_position(line, width, self.cx)
                # Clamp to the rows actually drawn -- if this line's own
                # wrapped rows didn't all fit before content_h cut the loop
                # off (a very long single element on a short terminal), the
                # unclamped math could point above where this line started
                # or at a row that was never drawn.
                cursor_row = min(cursor_row, len(rows) - 1)
                base_row = max(0, screen_row - len(rows) + cursor_row)
                base_row = min(base_row, screen_row - 1)
                if indent is None:
                    line_x = max(origin, page_right - len(plain_rows[cursor_row]))
                else:
                    line_x = origin + indent
                cursor_screen_pos = (base_row, line_x + cursor_col)
            i += 1

        # status bar
        mode_str = "-- READ-ONLY --" if self.readonly else f"-- {self.mode} --"
        type_str = TYPE_LABELS.get(self.buffer[self.cy]["type"], "?")
        pages = self.page_estimate()
        mins = self.runtime_estimate_minutes()
        runtime_str = f"{mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"{mins}min"
        dirty_flag = "*" if self.dirty else ""
        fname = Path(self.filepath).name if self.filepath else "[No Name]"
        left = f"{mode_str}  [{type_str}]  {fname}{dirty_flag}"
        cur_page = self.page_number_at(self.cy)
        right = f"~{pages}p (~{runtime_str})  Pg {cur_page}/{pages}  Ln {self.cy+1}/{len(self.buffer)}"
        safe_addstr(stdscr, h - 3, 0, "-" * w, self.pairs.get("accent", 0))
        safe_addstr(stdscr, h - 2, 1, left, curses.A_BOLD)
        safe_addstr(stdscr, h - 2, max(1, w - len(right) - 1), right)

        if self.mode == "COMMAND":
            safe_addstr(stdscr, h - 1, 0, ":" + self.cmdline)
        elif self.mode == "SEARCH":
            safe_addstr(stdscr, h - 1, 0, "/" + self.cmdline)
        else:
            safe_addstr(stdscr, h - 1, 0, self.status[: w - 1])

        # cursor_screen_pos stays None when the current line's row never
        # got drawn at all -- e.g. a terminal resized down so far that
        # content_h is 0 or the current line falls outside the visible
        # window. Previously that meant move() was simply skipped for the
        # frame, leaving the real terminal cursor sitting at whatever
        # position it was left at *before* the resize -- which can be well
        # outside the new, smaller screen. Always move() somewhere valid
        # for the current dimensions (falling back to the bottom-left,
        # which is always in range) so the cursor never goes stale across
        # a resize.
        if cursor_screen_pos:
            sy, sx = cursor_screen_pos
        else:
            sy, sx = h - 1, 0
        stdscr.move(max(0, min(sy, h - 1)), max(0, min(sx, w - 1)))
        stdscr.refresh()


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


if __name__ == "__main__":
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

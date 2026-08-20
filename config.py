"""Config: defaults, loading/merging user config.toml, and the small set
of runtime-tunable globals (AUTOSAVE_INTERVAL, MAX_RECENT_FILES, RECOVERY_DIR,
CONFIG_DIR) that apply_runtime_config() rebinds at startup / in tests.

Other modules that read these four names must do so via `config.NAME`
(live attribute lookup), never `from config import NAME`, since a bare
import copies the value at import time and goes stale the moment this
module reassigns it (see apply_runtime_config()) or a test monkeypatches
it via `monkeypatch.setattr(config, "NAME", ...)`.
"""

import curses
import copy
import re
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # Python < 3.11 fallback (installed by install.sh)
    except ImportError:
        tomllib = None

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
        "save_dir": str(Path.home() / "Documents" / "Scripts"),
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
        # Insert a blank line below the cursor and stay in NORMAL --
        # unlike open_below/"o", never enters INSERT. (Enter, in INSERT
        # mode, already opens a new line of its own -- this is the
        # NORMAL-mode equivalent for when you just want the gap without
        # dropping into typing.)
        "blank_line": "N",
        "move_left": "h", "move_down": "j", "move_up": "k", "move_right": "l",
        "delete_char": "x", "delete_line": "d",
        # Vim-style line register: "yy" copies the current line, "dd"
        # (delete_line above) also copies what it deletes -- "cut", not
        # just remove -- and "p"/"P" paste after/before the cursor line.
        # One line deep, no numbered/named registers -- see Limitations.
        "yank_line": "y", "paste_after": "p", "paste_before": "P",
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


_OLD_DEFAULT_SAVE_DIRS = {"~/Documents", str(Path.home() / "Documents"),
                           "~/Documents/Scriptee",
                           str(Path.home() / "Documents" / "Scriptee")}


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
            # save_dir used to default to bare ~/Documents, then to
            # ~/Documents/Scriptee. If the config on disk still has
            # exactly one of those old untouched values, this is almost
            # certainly a config nobody hand-edited (just an old default
            # written out by an earlier version of write_default_config()),
            # so it's safe to carry it forward to the current default of
            # ~/Documents/Scripts rather than leaving scripts scattered
            # under a stale folder name. A save_dir the user actually
            # customized to something else entirely is left untouched.
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

    Imports fountain/text_format/pdf_geometry *inside* the function body
    (deferred import) rather than at module level: those modules don't
    need anything from config at import time, but pdf_geometry.py's own
    top-level code does reference DEFAULT_CONFIG from this module, so a
    top-level `import pdf_geometry` here would be a circular import.
    Deferring it until the function actually runs sidesteps that.
    """
    import fountain
    import text_format
    import pdf_geometry

    global AUTOSAVE_INTERVAL, MAX_RECENT_FILES

    behavior = cfg.get("behavior", DEFAULT_CONFIG["behavior"])
    AUTOSAVE_INTERVAL = behavior.get("autosave_interval_secs",
                                      DEFAULT_CONFIG["behavior"]["autosave_interval_secs"])
    MAX_RECENT_FILES = behavior.get("max_recent_files",
                                     DEFAULT_CONFIG["behavior"]["max_recent_files"])

    transitions = cfg.get("transitions", DEFAULT_CONFIG["transitions"])
    fountain.TRANSITION_KEYWORDS[:] = transitions.get(
        "builtins", DEFAULT_CONFIG["transitions"]["builtins"])

    fmt = cfg.get("format", DEFAULT_CONFIG["format"])
    text_format.WRAP_WIDTH.update(fmt.get("wrap_width", DEFAULT_CONFIG["format"]["wrap_width"]))
    text_format.INDENT.update(fmt.get("indent", DEFAULT_CONFIG["format"]["indent"]))
    pdf_geometry._recompute_pdf_geometry(cfg)


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
        # This file lives in scriptee_pkg/, one level below the repo
        # root (or below the installed app/ dir) that fonts/ sits in --
        # see install.sh, which copies the whole app/ tree (scriptee.py +
        # scriptee_pkg/ + fonts/) together, not just this one file.
        Path(__file__).resolve().parent.parent / "fonts",
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


def _custom_font_section_span(text):
    """Return (start, end) char offsets of the [format.pdf.custom_font]
    table's body within `text` (after its header line, up to the next
    `[section]` header or EOF), or None if the header isn't present.
    Used to scope regular/bold/italic lookups to that table specifically,
    since those key names aren't unique enough to safely regex the whole
    file."""
    m = re.search(r'^\[format\.pdf\.custom_font\]\s*$', text, re.MULTILINE)
    if m is None:
        return None
    start = m.end()
    nxt = re.search(r'^\[', text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(text)
    return start, end


def _read_font_settings(text):
    """Pull font_family plus the three custom_font paths currently in
    `text`. Any value not found returns None for that key, e.g. a
    hand-edited file missing a key entirely, or the section header
    itself missing -- callers treat that as "don't know, leave alone"
    rather than guessing."""
    m = re.search(r'^font_family\s*=\s*"([^"]*)"', text, re.MULTILINE)
    family = m.group(1) if m else None
    span = _custom_font_section_span(text)
    paths = {"regular": None, "bold": None, "italic": None}
    if span is not None:
        section = text[span[0]:span[1]]
        for name in paths:
            pm = re.search(rf'^{name}\s*=\s*"([^"]*)"', section, re.MULTILINE)
            if pm:
                paths[name] = pm.group(1)
    return family, paths


def upgrade_bundled_font_config():
    """Point an *existing* config.toml at the bundled Courier Prime font,
    the same way write_default_config() does for a brand-new one. Called
    by install.sh on every run, after a fresh config.toml would otherwise
    just be left alone.

    Two cases, both conservative about never touching a real choice:

    1. Untouched default (font_family still literally "courier", all
       three custom_font paths still blank) -- the common "installed a
       while back, before Courier Prime was bundled/detected" case.
       Flips font_family to "custom" and fills in all three paths.

    2. font_family is already "custom", but one or more of the three
       paths is either blank or points at a file that no longer exists
       on this machine -- e.g. install.sh ran once before the fonts/
       directory was bundled (so "custom" was selected but never had
       real files to point at), or a config was copied from another
       machine with stale absolute paths. Only the blank/missing paths
       are repaired to the bundled files; any path that's already set
       *and exists on disk* is left exactly alone, since that's someone's
       real font choice, not a gap to fill.

    Anything else -- font_family isn't "courier" or "custom" at all
    (hand-edited/typo'd), or the file doesn't have a recognizable
    [format.pdf.custom_font] table -- is left untouched rather than
    guessed at. Returns True if the file was rewritten, False otherwise
    (nothing to do, no bundled fonts found, no config on disk yet, or
    already fully pointed at real files).
    """
    if not CONFIG_PATH.exists():
        return False
    fonts = _bundled_courier_prime()
    if not fonts:
        return False
    text = CONFIG_PATH.read_text()
    family, paths = _read_font_settings(text)

    if family == "courier":
        default_line = 'font_family = "courier"    # "courier" or "custom"'
        if default_line not in text:
            return False  # reformatted/hand-edited -- don't guess
        if any(v for v in paths.values()):
            return False  # a path is already filled in -- don't clobber it
        text = (
            text
            .replace(default_line, 'font_family = "custom"     # "courier" or "custom"')
            .replace('regular = ""', f'regular = "{fonts["regular"]}"')
            .replace('bold = ""', f'bold = "{fonts["bold"]}"')
            .replace('italic = ""', f'italic = "{fonts["italic"]}"')
        )
        CONFIG_PATH.write_text(text)
        return True

    if family == "custom":
        span = _custom_font_section_span(text)
        if span is None:
            return False
        start, end = span
        section = text[start:end]
        changed = False
        for name, current in paths.items():
            if current is None:
                continue  # key missing entirely -- don't invent it
            needs_fix = current == "" or not Path(current).expanduser().is_file()
            if needs_fix:
                section = re.sub(
                    rf'^{name}\s*=\s*"[^"]*"',
                    f'{name} = "{fonts[name]}"',
                    section, count=1, flags=re.MULTILINE)
                changed = True
        if not changed:
            return False
        text = text[:start] + section + text[end:]
        CONFIG_PATH.write_text(text)
        return True

    return False  # unrecognized font_family value -- leave it alone


DEFAULT_TOML_TEXT = """\
[general]
save_dir = "~/Documents/Scripts"
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
blank_line = "N"         # insert a blank line below the cursor, stay in NORMAL
move_left = "h"
move_down = "j"
move_up = "k"
move_right = "l"
delete_char = "x"
delete_line = "d"        # pressed twice, vim-style ("dd"); also fills the register
yank_line = "y"          # pressed twice, vim-style ("yy") -- copies into the register
paste_after = "p"        # paste the register as a new line below the cursor
paste_before = "P"       # paste the register as a new line above the cursor
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


"""The Editor class: the main curses-driven editing loop, NORMAL/INSERT/
COMMAND/SEARCH modes, undo/redo, autosave, and every ":command".

Reads config.AUTOSAVE_INTERVAL and pdf_geometry.PDF_FONT_WARNING via
qualified access since both are reassigned at runtime elsewhere (see
config.py / pdf_geometry.py module docstrings) -- everything else here
is either a plain function/class import or a dict/list mutated in place
(WRAP_WIDTH, INDENT, TRANSITION_KEYWORDS), both safe as bare imports.
"""

import curses
import os
import re
import copy
import difflib
import shlex
import textwrap
import time
from datetime import date
from pathlib import Path

import config
import pdf_geometry
from config import DEFAULT_CONFIG, COLOR_MAP
from text_format import (
    WRAP_WIDTH, INDENT, TYPE_LABELS, NEXT_TYPE_ON_ENTER, UPPERCASE_TYPES,
    styled_wrap, wrapped_lines_for, cursor_position, raw_cx_for_visual,
)
from fountain import to_fountain, TRANSITION_KEYWORDS, split_character_cue
from stats import compute_stats
from recovery import (
    atomic_write_text, recovery_path_for,
    cursor_pos_path_for, new_untitled_recovery_path,
    record_recent_file,
)
from pdf_export import (
    find_dual_pair, _dual_pair_opener_above,
    export_pdf, paginate_buffer, scene_bounds, sides_export_settings,
    character_export_settings, names_for_character, scenes_matching_character,
    buffer_for_scenes, parse_pdf_scope, default_sides_title, sides_excerpt_subtitle,
    tag_revision_marks,
)
from ui_helpers import read_key, is_printable_char, safe_addstr, prompt_line
from screens import open_file_screen, new_file_metadata, prompt_sides_title, confirm_yes_no
from versions import (
    version_info_for_path, version_path_for, sibling_versions,
    next_version_number, label_for_version_file, possible_version_collision,
)

# --------------------------------------------------------------------------
# Revision color-set tracking ("what changed since last draft")
# --------------------------------------------------------------------------
#
# The standard US industry page-color rotation, in order -- White is the
# first (unrevised) draft and is never itself "locked" (there's nothing to
# mark changes against yet). Once a script goes out for a revision pass,
# every subsequent locked-in color moves one step through this list;
# after Cherry it wraps back around to a second round starting again at
# Blue (handled by lock_next_revision_color() below), matching the
# convention of re-using the same nine colors on a long-running production
# rather than inventing new ones.
REVISION_COLORS = ["White", "Blue", "Pink", "Yellow", "Green",
                    "Goldenrod", "Buff", "Salmon", "Cherry"]

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
        # Undo TREE (not a flat stack) -- see the "undo / redo" section
        # below for the full design. self.undo_root is the very first
        # state this session started from; self.undo_current always
        # points to the node matching the *live* self.buffer. Every node
        # keeps its children around forever (pruning only ever trims the
        # far, unbranched past off the root -- see _prune_undo_tree()),
        # so undoing and then making a fresh edit creates a new sibling
        # branch instead of destroying whatever redo history existed
        # before -- the actual bug being fixed here.
        self.undo_root = {"buffer": copy.deepcopy(self.buffer), "cy": 0, "cx": 0,
                           "parent": None, "children": [], "ts": time.time()}
        self.undo_current = self.undo_root
        self._awaiting_new_undo_node = False
        self._undo_node_count = 1
        self.register = None             # last yanked/deleted line, for p/P
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
        # Revision color-set tracking ("what changed since last draft") --
        # see the "revision tracking" section below. Reopening a file that
        # was previously locked to a color (its title page carries a
        # "Revision: <color>" field, see lock_revision()) resumes on that
        # same color -- but the baseline to diff against for *this*
        # session always starts as "whatever's on disk right now", since
        # the line-by-line state at the moment of the last lock isn't
        # itself persisted to the .fountain file (only the color name and
        # date are). Practically: marks always mean "changed since this
        # file was opened or since :revision was last run in this
        # session", same honest scope autosave/undo already have.
        self.revision_color = self.metadata.get("Revision", "White") or "White"
        self.revision_baseline = copy.deepcopy(self.buffer)
        self.revision_history = []       # [{"color", "date"}], this session only
        self._revision_marks_cache = None  # (buffer_rev, baseline_id, set) -- see revised_line_indices()
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

    # -- undo / redo (branching tree) ---------------------------------------
    #
    # Every call site elsewhere in this file follows the same two-step
    # pattern: self.snapshot() right before mutating self.buffer, then
    # self.touch() once the mutation is done (possibly several touch()es
    # in a row with no new snapshot() in between, e.g. every keystroke of
    # one INSERT-mode session -- that's what groups a whole typed
    # sentence into a single undo step, exactly like the old flat stack
    # did). What changes here is only *how* those two calls are recorded:
    #
    #   - snapshot() no longer immediately pushes a copy of the buffer.
    #     It just arms `_awaiting_new_undo_node`, so a snapshot() that's
    #     never followed by an actual edit (e.g. "i" then immediately Esc
    #     with nothing typed) creates no undo step at all, instead of a
    #     do-nothing entry cluttering the tree.
    #   - touch() is what actually creates (on the *first* touch after a
    #     snapshot()) or keeps updating (on every touch after that) a
    #     node for the state currently being edited toward. That node's
    #     "buffer" is always a snapshot of self.buffer *as of the last
    #     touch()* -- i.e. exactly what undo() should restore to if the
    #     user backs out of the edit-in-progress.
    #   - undo() walks self.undo_current up to its parent (matching the
    #     old "pop the last pushed state" behavior) but leaves the node
    #     itself in the tree, still linked under its parent's children.
    #   - redo() walks back down to a child -- by default the same one
    #     just undone (remembered via parent["_last_child"]), so plain
    #     u/Ctrl-R still feels exactly like the old linear undo/redo.
    #   - A *fresh* edit made after undo()ing creates a brand new sibling
    #     child under whatever node is currently checked out, rather than
    #     clearing anything -- the old "future" is still hanging off its
    #     parent, just no longer the default redo target. :undotree lists
    #     every such branch (every leaf in the tree) so an abandoned one
    #     is never actually unreachable, only out of the default path.
    def max_undo_steps(self):
        return self.cfg.get("behavior", {}).get("max_undo_steps", 50)

    def snapshot(self):
        self._awaiting_new_undo_node = True

    def _prune_undo_tree(self):
        """Best-effort cap on tree size, mirroring the old flat stack's
        max_undo_steps trim -- but since destroying a branch is exactly
        what this feature exists to stop doing, pruning only ever removes
        the *unambiguous* (single-child, i.e. non-branching) tail of the
        tree hanging off the root, and never touches the node currently
        checked out. A tree with active branches can grow past the
        configured step count; that's the deliberate trade-off of keeping
        branches at all."""
        limit = self.max_undo_steps()
        while (self._undo_node_count > limit
               and len(self.undo_root["children"]) == 1
               and self.undo_current is not self.undo_root):
            child = self.undo_root["children"][0]
            child["parent"] = None
            self.undo_root = child
            self._undo_node_count -= 1

    def _sync_current_undo_node(self):
        """Create (first call since the last snapshot()) or refresh
        (every call after that) the tree node for the edit currently in
        progress, from touch()."""
        if self._awaiting_new_undo_node:
            node = {"buffer": copy.deepcopy(self.buffer), "cy": self.cy, "cx": self.cx,
                    "parent": self.undo_current, "children": [], "ts": time.time()}
            self.undo_current["children"].append(node)
            # Remembered so a plain redo() from here defaults back to this
            # branch -- the "feels just like linear undo/redo" behavior --
            # even though older branches (if any) are still reachable via
            # :undotree.
            self.undo_current["_last_child"] = node
            self.undo_current = node
            self._undo_node_count += 1
            self._awaiting_new_undo_node = False
            self._prune_undo_tree()
        else:
            self.undo_current["buffer"] = copy.deepcopy(self.buffer)
            self.undo_current["cy"] = self.cy
            self.undo_current["cx"] = self.cx

    def _load_undo_node(self, node):
        self.undo_current = node
        self.buffer = copy.deepcopy(node["buffer"])
        self.cy, self.cx = node["cy"], node["cx"]
        self._awaiting_new_undo_node = False
        self.clamp_cursor()
        self.dirty = True
        self.buffer_rev += 1

    def undo(self):
        if self.undo_current["parent"] is None:
            self.status = "Already at oldest change."
            return
        self._load_undo_node(self.undo_current["parent"])
        self.status = "Undo."

    def redo(self):
        children = self.undo_current["children"]
        if not children:
            self.status = "Nothing to redo."
            return
        target = self.undo_current.get("_last_child")
        if target not in children:
            target = children[-1]
        self._load_undo_node(target)
        self.status = "Redo."

    def _undo_leaves(self):
        """Every branch tip in the undo tree, in the order first reached
        by a depth-first walk from the root -- used by :undotree so an
        edit made after undo()ing (which no longer destroys the old
        "future", see above) stays genuinely reachable, not just
        theoretically un-deleted."""
        leaves = []
        stack = [self.undo_root]
        while stack:
            node = stack.pop()
            if node["children"]:
                stack.extend(reversed(node["children"]))
            else:
                leaves.append(node)
        return leaves

    def show_undo_tree(self):
        """:undotree -- browse every branch (not just the one plain u/
        Ctrl-R would walk) and jump straight to any of them. Only useful
        once history has actually forked (undo, then a fresh edit) --
        with a single linear history there's nothing to pick between."""
        leaves = self._undo_leaves()
        if len(leaves) <= 1:
            self.status = "No alternate undo branches yet (only one history so far)."
            return
        idx = leaves.index(self.undo_current) if self.undo_current in leaves else 0
        while True:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            safe_addstr(self.stdscr, 1, 2,
                        "UNDO BRANCHES  (j/k move, Enter jump to that branch, q back)",
                        curses.A_BOLD)
            for i, node in enumerate(leaves[: h - 4]):
                n_lines = len(node["buffer"])
                when = time.strftime("%H:%M:%S", time.localtime(node["ts"]))
                marker = "  <- current" if node is self.undo_current else ""
                label = f"branch {i+1}   {when}   {n_lines} line(s){marker}"
                attr = curses.A_REVERSE if i == idx else 0
                safe_addstr(self.stdscr, 3 + i, 2, label[: w - 4], attr)
            self.stdscr.refresh()
            ch = read_key(self.stdscr)
            if ch in (ord("j"), curses.KEY_DOWN):
                idx = min(idx + 1, len(leaves) - 1)
            elif ch in (ord("k"), curses.KEY_UP):
                idx = max(idx - 1, 0)
            elif ch in (curses.KEY_ENTER, 10, 13):
                self._load_undo_node(leaves[idx])
                self.status = f"Jumped to undo branch {idx+1}/{len(leaves)}."
                return
            elif ch in (ord("q"), 27):
                return

    def clamp_cursor(self):
        self.cy = max(0, min(self.cy, len(self.buffer) - 1))
        self.cx = max(0, min(self.cx, len(self.buffer[self.cy]["text"])))

    def touch(self):
        """Mark the buffer dirty *and* bump buffer_rev so revision-keyed
        caches (e.g. Tab autocomplete's candidate list) know to recompute.
        Use this instead of setting self.dirty directly whenever the
        buffer's actual content changes.

        Also keeps the undo tree's current node in sync -- see the
        "undo / redo (branching tree)" section above."""
        self.dirty = True
        self.buffer_rev += 1
        self._sync_current_undo_node()

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

    # -- revision color-set tracking ---------------------------------------
    #
    # The industry "what changed since last draft" workflow: a script's
    # *first* draft is printed on White pages. Once notes come back and a
    # pass of changes goes out, the whole script reprints on a new color
    # (Blue, then Pink, then Yellow, ...) and every line that changed
    # since the previous color gets a "*" in the margin -- so a reader
    # holding a Blue-and-White script can flip straight to the asterisked
    # lines instead of rereading the whole thing.
    #
    # Modeled here as: self.revision_color is the color currently "in
    # effect" (starts "White", meaning no locked draft yet -- nothing is
    # ever marked while White is in effect, matching how a first draft has
    # nothing to compare itself against). self.revision_baseline is a
    # snapshot of the buffer as it stood at the *start* of the current
    # color -- :revision <color> ("lock_revision") snapshots the buffer as
    # the new baseline and advances the color, so change-marks always mean
    # "changed since the most recent lock", not "changed since the file
    # was created". revised_line_indices() diffs the live buffer against
    # that baseline on demand (content-based, via difflib -- so insertions/
    # deletions elsewhere in the script don't throw off which lines
    # further down are considered "changed", the same way a real diff
    # tool handles it) rather than tagging individual lines as they're
    # edited, which would have to survive every insert/delete/undo/redo
    # perfectly to stay accurate.
    def revised_line_indices(self):
        """Buffer indices whose content differs from self.revision_baseline
        -- i.e. what should carry a "*" mark right now. Empty whenever
        revision_color is still "White" (nothing has ever been locked in
        this session) even if revision_baseline happens to differ, so a
        freshly-opened file with no :revision run yet never shows marks.
        Cached against buffer_rev (like _heading_indices()) since this is
        called on every render() to draw the gutter mark."""
        if self.revision_color == "White" and not self.revision_history:
            return set()
        cached = self._revision_marks_cache
        if cached is not None and cached[0] == self.buffer_rev and cached[1] is self.revision_baseline:
            return cached[2]
        baseline_keys = [(ln["type"], ln["text"]) for ln in self.revision_baseline]
        current_keys = [(ln["type"], ln["text"]) for ln in self.buffer]
        sm = difflib.SequenceMatcher(a=baseline_keys, b=current_keys, autojunk=False)
        marks = set()
        for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                marks.update(range(j1, j2))
        self._revision_marks_cache = (self.buffer_rev, self.revision_baseline, marks)
        return marks

    def lock_revision(self, color):
        """Lock the current buffer in as the new baseline and start
        tracking changes against it under `color` -- ':revision <color>'/
        ':revision next'. Everything that was marked "*" a moment ago
        goes back to unmarked (it's now *part of* this color's printing,
        not a change within it); new marks only appear for edits made
        from this point forward."""
        self.revision_baseline = copy.deepcopy(self.buffer)
        self.revision_color = color
        entry = {"color": color, "date": date.today().isoformat()}
        self.revision_history.append(entry)
        # Persisted to the title page as a plain "Revision: <color>" field
        # (see from_fountain()'s generic "Key: Value" title-page parsing)
        # so reopening the file resumes on the right color -- though, per
        # the __init__ comment above, the *baseline* itself is session-only
        # and starts fresh from whatever's on disk on the next open.
        self.metadata["Revision"] = color
        # Bumped directly rather than via touch() -- locking a revision
        # doesn't change any buffer *content* (nothing here is undoable),
        # it only resets what marks are measured against, so buffer_rev
        # is bumped by hand purely to invalidate revised_line_indices()'s
        # cache (and anything else keyed on it).
        self.dirty = True
        self.buffer_rev += 1
        self.status = (f"Locked revision: {color}.  Changes from here on are "
                        f"marked with * (:revision to check status, "
                        f":revision history for the full log).")

    def do_revision(self, arg=None):
        if not arg or not arg.strip():
            n = len(self.revised_line_indices())
            marked = f"  ({n} changed line(s) since lock)" if n else ""
            self.status = (f"Revision: {self.revision_color}{marked}   "
                            ":revision <color>|next|history|off")
            return
        a = arg.strip()
        al = a.lower()
        if al in ("history", "log"):
            self.show_revision_history()
            return
        if al in ("off", "none", "clear"):
            self.revision_color = "White"
            self.revision_history = []
            self.revision_baseline = copy.deepcopy(self.buffer)
            self.metadata.pop("Revision", None)
            self._revision_marks_cache = None
            self.dirty = True
            self.buffer_rev += 1
            self.status = "Revision tracking cleared -- back to White (no marks)."
            return
        if al == "next":
            try:
                i = REVISION_COLORS.index(self.revision_color)
            except ValueError:
                i = 0
            # Wraps past Cherry back to Blue (index 1), never back to
            # White -- White only ever means "before the first lock".
            color = REVISION_COLORS[i + 1] if i + 1 < len(REVISION_COLORS) else REVISION_COLORS[1]
        else:
            match = next((c for c in REVISION_COLORS if c.lower() == al), None)
            color = match or a.title()
        self.lock_revision(color)

    def show_revision_history(self):
        if not self.revision_history:
            self.status = "No revisions locked yet this session -- still on White."
            return
        rows = [f"{i+1}. {e['color']}  --  {e['date']}"
                for i, e in enumerate(self.revision_history)]
        top = 0
        while True:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            body_h = h - 4
            safe_addstr(self.stdscr, 1, 2,
                        f"REVISION HISTORY  (current: {self.revision_color})"
                        "  (j/k to scroll, Esc/q to close)",
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

    # -- autosave / recovery ---------------------------------------------
    def maybe_autosave(self):
        """Best-effort periodic write to the recovery slot -- never touches
        the real file (only :w/:wq do that) and never raises, so a
        permissions hiccup or full disk can't interrupt editing."""
        if not self.dirty:
            return
        now = time.monotonic()
        if now - self.last_autosave < config.AUTOSAVE_INTERVAL:
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
    @staticmethod
    def _case_matched_replacement(matched_text, new_u):
        """`new_u` (already uppercase) reshaped to mirror the case pattern
        of `matched_text`, an individual prose match of the old name --
        ALL CAPS stays ALL CAPS, "Title Case"/"Sentence case" gets each
        word capitalized, anything else (all-lowercase, or genuinely mixed
        like "mAc") falls back to all-lowercase. Keeps a prose sweep from
        leaving a jarring "JOHN" or "MARK" behind mid-sentence just because
        the CHARACTER-cue form of the name is uppercase. This is a
        heuristic, not a grammar-aware rewrite -- unusual capitalization in
        the source prose can still come out wrong; re-check the result.
        """
        if matched_text.isupper():
            return new_u
        if matched_text[:1].isupper():
            return new_u.title()
        return new_u.lower()

    def rename_character(self, old, new, include_prose=False):
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

        prose_count = 0
        if include_prose:
            # Same line types :pdf char scopes an export by (ACTION +
            # DIALOGUE by default, configurable) -- see
            # character_export_settings()/scenes_matching_character() in
            # pdf_export.py, which this deliberately mirrors rather than
            # inventing its own notion of "prose".
            search_in, _aliases = character_export_settings(self.cfg)
            word_re = re.compile(r'\b' + re.escape(old_u) + r'\b', re.IGNORECASE)
            for ln in changed:
                if ln["type"] not in search_in:
                    continue
                new_text, n = word_re.subn(
                    lambda m: self._case_matched_replacement(m.group(0), new_u),
                    ln["text"])
                if n:
                    ln["text"] = new_text
                    prose_count += n

        if count == 0 and prose_count == 0:
            if include_prose:
                self.status = (f"No CHARACTER cues or prose mentions of "
                                f"'{old_u}' matched.")
            else:
                self.status = f"No CHARACTER cues matched '{old_u}'."
            return
        self.snapshot()
        self.buffer = changed
        self.touch()
        if include_prose:
            self.status = (f"Renamed {count} cue(s) + {prose_count} prose "
                            f"mention(s): {old_u} -> {new_u}")
        else:
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
            (kb['blank_line'], "Insert a blank line below the cursor and "
                                "stay in NORMAL (no INSERT) -- just opens "
                                "up the gap"),
            (f"{kb['move_left']} {kb['move_down']} {kb['move_up']} {kb['move_right']}",
             "Move left / down / up / right"),
            (kb['delete_char'], "Delete character under cursor"),
            (kb['delete_line'] * 2, "Delete (cut) current line -- also "
                                     f"fills the register, like {kb['yank_line']}"
                                     f"{kb['yank_line']}"),
            (kb['yank_line'] * 2, "Yank (copy) current line into the register"),
            (kb['paste_after'], "Paste the register on a new line below the cursor"),
            (kb['paste_before'], "Paste the register on a new line above the cursor"),
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
                                   "in the exported PDF (and here in the "
                                   "editor too). Write that block first, "
                                   "then use this on the *next* cue -- "
                                   "there must already be a "
                                   "CHARACTER+DIALOGUE block directly above "
                                   "to pair with, or nothing happens. Same "
                                   "as ':dual'."),
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
                                  '"OLD MAN" "YOUNG MAN". Add --all to also '
                                  'rename mentions of the name in ACTION/'
                                  'DIALOGUE prose.'),
            (":dual", f"Same as the {kb['dual_dialogue']} key -- toggle dual "
                       "dialogue on the current CHARACTER line."),
            (":undotree", "Browse every undo branch (not just the linear u/"
                           f"Ctrl-{kb['redo'].upper()} path) and jump to any "
                           "of them -- useful after undoing, then making a "
                           "different edit than the one you undid."),
            (":revision", "Show the current revision color + how many lines "
                           "are marked changed since it was locked."),
            (":revision <color>", "Lock the buffer as the new baseline and "
                                   "start tracking changes under <color> -- "
                                   "changed lines get a '*' (in-editor and "
                                   "in the PDF) until the next lock. Standard "
                                   "order: White, Blue, Pink, Yellow, Green, "
                                   "Goldenrod, Buff, Salmon, Cherry."),
            (":revision next", "Lock in the next color in the standard "
                                "rotation (wraps Cherry back to Blue)."),
            (":revision history", "List every color locked in this session."),
            (":revision off", "Clear revision tracking -- back to White, no "
                               "marks."),
            (":version", "Show which version this file is, and name it "
                          "(prompts for a label -- Enter keeps the current "
                          "one, clear + Enter removes it)."),
            (":version new [label]", "Branch off a new numbered sibling "
                                      "file next to this one (Interlude.fountain "
                                      "-> Interlude_v2.fountain, ...), carrying "
                                      "over the current buffer, and switch to "
                                      "editing it. The old file is left "
                                      "untouched. ':version n' also works."),
            (":version switch", "Browse every version of this script -- "
                                 "j/k move, Enter to switch. Also ':version "
                                 "list'."),
            (":version N", "Jump straight to version N."),
            (":version label <name>", "Name the current version in one "
                                       "line, without the prompt."),
            (":version ...!", "Add '!' (e.g. ':version 3!', ':version "
                               "switch!') to switch and discard unsaved "
                               "changes, like ':q!'."),
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

    @staticmethod
    def _flatten_help_rows(rows, wrap_width):
        """Expand help_text()'s (left, desc) rows into one entry per
        actual *screen line*: a header, a blank spacer, a command's own
        first line, or one continuation line per extra line its
        description wraps to.

        show_help() used to scroll/page by *row* (`y = 2 + i` against
        `rows` directly) while a multi-line wrapped description actually
        draws several physical lines -- so the next row's `y` landed back
        on top of lines the previous entry's wrap loop had just written,
        corrupting (overlapping or blanking, depending on scroll offset)
        whatever both entries drew. Flattening first means `top`/`body_h`
        always index real screen lines 1:1, so scrolling can never land
        mid-entry or double up a line again."""
        flat = []
        for left, desc in rows:
            if desc is None:
                flat.append(("header", left, None))
            elif left == "":
                flat.append(("blank", "", None))
            else:
                wrapped = textwrap.wrap(desc, max(10, wrap_width)) or [""]
                flat.append(("cmd", left, wrapped[0]))
                for cont in wrapped[1:]:
                    flat.append(("cont", "", cont))
        return flat

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
            # Reflowed every frame (not cached) since `w` can change on a
            # terminal resize -- cheap either way, this screen is a
            # couple hundred short strings.
            flat = self._flatten_help_rows(rows, w - 26)
            top = max(0, min(top, max(0, len(flat) - body_h)))
            visible = flat[top: top + body_h]
            for i, (kind, left, desc) in enumerate(visible):
                y = 2 + i
                if kind == "header":
                    safe_addstr(self.stdscr, y, 2, left, curses.A_BOLD)
                elif kind == "blank":
                    continue
                elif kind == "cmd":
                    safe_addstr(self.stdscr, y, 2, left, curses.A_BOLD)
                    safe_addstr(self.stdscr, y, 20, desc)
                else:  # "cont" -- wrapped continuation line, no left label
                    safe_addstr(self.stdscr, y, 20, desc)
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
                self.key("blank_line"),
                self.key("delete_char"), self.key("delete_line"),
                self.key("paste_after"), self.key("paste_before"),
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
        if ch == self.key("blank_line"):
            self.insert_blank_line()
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
                # "dd" cuts, not just deletes -- like vim, the removed line
                # lands in the register so a following "p"/"P" can bring it
                # back (elsewhere in the buffer, or after undo-ing the
                # deletion and pasting a second copy).
                self.register = copy.deepcopy(self.buffer[self.cy])
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
        if ch == self.key("yank_line"):
            if pending == "yank_line":
                self.register = copy.deepcopy(line)
                self.status = "Yanked 1 line."
            else:
                self.pending_key = "yank_line"
            return
        if ch == self.key("paste_after"):
            if self.register is None:
                self.status = "Nothing yanked or deleted yet."
                return
            self.snapshot()
            self.buffer.insert(self.cy + 1, copy.deepcopy(self.register))
            self.cy += 1
            self.cx = 0
            self.touch()
            self.status = "Pasted below."
            return
        if ch == self.key("paste_before"):
            if self.register is None:
                self.status = "Nothing yanked or deleted yet."
                return
            self.snapshot()
            self.buffer.insert(self.cy, copy.deepcopy(self.register))
            self.cx = 0
            self.touch()
            self.status = "Pasted above."
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
            c = chr(ch)
            if c.isalpha() and c.islower() and self._autocap_letter(self.cy, self.cx):
                c = c.upper()
            line["text"] = t[: self.cx] + c + t[self.cx:]
            self.cx += 1
            self.touch()
            return

    # -- auto-capitalization -------------------------------------------
    # After ". "/"! "/"? " (or at the start of a line that continues one
    # that just ended that way), the next letter typed starts a new
    # sentence, so capitalize it automatically instead of making the
    # writer reach for Shift every time.
    SENTENCE_ENDERS = (".", "!", "?")

    def _autocap_letter(self, cy, cx):
        """Whether a letter about to be typed at (cy, cx) should be
        upper-cased because it starts a new sentence. Only applies to
        freeform prose types (action/dialogue/parenthetical) --
        heading/character/transition/shot are rendered upper-case
        regardless of what's typed, so capitalization there is moot."""
        line = self.buffer[cy]
        if line["type"] in UPPERCASE_TYPES:
            return False
        before = line["text"][:cx]
        stripped = before.rstrip(" ")
        if stripped:
            # Only counts if at least one space separates the sentence-
            # ender from the cursor (". " not just "." with nothing after).
            return stripped[-1] in self.SENTENCE_ENDERS and stripped != before
        # Nothing but spaces (or nothing at all) before the cursor -- this
        # is effectively the start of the line, so a new-line-after-a-
        # full-stop (via Enter) should still capitalize: look at how the
        # previous line (same type) ended instead.
        if cy == 0:
            return False
        prev = self.buffer[cy - 1]
        if prev["type"] != line["type"]:
            return False
        prev_stripped = prev["text"].rstrip(" ")
        return bool(prev_stripped) and prev_stripped[-1] in self.SENTENCE_ENDERS

    # -- new-line creation (Enter / "o") -----------------------------------
    # Enter/`o` always just opens the new line and drops straight into
    # INSERT -- no popup, no guessing prompt. Continuing dialogue
    # (character/parenthetical/dialogue -> dialogue) picks DIALOGUE;
    # anything else defaults to ACTION. If that default isn't what you
    # want, `:` + a type letter (h/a/c/d/p/s/t) in NORMAL mode changes the
    # current line's type -- that's the only place element type changes
    # happen, so Enter never surprises you or interrupts typing.
    def open_new_line(self, index, prev_type, text):
        if text:
            # There's already-written text being carried onto the new line
            # (e.g. Enter pressed mid-line, or at the very start of a line,
            # just to open up spacing on a rewrite) -- that's pre-existing
            # content, not a fresh element starting from nothing, so keep
            # its original type instead of guessing the next one. The
            # NEXT_TYPE_ON_ENTER guess is only useful when the line being
            # opened is genuinely blank/unwritten.
            new_type = prev_type
        else:
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

    def insert_blank_line(self):
        """Bare NORMAL-mode 'blank_line' key -- insert an empty ACTION
        line below the cursor and move onto it, staying in NORMAL mode
        throughout. Deliberately the odd one out next to open_below/
        open_above ("o"/"O"), which both drop into INSERT: this is for
        just opening up a gap (e.g. spacing before a new scene) without
        being dumped into typing afterward. Enter, in INSERT mode, already
        opens a new line of its own (see handle_insert()) -- this is the
        NORMAL-mode equivalent of that for when you don't want to type
        anything yet.

        Always ACTION regardless of the current line's type, unlike
        open_new_line()'s NEXT_TYPE_ON_ENTER guess -- a bare spacer line
        isn't "continuing" whatever element the cursor happened to be on,
        so there's no type worth guessing at."""
        self.snapshot()
        self.buffer.insert(self.cy + 1, {"type": "action", "text": ""})
        self.cy += 1
        self.cx = 0
        self.status = "Blank line added."
        # Same cache-invalidation requirement as open_new_line()/"O" --
        # inserting a line shifts every buffer index after it, which
        # invalidates buffer_rev-keyed caches (heading positions,
        # autocomplete, page numbers) even though no line's own text
        # changed.
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
                               head in ("rename", "lc", "lastchar", "lh", "lt", "dual",
                                        "revision", "undotree")):
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
                # (see config.AUTOSAVE_INTERVAL), but a screenplay editor whose
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
            # Optional trailing "--all"/"-a" flag: also sweep name
            # mentions in ACTION/DIALOGUE prose, not just CHARACTER cues.
            # Checked/stripped before the arg-count check below so
            # ":rename OLD NEW --all" doesn't get mistaken for the
            # too-many-arguments case.
            include_prose = False
            if parts and parts[-1].lower() in ("--all", "-a"):
                include_prose = True
                parts = parts[:-1]
            if len(parts) != 2:
                self.status = ('Usage: :rename OLD NEW [--all]  '
                                '(quote multi-word names, e.g. '
                                ':rename "OLD MAN" "YOUNG MAN"; --all also '
                                'renames prose mentions, not just cues)')
                return
            old, new = parts
            self.rename_character(old, new, include_prose=include_prose)
            return
        if head == "dual":
            self.toggle_dual_dialogue()
            return
        if head == "undotree":
            self.show_undo_tree()
            return
        if head == "revision":
            self.do_revision(arg)
            return
        if head == "version":
            return self.do_version(arg)
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

    # -- versions ------------------------------------------------------
    # ':version' family -- branch the current script into numbered sibling
    # files (Interlude.fountain = v1, Interlude_v2.fountain, ...) living in
    # the same folder. See versions.py for the on-disk naming rules; this
    # section is just the COMMAND-mode UI on top of it.
    def do_version(self, arg):
        if not (arg and arg.strip()):
            return self.do_version_show()
        parts = arg.strip().split(maxsplit=1)
        sub = parts[0]
        rest = parts[1].strip() if len(parts) > 1 else None
        # A trailing "!" (":version 3!", ":version switch!") forces past
        # the "you have unsaved changes" guard, same spirit as ":q!".
        force = sub.endswith("!") and sub != "!"
        if force:
            sub = sub[:-1]
        sub_l = sub.lower()
        if sub_l in ("new", "n"):
            return self.do_version_new(rest)
        if sub_l in ("switch", "list"):
            return self.do_version_switch(force=force)
        if sub_l == "label":
            if not rest:
                self.status = ("Usage: :version label <name>  (or bare "
                                ":version to be prompted instead)")
                return
            return self.do_version_set_label(rest)
        if sub_l.isdigit():
            return self.do_version_jump(int(sub_l), force=force)
        self.status = ("Usage: :version  |  :version new [label]  |  "
                        ":version switch  |  :version N  |  "
                        ":version label <name>")

    def do_version_show(self):
        """Bare ':version' -- show which version this file is, and let
        the user (re)name it right there, which is the only place a
        version's label lives (see versions.label_for_version_file)."""
        if not self.filepath:
            self.status = "Not saved yet -- :w to save, then :version to start tracking versions."
            return
        base_stem, n = version_info_for_path(self.filepath)
        current_label = self.metadata.get("Version Label", "")
        if self.readonly:
            tail = f" -- '{current_label}'" if current_label else " (unnamed)"
            self.status = (f"Version {n} of '{base_stem}'{tail}  "
                            "(read-only -- press 'e' to rename)")
            return
        self.stdscr.clear()
        h, w = self.stdscr.getmaxyx()
        tail = f" -- currently named '{current_label}'" if current_label else " -- unnamed so far"
        safe_addstr(self.stdscr, 1, 2,
                    f"Version {n} of '{base_stem}'{tail}"[: w - 3], curses.A_BOLD)
        safe_addstr(self.stdscr, 3, 2,
                    "Name this version (Enter keeps it, clear + Enter removes it):"[: w - 3])
        new_label = prompt_line(self.stdscr, 5, 2, "Label", initial=current_label).strip()
        self.stdscr.clear()
        if new_label == current_label:
            tail = f" -- '{current_label}'" if current_label else " (unnamed)"
            self.status = f"Version {n} of '{base_stem}'{tail}"
            return
        if new_label:
            self.metadata["Version Label"] = new_label
        else:
            self.metadata.pop("Version Label", None)
        self.touch()
        self.save()
        self.status = (f"Version {n} named '{new_label}'." if new_label
                        else f"Version {n}'s name cleared.")

    def do_version_set_label(self, text):
        """':version label <name>' -- same as the naming prompt in
        do_version_show(), but scriptable in one line."""
        if self.readonly:
            self.status = "Read-only -- press 'e' to enable editing."
            return
        if not self.filepath:
            self.status = "Save the script first (:w) before naming a version."
            return
        base_stem, n = version_info_for_path(self.filepath)
        if text:
            self.metadata["Version Label"] = text
        else:
            self.metadata.pop("Version Label", None)
        self.touch()
        self.save()
        self.status = (f"Version {n} named '{text}'." if text
                        else f"Version {n}'s name cleared.")

    def do_version_new(self, label):
        """':version new [label]' (':version n' also works) -- branch off
        a new numbered sibling file next to this one, carrying over
        whatever's currently in the buffer (including unsaved edits --
        that in-progress work becomes the new version). The old file on
        disk is never touched: nothing here ever writes to self.filepath,
        only to the freshly computed one, and self.filepath is only
        repointed *after* the new file exists."""
        if self.readonly:
            self.status = "Read-only -- press 'e' to enable editing."
            return
        if not self.filepath:
            self.status = "Save the script first (:w) before creating a version."
            return
        n = next_version_number(self.filepath)
        new_path = version_path_for(self.filepath, n)
        if new_path.exists():
            # Shouldn't normally happen (next_version_number() looks at
            # exactly this), but guard against a stray same-named file
            # dropped in out-of-band rather than silently clobbering it.
            self.status = f"{new_path.name} already exists -- try :version switch."
            return
        if label is None:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            safe_addstr(self.stdscr, 1, 2,
                        f"New version {n} -- name it? (optional, Enter to skip)"[: w - 3],
                        curses.A_BOLD)
            label = prompt_line(self.stdscr, 3, 2, "Label").strip()
            self.stdscr.clear()
        new_metadata = dict(self.metadata)
        if label:
            new_metadata["Version Label"] = label
        else:
            new_metadata.pop("Version Label", None)
        atomic_write_text(new_path, to_fountain(new_metadata, self.buffer))
        self.discard_recovery()
        self.filepath = str(new_path)
        self.metadata = new_metadata
        self.recovery_path = recovery_path_for(new_path)
        self.save_cursor_pos()
        self.dirty = False
        record_recent_file(new_path)
        tail = f" -- '{label}'" if label else ""
        self.status = f"Created version {n}{tail} ({new_path.name}). Now editing this version."

    def do_version_switch(self, force=False):
        """':version switch' (or ':list') -- browse every version of this
        script and jump to one, j/k + Enter. Returns ("OPEN", path) up
        through execute_command/run() to be reopened properly (fresh
        parse, cursor restore, recovery check) rather than trying to
        hand-splice a different file's content into this live session."""
        if not self.filepath:
            self.status = "Not saved yet -- nothing to switch between."
            return
        if self.dirty and not force:
            self.status = ("Unsaved changes -- :w first, or :version switch! "
                            "to discard them and switch anyway.")
            return
        versions = sibling_versions(self.filepath)
        if len(versions) <= 1:
            self.status = "Only one version so far -- :version new to branch off another."
            return
        idx = next((i for i, (_, p) in enumerate(versions) if str(p) == self.filepath), 0)
        while True:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            safe_addstr(self.stdscr, 1, 2,
                        "Versions  (j/k move, Enter switch, q/Esc back)", curses.A_BOLD)
            for i, (n, p) in enumerate(versions[: h - 4]):
                label = label_for_version_file(p)
                current = "  <- current" if str(p) == self.filepath else ""
                line = f"v{n}  {label or '(unnamed)'}  ({p.name}){current}"
                attr = curses.A_REVERSE if i == idx else 0
                safe_addstr(self.stdscr, 3 + i, 2, line[: w - 3], attr)
            self.stdscr.refresh()
            ch = read_key(self.stdscr)
            if ch in (ord("j"), curses.KEY_DOWN):
                idx = min(idx + 1, len(versions) - 1)
            elif ch in (ord("k"), curses.KEY_UP):
                idx = max(idx - 1, 0)
            elif ch in (curses.KEY_ENTER, 10, 13):
                n, p = versions[idx]
                if str(p) == self.filepath:
                    return
                return ("OPEN", str(p))
            elif ch in (ord("q"), 27):
                return

    def do_version_jump(self, n, force=False):
        """':version N' -- jump straight to version N without the picker."""
        if not self.filepath:
            self.status = "Not saved yet -- nothing to switch between."
            return
        if self.dirty and not force:
            self.status = (f"Unsaved changes -- :w first, or :version {n}! "
                            "to discard them and switch anyway.")
            return
        target = version_path_for(self.filepath, n)
        if not target.is_file():
            self.status = f"No version {n} found (looked for {target.name})."
            return
        if str(target) == self.filepath:
            self.status = f"Already on version {n}."
            return
        return ("OPEN", str(target))

    def resolve_version_collision(self, new_path, existing_path):
        """Called from save() when about to create a brand-new file whose
        name looks like it could be another version of `existing_path`
        already sitting in the same folder (e.g. saving "Interlude
        v2.fountain" next to an existing "Interlude.fountain") -- see
        versions.possible_version_collision(). Asks the user whether to
        fold it into that version group (renaming it into the standard
        Base_vN.fountain slot, and offering the same optional label as
        ':version new') or keep it as its own, unrelated script, tucked
        into its own subfolder so the similar name can't collide with --
        or be mistaken for -- the existing one again.

        Returns (path_to_save_to, status_message).
        """
        base_stem, _ = version_info_for_path(existing_path)
        lines = [
            f"'{new_path.name}' looks like it could be another version of",
            f"'{existing_path.name}', already in this folder.",
            "",
            "Save it as a new version of that script?",
        ]
        if confirm_yes_no(self.stdscr, lines, default="y"):
            n = next_version_number(existing_path)
            versioned_path = version_path_for(existing_path, n)
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            safe_addstr(self.stdscr, 1, 2,
                        f"Saving as version {n} -- name it? (optional, Enter to skip)"[: w - 3],
                        curses.A_BOLD)
            label = prompt_line(self.stdscr, 3, 2, "Label").strip()
            self.stdscr.clear()
            if label:
                self.metadata["Version Label"] = label
            else:
                self.metadata.pop("Version Label", None)
            tail = f" -- '{label}'" if label else ""
            return versioned_path, f"Saved as version {n}{tail} of '{base_stem}'."
        # Keep it separate: its own subfolder next to (not mixed in with)
        # the existing script's files, rather than two similarly-named
        # standalone .fountain files sitting loose in the same directory.
        subdir = new_path.parent / new_path.stem
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / new_path.name, f"Kept separate from '{existing_path.name}' -- saved in {subdir.name}/."

    # -- save / export -----------------------------------------------------
    @staticmethod
    def safe_filename(title):
        return re.sub(r'[^A-Za-z0-9_\- ]', '', title or "").strip().replace(" ", "_") or "untitled"

    def new_script_dir(self):
        """Where a brand-new, never-saved script's files live: <save_dir>/
        <Title>/ -- e.g. ~/Documents/Scripts/Interlude/ for a script
        titled "Interlude". Shared by resolve_save_path() and
        do_export_pdf()'s own fallback so the .fountain and its default
        PDF land in the same per-title folder, not just the bare
        save_dir root."""
        base_dir = Path(os.path.expanduser(self.cfg["general"]["save_dir"]))
        safe_title = self.safe_filename(self.metadata.get("Title", "untitled"))
        return base_dir / safe_title, safe_title

    def resolve_save_path(self, arg):
        if arg:
            p = Path(os.path.expanduser(arg))
        elif self.filepath:
            p = Path(self.filepath)
        else:
            save_dir, safe_title = self.new_script_dir()
            save_dir.mkdir(parents=True, exist_ok=True)
            p = save_dir / f"{safe_title}.fountain"
        if p.suffix == "":
            p = p.with_suffix(".fountain")
        return p

    def save(self, arg=None):
        p = self.resolve_save_path(arg)
        status_override = None
        if not p.exists():
            # Only relevant the moment a brand-new file is about to be
            # created -- an ordinary re-save of an already-existing path
            # never hits this. See versions.possible_version_collision()
            # and resolve_version_collision() for what this is catching:
            # e.g. saving "Interlude v2.fountain" right next to an
            # existing "Interlude.fountain" in the same folder.
            collision = possible_version_collision(p)
            if collision is not None:
                p, status_override = self.resolve_version_collision(p, collision)
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
        self.status = status_override or f"Saved: {p}"
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

        # Tag lines changed since the last :revision lock ("_revised") on
        # a shallow copy of the buffer *before* any scene/character
        # filtering below -- buffer_for_scenes() itself shallow-copies
        # each dict again, so the tag survives into a scoped sides/
        # character export too. Built even when nothing is marked (an
        # empty marked-indices set); tag_revision_marks() is a no-op copy
        # in that case, still cheap.
        export_buffer = tag_revision_marks(self.buffer, self.revised_line_indices())
        revision_note = None
        if self.revision_color != "White" or self.revision_history:
            last_date = self.revision_history[-1]["date"] if self.revision_history else ""
            revision_note = f"{self.revision_color.upper()} REVISION" + (f" -- {last_date}" if last_date else "")
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
                export_buffer = buffer_for_scenes(export_buffer, wanted)
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
                export_buffer = buffer_for_scenes(export_buffer, in_range)
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
                # No filepath yet -- this script has never been saved (no
                # :w/:wq). Previously this branch resolved a path under
                # save_dir but never created the directory (unlike
                # resolve_save_path()'s equivalent branch, which does),
                # so export_pdf()'s own open(..., "wb") raised
                # FileNotFoundError the first time -- caught below and
                # shown as "PDF export failed: ...", making :pdf look
                # like it flatly required a prior :wq before it would
                # work at all. Same per-title subfolder as a fresh :w
                # would use (see new_script_dir()), created up front so
                # the export always has somewhere to land.
                save_dir, safe_title = self.new_script_dir()
                save_dir.mkdir(parents=True, exist_ok=True)
                p = save_dir / f"{safe_title}{scope_suffix}.pdf"
            scene_numbers = self.cfg["general"].get("pdf_scene_numbers", True)
            if sides_kind is not None:
                export_pdf(p, sides_metadata or {}, export_buffer,
                           scene_numbers=scene_numbers,
                           cover_page=sides_cover_enabled,
                           subtitle=sides_subtitle,
                           revision_note=revision_note)
            else:
                export_pdf(p, self.metadata, export_buffer,
                           scene_numbers=scene_numbers,
                           cover_page=cover_enabled,
                           revision_note=revision_note)
            self.status = f"Exported PDF: {p}"
            if pdf_geometry.PDF_FONT_WARNING:
                self.status += f" ({pdf_geometry.PDF_FONT_WARNING})"
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
    # Two-column layout for a Fountain dual-dialogue pair (see
    # find_dual_pair()) -- splits PAGE_CONTENT_WIDTH the same way export_pdf()
    # splits the printed page, so the terminal view stops lying about what
    # the PDF will actually look like. Independent of WRAP_WIDTH/INDENT
    # (which are user-configurable per format.wrap_width/indent) since a
    # dual pair's columns are a fixed fraction of the page block, not a
    # per-element setting -- mirrors how PAGE_LEFT_GUTTER etc. above aren't
    # configurable either.
    PAGE_DUAL_GUTTER = 4
    PAGE_DUAL_COL_WIDTH = (PAGE_CONTENT_WIDTH - PAGE_DUAL_GUTTER) // 2

    def _render_dual_block(self, stdscr, origin, screen_row, content_h,
                            i, block1_end, second_start, block2_end):
        """Draw a Fountain dual-dialogue pair (see find_dual_pair()) as two
        real side-by-side columns at PAGE_DUAL_COL_WIDTH, mirroring
        export_pdf()'s layout instead of the old single-column stand-in.
        Returns (screen_row, cursor_screen_pos) -- cursor_screen_pos stays
        None unless self.cy falls on a line inside this block, in which
        case it's the (row, col) render() should move the terminal cursor
        to, same contract as the single-column path below.

        Rows are built per-source-line (not flattened across the whole
        block) specifically so each row stays tagged with the buffer index
        and local wrapped-row it came from -- that's what lets the cursor
        be placed correctly when it's sitting inside this block, the same
        way the single-column loop tracks it via `screen_row - len(rows) +
        cursor_row`.
        """
        col_w = self.PAGE_DUAL_COL_WIDTH
        col1_x = origin
        col2_x = origin + col_w + self.PAGE_DUAL_GUTTER

        def column_rows(start, end):
            # -> [(buf_idx, local_row_idx, row), ...] flattened in draw order
            out = []
            for idx in range(start, end + 1):
                for li, row in enumerate(styled_wrap(self.buffer[idx], col_w)):
                    out.append((idx, li, row))
            return out

        col1 = column_rows(i, block1_end)
        col2 = column_rows(second_start, block2_end)
        n_rows = max(len(col1), len(col2))
        cursor_screen_pos = None

        for r in range(n_rows):
            if screen_row >= content_h:
                break
            for col_x, col in ((col1_x, col1), (col2_x, col2)):
                if r >= len(col):
                    continue
                buf_idx, local_row, row = col[r]
                t = self.buffer[buf_idx]["type"]
                plain = "".join(c for c, _ in row)
                # CHARACTER cues center over their column, like the PDF's
                # _draw_styled_row_centered(); PARENTHETICAL/DIALOGUE stay
                # left-aligned to the column edge -- there's no room in a
                # ~35-char column for the wider single-column indents.
                x = col_x + max(0, (col_w - len(plain)) // 2) if t == "character" else col_x
                cx = x
                for substr, style in row:
                    attr = self.attr_for_type(t)
                    if style == "bold":
                        attr |= curses.A_BOLD
                    elif style == "italic":
                        attr |= curses.A_UNDERLINE
                    safe_addstr(stdscr, screen_row, cx, substr, attr)
                    cx += len(substr)
                if buf_idx == self.cy:
                    cursor_row, cursor_col = cursor_position(self.buffer[buf_idx], col_w, self.cx)
                    if cursor_row == local_row:
                        cursor_screen_pos = (screen_row, x + cursor_col)
            screen_row += 1
        return screen_row, cursor_screen_pos

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
        revised_marks = self.revised_line_indices()

        screen_row = 0
        cursor_screen_pos = None
        i = start
        while i < len(self.buffer) and screen_row < content_h:
            line = self.buffer[i]
            t = line["type"]
            # A dual-dialogue pair (see find_dual_pair()) renders as two
            # real side-by-side columns here, matching what :pdf actually
            # prints, instead of walking the two blocks one after another
            # like ordinary single-column dialogue.
            if t == "character" and not line.get("dual"):
                pair = find_dual_pair(self.buffer, i)
                if pair is not None:
                    block1_end, second_start, block2_end = pair
                    screen_row, dual_cursor = self._render_dual_block(
                        stdscr, origin, screen_row, content_h,
                        i, block1_end, second_start, block2_end)
                    if dual_cursor is not None:
                        cursor_screen_pos = dual_cursor
                    i = block2_end + 1
                    continue
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
                elif t == "character":
                    # Center over the DIALOGUE block below it (not its own
                    # INDENT/WRAP_WIDTH band, which starts further right
                    # and extends well past where dialogue actually ends)
                    # -- INDENT["character"] alone is a fixed left tab, so
                    # a short cue like "JOHN" sat visibly left of a long
                    # one like "THE STRANGER" instead of both reading as
                    # centered above the same dialogue paragraph. Mirrors
                    # the same (width - len(plain)) // 2 treatment
                    # _render_dual_block() already does for character cues
                    # over a dual-dialogue column, just referenced against
                    # dialogue's band instead of the character column's
                    # own. export_pdf() intentionally keeps the fixed-tab
                    # convention for non-dual cues -- this only changes
                    # how it looks while writing.
                    dlg_indent = INDENT.get("dialogue", 10)
                    dlg_width = WRAP_WIDTH.get("dialogue", 40)
                    dlg_center = origin + dlg_indent + dlg_width / 2
                    x = max(origin, round(dlg_center - len(plain) / 2))
                else:
                    x = origin + indent
                # Scene number gutter: only on a heading's first wrapped
                # row, so a long heading that wraps doesn't repeat it.
                if t == "heading" and wi == 0:
                    scene_no = str(self.scene_number_at(i))
                    safe_addstr(stdscr, screen_row, max(0, x - len(scene_no) - 1),
                                scene_no, self.pairs.get("accent", 0))
                # Revision mark: a "*" one column left of the page-block's
                # own left edge, on an element's first wrapped row only
                # (matching how the scene-number gutter above also only
                # marks a heading's first row) -- see revised_line_indices().
                if wi == 0 and i in revised_marks:
                    safe_addstr(stdscr, screen_row, max(0, block_x0),
                                "*", curses.A_BOLD | self.pairs.get("accent", 0))
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



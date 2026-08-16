# Scriptee

A vim-motion TUI screenwriter for Linux.

## Install (any Linux distro)

```bash
chmod +x install.sh
./install.sh
```

`install.sh` detects your package manager (pacman, apt, dnf, zypper, or
apk) to make sure Python 3 + pip are present, then `pip install`s
`reportlab` (for `:pdf` export), drops a default config at
`~/.config/scriptee/config.toml`, and installs a real `scriptee`
executable to `~/.local/bin/scriptee`. If `~/.local/bin` isn't already on
your `PATH`, the script tells you the one line to add for your shell
(fish/bash/zsh).

Scriptee itself is pure Python (`curses` is part of the standard library)
plus `reportlab`, both of which run identically on x86_64 and ARM64 --
`reportlab` ships prebuilt wheels for both, so nothing here is tied to a
particular CPU architecture either.

## Run

```bash
scriptee
```

## Usage

On launch you're asked to create a **[n]ew** screenplay or **[o]pen** an
existing one. New screenplays prompt for Title, Author, Genre, Year, and
two contact fields — all optional, just hit Enter to skip any of them.
(These prompts are configurable — see below.)

### Modes (vim-style)

- **NORMAL** — move around, issue commands. `hjkl` to move, `i`/`a` to
  insert, `o`/`O` for new line below/above, `x` delete char, `dd` delete
  line, `u` undo, `Ctrl-r` redo, `/text` search, `n` next match.
- **INSERT** — type normally. `Esc` back to NORMAL.
- **COMMAND** (`:`) — set line type or run an action, `Enter` to execute.

### Line types (`:` + letter, in NORMAL mode)

| Command | Element         |
|---------|-----------------|
| `:h`    | Scene heading (INT./EXT.) |
| `:a`    | Action          |
| `:c`    | Character       |
| `:d`    | Dialogue        |
| `:p`    | Parenthetical *(bonus, beyond the original spec)* |
| `:s`    | Shot            |
| `:t`    | Transition      |

These set the type of the **current line** (cursor's line), and formatting
(indent, width, uppercasing) updates live.

### Flowing between elements — `Enter`

`Enter` always just opens a new line and drops you straight into INSERT —
no popup, no guessing prompt. After CHARACTER, PARENTHETICAL, or DIALOGUE
it continues into DIALOGUE (continuing dialogue is by far the most common
thing to do next); after anything else (ACTION, HEADING, SHOT,
TRANSITION) it defaults to ACTION. `o` (open line below) behaves the same
way.

If the default isn't the element you want, change it with `:` + a type
letter (`:h`, `:a`, `:c`, `:d`, `:p`, `:s`, `:t`) in NORMAL mode — `:` is
the *only* thing that changes a line's type. `Enter` never guesses its
way into switching modes on you, so backspacing right after `Enter`
behaves exactly like undoing the newline (merges you back into the end of
the previous line), the same as any other editor.

### Tab autocomplete

In INSERT mode, `Tab` autocompletes based on what you've already used
elsewhere in the script:

- **CHARACTER** lines cycle through character names used anywhere else.
- **HEADING** lines cycle through scene headings you've already written.
- **TRANSITION** lines cycle a handful of built-ins (`CUT TO:`,
  `FADE OUT.`, ...) plus any you've typed yourself.

Typing a few letters first narrows the matches; pressing `Tab` again
cycles to the next match; any other keypress ends the cycle.

### `:help`

Pop up a full reference of every mode, keybind, and command. `j`/`k` or
the arrow keys scroll, `Esc`/`q` closes it.

### Inline styling

- `*text*` → italic (rendered underlined in-terminal, since most terminal
  fonts don't render true italics). Exported PDF uses real
  Courier-Oblique/Courier-Bold (two of the PDF spec's 14 built-in "base"
  fonts, so nothing needs installing) for true bold/italic, not the
  underline substitution the terminal view falls back to.
- `**text**` → bold.

Bold/italic spans render correctly even when a wrap point falls in the
middle of one — styling is tokenized before wrapping, not re-parsed per
wrapped sub-line, so a long emphasized phrase that spills onto a second row
keeps its styling on both rows.

### File commands

| Command       | Action |
|---------------|--------|
| `:w`          | save |
| `:w <path>`   | save to a specific path |
| `:wq`         | save and quit |
| `:wq <path>`  | save to path and quit |
| `:q`          | quit without saving |
| `:pdf`        | export industry-formatted PDF next to the saved file |
| `:pdf <path>` | export PDF to a specific path |
| `:scenes`     | pop up a jump-list of all scene headings |
| `:stats`      | word counts + per-character dialogue breakdown |
| `:42`         | jump to line 42 |
| `:rename OLD NEW` | rename a character everywhere — sweeps every CHARACTER cue exactly matching `OLD` (case-insensitive) to `NEW`, preserving any `(V.O.)`/`(CONT'D)`-style extension. One undoable step. |

### `:lc` / `:lh` / `:lt` — resume the last character / heading / transition

On an empty line:

- `:lc` fills it in as a CHARACTER cue using whichever name was most
  recently used above the cursor (stripping any `(V.O.)`-style extension)
  and drops you into INSERT — handy for jumping back to the same speaker
  after an action beat: `o` → `:lc` → `Enter` continues into DIALOGUE as
  usual.
- `:lh` does the same for the last SCENE HEADING used above the cursor
  (verbatim, e.g. re-establishing `INT. KITCHEN - DAY`).
- `:lt` does the same for the last TRANSITION used above the cursor (e.g.
  repeating `CUT TO:` without retyping or Tab-cycling to it).

### `.` — repeat the last command

In NORMAL mode, `.` re-runs whatever `:` command you last executed —
`:lc`, `:rename OLD NEW`, `:w`, `:pdf`, an element-type shortcut, etc.
Doesn't count itself, so repeated `.` presses keep re-running the same
original command rather than "repeating the repeat."

Files save as **`.fountain`** — a plain-text, git-diffable screenplay
format that other screenwriting tools (Highland, Fade In, etc.) can also
open. Default save location is `~/Documents` (configurable), or supply a
full path with `:wq <path>`.

### Undo / redo

`u` undoes the last change; `Ctrl-r` redoes it. Making a new edit after an
undo clears the redo history, same as most editors — the "future" you
undid past is gone once you diverge from it. Up to 50 steps are kept.

### Opening a file — fuzzy filter

At the **[o]pen** screen, `j`/`k` browse the list and `Enter` opens the
highlighted file as before. Press `/` to start typing a filter: the list
narrows live to files whose name contains what you've typed *as a
subsequence*, in order but not necessarily contiguous (so `scnt` matches
`scriptee_confidential.fountain`) — tighter, more contiguous matches sort
first. `Esc` clears the filter and drops back to full browsing, `Enter`
opens the top match. Press `e` instead of `/` if you'd rather type an
exact path that isn't in the list.

### Autosave / crash recovery

While you're editing, Scriptee periodically (every ~15s, only while there
are unsaved changes) writes a recovery snapshot to
`~/.config/scriptee/recovery/` — separate from your real file, which is
only ever touched by `:w`/`:wq`. If Scriptee gets killed, the terminal
closes, or the machine crashes before you save:

- **Opening the same file again** checks for a newer recovery snapshot and
  asks whether to load it instead of the last saved-to-disk version.
- **A screenplay that was never saved at all** (so there's no real path to
  key its recovery slot off) shows up as **[r] Recover unsaved session**
  on the start screen instead.

A clean `:w`/`:wq`, or quitting with `:q` when there's nothing unsaved,
clears the recovery slot — it only ever exists to cover the gap between
edits and your next save.

## Length, page, and scene numbers

The status bar shows an estimated total page count and a rough runtime
(`~12p (~12min)`, using the standard industry rule of thumb that one
properly-formatted page runs about a minute of screen time -- exactly as
approximate as that rule is), plus which of those estimated pages your
cursor is currently on (`Pg 3/12`). Scene headings are numbered in the
left gutter as you write, matching the order `:scenes` lists them in.

These are all estimates based on the same 55-lines-per-page approximation
most screenwriting tools use; they'll drift slightly from a locked PDF's
actual pagination (real production numbering is a manual, one-time-lock
process), but they track your actual line wrapping (via the same
`styled_wrap()` the editor renders with) so they move consistently as you
write rather than jumping around.

## Performance & memory usage

Scriptee is genuinely lightweight — it's a single-process TUI over plain
Python data structures, with no background services, no database, and no
network calls. Measured on Linux (process RSS, i.e. actual resident
memory, not virtual):

| Scenario | RSS |
|---|---|
| Bare Python 3 interpreter | ~9 MB |
| + `curses` (stdlib) | ~10 MB |
| + `reportlab` import (only needed for `:pdf`) | ~27 MB |
| Editing a full feature-length script (~120 pages / 6,600 lines, all lines wrapped/rendered at least once) | ~38–40 MB |

A couple of things worth knowing:

- **`reportlab` is most of the footprint**, not your script. It's
  imported once at startup (for `:pdf` export) whether or not you ever
  run `:pdf` in a given session; without it, a session stays closer to
  15–20 MB even on a long script.
- **Your actual screenplay text is tiny in memory.** A 120-page script is
  a few hundred KB as plain text; wrapped-row caching (`styled_wrap()`,
  see "Why these design choices") adds a modest, bounded amount on top —
  it does *not* grow unboundedly, since each line's cache is just its
  own most recent wrap result, not a history.
- Memory scales roughly linearly with script length and stays flat while
  you type — there's no known leak (no cache in the codebase grows
  without a corresponding invalidation), and nothing here should get
  slower or heavier the longer a session runs.

In short: a full-length feature script comfortably fits in well under
50 MB, even including PDF export support. This isn't a rigorous
benchmark harness — it's process RSS sampled around a synthetic
120-page buffer — but it's representative of real usage.

## Config

`~/.config/scriptee/config.toml` (created on first run) lets you set:

- **`general.save_dir`** — default save/open location.
- **`general.pdf_scene_numbers`** — stamp scene numbers in the PDF's left
  margin on `:pdf` export (default `true`); set `false` for a clean draft
  PDF.
- **`general.prompt_missing_titlepage`** — if a script has no title page
  at all (e.g. a `.fountain` file imported from elsewhere that never had
  one) and you hit `:pdf`, ask once for the same fields `[n]ew` gets
  (default `true`) — otherwise `export_pdf()` silently skips the cover
  page (it only adds one `if metadata:`). Leaving every field blank when
  asked is treated as "no cover page, on purpose" and isn't asked again
  for that file this session. Set `false` to never ask and just export
  without one.
- **`prompts.fields`** — which fields are asked when starting a new
  screenplay, and in what order. Add your own (e.g. `"Draft Date"`) or
  remove ones you don't need.
- **`behavior`** — `autosave_interval_secs`, `max_undo_steps`,
  `max_recent_files`, and `esc_delay_ms` (ncurses' `ESCDELAY` — how long a
  lone Esc waits before it's treated as a real Esc rather than the start
  of an arrow-key sequence).
- **`keybinds`** — every key scriptee reads is remappable here, not just
  the line-type letters: movement (`move_left`/`move_down`/...), `insert_before`/`insert_after`/`open_below`/`open_above`,
  `delete_char`/`delete_line`, `undo`/`redo` (redo is held with Ctrl
  automatically), `search`/`next_match`, `command`, `repeat`,
  `jump_end`, `toggle_readonly`, `dual_dialogue`, plus the original
  `heading`/`action`/`character`/`dialogue`/`parenthetical`/`shot`/
  `transition` line-type setters. `:help` always reflects whatever
  you've actually bound, so it never goes stale against a remap.
- **`colors`** — per-element terminal colors (heading/character/dialogue/
  transition/action/shot/parenthetical/accent).
- **`transitions.builtins`** — the un-forced transition text (`CUT TO:`,
  `FADE OUT.`, ...) recognized on Fountain import and offered by
  Tab-complete. Add your own house-style transitions.
- **`format.wrap_width`** / **`format.indent`** — terminal column widths
  and left-indents per element.
- **`format.pdf`** — `font_size`, `page_size` (`"letter"`/`"a4"`), the
  five column left-edges/margins, `dual_dialogue_gutter_in` (the gap
  between dual-dialogue columns), and `font_family`. These drive
  `paginate_buffer()`'s live page-count estimate too, so the in-editor
  estimate and the actual `:pdf` export can't drift out of sync just
  because you changed a margin.

  Note: `format.wrap_width`/`format.indent` (terminal) and `format.pdf`
  (PDF) are edited independently, not derived from one formula — they
  ship in lockstep by default, but if you change PDF margins without
  touching the matching terminal wrap width, the terminal view and the
  exported PDF will wrap text differently. `config.toml` has a comment on
  this at the point it matters.
- **`format.pdf.font_family`** — `"courier"` (default) uses the PDF
  spec's built-in base-14 Courier family: zero embedding, renders
  identically in every PDF viewer, which is what makes Courier the safe
  default. Set to `"custom"` and fill in `[format.pdf.custom_font]`
  (`regular`, `bold`, `italic` — absolute `.ttf` paths, `bold`/`italic`
  optional and fall back to `regular`) to embed your own font instead —
  e.g. [Courier Prime](https://quoteunquoteapps.com/courierprime/), a
  free font built to match Courier's own spacing but with cleaner,
  more legible letterforms, which is what most modern screenwriting
  apps (Highland, Fade In, Trelby) actually ship as their default these
  days instead of raw Courier. A bad or missing path falls back to
  Courier automatically, with a note appended to the `:pdf` status
  message so a typo is visible instead of silently doing nothing.
- **`format.pdf.emphasis`** — which elements get force-styled on `:pdf`
  export, independent of whatever `**bold**`/`*italic*` markup (or lack
  of it) you actually typed: `heading_bold`, `character_bold`,
  `transition_bold` (all default `true`), and `parenthetical_italic`
  (default `true`). These are the standard screenplay-format
  conventions — turn any one off individually if your house style
  differs, e.g. `character_bold = false` if you don't want CHARACTER
  cues bold. ACTION and DIALOGUE are never force-styled either way; they
  only ever pick up bold/italic from `**`/`*` markers you type yourself,
  and that's unaffected by these settings.

## Why these design choices

- **Fountain as the storage format**, not a custom binary/JSON: it's a
  real, established plain-text screenplay standard. Your scripts stay
  readable, diffable in git, and portable to other tools — without giving
  up any of the modal editing experience on top.
- **`curses` over a widget framework** (e.g. Textual): keeps the
  dependency footprint tiny (curses is stdlib) and gives full manual
  control over the exact column-based formatting screenplays need —
  which fits the "minimal but precise" brief better than a reactive
  widget tree would.

## New this pass (round 2)

Round 1 (below) picked off `:stats` and PDF scene numbers. This pass
closes out the two items that were explicitly deferred at the time —
full config coverage and dual dialogue — which is what this app needed
to call itself v1.

- **Config now covers everything meant to be tunable** — every bare
  NORMAL-mode key (not just the 7 line-type letters), autosave interval,
  undo depth, recent-files cap, `ESCDELAY`, the transition-keyword list,
  and both the terminal wrap widths *and* the PDF's margins/font/page
  size. See the Config section above for the full list. `:help` reads
  its keybind labels from config too, so a remap can't leave the help
  screen showing stale letters.
- **Dual (simultaneous) dialogue** — `:dual` (or the `dual_dialogue` key,
  `D` by default) marks the current CHARACTER cue as pairing with the
  dialogue block directly above it, matching Fountain's own `^`
  convention on save/re-import. Write the first CHARACTER+DIALOGUE block,
  then the second CHARACTER cue, *then* use `:dual`/`D` on that second cue
  — it always pairs backward with whatever's directly above it (blank
  lines OK). If there's nothing valid to pair with there, the flag isn't
  set and the status bar says so, rather than silently doing nothing (see
  "Fixed in this pass" below). `:pdf` prints a marked pair as two real
  side-by-side columns, each cue centered over its own column — not just
  a flag that round-trips through the file with no visual effect. A pair
  that doesn't fit in what's left of the current page moves to a fresh
  page as a whole unit rather than splitting mid-block (a dual pair has
  no clean `(MORE)`/`(CONT'D)` equivalent, so this was a deliberate
  scope line, not an oversight — see Limitations below). The terminal
  itself stays single-column with a small `^` gutter marker on *both*
  cues of a paired set, rather than faking a two-column curses layout —
  the PDF is where the two-column format actually has to be correct.

## Feature ideas that fit the minimal-TUI philosophy

Still worth doing, roughly in order — the bigger production-workflow lifts
that didn't make this pass:

1. **Revision color mode** — industry scripts track revisions with
   colored page sets (white → blue → pink → yellow...). Could tag edited
   scenes and shade them in a sidebar, and stamp the PDF pages
   accordingly. Still the biggest lift on this list — it needs a real
   revision-tracking data model, not just a formatting tweak.
2. **Page-locked PDF layout preview** — a `:preview` command that renders
   the current page's exact PDF layout in the terminal before export, so
   you can sanity-check margins without opening a PDF viewer.
3. **Multi-level undo *tree*** rather than a flat undo/redo stack (see
   "Fixed in an earlier pass" below for the flat redo that already
   exists) — lets you branch and recover any prior state, not just the
   most recent one you diverged from.

(Fountain import notes/boneyard hardening, previously listed here, is
done — see "Fixed in this pass (round 4 — v1)" above.)

## Testing

```bash
pip install --user --break-system-packages pytest
# Optional -- only needed for the PDF-content assertions:
pip install --user --break-system-packages pypdf
python3 -m pytest tests/ -v
```

Covers Fountain round-tripping (including dual dialogue's `^` marker),
the inline bold/italic tokenizer, line wrapping, cursor-position mapping
on wrapped lines, Unicode key input, config merging and every config
value actually being consulted at runtime (not just stored), remappable
keybinds, save path/filename sanitization, `:stats` word/character
counting, PDF scene-number stamping, dual-dialogue pagination/export, and
Editor key handling (`dd`, undo, insert, `:` commands) — including
regression tests for each bug fixed below. 211 tests total.

## Fixed in this pass (round 6)

- **A `.fountain` file imported from elsewhere with no title page
  exported to PDF with no cover page, silently.** `export_pdf()` has
  always only added a title page `if metadata:` — correct behavior, but
  nothing ever told you *why* your export came out cover-page-less if
  the import never had one to begin with (unlike a script started with
  `[n]ew`, which always gets asked). `:pdf` now checks for this case: if
  `metadata` is empty, it prompts once for the same fields `[n]ew`
  screenplays get, right before exporting, instead of exporting blind.
  Declining (leaving every field blank) is remembered as intentional for
  the rest of that session, so it's a one-time ask, not a nag on every
  `:pdf`. New `general.prompt_missing_titlepage` config flag (default
  `true`) turns this off entirely for anyone who'd rather it just export
  quietly either way. Gated to the export action itself, not file-open —
  opening a file to peek at it (including the read-only `scriptee
  some/file.fountain` shell launch) never triggers a prompt, only a
  deliberate `:pdf` does.

## Fixed in this pass (round 5)

- **No way to turn off the standard forced bold/italic on `:pdf`
  export.** Scene headings, character cues, and transitions were always
  bold, and parentheticals always italic, on export — a fixed,
  hardcoded screenplay-format convention with no override. Added
  `[format.pdf.emphasis]` (`heading_bold`, `character_bold`,
  `transition_bold`, `parenthetical_italic`, all `true` by default) so
  anyone whose house style differs — e.g. not wanting CHARACTER cues
  bold — can turn just that one off. ACTION/DIALOGUE were never
  affected either way; they already only get bold/italic from `**`/`*`
  markers typed inline, and still do. Also added a test that parses
  `DEFAULT_TOML_TEXT` (the config written on first run) and checks it
  deep-equals `DEFAULT_CONFIG` (the in-code fallback) — these are two
  independently hand-maintained copies of the same defaults with
  nothing generating one from the other, and this new setting almost
  shipped in one but not the other before this test caught it.

## Fixed in this pass (round 4 — v1)

- **`.fountain` import had no notion of Fountain's two invisible-content
  syntaxes**, boneyard comments (`/* ... */`, which can span several
  lines) and notes (`[[ ... ]]`, usually inline). Other tools (Highland,
  Slugline, Fade In) never print or import these as visible script
  content; Scriptee previously imported their literal text as ordinary
  ACTION/DIALOGUE. Both are now stripped before line classification, so a
  `.fountain` file round-tripped through another app that uses them opens
  clean. This was item 1 on the "Feature ideas" list below and is now
  done.
- **The terminal cursor could go stale on an extreme resize.** If the
  window shrank so far that the current line's row genuinely couldn't be
  drawn (e.g. a terminal down to 2-3 rows), `render()` skipped
  repositioning the terminal cursor for that frame entirely, leaving it
  sitting wherever it was *before* the resize — potentially well outside
  the new, smaller screen. `render()` now always repositions the cursor
  every frame, falling back to a safe in-bounds position when the current
  line isn't drawable, so a resize can no longer leave a stale cursor
  behind. Added test coverage driving `render()` through a sequence of
  very wide, very narrow, and near-zero-content terminal sizes — this had
  been an untested claim ("hasn't been specifically hardened or tested")
  rather than a checked one; it's checked now.
- Removed a leftover unused `reportlab.lib.pagesizes.letter` import —
  page sizing has gone through the `PAGE_SIZES_PT` dict for a while, so
  this was dead weight flagged by a lint pass, not a behavior change.

## Fixed in this pass (round 3)

- **PDF export could run longer than industry standard if you used blank
  lines in the editor for your own visual spacing.** The standard single
  blank-line gap between elements was already being added automatically
  on export — but any blank buffer lines you'd also typed (e.g. a few
  empty lines between two ACTION beats to give yourself visual breathing
  room while writing) got counted as *additional* gap on top of that,
  each one its own row. A run of manual blank lines now contributes
  nothing to the exported PDF or the live page-count estimate — spacing
  in the editor is entirely up to you, but `:pdf` always comes out at
  exactly the one-blank-line industry-standard gap, regardless of how
  much whitespace you leave yourself while writing.
- **Dual dialogue could be marked with no error and no visible effect.**
  `:dual`/`D` always set the flag on the current CHARACTER cue, even when
  there was nothing above it to actually pair with (e.g. the very first
  line of the script, or a cue with only an ACTION line above it) — the
  status bar said "on" either way, but the PDF quietly printed it as
  ordinary single-column dialogue, since pairing only ever happens if a
  valid CHARACTER+DIALOGUE block sits directly above. The toggle now
  checks for a real pairing target first and refuses with a clear message
  if there isn't one, so the flag can no longer silently do nothing. The
  in-editor `^` gutter marker also now shows on *both* cues of a paired
  set (previously only the second, flagged one), so a pair stays visible
  while scrolling past its first half too.
- **PDF font was hardcoded to the built-in Courier base-14 font**, with
  no way to use anything else. `[format.pdf].font_family` now accepts
  `"custom"` plus a `[format.pdf.custom_font]` path to your own `.ttf` —
  e.g. Courier Prime — alongside the existing `"courier"` default. See
  the Config section above.

## Fixed in this pass

- **Non-ASCII keystrokes were silently dropped everywhere text gets
  typed** — a screenplay name with an accent (`José`), a typographic em
  dash for interrupted dialogue (`Wait, I didn't—`), curly quotes, or any
  non-English script just did nothing when typed, with no error. Every
  input point (the editor itself, all prompts, search, the open-file
  filter) read keys with `stdscr.getch()`, which only ever returns one
  raw byte at a time; a multi-byte UTF-8 character arrived as several
  bytes each individually >127, none of which passed the ASCII-only
  `32 <= ch < 127` "is this typeable text" check anywhere in the file —
  so it wasn't garbled, it just vanished, a byte at a time. Replaced
  every `getch()` call with `read_key()`, which uses `stdscr.get_wch()`
  (proper multi-byte decoding) and normalizes its result so every
  existing `ch == ord("d")`-style comparison keeps working unchanged.
  Also added `locale.setlocale(locale.LC_ALL, "")` at startup, since
  `get_wch()` (for input) and `addstr()` (for on-screen display of what
  you typed) both need a non-"C" locale to handle non-ASCII correctly.
- **`:rename` couldn't handle a character name with a space in it** —
  `OLD MAN`, `YOUNG SARAH`, `AGENT SMITH`, etc. The command parsed its
  argument with a plain `arg.split()` and required exactly two resulting
  words, so any multi-word name (extremely common) failed with a
  `Usage: :rename OLD NEW` error before it ever reached
  `rename_character()` — which itself matches and replaces a multi-word
  cue just fine and always has. Now parsed with `shlex.split()`, so
  multi-word names work when quoted: `:rename "OLD MAN" "YOUNG MAN"`.
  Single-word names are unaffected and still work unquoted.

## Fixed in an earlier pass

- **The cursor visibly froze every time you pressed the space bar, and
  could permanently swallow the first character(s) typed after switching
  into INSERT.** This was the real "typing feels broken" bug. The wrap
  algorithm doesn't render a run of whitespace until a following word
  gives it something to sit between (matching `textwrap`'s collapsing —
  a space right after a word, or several leading spaces on an otherwise
  blank line, don't show up on screen until you type the *next* word).
  The cursor math was clamping to the end of whatever was actually
  rendered, so it sat frozen through every space press and only caught
  up (jumping forward several columns at once) once you typed something
  after it — and if a leading space was the very first thing typed on a
  line, it just vanished, permanently shifting the cursor for that line.
  Replaced the cursor math with `cursor_position()` (backed by
  `_collapsed_offset()`), which lets the cursor keep advancing one
  column per raw character typed even through whitespace that isn't
  rendered yet — there's nothing to draw for a bare space, so there's no
  reason for the cursor to sit still while you type one. The one
  remaining rough edge: if you type two-or-more consecutive spaces and
  *then* keep typing another word right after them, the extra spaces
  collapsing down causes a one-time single-column reflow right at that
  moment (not a freeze, not a lost character) — normal single-spaced
  typing, by far the common case, doesn't hit this at all.

## Fixed in an earlier pass

- **The cursor drifted out of sync with the actual text on any line using
  `*italic*`/`**bold**` styling** — arguably the biggest "the cursor just
  feels broken" culprit, since it wasn't occasional, it was every single
  styled line for the rest of that line once one span closed. The cursor
  position (`cx`) is tracked in *raw* text (including the literal `*`/`**`
  marker characters), but it was being mapped directly onto the *display*
  text, where those markers are stripped out and rendered as
  underline/bold instead. The two only match up until the first complete
  span closes — the instant you finish typing a closing `*` or `**`, the
  display text is suddenly shorter than the raw text, and every keystroke
  or arrow-key move after that landed the terminal cursor increasingly far
  from where you actually were. Added `display_offset()`, which walks the
  same tokenizer boundaries `render()` draws from to convert a raw offset
  into the correct display offset before it's ever turned into a screen
  row/column — so raw and display positions are always computed from one
  shared source of truth instead of drifting apart.
- **The terminal cursor could land off-screen or in the wrong spot** if
  the current line's own wrapped rows didn't all fit in the visible area
  (a very long single action/dialogue block on a short terminal). The
  screen-position math assumed every wrapped row of the current line got
  drawn; now it's clamped to the rows actually drawn.

## Fixed in an even earlier pass

- **PDF export ran several pages longer than the same script in other
  screenwriting apps** (e.g. 43 pages here vs. 37-38 elsewhere). Every
  scene heading was getting an extra blank-line decrement on top of the
  standard one-blank-line gap already added after the previous element —
  so every heading was preceded by *two* blank lines instead of one. On a
  multi-scene script that adds up fast. Removed the duplicate decrement;
  spacing before/after every element type is now uniform.
- **The `Enter`-triggered `:` prompt (added in an earlier pass to
  disambiguate what comes after ACTION/HEADING/SHOT/TRANSITION) made
  typing and backspacing feel broken.** Landing in COMMAND mode on a new
  line meant backspace no longer merged back into the previous line (it
  just edited the empty command string instead), so the everyday "type,
  overshoot, backspace back into the previous line" flow silently broke
  every time you ended one of those four line types. `Enter` (and `o`) now
  always drop straight into INSERT with no prompt — same defaulting as
  before (ACTION, or DIALOGUE when continuing a speech), but backspace
  right after `Enter` now genuinely undoes the newline, merging back into
  the previous line like any other editor. `:` + a type letter in NORMAL
  mode is now the *only* way an element's type changes. As a small related
  fix, backspacing on an already-empty `:` prompt now backs out to NORMAL
  instead of doing nothing.
- **`Esc` felt like it hung for a second-plus** (worst on closing
  `:help`). This wasn't the help screen specifically -- it's `ncurses`
  waiting `ESCDELAY` milliseconds after a lone `Esc` byte to check whether
  it's actually the start of a longer escape sequence (arrow keys, etc.
  all start with `Esc` on the wire); the default is 1000ms and some
  terminals/multiplexers push it higher. Scriptee doesn't use any
  Alt-modified sequences, so `ESCDELAY` is now set to 25ms at startup.
  Esc should feel instant everywhere now, not just in `:help`.
- **`install.sh` only worked on Arch (`pacman`).** Rewrote it to detect
  pacman/apt/dnf/zypper/apk and use whichever is present; falls back to a
  clear error telling you what to install by hand if none match. Nothing
  in Scriptee itself is architecture-specific (pure Python + stdlib
  `curses` + `reportlab`, which ships ARM64 and x86_64 wheels), so this
  was purely a distro-detection gap, not a real portability bug.
- **PDF export silently ignored `**bold**`/`*italic*` markers** and
  printed the literal `**`/`*` characters instead. Now uses real
  `Courier-Bold`/`Courier-Oblique` -- both are PDF "base 14" fonts every
  viewer has built in, so no font file needed. PDF export also now
  stamps industry-standard page numbers (top-right, `2.`/`3.`/...,
  unnumbered title page and first script page).
- **Tab autocomplete rescanned the whole document on every fresh Tab
  press.** Character/heading/transition candidate lists are now cached
  and only recomputed when the buffer actually changes.
- **No way to resume a heading or transition, only a character.** `:lc`
  (resume last CHARACTER) already existed; added `:lh` and `:lt` for
  SCENE HEADING and TRANSITION the same way.
- **No command repeat.** Added `.` in NORMAL mode to re-run whatever `:`
  command was last executed.
- **No sense of how long the script is while writing, beyond a raw page
  count.** Status bar now also shows an estimated runtime and which
  estimated page the cursor is on; scene numbers now render in the
  editor's left gutter as you write, not just in the `:scenes` popup.

## Fixed in an earlier pass

- **Typing lagged noticeably on long screenplays.** The status bar's page
  estimate (`page_estimate()`) re-wrapped **every line in the entire
  document** from scratch on **every single keystroke**, since `render()`
  called it unconditionally on every frame — on a 100+ page script that's
  a full-document regex/word-wrap pass per character typed. Two fixes:
  `styled_wrap()` now memoizes its result on each line dict (invalidated
  automatically when that line's own text/type/width changes), and
  `page_estimate()` is throttled to recompute at most twice a second
  instead of every frame — a status-bar estimate doesn't need to be
  real-time. Typing should now feel smooth regardless of document length.
- **`Enter` always silently guessed the next element.** Continuing
  dialogue (character/parenthetical/dialogue → dialogue) is unambiguous
  and still flows straight into INSERT with no interruption. But ending an
  action/heading/shot/transition line — where the guess is just a generic
  "action" catch-all — now opens a quick `:` prompt instead: type a
  line-type letter + `Enter` to override it, or `Enter` alone to accept
  the default, either way landing back in INSERT with no extra `i`. `o`
  behaves the same way.
- **Config silently ignored on Python < 3.11.** `install.sh` installs
  `tomli` as a fallback TOML parser on older Python, but `scriptee.py`
  only ever tried `import tomllib` and fell back to `None` — meaning
  `~/.config/scriptee/config.toml` was never actually read on anything
  older than 3.11, and every user setting (save dir, keybinds, colors,
  prompt fields) was silently dropped in favor of hardcoded defaults.
  Now falls back to `import tomli as tomllib`.
- **Stale `d` could silently delete an unrelated line.** `dd` (delete
  line) worked by arming a `pending_key = "d"` flag on the first press,
  but only *unhandled* keys cleared it. Pressing `d` once, then doing
  anything else — moving with `hjkl`, entering/leaving INSERT mode,
  searching — left the flag armed, so a later, completely unrelated `d`
  press would immediately delete whatever line the cursor happened to be
  on, with no warning. `pending_key` is now reset on every keypress
  unless that keypress is itself the second `d` of a real `dd`.
- **Cursor drawn in the wrong place on any wrapped line.** The terminal
  cursor was only ever positioned against the *first* wrapped sub-line of
  the current line; on any action/dialogue line long enough to wrap
  (i.e. most of them), the blinking cursor rendered on the wrong row and
  usually the wrong column once you moved past the first ~40-70 columns
  of raw text, even though edits were still applied at the right
  character position internally. Added `locate_cursor()` to map the raw
  cursor offset onto the correct wrapped row/column, with tests.
  (Textwrap collapses runs of whitespace, so this is exact for normal
  single-spaced text and an approximation for irregular spacing — same
  caveat any wrapped-line editor has.)
- **Opening a directory or unreadable file crashed the whole app.** The
  `except FileNotFoundError` around opening a file didn't catch
  `IsADirectoryError`/`PermissionError`/decode errors, so e.g. fat-
  fingering a directory into the `:o` path prompt took down curses with
  a raw traceback. Now caught and surfaced as a status message with a
  blank buffer instead.
- **`:pdf` used a different filename sanitizer than `:w`.** Saving used a
  regex to strip unsafe characters from the title for the default
  filename; PDF export only did `.replace(" ", "_")`, so a title with
  `/`, `:`, quotes, etc. could produce a broken path on export but not on
  save. Both now share one `safe_filename()`.
- **`dd`-ing the last remaining line left its old type behind.** Deleting
  down to one line cleared the text but kept whatever element type
  (HEADING, CHARACTER, ...) it had, so the "empty" line after clearing
  everything could still render/uppercase like a scene heading. Now
  resets to `action`, matching a genuinely blank document.
- **No redo.** `u` undid a change with no way back. Added `Ctrl-r` redo, a
  standard stack that clears on a fresh edit after an undo (see "Undo /
  redo" above).
- **No fuzzy filter in the open-file list.** Long lists meant scrolling
  with `j`/`k` through everything. `/` now opens a live type-ahead filter
  (see "Opening a file — fuzzy filter" above).
- **No character rename sweep.** Renaming a character meant manually
  retyping every CHARACTER cue. Added `:rename OLD NEW`.
- **No autosave/recovery file.** A crash or killed terminal lost anything
  since your last `:w`. Scriptee now autosaves to a separate recovery
  slot and offers to restore it on next open (see "Autosave / crash
  recovery" above) — your real file is still never touched except by an
  explicit `:w`/`:wq`.
- **Bold/italic spans couldn't cross a wrap boundary.** Styling used to be
  re-parsed per wrapped sub-line, so a `**bold**`/`*italic*` marker that
  landed on either side of a wrap point lost its styling. Styling is now
  tokenized before wrapping instead of after, so it survives the split.

## Limitations of this v1 (honest list)

- Single-level-per-action undo/redo (a snapshot per insert-session/delete,
  not per keystroke) on a flat stack — fine for line-level mistakes, not a
  full undo tree you can branch around in.
- `:rename` matches CHARACTER cues exactly (extension-aware, e.g.
  `(V.O.)`); it doesn't touch a name mentioned in ACTION prose, since
  there's no reliable way to tell a name from an ordinary capitalized word
  there without much more NLP than this editor wants to carry.
- No revision color-set tracking — a real production need (see "Feature
  ideas" above), but a bigger lift than this pass; PDF scene numbering
  itself already exists (`:pdf`, on by default).
- A dual-dialogue pair prints as one atomic two-column block and moves to
  a fresh page as a whole if it doesn't fit on the current one — it never
  splits mid-block the way a single-column speech can with
  `(MORE)`/`(CONT'D)`, since a two-column block has no clean equivalent
  of that. For any dialogue exchange short enough to reasonably be
  "simultaneous," this is very unlikely to matter in practice.
- The terminal view shows a dual-dialogue pair as two ordinary
  single-column blocks with a small `^` marker on the second cue, not a
  live side-by-side preview — the two-column layout is real (and
  page-break-aware) in the exported PDF, which is what a two-column
  format actually needs to be correct for.
- Scene/character detection on import is heuristic — very unusual
  formatting in a foreign `.fountain` file may misclassify a line or two;
  just retype `:h`/`:c`/etc. on the current line to fix it.
- No spellcheck, no cloud sync/collaboration, and Fountain's `.fdx`
  (Final Draft) format isn't read or written — only `.fountain`. If you
  need to hand a script to someone on Final Draft, export to PDF or have
  them import the `.fountain` file directly (Highland, Fade In, and
  others all read it).

# Scriptee — Reference

Everything Scriptee does, and how to use it. For the one-paragraph pitch
and philosophy, see [`README.md`](README.md).

- [Install & run](#install--run)
- [Modes](#modes)
- [Line types](#line-types)
- [Moving between elements](#moving-between-elements)
- [Movement & basic editing](#movement--basic-editing)
- [Undo / redo](#undo--redo)
- [Search](#search)
- [Inline styling](#inline-styling)
- [Tab autocomplete](#tab-autocomplete)
- [`:lc` / `:lh` / `:lt` — resume the last cue](#lc--lh--lt--resume-the-last-cue)
- [`:rename` — rename a character everywhere](#rename--rename-a-character-everywhere)
- [`:dual` — dual (simultaneous) dialogue](#dual--dual-simultaneous-dialogue)
- [`:scenes` / `:stats` / jumping around](#scenes--stats--jumping-around)
- [`.` — repeat the last command](#--repeat-the-last-command)
- [Saving, quitting, opening](#saving-quitting-opening)
- [`:pdf` / `:cover` — PDF export](#pdf--cover--pdf-export)
- [`:help`](#help)
- [Opening files: fuzzy filter & recents](#opening-files-fuzzy-filter--recents)
- [Opening a file from the shell](#opening-a-file-from-the-shell)
- [Autosave / crash recovery](#autosave--crash-recovery)
- [Status bar: length, page, and scene numbers](#status-bar-length-page-and-scene-numbers)
- [Config reference](#config-reference)
- [Testing](#testing)
- [Limitations (honest list)](#limitations-honest-list)

## Install & run

```bash
chmod +x install.sh
./install.sh
scriptee
```

`install.sh` detects your package manager (pacman, apt, dnf, zypper, apk),
makes sure Python 3 + pip are present, `pip install`s `reportlab` (only
needed for `:pdf`), writes a default config to
`~/.config/scriptee/config.toml`, and installs `scriptee` to
`~/.local/bin`. It'll tell you the one line to add to your shell config if
`~/.local/bin` isn't already on `PATH`.

## Modes

Vim-style modal editing — four modes, `Esc` always gets you back to NORMAL.

| Mode | Entered with | What it does |
|---|---|---|
| **NORMAL** | (default / `Esc`) | Move around, issue commands |
| **INSERT** | `i` / `a` / `o` / `O` / `Enter` | Type screenplay text |
| **COMMAND** | `:` | Set a line's type, or run a `:`-command |
| **SEARCH** | `/` | Search for text in the buffer |

## Line types

`:` + a letter, in NORMAL mode, sets the **current line's** (cursor's
line) element type. Formatting (indent, wrap width, uppercasing) updates
live the moment you do this.

| Command | Element | Example |
|---|---|---|
| `:h` | Scene heading | `INT. KITCHEN - DAY` |
| `:a` | Action | `He pours the coffee, hands shaking.` |
| `:c` | Character | `VIKRANTH` |
| `:d` | Dialogue | `Parledu ra.` |
| `:p` | Parenthetical *(bonus, beyond the original Fountain spec)* | `(quietly)` |
| `:s` | Shot | `CLOSE ON THE PHOTO FRAME` |
| `:t` | Transition | `CUT TO:` |

**Example** — turning a plain line into a scene heading:

```
1. Type "int kitchen day" on a blank line (any type, doesn't matter yet).
2. Press Esc to leave INSERT.
3. Type :h and Enter.
   -> the line becomes "INT KITCHEN DAY", uppercased and formatted like
      a heading. Fix it up to "INT. KITCHEN - DAY" and keep writing.
```

`:` is the *only* thing that changes a line's type — `Enter` (below)
never silently reclassifies a line on you.

## Moving between elements

`Enter` (and `o`, open line below) always opens a new line and drops you
straight into INSERT — no popup, no guessing prompt:

- After **CHARACTER**, **PARENTHETICAL**, or **DIALOGUE**, the new line
  continues as **DIALOGUE** (by far the most common next element).
- After anything else (**ACTION**, **HEADING**, **SHOT**,
  **TRANSITION**), the new line defaults to **ACTION**.

If the default isn't what you want, override it with `:` + a type letter.
Because `Enter` never switches modes on its own, backspacing right after
`Enter` just merges you back into the end of the previous line, exactly
like any other editor.

**Example — a full exchange, start to finish:**

```
:c  Enter  VIKRANTH  Enter        -> types "VIKRANTH", Enter continues DIALOGUE
Parledu ra.  Enter                -> still DIALOGUE, types the line, Enter continues DIALOGUE
:c  Enter  RISHITHA  Enter        -> new speaker: :c switches type, Enter continues DIALOGUE
Vaadu na mundhe...  Esc           -> done, back to NORMAL
```

## Movement & basic editing

Standard vim motions (remappable — see [Config](#config-reference)):

| Key | Action |
|---|---|
| `h` `j` `k` `l` | left / down / up / right |
| `i` | insert before cursor |
| `a` | insert after cursor |
| `o` | open a new line below, insert |
| `O` | open a new line above, insert |
| `x` | delete character under cursor |
| `dd` | delete current line (press `d` twice) |
| `e` | unlock a file opened read-only from the shell (see below) |

## Undo / redo

`u` undoes the last change, `Ctrl-r` redoes it. A fresh edit after an
undo clears the redo stack, same as most editors. Up to
`behavior.max_undo_steps` (default 50) steps are kept.

## Search

`/text` + `Enter` searches the whole buffer (wraps around); `n` repeats
the last search, jumping to the next match. Case-insensitive.

## Inline styling

Type Fountain's own markup directly into ACTION/DIALOGUE/etc. text:

| Type this | Get |
|---|---|
| `*italic*` | *italic* (shown underlined in-terminal — most terminal fonts fake italics badly; exported PDF uses real `Courier-Oblique`) |
| `**bold**` | **bold** (real `Courier-Bold` in the PDF) |

```
He picks up the *photo frame* and just **stares**.
```

Bold/italic spans survive a wrap point — styling is tokenized before
wrapping, not re-parsed per wrapped sub-line, so a long emphasized phrase
that spills onto a second row stays styled on both rows.

## Tab autocomplete

In INSERT mode, `Tab` autocompletes from what you've already written
elsewhere in the script:

- **CHARACTER** lines cycle through character names used anywhere else.
- **HEADING** lines cycle through scene headings you've already written.
- **TRANSITION** lines cycle built-ins (`CUT TO:`, `FADE OUT.`, ... —
  configurable, see `[transitions]`) plus any you've typed yourself.

Type a few letters first to narrow the matches; press `Tab` again to
cycle to the next match; any other key ends the cycle.

```
:c Enter VIK<Tab>   -> if "VIKRANTH" appears earlier in the script,
                        Tab completes it immediately.
```

## `:lc` / `:lh` / `:lt` — resume the last cue

On an **empty** line, fill it in from whatever was most recently used
above the cursor, and drop straight into INSERT:

| Command | Resumes |
|---|---|
| `:lc` | Last **CHARACTER** used above (strips any `(V.O.)`-style extension) |
| `:lh` | Last **SCENE HEADING** used above, verbatim |
| `:lt` | Last **TRANSITION** used above, verbatim |

**Example** — resuming the same speaker after an action beat:

```
VIKRANTH
Parledu ra.

He looks away, then back at her.

o :lc Enter        -> new line, filled with "VIKRANTH", Enter continues DIALOGUE
Nenu osthanu.
```

## `:rename` — rename a character everywhere

```
:rename OLD NEW
:rename "OLD MAN" "YOUNG MAN"      (quote multi-word names)
```

Sweeps every CHARACTER cue matching `OLD` (case-insensitive) to `NEW`,
preserving any `(V.O.)`/`(CONT'D)`-style extension on each cue. One
undoable step (`u` reverts the whole sweep at once). Only touches actual
CHARACTER cues, not the name if it happens to appear in ACTION prose —
see [Limitations](#limitations-honest-list).

## `:dual` — dual (simultaneous) dialogue

Marks the **current** CHARACTER cue as pairing with the CHARACTER +
DIALOGUE block directly above it — two people talking over each other,
printed as two side-by-side columns on export. Same as pressing the
`dual_dialogue` key (`D` by default).

**Example:**

```
VIKRANTH
Nuvvu matladaku..

RISHITHA           <- write this cue + its dialogue first
Em chestav ra?
                     then, with cursor on RISHITHA's line: :dual (or D)
                     -> both cues show a small '^' in the gutter,
                        and :pdf prints them as two side-by-side columns.
```

Write the *first* CHARACTER+DIALOGUE block, then the second cue, **then**
run `:dual`/`D` on that second cue — it always pairs backward with
whatever's directly above (blank lines OK). If there's nothing valid to
pair with, the flag isn't set and the status bar says why, instead of
silently doing nothing.

## `:scenes` / `:stats` / jumping around

| Command | Does |
|---|---|
| `:scenes` | Pop up a jump-list of every scene heading. `j`/`k` move, `Enter` jumps, `q`/`Esc` closes. |
| `:stats` | Word counts + a per-character dialogue breakdown (lines/words each). `j`/`k` scrolls. |
| `:scene N` | Jump straight to scene `N` (same numbering as the left gutter / `:scenes`) |
| `<N>G` | Same as `:scene N`, from NORMAL mode — e.g. `12G` jumps to scene 12 |
| `G` | Jump to the last line (no count prefix) |
| `:42` | Jump to line 42 |

## `.` — repeat the last command

In NORMAL mode, `.` re-runs whatever `:` command you last executed —
`:lc`, `:rename OLD NEW`, `:w`, `:pdf`, an element-type shortcut, etc.
`.` doesn't count as a command itself, so repeated presses keep re-running
the *original* command rather than "repeating the repeat."

## Saving, quitting, opening

| Command | Does |
|---|---|
| `:w` | Save (Fountain format) |
| `:w <path>` | Save to a specific path |
| `:wq` | Save and quit |
| `:wq <path>` | Save to path and quit |
| `:q` | Quit — refuses with a warning if there are unsaved changes |
| `:q!` | Quit, discarding unsaved changes |
| `:o` | Open a different screenplay without leaving Scriptee (same picker as `[o]` at the start menu) |

Files save as **`.fountain`** — plain-text, git-diffable, readable in
`cat`/`grep`/any editor, importable by other Fountain tools (Highland,
Fade In, Slugline, ...). Default save location is `general.save_dir`
(`~/Documents/Scriptee` out of the box).

## `:pdf` / `:cover` — PDF export

```
:pdf                        -> export next to the saved file
:pdf ~/Desktop/draft.pdf    -> export to a specific path
:cover                      -> (re-)fill in Title/Author/Genre/... fields
```

`:pdf` produces an industry-formatted screenplay PDF: correct margins,
Courier 12pt (or a custom embedded font, see Config), scene numbers in
the left gutter, bold scene headings/transitions, semi-bold character
cues, italic parentheticals, `(MORE)`/`(CONT'D)` on a speech that splits
across a page, dual-dialogue columns, and a title page built from
whatever cover fields are set.

**Blank-line spacing is normalized, not copied verbatim.** Leave 1 or 10
blank lines between two action beats in the editor for your own writing
comfort — either way, `:pdf` always prints exactly the standard
single-line gap between elements, matching what Final Draft/Highland/etc.
output. Extra editor spacing is preserved across save/reopen (it's part
of your draft), it just never leaks into the exported page count.

If a script has no title page at all yet (e.g. freshly imported from a
`.fountain` file that never had one) and you run `:pdf`, Scriptee asks
once for the same fields `[n]ew` screenplays get — `:cover` does the same
prompt on demand, prefilled with whatever's already set, any time you
want to fix a typo or add a field later.

## `:help`

Pops up a full, always-current reference of every mode, keybind, and
command — reads its labels straight from your config, so a remap never
leaves it showing stale letters. `j`/`k`/arrows or Page Up/Down scroll,
`Esc`/`q` closes.

## Opening files: fuzzy filter & recents

At the **`[o]`** open screen (or `:o` mid-session):

- `j`/`k` browse, `Enter` opens the highlighted file.
- `/` starts a live filter: the list narrows as you type, matching the
  filename as a **subsequence** — not necessarily contiguous, so `scnt`
  matches `scriptee_confidential.fountain`. Tighter, more contiguous
  matches sort first. `Esc` clears the filter.
- `e` types an exact path instead of picking from the list.

Recently-opened files (including ones outside `save_dir`, e.g. opened via
`scriptee some/where/else.fountain`) show up first, deduplicated against
the rest of `save_dir`.

## Opening a file from the shell

```bash
scriptee some/screenplay.fountain
```

Skips the start menu and opens that file directly. If it already exists,
it opens **read-only** (press `e` to unlock editing) — so a quick "let me
check this scene" launch can't turn into an accidental edit. A path that
doesn't exist yet behaves like `[n]ew`, fully editable, saving there.

## Autosave / crash recovery

While there are unsaved changes, Scriptee periodically (every
`behavior.autosave_interval_secs`, default 15s) writes a recovery
snapshot to `~/.config/scriptee/recovery/` — separate from your real
file, which only `:w`/`:wq` ever touch.

- Reopening the same file checks for a newer recovery snapshot and asks
  whether to load it instead.
- A screenplay that was **never** saved at all shows up as
  **`[r] Recover unsaved session`** on the start menu.

A clean `:w`/`:wq`, or `:q` with nothing unsaved, clears the recovery
slot — it only exists to cover the gap between edits and your next save.

## Status bar: length, page, and scene numbers

The status bar shows an estimated page count and runtime
(`~12p (~12min)`, one page ≈ one minute of screen time, the standard
industry rule of thumb) plus which page the cursor is currently on
(`Pg 3/12`). Scene headings are numbered live in the left gutter as you
write, matching the order `:scenes` lists them in. These are estimates
(55 lines/page) that track your actual line wrapping as you type, but
will drift slightly from a final locked PDF's real pagination — same as
any screenwriting tool's live estimate.

## Config reference

`~/.config/scriptee/config.toml`, created with sane defaults on first
run. Edit it, save, relaunch. Every section below is a table in that
file.

### `[general]`

| Key | Default | Meaning |
|---|---|---|
| `save_dir` | `~/Documents/Scriptee` | Default save/open location |
| `pdf_scene_numbers` | `true` | Stamp scene numbers in the PDF's left margin on `:pdf` |
| `cover_page` | `true` | Master on/off for the PDF cover page — `false` means `:pdf` never draws one, period |
| `prompt_missing_titlepage` | `true` | Ask once for title-page fields if `:pdf` is run on a script that has none |

### `[prompts]`

`fields` — which fields `[n]ew` (and `:cover`) ask for, in order. Default
`["Title", "Author", "Genre", "Year", "Contact (Number)", "Contact (Email)"]`.
Add/remove/reorder freely; any field left blank is just skipped.

### `[behavior]`

| Key | Default | Meaning |
|---|---|---|
| `autosave_interval_secs` | `15` | Seconds of unsaved editing between autosave snapshots |
| `max_undo_steps` | `50` | Undo/redo stack depth |
| `max_recent_files` | `15` | How many paths the `[o]`pen recents list remembers |
| `esc_delay_ms` | `25` | ncurses `ESCDELAY` — how long a lone `Esc` byte waits before it's treated as a real Esc rather than the start of an arrow-key sequence |

### `[keybinds]`

Every key Scriptee reads is remappable, not just the 7 line-type letters:
`heading`/`action`/`character`/`dialogue`/`parenthetical`/`shot`/`transition`
(COMMAND-mode type setters) plus bare NORMAL-mode keys —
`insert_before`/`insert_after`/`open_below`/`open_above`,
`move_left`/`move_down`/`move_up`/`move_right`, `delete_char`/`delete_line`,
`undo`/`redo` (redo is held with Ctrl automatically, e.g. `r` → `Ctrl-r`),
`search`/`next_match`, `command`, `repeat`, `jump_end`, `toggle_readonly`,
`dual_dialogue`. `:help` always reflects your actual bindings.

```toml
[keybinds]
move_down = "n"   # example remap: n/e/i/o instead of j/k/h/l
move_up = "e"
move_left = "i"
move_right = "o"
```

### `[colors]`

Per-element terminal colors: `heading`, `character`, `dialogue`,
`transition`, `action`, `shot`, `parenthetical`, `accent`. Any of
`black`/`red`/`green`/`yellow`/`blue`/`magenta`/`cyan`/`white`/`default`.

### `[transitions]`

`builtins` — un-forced transition text (no leading `>`) recognized on
`.fountain` import and offered by Tab-complete on TRANSITION lines.
Default `["CUT TO:", "FADE OUT.", "FADE IN:", "DISSOLVE TO:", "SMASH CUT TO:"]`.
Add your own house style, e.g. `"WIPE TO:"`.

### `[format.wrap_width]` / `[format.indent]`

Terminal column widths and left-indents per element. Independent of
`[format.pdf]`'s margins below — not derived from them. They ship in
lockstep by default; if you change a PDF margin without the matching
terminal wrap width, the terminal view and the exported PDF will wrap
text differently.

### `[format.pdf]`

| Key | Default | Meaning |
|---|---|---|
| `font_size` | `12` | |
| `page_size` | `"letter"` | `"letter"` or `"a4"` |
| `font_family` | `"courier"` | `"courier"` (built-in, zero embedding) or `"custom"` (see below) |
| `left_edge_in` | `1.5` | Heading/action/shot left margin |
| `dialogue_left_in` | `2.5` | |
| `parenthetical_left_in` | `2.8` | |
| `character_left_in` | `3.5` | |
| `right_margin_in` / `top_margin_in` / `bottom_margin_in` | `1.0` each | |
| `dual_dialogue_gutter_in` | `0.3` | Gap between the two dual-dialogue columns |

These also drive the in-editor page-count estimate, so it can't drift
out of sync from `:pdf`'s actual output just because you changed a margin.

### `[format.pdf.custom_font]`

Only used when `font_family = "custom"`. Absolute `.ttf` paths:

```toml
[format.pdf]
font_family = "custom"

[format.pdf.custom_font]
regular = "/home/you/.fonts/CourierPrime-Regular.ttf"
bold    = "/home/you/.fonts/CourierPrime-Bold.ttf"
italic  = "/home/you/.fonts/CourierPrime-Italic.ttf"
```

`bold`/`italic` are optional and fall back to `regular` if left blank. A
bad or missing path falls back to plain Courier automatically, with a
note appended to the `:pdf` status line.

### `[format.pdf.emphasis]`

Which elements are force-styled on `:pdf`, regardless of any inline
`**bold**`/`*italic*` you typed — the standard screenplay-format
conventions, on by default:

| Key | Default | Applies to |
|---|---|---|
| `heading_bold` | `true` | Scene headings — full bold |
| `character_bold` | `true` | Character cues — semi-bold (a hairline double-strike on the regular weight, deliberately lighter than headings/transitions so cues don't compete visually with them) |
| `transition_bold` | `true` | Transitions — full bold |
| `parenthetical_italic` | `true` | Parentheticals — italic |

ACTION and DIALOGUE are never force-styled either way — they only ever
get bold/italic from `**`/`*` markers you actually type, unaffected by
this section.

## Testing

```bash
pip install --user --break-system-packages pytest
pip install --user --break-system-packages pypdf   # optional, only for PDF-content assertions
python3 -m pytest tests/ -v
```

Covers Fountain round-tripping (dual dialogue's `^` marker included),
the inline bold/italic tokenizer, line wrapping, cursor-position mapping
on wrapped lines, Unicode key input, config merging (every value is
actually consulted at runtime, not just parsed), remappable keybinds,
save-path sanitization, `:stats` counting, PDF scene-number stamping,
dual-dialogue pagination/export, and core `Editor` key handling (`dd`,
undo, insert, `:` commands).

## Limitations (honest list)

- Undo/redo is a flat stack (one snapshot per edit action), not a branching
  undo tree — fine for line-level mistakes, not full history exploration.
- `:rename` matches CHARACTER cues exactly (extension-aware, e.g.
  `(V.O.)`) — it doesn't touch a name mentioned in ACTION prose, since
  there's no reliable way to tell a name from an ordinary capitalized
  word there without far more NLP than this editor wants to carry.
- No revision color-set tracking (white/blue/pink/yellow page sets) yet —
  a real production need, but a bigger lift than page-numbering, which
  already exists.
- A dual-dialogue pair prints as one atomic two-column block and moves to
  a fresh page as a whole if it doesn't fit — it never splits mid-block
  the way single-column dialogue can with `(MORE)`/`(CONT'D)`, since a
  two-column block has no clean equivalent of that.
- The terminal view shows a dual-dialogue pair as two ordinary
  single-column blocks with a `^` marker on both cues, not a live
  side-by-side preview — the two-column layout is real (and
  page-break-aware) only in the exported PDF, which is what actually
  needs to be correct.
- Scene/character detection on `.fountain` import is heuristic — very
  unusual formatting from a foreign file may misclassify a line or two;
  retype `:h`/`:c`/etc. on the affected line to fix it.
- No spellcheck, no cloud sync/collaboration, and Final Draft's `.fdx`
  isn't read or written — only `.fountain`. To hand a script to someone
  on Final Draft, export to PDF, or have them import the `.fountain` file
  directly (Highland, Fade In, and others all read it).

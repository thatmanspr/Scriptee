# Scriptee

A vim-motion TUI screenwriter for Linux.

Scriptee obliges with suckless UNIX philosophy. Do only one thing, and do it right.

Grab the latest release.

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

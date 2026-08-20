# Scriptee — Reference

Everything Scriptee does, and how to use it. For the one-paragraph pitch,
see [`README.md`](README.md).

- [Install & run](#install--run)
- [Modes](#modes)
- [Line types](#line-types)
- [Moving between elements](#moving-between-elements)
- [Movement & basic editing](#movement--basic-editing)
- [Yank / paste](#yank--paste)
- [Undo / redo](#undo--redo)
- [Revision color-set tracking](#revision-color-set-tracking)
- [Search](#search)
- [Inline styling](#inline-styling)
- [Tab autocomplete](#tab-autocomplete)
- [`:lc` / `:lh` / `:lt` — resume the last cue](#lc--lh--lt--resume-the-last-cue)
- [`:rename` — rename a character everywhere](#rename--rename-a-character-everywhere)
- [`:dual` — dual (simultaneous) dialogue](#dual--dual-simultaneous-dialogue)
- [`:version` — numbered drafts of a script](#version--numbered-drafts-of-a-script)
- [`:scenes` / `:stats` / jumping around](#scenes--stats--jumping-around)
- [`.` — repeat the last command](#--repeat-the-last-command)
- [Saving, quitting, opening](#saving-quitting-opening)
- [`:pdf` / `:cover` — PDF export](#pdf--cover--pdf-export)
  - [Sides — exporting a scene range or a single character's scenes](#sides--exporting-a-scene-range-or-a-single-characters-scenes)
  - [Sides title page](#sides-title-page)
- [`:help`](#help)
- [Opening files: fuzzy filter & recents](#opening-files-fuzzy-filter--recents)
- [Opening a file from the shell](#opening-a-file-from-the-shell)
- [Autosave / crash recovery](#autosave--crash-recovery)
- [Status bar: length, page, and scene numbers](#status-bar-length-page-and-scene-numbers)
- [Config reference](#config-reference)
- [Testing](#testing)
- [Resource usage](#resource-usage)
- [Limitations](#limitations)

## Install & run

```bash
chmod +x install.sh
./install.sh
scriptee
```

`install.sh` detects your package manager (pacman, apt, dnf, zypper, apk),
ensures Python 3 + pip are present, installs `reportlab` (needed for
`:pdf`), installs the bundled Courier Prime font files, writes a default
config to `~/.config/scriptee/config.toml`, and installs `scriptee` to
`~/.local/bin`. It prints the line to add to your shell config if
`~/.local/bin` isn't already on `PATH`.

A fresh config defaults `format.pdf.font_family` to `"custom"` and
points it at the bundled Courier Prime, rather than the PDF spec's
built-in base-14 Courier (see `[format.pdf]` below for the difference).
Re-running `install.sh` against an **existing** config only flips this
over if `font_family`/`custom_font` are still at that untouched default
— any config that's already been pointed at a font (this one or your
own) is left exactly as it is.

## Modes

Vim-style modal editing. `Esc` always returns to NORMAL.

| Mode | Entered with | What it does |
|---|---|---|
| **NORMAL** | (default / `Esc`) | Move around, issue commands |
| **INSERT** | `i` / `a` / `o` / `O` / `Enter` | Type screenplay text |
| **COMMAND** | `:` | Set a line's type, or run a `:`-command |
| **SEARCH** | `/` | Search for text in the buffer |

## Line types

`:` + a letter, in NORMAL mode, sets the current line's element type.
Indent, wrap width, and uppercasing update immediately.

| Command | Element | Example |
|---|---|---|
| `:h` | Scene heading | `INT. KITCHEN - DAY` |
| `:a` | Action | `He pours the coffee, hands shaking.` |
| `:c` | Character | `VIKRANTH` |
| `:d` | Dialogue | `Parledu ra.` |
| `:p` | Parenthetical | `(quietly)` |
| `:s` | Shot | `CLOSE ON THE PHOTO FRAME` |
| `:t` | Transition | `CUT TO:` |

**Example** — turning a plain line into a scene heading:

```
1. Type "int kitchen day" on a blank line.
2. Press Esc to leave INSERT.
3. Type :h and Enter.
   -> the line becomes "INT KITCHEN DAY", formatted as a heading.
      Fix it up to "INT. KITCHEN - DAY" and keep writing.
```

`:` is the only thing that changes a line's type — `Enter` never
reclassifies a line on its own.

## Moving between elements

`Enter` (and `o`, open line below) opens a new line and drops you
straight into INSERT:

- After **CHARACTER**, **PARENTHETICAL**, or **DIALOGUE**, the new line
  continues as **DIALOGUE**.
- After anything else (**ACTION**, **HEADING**, **SHOT**,
  **TRANSITION**), the new line defaults to **ACTION**.

Override the default with `:` + a type letter. Backspacing right after
`Enter` merges back into the end of the previous line, as usual.

**Example — a full exchange:**

```
:c  Enter  VIKRANTH  Enter        -> types "VIKRANTH", continues DIALOGUE
Parledu ra.  Enter                -> types the line, continues DIALOGUE
:c  Enter  RISHITHA  Enter        -> new speaker
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
| `N` | insert a blank line below the cursor, **stay in NORMAL** (no insert) |
| `x` | delete character under cursor |
| `dd` | delete (cut) current line (press `d` twice) |
| `yy` | yank (copy) current line (press `y` twice) |
| `p` | paste the register on a new line below the cursor |
| `P` | paste the register on a new line above the cursor |
| `e` | unlock a file opened read-only from the shell |

`j`/`k` (and the arrow keys in INSERT mode) move by visual row, not
straight to the next element. On a wrapped ACTION/DIALOGUE line, moving
down from the first row lands on the line's second row at the same
column, not on the next element — only from the last row does down
cross into the next element. Up mirrors this.

`N` is the odd one out next to `o`/`O`: it opens a blank ACTION line
below the cursor (moving you onto it) but leaves you in NORMAL instead
of dropping into INSERT — for when you just want the spacing (e.g.
before a new scene) without typing right away. `Enter`, in INSERT mode,
already opens a new line of its own (see [Moving between
elements](#moving-between-elements) above) — `N` is the NORMAL-mode
equivalent for when you don't want to type yet.

## Yank / paste

`yy` copies the whole current line (element type included) into a
single-line register; `dd` fills the same register with whatever it cuts,
so a delete can be moved elsewhere with `p`/`P` just like a yank. `p`
pastes as a new line below the cursor, `P` above it — always a full new
line, never a mid-line splice. Both are one undoable step (`u` reverts
a paste like any other edit).

There's only the one register — a later `yy`/`dd` overwrites whatever
was there before — and it holds a single line, not an arbitrary range or
character-wise selection. No named/numbered registers (`"a`, `"1`, …)
and nothing to grab across app restarts; it lives only in memory for
the current session. See [Limitations](#limitations).

## Undo / redo

`u` undoes the last change, `Ctrl-r` redoes it — undo history is a real
*tree*, not a flat stack: undoing and then making a different edit than
the one you undid doesn't destroy the old "future", it just becomes a
sibling branch. Plain `u`/`Ctrl-r` always follow the branch you were
just on (so day-to-day it feels exactly like a linear undo/redo), and
`:undotree` opens a picker over every branch the session has forked
into, letting you jump straight to any of them. Up to
`behavior.max_undo_steps` (default 50) steps of *unbranched* history are
kept — a tree with active branches can grow past that cap, since
discarding a branch to make room is exactly what this feature exists to
avoid.

## Revision color-set tracking

`:revision <color>` locks the current draft in as a new baseline and
starts tracking every line changed from that point on — the standard
white/blue/pink/yellow "what changed since last draft" workflow. Locked
lines get a `*` both in the editor's left gutter and in the exported
PDF's right margin, and every PDF page carries a small
"`COLOR REVISION -- date`" stamp once a color's been locked.

| Command | Effect |
|---|---|
| `:revision` | Show the current color and how many lines are marked changed |
| `:revision <color>` | Lock the buffer as the new baseline, start tracking under `<color>` |
| `:revision next` | Lock in the next color in the standard rotation |
| `:revision history` | List every color locked this session |
| `:revision off` | Clear tracking, back to White (no marks) |

The standard nine-color rotation is White → Blue → Pink → Yellow →
Green → Goldenrod → Buff → Salmon → Cherry, wrapping back to Blue after
Cherry (White is never re-entered by `next` — it only ever means "before
the first lock"). The current color is saved to the title page as a
plain `Revision: <color>` field, so reopening the file resumes on the
right color; the exact baseline to diff against for marks is
session-only, same honest scope the undo tree and autosave already have
— see [Limitations](#limitations).

## Search

`/text` + `Enter` searches the whole buffer (wraps around); `n` repeats
the last search. Case-insensitive.

## Inline styling

Fountain markup typed directly into ACTION/DIALOGUE/etc. text:

| Type this | Get |
|---|---|
| `*italic*` | Italic (real `Courier-Oblique` in the exported PDF) |
| `**bold**` | Bold (real `Courier-Bold` in the exported PDF) |

```
He picks up the *photo frame* and just **stares**.
```

Bold/italic spans survive a wrap point — a styled phrase that spills
onto a second row stays styled on both rows.

## Tab autocomplete

In INSERT mode, `Tab` autocompletes from what you've already written
elsewhere in the script:

- **CHARACTER** lines cycle through character names used elsewhere.
- **HEADING** lines cycle through scene headings already written.
- **TRANSITION** lines cycle built-ins (`CUT TO:`, `FADE OUT.`, ... —
  configurable, see `[transitions]`) plus any you've typed yourself.

Type a few letters to narrow the matches; press `Tab` again to cycle to
the next match; any other key ends the cycle.

```
:c Enter VIK<Tab>   -> if "VIKRANTH" appears earlier in the script,
                        Tab completes it immediately.
```

## `:lc` / `:lh` / `:lt` — resume the last cue

On an empty line, fill it in from whatever was most recently used above
the cursor, and drop straight into INSERT:

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

o :lc Enter        -> new line, filled with "VIKRANTH", continues DIALOGUE
Nenu osthanu.
```

## `:rename` — rename a character everywhere

```
:rename OLD NEW
:rename "OLD MAN" "YOUNG MAN"      (quote multi-word names)
:rename OLD NEW --all              (also sweep ACTION/DIALOGUE prose)
```

Sweeps every CHARACTER cue matching `OLD` (case-insensitive) to `NEW`,
preserving any `(V.O.)`/`(CONT'D)`-style extension. One undoable step
(`u` reverts the whole sweep).

By default that's *all* it touches — a mention of the name in ACTION
prose or DIALOGUE is left alone. Add `--all` (or `-a`) to also replace
whole-word, case-insensitive mentions of `OLD` in the same line types
`:pdf char` searches (ACTION + DIALOGUE by default, configurable via
`character_export.search_in` — see [Config reference](#config-reference)). Each
prose match is reshaped to match its own capitalization — ALL CAPS stays
ALL CAPS, "Title Case"/"Sentence case" becomes Title Case, anything else
becomes lowercase — so a sweep doesn't leave a stray "JOHN" or "MARK"
sitting in the middle of a sentence. That's a heuristic: unusual
capitalization in the source can still come out wrong, so skim the
result. `--all` does not follow `character_export.aliases` — it matches
`OLD` literally, the same as the cue sweep. See
[Limitations](#limitations).

## `:dual` — dual (simultaneous) dialogue

Marks the current CHARACTER cue as pairing with the CHARACTER + DIALOGUE
block directly above it — two people talking over each other, printed
as two side-by-side columns on export. Same as pressing the
`dual_dialogue` key (`D` by default).

**Example:**

```
VIKRANTH
Nuvvu matladaku..

RISHITHA           <- write this cue + its dialogue first
Em chestav ra?
                     then, with cursor on RISHITHA's line: :dual (or D)
                     -> both cues render as two side-by-side columns,
                        right here in the editor and in :pdf.
```

Write the first CHARACTER+DIALOGUE block, then the second cue, then run
`:dual`/`D` on that second cue — it pairs backward with whatever's
directly above (blank lines OK). If there's nothing valid to pair with,
the status bar explains why.

If a pair runs long enough to hit a page break in `:pdf`, it splits
mid-block instead of moving to a fresh page as one atomic unit:
whichever column(s) are still mid-speech at the break get `(MORE)` at
the foot of the page and their cue repeated with `(CONT'D)` at the top
of the next, the same treatment single-column dialogue gets. A break
that instead falls between cues (rather than mid-speech) just rolls
onto a fresh page plainly, with no `(MORE)`/`(CONT'D)` — same rule
single-column dialogue follows.

## `:version` — numbered drafts of a script

Branch a script into numbered sibling files (`Interlude.fountain` →
`Interlude_v2.fountain` → `Interlude_v3.fountain`, ...) without losing
track of earlier drafts, and move between them from inside the editor.
Needs the current file saved at least once (`:w`) before any of this
works — there's nothing to number yet otherwise.

| Command | Does |
|---|---|
| `:version` | Show which version this file is, and prompt to (re)name it — Enter keeps the current label, clear + Enter removes it |
| `:version new [label]` | Branch off a new numbered sibling file, carrying over the current buffer (including unsaved edits), and switch to editing it. The old file is left untouched. Prompts for a label if none is given; `:version n` also works. |
| `:version switch` | Browse every version of this script — `j`/`k` move, `Enter` switches, `q`/`Esc` backs out. `:version list` also works. |
| `:version N` | Jump straight to version `N` without the picker |
| `:version label <name>` | Name the current version in one line, no prompt |

Switching versions with unsaved changes is refused, same as `:q` — add
`!` to override and discard them anyway, e.g. `:version 3!` or
`:version switch!`.

**Example:**

```
:w                       -> save "Interlude.fountain" first
:version new             -> prompts for a label, then creates and
                             switches to "Interlude_v2.fountain"
...edit...
:version switch          -> browse every version, Enter to jump back
:version 1               -> or jump straight to version 1
```

Saving a brand-new file under a name that looks like another version of
an existing one already in the same folder (e.g. saving
`Interlude v2.fountain` next to an existing `Interlude.fountain`) asks
whether to fold it into that version group instead of leaving two
similarly-named standalone files sitting loose in the same directory.

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
Repeated presses re-run the original command, not "repeat the repeat."

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

Files save as `.fountain` — plain-text, git-diffable, readable in any
editor, importable by other Fountain tools (Highland, Fade In,
Slugline, ...). Default save location is `general.save_dir`
(`~/Documents/Scripts` out of the box). A brand-new, never-saved script
gets its own subfolder named after its Title — e.g. a script titled
"Interlude" saves to `~/Documents/Scripts/Interlude/Interlude.fountain`
(and any default-location `:pdf` export lands right alongside it in the
same folder). Once a script has a real path (saved, or opened from
somewhere else), that path is used as-is — this only applies to picking
a location for a script that's never been saved before.

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

Blank-line spacing in the editor is normalized on export — `:pdf`
always prints the standard single-line gap between elements, matching
Final Draft/Highland/etc. output, regardless of how many blank lines you
left while writing.

If a script has no title page at all (e.g. imported from a `.fountain`
file that never had one) and you run `:pdf`, Scriptee asks once for the
same fields `[n]ew` screenplays get. `:cover` runs the same prompt on
demand, prefilled with whatever's already set.

### Sides — exporting a scene range or a single character's scenes

```
:pdf 5                       -> just scene 5
:pdf 1-10                    -> scenes 1 through 10
:pdf 1-10 ~/Desktop/side.pdf -> scenes 1-10, to a specific path
:pdf char VIKRANTH           -> every scene VIKRANTH appears in
:pdf char "OLD MAN"          -> quote multi-word names
:pdf character VIKRANTH out.pdf   -> "character" also works, with a path
```

A **scene range** (`N` or `N-M`) exports only those scene headings and
everything under them, in order.

A **character filter** (`char`/`character` + a name) exports every
scene that name appears in — as a CHARACTER cue, mentioned in ACTION
prose, and mentioned in another character's DIALOGUE (all three by
default; see `[character_export]` below to change this). This means a
scene where the character has no line of their own, or is only ever
talked *about* by someone else, is still included for full context.
A scene where the character has their own CHARACTER cue is always
included, regardless of that config.

Name matching is always case-insensitive and whole-word: `:pdf char AL`
won't match `ALEX` or `ALWAYS`, and `DANNY`/`danny`/`Danny` all match
the same way. If nothing matches — a bad scene number or a name that
isn't in the script — the status bar says so and nothing is exported.

**Nicknames/aliases:** if other characters refer to someone by a name
other than their CHARACTER cue (e.g. dialogue calls DANNY "Dan"), add an
alias so both names resolve to the same character:

```toml
[character_export.aliases]
DANNY = ["Dan"]
```

`:pdf char Danny` and `:pdf char Dan` then both pull in every scene
mentioning either name. See `[character_export]` in the
[Config reference](#config-reference) for the full options.

A scoped export:

- Keeps each scene's original number in the left-gutter stamp — a sides
  PDF for scenes 4, 9, and 12 is still labeled `4`, `9`, `12`.
- Gets its own title page, separate from the full script's own cover
  fields — see [Sides title page](#sides-title-page) below.
- Gets an auto-suffixed filename when no explicit path is given:
  `script.fountain` → `script_scenes_1-10.pdf` or `script_VIKRANTH.pdf`.
  Give an explicit path (as the last argument) to override that.

### Sides title page

Before writing the PDF, a scoped export asks for a one-line Title — just
that field, not the full `[prompts]` list `[n]ew`/`:cover` ask for:

```
Title -> [type something, or leave blank + Enter]
```

Leave it blank and Scriptee builds one for you:

- A scene range becomes `Sides (Scene 4 - Scene 9)` (or `Sides (Scene 5)`
  for a single scene).
- `:pdf char VIKRANTH` becomes `Sides - VIKRANTH Draft`.

Either way — typed or auto-generated — the rest of the title page
(Author, Genre, Year, Contact) is carried over as-is from the full
script's own cover fields (whatever `:cover` has set), and a small note
is printed under the title naming which script this is excerpted from:

```
      SIDES (SCENE 4 - SCENE 9)
   (an excerpt from Beach House)

         by Priya Menon
```

That excerpt note uses the full script's own Title, so it shows up
whether you typed a custom sides title or let one auto-generate — the
only thing that turns it off is `sides.excerpt_note = false` in config.
If the full script has no title of its own (see below), the note is
simply skipped rather than printing `(an excerpt from )`.

**Imported Fountain with no title page at all:** if you're working from
a `.fountain` file that never had a title page (nothing shows when you
run `:cover`), a scoped export still gets its own title page — `Sides
(Scene 4)`, `Sides - VIKRANTH Draft`, etc. — since that title doesn't
depend on the full script having one. There's just no Author/Contact/etc.
to carry over, and no excerpt note (nothing to excerpt *from*). Note
that this is separate from the *full* `:pdf` export's own "no title page
in this file" prompt (see above) — a scoped export never triggers that
one; it only ever asks its own one-field Title prompt.

Every part of this is configurable — see `[sides]` in the
[Config reference](#config-reference):

- `cover_page = false` turns off the scoped title page entirely,
  restoring the old "sides never get a cover page" behavior.
- `prompt_title = false` skips the Title prompt and always
  auto-generates.
- `title_format` / `title_format_single` / `character_title_format`
  control the auto-generated wording.
- `excerpt_note = false` (or `excerpt_format`) controls the "(an excerpt
  from ...)" line.

## `:help`

Pops up a reference of every mode, keybind, and command, reflecting your
actual config (a remap never leaves it showing stale letters). `j`/`k`/
arrows or Page Up/Down scroll, `Esc`/`q` closes.

## Opening files: fuzzy filter & recents

At the `[o]` open screen (or `:o` mid-session):

- `j`/`k` browse, `Enter` opens the highlighted file.
- `/` starts a live filter: the list narrows as you type, matching the
  filename as a subsequence (not necessarily contiguous), so `scnt`
  matches `scriptee_confidential.fountain`. `Esc` clears the filter.
- `e` types an exact path instead of picking from the list.

Recently-opened files (including ones outside `save_dir`) show up
first, deduplicated against the rest of `save_dir`.

## Opening a file from the shell

```bash
scriptee some/screenplay.fountain
```

Skips the start menu and opens that file directly. If it already
exists, it opens read-only (press `e` to unlock editing). A path that
doesn't exist yet behaves like `[n]ew`, fully editable, saving there.

## Autosave / crash recovery

While there are unsaved changes, Scriptee periodically (every
`behavior.autosave_interval_secs`, default 15s) writes a recovery
snapshot to `~/.config/scriptee/recovery/`, separate from your real
file, which only `:w`/`:wq` touch.

- Reopening the same file checks for a newer recovery snapshot and asks
  whether to load it instead.
- A screenplay that was never saved shows up as
  `[r] Recover unsaved session` on the start menu.

A clean `:w`/`:wq`, or `:q` with nothing unsaved, clears the recovery
slot.

## Status bar: length, page, and scene numbers

The status bar shows an estimated page count and runtime
(`~12p (~12min)`, one page ≈ one minute) plus the current page
(`Pg 3/12`). Scene headings are numbered live in the left gutter as you
write, matching `:scenes`. These are estimates (55 lines/page) and may
drift slightly from a final locked PDF's real pagination.

## Config reference

`~/.config/scriptee/config.toml`, created with defaults on first run.
Edit it, save, relaunch. Every section below is a table in that file.

### `[general]`

| Key | Default | Meaning |
|---|---|---|
| `save_dir` | `~/Documents/Scripts` | Default save/open location (each new script gets its own `<save_dir>/<Title>/` subfolder) |
| `pdf_scene_numbers` | `true` | Stamp scene numbers in the PDF's left margin on `:pdf` |
| `cover_page` | `true` | Master on/off for the *full-script* PDF cover page. Scoped (sides/character) exports have their own switch — see `[sides]` below. |
| `prompt_missing_titlepage` | `true` | Ask once for title-page fields if a full (unscoped) `:pdf` is run on a script that has none |

### `[prompts]`

`fields` — which fields `[n]ew` (and `:cover`) ask for, in order.
Default `["Title", "Author", "Genre", "Year", "Contact (Number)", "Contact (Email)"]`.
Add/remove/reorder freely; any field left blank is skipped.

### `[sides]`

Title page for a *scoped* `:pdf` export — a scene range or `:pdf char
NAME` — see [Sides title page](#sides-title-page) above. Independent of
`[general]`'s `cover_page`/`prompt_missing_titlepage`, which only ever
govern the full-script export.

| Key | Default | Meaning |
|---|---|---|
| `cover_page` | `true` | Master on/off for a scoped export's title page. `false` restores the old "sides never get a cover page" behavior, no matter what else is set below. |
| `prompt_title` | `true` | Ask for a custom Title before each scoped export (just that one field). `false` skips the prompt and always auto-generates. |
| `title_format` | `"Sides (Scene {start} - Scene {end})"` | Auto-generated title for a scene-range export when the prompt is left blank/skipped. `{start}`/`{end}` are the first/last scene numbers exported. |
| `title_format_single` | `"Sides (Scene {start})"` | Used instead of `title_format` when the range is a single scene, so it reads `Sides (Scene 4)` rather than `Sides (Scene 4 - Scene 4)`. |
| `character_title_format` | `"Sides - {name} Draft"` | Auto-generated title for a `:pdf char NAME` export. `{name}` is the character name exactly as typed. |
| `excerpt_note` | `true` | Draw a small "(an excerpt from ...)" line under the sides title, naming the full script's own Title. Skipped automatically when the full script has no title. |
| `excerpt_format` | `"(an excerpt from {title})"` | Wording of that line. `{title}` is the full script's Title. |

### `[character_export]`

Controls `:pdf char NAME` / `:pdf character NAME` (see
[Sides](#sides--exporting-a-scene-range-or-a-single-characters-scenes)).

| Key | Default | Meaning |
|---|---|---|
| `search_in` | `["action", "dialogue"]` | Line types searched for the name/alias, in addition to an exact CHARACTER-cue match (which always counts). Also accepts `"parenthetical"`, `"shot"`, `"transition"`. Set to `[]` to match only exact CHARACTER cues. |
| `aliases` | `{}` | `[character_export.aliases]` sub-table mapping a character's name to a list of nicknames, e.g. `DANNY = ["Dan"]`. Matched case-insensitively and whole-word, same as `:pdf char`. |

### `[behavior]`

| Key | Default | Meaning |
|---|---|---|
| `autosave_interval_secs` | `15` | Seconds of unsaved editing between autosave snapshots |
| `max_undo_steps` | `50` | Undo tree depth (unbranched history only — see [Undo / redo](#undo--redo)) |
| `max_recent_files` | `15` | How many paths the `[o]`pen recents list remembers |
| `esc_delay_ms` | `25` | ncurses `ESCDELAY` — how long a lone `Esc` byte waits before being treated as a real Esc rather than the start of an arrow-key sequence |

### `[keybinds]`

Every key Scriptee reads is remappable: the 7 line-type letters
(`heading`/`action`/`character`/`dialogue`/`parenthetical`/`shot`/`transition`)
plus bare NORMAL-mode keys — `insert_before`/`insert_after`/
`open_below`/`open_above`/`blank_line`, `move_left`/`move_down`/`move_up`/
`move_right`, `delete_char`/`delete_line`, `yank_line`/`paste_after`/
`paste_before`, `undo`/`redo` (redo is held with Ctrl automatically,
e.g. `r` → `Ctrl-r`), `search`/`next_match`, `command`, `repeat`,
`jump_end`, `toggle_readonly`, `dual_dialogue`. `:help` always reflects
your actual bindings.

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
Add your own, e.g. `"WIPE TO:"`.

### `[format.wrap_width]` / `[format.indent]`

Terminal column widths and left-indents per element. Independent of
`[format.pdf]`'s margins below — they ship in lockstep by default, but
changing a PDF margin without the matching terminal wrap width means the
terminal view and the exported PDF wrap text differently.

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

These also drive the in-editor page-count estimate.

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
bad or missing path falls back to plain Courier, with a note on the
`:pdf` status line.

### `[format.pdf.emphasis]`

Which elements are force-styled on `:pdf`, regardless of any inline
`**bold**`/`*italic*` you typed:

| Key | Default | Applies to |
|---|---|---|
| `heading_bold` | `true` | Scene headings — bold |
| `character_bold` | `true` | Character cues — semi-bold |
| `transition_bold` | `true` | Transitions — bold |
| `parenthetical_italic` | `true` | Parentheticals — italic |

ACTION and DIALOGUE are never force-styled — they only get bold/italic
from `**`/`*` markers you type.

## Testing

```bash
pip install --user --break-system-packages pytest
pip install --user --break-system-packages pypdf   # optional, for PDF-content assertions
python3 -m pytest tests/ -v
```

Covers Fountain round-tripping, the inline bold/italic tokenizer, line
wrapping, cursor-position mapping on wrapped lines, Unicode key input,
config merging, remappable keybinds, save-path sanitization, `:stats`
counting, PDF scene-number stamping, scoped PDF export (`:pdf` scene
ranges, `:pdf char`, aliases), dual-dialogue pagination/export, and core
`Editor` key handling.

## Code layout

Scriptee is a package (`scriptee_pkg/`) plus a thin `scriptee.py`
launcher at the repo root, rather than one single file. `install.sh`
copies the whole tree (`scriptee.py`, `scriptee_pkg/`, `fonts/`) to
`~/.local/lib/scriptee/` and puts a small wrapper script on `PATH` that
execs the real launcher — running `python3 scriptee.py` straight out of
a checkout also still works, same as before.

| Module | Responsibility |
|---|---|
| `config.py` | Defaults, `config.toml` loading/merging, `apply_runtime_config()` |
| `text_format.py` | Wrap widths/indents, inline styling, wrapped-row cursor mapping |
| `fountain.py` | `.fountain` save/load, `TRANSITION_KEYWORDS`, character-cue parsing |
| `stats.py` | `:stats` word/scene/character counting |
| `recovery.py` | Autosave, crash recovery, recent-files list |
| `pdf_geometry.py` | Page/margin/column math, font resolution |
| `pdf_export.py` | Row drawing, pagination, `:pdf`/`:cover`/sides export |
| `ui_helpers.py` | Low-level curses helpers (key reading, prompts) |
| `screens.py` | Start menu, recovery prompt, open-file picker |
| `editor.py` | The `Editor` class — the main edit loop and all `:commands` |
| `app.py` | Startup flow and `main()` |

**If you add a new module-level variable that gets reassigned at
runtime** (as opposed to a dict/list that's only ever mutated in place
via `.update()`/`[:] =`), every module *other* than the one that owns it
must read it as `owning_module.NAME`, never `from owning_module import
NAME` — a plain import copies the value once at import time and won't
see later reassignments, whether those come from `apply_runtime_config()`
or from a test's `monkeypatch.setattr(owning_module, "NAME", ...)`. The
existing `config.AUTOSAVE_INTERVAL`/`config.RECOVERY_DIR` and the
`pdf_geometry.PDF_*` constants are the pattern to follow; `WRAP_WIDTH`,
`INDENT`, and `TRANSITION_KEYWORDS` are the counterexample — those are
mutated in place, so plain imports of them are fine everywhere.

## Resource usage

Measured RSS at idle startup (start menu, before opening a file),
averaged over several runs: **~29.6 MB**. For reference, the v1
single-file release measured ~35.8 MB under the same conditions — the
package split didn't add runtime weight; if anything it trimmed some.

## Limitations

- Undo history is a tree (see [Undo / redo](#undo--redo)), but the tree
  itself only exists in memory for the current session — it doesn't
  survive quitting Scriptee, and `max_undo_steps` pruning only ever
  trims *unbranched* history off the far end (an actively-branching tree
  can grow past the configured cap; see `_prune_undo_tree()`'s own
  comment in `editor.py`).
- Yank/paste has one register, holding at most one whole line — no
  named/numbered registers, no character-wise or multi-line visual
  selection, and it doesn't survive quitting Scriptee.
- `:rename` matches CHARACTER cues exactly by default — pass `--all`
  to also sweep name mentions in ACTION/DIALOGUE prose (see
  [`:rename`](#rename--rename-a-character-everywhere)). (`:pdf char`
  independently searches those line types for scoping an export — see
  [Sides](#sides--exporting-a-scene-range-or-a-single-characters-scenes).)
- Revision tracking (see [Revision color-set tracking](#revision-color-set-tracking))
  saves the *current color name* to the title page (`Revision: <color>`),
  so reopening a file resumes on the right color — but the line-by-line
  baseline it diffs against to compute `*` marks is session-only, same
  as the undo tree above. Reopening a file always starts marks fresh
  from whatever's on disk; it can't reconstruct exactly which lines
  were changed in a previous sitting after the file's been closed and
  reopened.
- Scene/character detection on `.fountain` import is heuristic — unusual
  formatting from a foreign file may misclassify a line; retype
  `:h`/`:c`/etc. on the affected line to fix it. Fountain's own forcing
  prefixes are recognized (`.` heading, `!` action, `>` transition, `@`
  character — the last one also being what `:pdf`/save itself writes for
  a CHARACTER name the plain heuristic can't round-trip on its own, e.g.
  one 40+ chars long or ending in `.`/`!`/`?`), so a foreign file that
  uses those is read correctly even where the plain heuristics
  wouldn't catch it.
- No spellcheck, no cloud sync/collaboration, and Final Draft's `.fdx`
  isn't read or written — only `.fountain`. To hand a script to someone
  on Final Draft, export to PDF, or have them import the `.fountain`
  file directly (Highland, Fade In, and others read it).

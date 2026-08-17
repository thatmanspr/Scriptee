# Scriptee

A vim-motion TUI screenwriter for Linux. `curses` in, Fountain out.

## Philosophy

Scriptee does one thing: let you write a screenplay in a terminal,
correctly formatted, without leaving the keyboard. Everything else was
left out on purpose.

- **Plain text, not a database.** Scripts are stored as
  [Fountain](https://fountain.io) — a real, open, diffable format. Your
  words are readable in `cat`, `grep`, `git diff`, and any other editor,
  forever. No proprietary format, no lock-in.
- **stdlib over frameworks.** `curses` + `reportlab` for PDF export. That's
  the entire dependency tree. No widget toolkit, no Electron, no runtime
  you didn't already have.
- **Config, not customization theater.** One `config.toml` — keybinds,
  colors, margins, PDF geometry, cover-page fields. All of it is plain
  data, remappable, with sane defaults so you never have to touch it.
- **Local. Always.** No accounts, no sync, no telemetry, no network calls
  of any kind. Your screenplay is a file on your disk and nowhere else.
- **Do the one thing right.** Modal editing, correct industry PDF
  formatting (scene numbers, dual dialogue, title pages), and nothing
  bolted on that a real screenwriting app wouldn't also need — no
  outlining boards, no cloud collab, no AI co-writer.

If a feature doesn't serve "write and format a screenplay fast," it
doesn't belong here.

## Install

```bash
chmod +x install.sh
./install.sh
scriptee
```

Full usage, every command, and the complete config reference: see
[`REFERENCE.md`](REFERENCE.md).

## Grab a release, not `main`

**`main` is for code review** — browse the source, read the design notes
in `REFERENCE.md`, see how it's built. It doesn't carry the full project
tree (tests included).

To actually run Scriptee, **grab a tagged release from the
[Releases](../../releases) page** — that's the complete package: source,
config, installer, and the test suite.

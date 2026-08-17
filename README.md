# Scriptee

A vim-motion TUI screenwriter for Linux. `curses` in, Fountain out.

## Install

```bash
chmod +x install.sh
./install.sh
scriptee
```

Full command and config reference: [`REFERENCE.md`](REFERENCE.md).

## Philosophy

Suckless software: do one thing, do it right, do nothing else.

Scriptee writes and formats a screenplay in a terminal. That's the whole
program.

- **Plain text, not a database.** Scripts are stored as
  [Fountain](https://fountain.io) — a real, open, diffable format.
  Readable in `cat`, `grep`, `git diff`, and any other editor, forever.
  No proprietary format, no lock-in.
- **stdlib over frameworks.** `curses` + `reportlab` for PDF export. That
  is the entire dependency tree.
- **Config, not customization theater.** One `config.toml`. Plain data,
  fully remappable, sane defaults, never required.
- **Local. Always.** No accounts, no sync, no telemetry, no network calls.
  Your screenplay is a file on your disk and nowhere else.

If a feature doesn't serve "write and format a screenplay fast," it
doesn't belong here — no outlining boards, no cloud collab, no AI
co-writer.

## Grab a release, not `main`

**`main` is for code review** — browse the source, read the design notes
in `REFERENCE.md`, see how it's built. It doesn't carry the full project
tree (tests included).

To actually run Scriptee, **grab a tagged release from the
[Releases](../../releases) page** — that's the complete package: source,
config, installer, and the test suite.

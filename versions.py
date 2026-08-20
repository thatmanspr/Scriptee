"""File-based screenplay "versions": lets a script branch into numbered
sibling files that live in the same folder as the original --
Interlude.fountain (version 1, the original/untouched file), then
Interlude_v2.fountain, Interlude_v3.fountain, and so on, created via
Editor.do_version() / ':version new'.

Kept deliberately dumb and filesystem-only: the version *number* always
lives in the filename (per the "keep versions as numbers" design), never
in a side manifest that could drift out of sync with what's actually on
disk. The only thing stored *inside* a version's own file is its
optional, human-facing "Version Label" title-page field -- everything
else (which number it is, what its siblings are) is derived by looking
at the folder.

No curses/Editor dependency here on purpose, so this stays plain,
testable file-path logic.
"""

import re
from pathlib import Path

from fountain import from_fountain

# --------------------------------------------------------------------------
# Naming convention
# --------------------------------------------------------------------------

# The file itself (e.g. "Interlude.fountain") is always version 1. Every
# later version is named "<same stem>_v<N>.fountain" alongside it -- this
# is the ONE strict pattern Scriptee itself ever writes, so recognizing it
# back is unambiguous (no risk of mistaking someone's already-existing
# "My_video2.fountain" for a version of "My_video.fountain").
VERSION_SUFFIX_RE = re.compile(r'^(.*)_v(\d+)$')

# Looser patterns used only to *detect a possible collision* worth asking
# the user about -- e.g. someone manually saving "Interlude v2.fountain"
# (a space, not our "_v2") next to an existing "Interlude.fountain". Never
# used to silently treat a file as a version; only ever to trigger the
# yes/no prompt in Editor.resolve_version_collision().
_LOOSE_VERSION_PATTERNS = [
    re.compile(r'^(?P<base>.+?)[ _-]+[vV](?P<num>\d+)$'),
    re.compile(r'^(?P<base>.+?)[ _-]+(?P<num>\d+)$'),
    re.compile(r'^(?P<base>.+?)\s*\((?P<num>\d+)\)$'),
]


def version_info_for_path(path):
    """(base_stem, version_number) for `path`. A plain "Foo.fountain"
    (no "_vN" suffix) is version 1 -- the original -- by definition;
    "Foo_v3.fountain" is version 3 of base "Foo"."""
    stem = Path(path).stem
    m = VERSION_SUFFIX_RE.match(stem)
    if m:
        return m.group(1), int(m.group(2))
    return stem, 1


def version_path_for(path, n):
    """Path version `n` of the same group as `path` would live at,
    whether or not it currently exists."""
    base_stem, _ = version_info_for_path(path)
    directory = Path(path).parent
    suffix = Path(path).suffix
    if n == 1:
        return directory / f"{base_stem}{suffix}"
    return directory / f"{base_stem}_v{n}{suffix}"


def sibling_versions(path):
    """Every version sharing `path`'s base name, in the same folder,
    as a sorted [(version_number, Path), ...] list. Only includes
    entries that actually exist on disk -- version 1 is included only
    if the plain base file is still there (it might have been renamed
    or removed out-of-band)."""
    base_stem, _ = version_info_for_path(path)
    directory = Path(path).parent
    suffix = Path(path).suffix
    found = {}
    base_path = directory / f"{base_stem}{suffix}"
    if base_path.is_file():
        found[1] = base_path
    if directory.is_dir():
        for f in directory.glob(f"{base_stem}_v*{suffix}"):
            if not f.is_file():
                continue
            b, n = version_info_for_path(f)
            if b == base_stem:
                found[n] = f
    # The file currently being edited might not have been saved as
    # itself yet in edge cases (shouldn't normally happen -- save()
    # always writes before this is used from a live session -- but
    # costs nothing to be defensive).
    p = Path(path)
    if p.is_file():
        b, n = version_info_for_path(p)
        if b == base_stem:
            found[n] = p
    return sorted(found.items())


def next_version_number(path):
    """The next free version number for `path`'s group -- one past
    whatever the highest existing version currently is."""
    versions = sibling_versions(path)
    nums = [n for n, _ in versions] or [1]
    return max(nums) + 1


def label_for_version_file(path):
    """Best-effort read of a version file's own "Version Label" title
    field, for display in the ':version switch' list. Never raises --
    an unreadable/unparsable sibling just shows as unlabeled."""
    try:
        text = Path(path).read_text()
    except OSError:
        return ""
    try:
        metadata, _ = from_fountain(text)
    except Exception:
        return ""
    return metadata.get("Version Label", "")


def _strip_loose_version_suffix(stem):
    for pat in _LOOSE_VERSION_PATTERNS:
        m = pat.match(stem)
        if m:
            base = m.group("base").strip()
            if base:
                return base
    return None


def possible_version_collision(path):
    """If `path` doesn't exist yet but its name looks like it might be
    meant as another version of an existing sibling file (e.g. saving
    "Interlude v2.fountain" next to an existing "Interlude.fountain"),
    return that existing file's Path. Returns None if there's nothing
    to ask about -- including when `path` already follows Scriptee's
    own "_vN" convention, since that's already an unambiguous,
    intentional version and needs no prompting."""
    p = Path(path)
    if p.exists():
        return None
    stem = p.stem
    if VERSION_SUFFIX_RE.match(stem):
        return None
    base_guess = _strip_loose_version_suffix(stem)
    if not base_guess or base_guess == stem:
        return None
    candidate = p.parent / f"{base_guess}{p.suffix}"
    if candidate.is_file() and candidate != p:
        return candidate
    return None

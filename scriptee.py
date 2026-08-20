#!/usr/bin/env python3
"""
Scriptee — a vim-motion TUI screenwriter for Linux.

Files are stored in Fountain format (plain-text, industry standard),
so they're git-diffable and open in other screenwriting tools too.

Run:  python3 scriptee.py

This file is a thin backward-compatible launcher. The actual
implementation lives in scriptee_pkg/ (config, text_format, fountain,
stats, recovery, pdf_geometry, pdf_export, ui_helpers, screens, versions,
editor, app) -- split out of what used to be one 4,515-line module. Everything
those submodules export is re-imported here at the top level too, so
`import scriptee as s; s.to_fountain(...)`, `s.Editor`, `s.DEFAULT_CONFIG`,
etc. all keep working exactly as before for anyone (or any test) that
imported the old single-file version.

Each submodule is also reachable as an attribute of this module (e.g.
`s.config`, `s.editor`, `s.pdf_geometry`) -- that's the *correct* place
to read or monkeypatch anything that's reassigned at runtime rather than
mutated in place (AUTOSAVE_INTERVAL, MAX_RECENT_FILES, RECOVERY_DIR,
CONFIG_DIR, CONFIG_PATH, and the PDF_* geometry/font constants), and the
correct place to monkeypatch export_pdf/new_file_metadata as called from
inside Editor. See each submodule's own docstring for why.
"""

import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent / "scriptee_pkg"
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import config
import text_format
import fountain
import stats
import recovery
import pdf_geometry
import pdf_export
import ui_helpers
import screens
import versions
import editor
import app

from config import *  # noqa: F401,F403
from text_format import *  # noqa: F401,F403
from fountain import *  # noqa: F401,F403
from stats import *  # noqa: F401,F403
from recovery import *  # noqa: F401,F403
from pdf_geometry import *  # noqa: F401,F403
from pdf_export import *  # noqa: F401,F403
from ui_helpers import *  # noqa: F401,F403
from screens import *  # noqa: F401,F403
from versions import *  # noqa: F401,F403
from editor import *  # noqa: F401,F403
from app import *  # noqa: F401,F403

# A few underscore-prefixed helpers the test suite calls directly; star
# imports skip names starting with "_", so pull these in by hand.
from pdf_export import _pdf_element_is_glued, _forced_style  # noqa: F401
from pdf_geometry import _recompute_pdf_geometry, _apply_pdf_font_config, _pdf_font_for_style  # noqa: F401
from config import _bundled_courier_prime  # noqa: F401
from fountain import _needs_action_force  # noqa: F401
from text_format import _styled_wrap_uncached, _collapsed_offset, _inverse_collapsed_offset, _inverse_display_offset  # noqa: F401

if __name__ == "__main__":
    app.run()

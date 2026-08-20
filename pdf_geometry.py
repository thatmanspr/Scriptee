"""PDF layout geometry: page/margin/column math derived from
cfg["format"]["pdf"], and font resolution (base-14 Courier vs a
registered custom TrueType font).

Every PDF_* name below is reassigned wholesale by _recompute_pdf_geometry()
/ _apply_pdf_font_config() (called from config.apply_runtime_config()),
not mutated in place -- so pdf_export.py and editor.py, which read these
values, must do so via qualified `pdf_geometry.NAME` access, never
`from pdf_geometry import NAME`. A bare import would copy the value at
import time and go stale the moment these functions reassign it.
"""

from pathlib import Path

try:
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    HAVE_REPORTLAB = True
except ImportError:
    HAVE_REPORTLAB = False

import config

# --------------------------------------------------------------------------
# PDF export
# --------------------------------------------------------------------------
#
# Fonts: Courier / Courier-Bold / Courier-Oblique / Courier-BoldOblique are
# four of the PDF spec's 14 "base" fonts -- every conformant PDF viewer has
# them built in, so reportlab can reference them with zero embedding and
# nothing extra to install. That also means **bold**/*italic* markers now
# render as actual bold/oblique Courier instead of literal asterisks (the
# previous export ignored inline styling entirely and printed the raw
# markers). True Courier-Oblique (real slant) reads better in a PDF than
# the underline substitution the in-terminal view uses, since terminals
# generally can't render italics but a PDF viewer always can.
#
# Margins/indents/leading below follow the standard US industry format:
# Courier 12pt, 1" top/bottom/right margins, 1.5" left margin for action/
# heading/shot, dialogue starting ~2.5" from the left edge, character cues
# ~3.7", 55 lines/page -- the same convention Final Draft/Highland/Fade In
# target, and the same WRAP_WIDTH columns the in-editor view itself wraps
# at, so the PDF's line breaks match what you saw while writing.

PDF_FONT = "Courier"
PDF_FONT_BOLD = "Courier-Bold"
PDF_FONT_ITALIC = "Courier-Oblique"
# Set by _apply_pdf_font_config() whenever a "custom" font_family falls
# back to Courier (missing reportlab, missing/bad path, registration
# error) -- surfaced in the status bar after :pdf so a config typo is
# visible instead of silently changing nothing.
PDF_FONT_WARNING = ""

# Plain (width_pt, height_pt) page sizes, defined directly rather than
# imported from reportlab.lib.pagesizes -- pagination (paginate_buffer(),
# used for the live page/runtime estimate in the status bar) has to work
# even when reportlab isn't installed (:pdf export is the only thing that
# actually needs it), so nothing pagination-related may import reportlab.
PAGE_SIZES_PT = {
    "letter": (8.5 * 72.0, 11.0 * 72.0),
    "a4": (595.28, 841.89),
}

# All of the following are geometry derived from cfg["format"]["pdf"] by
# _recompute_pdf_geometry() (called from apply_runtime_config()) -- the
# values here are just startup defaults so the module works before a
# config is ever loaded (e.g. under test). Both export_pdf() and
# paginate_buffer() read these instead of their own hardcoded numbers, so
# a margin/font-size change in config.toml can't leave the two disagreeing
# about where pages break -- the exact "kept in lockstep" property the
# in-code comments below already describe for WRAP_WIDTH.
PDF_PAGE_W, PDF_PAGE_H = PAGE_SIZES_PT["letter"]
PDF_FONT_SIZE = 12
PDF_LEADING = 12  # tied to font size, not separately configurable
PDF_LEFT_EDGE = 1.5 * 72.0
PDF_DIALOGUE_LEFT = 2.5 * 72.0
PDF_PAREN_LEFT = 2.8 * 72.0
PDF_CHARACTER_LEFT = 3.5 * 72.0
PDF_RIGHT_EDGE = PDF_PAGE_W - 1.0 * 72.0
PDF_TOP_Y = PDF_PAGE_H - 1.0 * 72.0
PDF_BOTTOM_Y = 1.0 * 72.0
PDF_DUAL_GUTTER = 0.3 * 72.0
# Dual-dialogue column geometry, also recomputed by _recompute_pdf_geometry():
# two equal columns spanning left_edge..right_edge with a gutter between.
PDF_DUAL_COL_WIDTH = (PDF_RIGHT_EDGE - PDF_LEFT_EDGE - PDF_DUAL_GUTTER) / 2
PDF_DUAL_COL1_X = PDF_LEFT_EDGE
PDF_DUAL_COL2_X = PDF_LEFT_EDGE + PDF_DUAL_COL_WIDTH + PDF_DUAL_GUTTER
PDF_DUAL_WRAP_WIDTH = 30  # chars -- recomputed below
PDF_HEADING_BOLD = True
PDF_CHARACTER_BOLD = True
PDF_TRANSITION_BOLD = True
PDF_PAREN_ITALIC = True


def _recompute_pdf_geometry(cfg):
    """Derive every PDF layout constant above from cfg["format"]["pdf"].
    Called once by apply_runtime_config(); pure arithmetic, no reportlab
    dependency, so it's safe to run even when reportlab isn't installed."""
    global PDF_PAGE_W, PDF_PAGE_H, PDF_FONT_SIZE, PDF_LEADING
    global PDF_LEFT_EDGE, PDF_DIALOGUE_LEFT, PDF_PAREN_LEFT, PDF_CHARACTER_LEFT
    global PDF_RIGHT_EDGE, PDF_TOP_Y, PDF_BOTTOM_Y, PDF_DUAL_GUTTER
    global PDF_DUAL_COL_WIDTH, PDF_DUAL_COL1_X, PDF_DUAL_COL2_X, PDF_DUAL_WRAP_WIDTH
    global PDF_ROWS_PER_PAGE
    global PDF_HEADING_BOLD, PDF_CHARACTER_BOLD, PDF_TRANSITION_BOLD, PDF_PAREN_ITALIC

    pdf_cfg = cfg.get("format", config.DEFAULT_CONFIG["format"]).get(
        "pdf", config.DEFAULT_CONFIG["format"]["pdf"])
    d = config.DEFAULT_CONFIG["format"]["pdf"]

    page_size_name = pdf_cfg.get("page_size", d["page_size"])
    PDF_PAGE_W, PDF_PAGE_H = PAGE_SIZES_PT.get(page_size_name, PAGE_SIZES_PT["letter"])
    PDF_FONT_SIZE = pdf_cfg.get("font_size", d["font_size"])
    PDF_LEADING = PDF_FONT_SIZE

    PDF_LEFT_EDGE = pdf_cfg.get("left_edge_in", d["left_edge_in"]) * 72.0
    PDF_DIALOGUE_LEFT = pdf_cfg.get("dialogue_left_in", d["dialogue_left_in"]) * 72.0
    PDF_PAREN_LEFT = pdf_cfg.get("parenthetical_left_in", d["parenthetical_left_in"]) * 72.0
    PDF_CHARACTER_LEFT = pdf_cfg.get("character_left_in", d["character_left_in"]) * 72.0
    right_margin = pdf_cfg.get("right_margin_in", d["right_margin_in"]) * 72.0
    top_margin = pdf_cfg.get("top_margin_in", d["top_margin_in"]) * 72.0
    bottom_margin = pdf_cfg.get("bottom_margin_in", d["bottom_margin_in"]) * 72.0
    PDF_RIGHT_EDGE = PDF_PAGE_W - right_margin
    PDF_TOP_Y = PDF_PAGE_H - top_margin
    PDF_BOTTOM_Y = bottom_margin
    PDF_DUAL_GUTTER = pdf_cfg.get("dual_dialogue_gutter_in", d["dual_dialogue_gutter_in"]) * 72.0

    PDF_DUAL_COL_WIDTH = max(1.0, (PDF_RIGHT_EDGE - PDF_LEFT_EDGE - PDF_DUAL_GUTTER) / 2)
    PDF_DUAL_COL1_X = PDF_LEFT_EDGE
    PDF_DUAL_COL2_X = PDF_LEFT_EDGE + PDF_DUAL_COL_WIDTH + PDF_DUAL_GUTTER
    # Courier's fixed advance is 0.6em -- 72/(0.6*size) chars fit per inch
    # at the configured font size (10 chars/inch at the standard 12pt).
    chars_per_inch = 72.0 / (0.6 * PDF_FONT_SIZE)
    PDF_DUAL_WRAP_WIDTH = max(4, int((PDF_DUAL_COL_WIDTH / 72.0) * chars_per_inch))

    # See the original PDF_ROWS_PER_PAGE comment: a row is drawn whenever
    # the current y hasn't yet dropped below the bottom margin, so a page
    # holds floor(usable_height / leading) + 1 rows.
    PDF_ROWS_PER_PAGE = int((PDF_TOP_Y - PDF_BOTTOM_Y) // PDF_LEADING) + 1

    emphasis = pdf_cfg.get("emphasis", d.get("emphasis", {}))
    d_emphasis = d.get("emphasis", {})
    PDF_HEADING_BOLD = emphasis.get("heading_bold", d_emphasis.get("heading_bold", True))
    PDF_CHARACTER_BOLD = emphasis.get("character_bold", d_emphasis.get("character_bold", True))
    PDF_TRANSITION_BOLD = emphasis.get("transition_bold", d_emphasis.get("transition_bold", True))
    PDF_PAREN_ITALIC = emphasis.get("parenthetical_italic", d_emphasis.get("parenthetical_italic", True))

    _apply_pdf_font_config(pdf_cfg, d)


def _apply_pdf_font_config(pdf_cfg, d):
    """Set PDF_FONT/PDF_FONT_BOLD/PDF_FONT_ITALIC from
    cfg["format"]["pdf"]["font_family"]. "courier" (the default) uses the
    PDF spec's built-in base-14 Courier family -- no file, no reportlab
    call needed. "custom" registers TrueType files named under
    [format.pdf.custom_font] instead, so a house font (e.g. Courier Prime)
    can be used. Any failure here (reportlab missing, path missing/bad,
    a corrupt font file) falls back to Courier rather than raising --
    a font typo should never be able to break :pdf export -- and records
    why in PDF_FONT_WARNING so the caller can surface it."""
    global PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC, PDF_FONT_WARNING
    PDF_FONT_WARNING = ""
    family = pdf_cfg.get("font_family", d.get("font_family", "courier"))
    if family != "custom":
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")
        return
    if not HAVE_REPORTLAB:
        PDF_FONT_WARNING = ("format.pdf.font_family is \"custom\" but reportlab "
                             "isn't installed -- using Courier instead.")
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")
        return
    paths = pdf_cfg.get("custom_font", d.get("custom_font", {}))
    regular = paths.get("regular", "")
    if not regular or not Path(regular).expanduser().is_file():
        PDF_FONT_WARNING = ("format.pdf.font_family is \"custom\" but "
                             "custom_font.regular is missing or not found -- "
                             "using Courier instead.")
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")
        return
    bold = paths.get("bold", "")
    italic = paths.get("italic", "")
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont("ScripteeCustom", str(Path(regular).expanduser())))
        PDF_FONT = "ScripteeCustom"
        if bold and Path(bold).expanduser().is_file():
            pdfmetrics.registerFont(TTFont("ScripteeCustom-Bold", str(Path(bold).expanduser())))
            PDF_FONT_BOLD = "ScripteeCustom-Bold"
        else:
            PDF_FONT_BOLD = PDF_FONT  # no distinct bold weight given
        if italic and Path(italic).expanduser().is_file():
            pdfmetrics.registerFont(TTFont("ScripteeCustom-Italic", str(Path(italic).expanduser())))
            PDF_FONT_ITALIC = "ScripteeCustom-Italic"
        else:
            PDF_FONT_ITALIC = PDF_FONT  # no distinct italic weight given
    except Exception as e:
        PDF_FONT_WARNING = f"Couldn't load custom PDF font ({e}) -- using Courier instead."
        PDF_FONT, PDF_FONT_BOLD, PDF_FONT_ITALIC = (
            "Courier", "Courier-Bold", "Courier-Oblique")


PDF_SEMIBOLD_OFFSET = 0.3  # pt -- hairline double-strike offset, see _draw_styled_row()


def _pdf_font_for_style(style):
    if style == "bold":
        return PDF_FONT_BOLD
    if style == "semibold":
        # No dedicated semibold weight ships with Courier Prime (or base-14
        # Courier), so "semibold" text is still measured/drawn in the
        # regular weight -- _draw_styled_row() fakes the extra heft with a
        # hairline double-strike rather than switching fonts.
        return PDF_FONT
    if style == "italic":
        return PDF_FONT_ITALIC
    return PDF_FONT



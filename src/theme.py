"""Design tokens and QSS themes for the workbench.

Values come from the Modern Enterprise design system: a 4px spacing grid, a
tonal surface palette, and 1px borders rather than shadows for hierarchy. Colors
live here and nowhere else -- widgets reference tokens, never raw hex.

The design system asks for Hanken Grotesk for headings and Inter for everything
else. Both are bundled under assets/fonts and registered by load_fonts(), so the
app looks the same on a machine that has neither installed. resolve_family()
still falls back to whatever is present if loading fails. Material Symbols is
not bundled -- nothing here draws icon glyphs.
"""

import os
import sys
import tomllib

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

# --- Spacing (4px grid) -----------------------------------------------------

SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 16
SPACING_LG = 24

# --- Radius -----------------------------------------------------------------

RADIUS_SM = 2
RADIUS_DEFAULT = 4
RADIUS_LG = 8

# --- Type scale: (size_px, weight) ------------------------------------------

TYPO_HEADLINE_SM = (16, 600)
TYPO_BODY_MD = (14, 400)
TYPO_BODY_SM = (13, 400)
TYPO_LABEL_MD = (12, 600)

# Dense tables of numbers, so the compact row height.
ROW_HEIGHT = 32
HEADER_HEIGHT = 32

FONT_HEADING_CANDIDATES = ["Hanken Grotesk", "Inter", "Cantarell", "Segoe UI", "DejaVu Sans"]
FONT_BODY_CANDIDATES = ["Inter", "Cantarell", "Segoe UI", "Noto Sans", "DejaVu Sans"]

# --- Palettes ---------------------------------------------------------------

LIGHT = {
    "dark": False,
    "background": "#faf9ff",
    "surface_lowest": "#ffffff",
    "surface_low": "#f1f3ff",
    "surface": "#e9edff",
    "surface_high": "#e1e8ff",
    "on_surface": "#051a3e",
    "on_surface_variant": "#434654",
    "outline": "#737685",
    "outline_variant": "#c3c6d6",
    "primary": "#003d9b",
    "on_primary": "#ffffff",
    "primary_container": "#0052cc",
    "primary_fixed": "#dae2ff",
    "error": "#ba1a1a",
}

DARK = {
    "dark": True,
    "background": "#0b121f",
    "surface_lowest": "#161c27",
    "surface_low": "#1e2738",
    "surface": "#252d3d",
    "surface_high": "#2d374a",
    "on_surface": "#edf0ff",
    "on_surface_variant": "#9ca3b8",
    "outline": "#3d4560",
    "outline_variant": "#252d3d",
    "primary": "#b2c5ff",
    "on_primary": "#00306e",
    "primary_container": "#0040a2",
    "primary_fixed": "#0040a2",
    "error": "#ffb4ab",
}


BUNDLED_FONTS = ["Inter.ttf", "HankenGrotesk.ttf"]


def fonts_directory():
    """assets/fonts, both from a source checkout and from a PyInstaller bundle."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return os.path.join(bundle, "assets", "fonts")

    source_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(source_root, "assets", "fonts")


def load_fonts():
    """Register the bundled fonts. Returns the families Qt actually took."""
    directory = fonts_directory()
    families = []
    for filename in BUNDLED_FONTS:
        font_id = QFontDatabase.addApplicationFont(os.path.join(directory, filename))
        if font_id != -1:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


# Omarchy ships one colors.toml per theme, in the same shape for light and
# dark, so its whole set can be offered here.
OMARCHY_THEME_DIRECTORIES = [
    os.path.expanduser("~/.config/omarchy/themes"),
    "/usr/share/omarchy/themes",
]

BUILT_IN_THEMES = {"Light": LIGHT, "Dark": DARK}


def _rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _mix(first, second, amount):
    a, b = _rgb(first), _rgb(second)
    return _hex(a[i] + (b[i] - a[i]) * amount for i in range(3))


def _luminance(value):
    r, g, b = (c / 255 for c in _rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette_from_colors(colors):
    """Map an Omarchy colors.toml onto this app's tokens.

    The same mapping serves both modes: a theme's `background` is always a step
    away from its `dark_background`, so surfaces sit above the canvas whichever
    direction that step goes.
    """
    accent = colors["accent"]
    foreground = colors["foreground"]

    # Text on the accent has to contrast with the accent, not with the rest of
    # the theme: a light theme's foreground is dark, and unreadable on a strong
    # accent. Pick whichever end of the palette is further from it.
    on_primary = max((colors["darker_background"], colors["bright_foreground"]),
                     key=lambda candidate: abs(_luminance(candidate) - _luminance(accent)))

    return {
        "dark": colors.get("mode", "dark") == "dark",
        "background": colors["dark_background"],
        "surface_lowest": colors["background"],
        "surface_low": colors["lighter_background"],
        "surface": colors["selection"],
        "surface_high": colors["selection"],
        "on_surface": foreground,
        "on_surface_variant": colors["dark_foreground"],
        "outline": colors["muted"],
        "outline_variant": colors["selection"],
        "primary": accent,
        "on_primary": on_primary,
        "primary_container": _mix(accent, foreground, 0.25),
        "primary_fixed": colors["selection"],
        "error": colors["red"],
    }


def theme_title(name):
    return name.replace("-", " ").title()


def available_themes():
    """Every theme on offer: the built-ins plus whatever Omarchy provides."""
    themes = dict(BUILT_IN_THEMES)

    for directory in OMARCHY_THEME_DIRECTORIES:
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name, "colors.toml")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as f:
                    colors = tomllib.load(f)
                themes[theme_title(name)] = palette_from_colors(colors)
            except (OSError, tomllib.TOMLDecodeError, KeyError):
                continue  # a theme we cannot read is simply not offered

    return themes


def resolve_family(candidates):
    """The first installed family from `candidates`, else the application font."""
    available = set(QFontDatabase.families())
    for family in candidates:
        if family in available:
            return family

    app = QApplication.instance()
    return app.font().family() if app else "sans-serif"


def stylesheet(palette=None, dark=False):
    c = palette or (DARK if dark else LIGHT)
    dark = c.get("dark", dark)
    body = resolve_family(FONT_BODY_CANDIDATES)
    heading = resolve_family(FONT_HEADING_CANDIDATES)

    return f"""
/* --- Base ------------------------------------------------------------ */
QMainWindow, QDialog {{
    background-color: {c["background"]};
}}
QWidget {{
    font-family: '{body}';
    font-size: {TYPO_BODY_MD[0]}px;
    color: {c["on_surface"]};
}}
QLabel {{
    color: {c["on_surface_variant"]};
    font-size: {TYPO_BODY_SM[0]}px;
}}
QToolTip {{
    background-color: {c["surface_lowest"]};
    color: {c["on_surface"]};
    border: 1px solid {c["outline_variant"]};
    padding: {SPACING_SM}px;
}}

/* --- Menu bar -------------------------------------------------------- */
QMenuBar {{
    background-color: {c["surface_low"]};
    border-bottom: 1px solid {c["outline_variant"]};
}}
QMenuBar::item {{
    padding: {SPACING_SM}px {SPACING_MD}px;
    background: transparent;
    color: {c["on_surface"]};
}}
QMenuBar::item:selected {{
    background-color: {c["surface_high"]};
}}
QMenu {{
    background-color: {c["surface_lowest"]};
    border: 1px solid {c["outline_variant"]};
    border-radius: {RADIUS_LG}px;
    padding: {SPACING_XS}px;
}}
QMenu::item {{
    padding: {SPACING_SM}px {SPACING_MD}px;
    border-radius: {RADIUS_DEFAULT}px;
    color: {c["on_surface"]};
}}
QMenu::item:selected {{
    background-color: {c["primary_fixed"]};
    color: {c["primary"] if dark else c["primary"]};
}}

/* --- Buttons --------------------------------------------------------- */
QPushButton {{
    font-size: {TYPO_LABEL_MD[0]}px;
    font-weight: {TYPO_LABEL_MD[1]};
    padding: {SPACING_SM}px {SPACING_MD}px;
    border-radius: {RADIUS_DEFAULT}px;
    border: 1px solid {c["outline_variant"]};
    background-color: {c["surface_lowest"]};
    color: {c["on_surface"]};
}}
QPushButton:hover {{
    background-color: {c["surface_low"]};
    border-color: {c["outline"]};
}}
QPushButton:pressed {{
    background-color: {c["surface"]};
}}
QPushButton:disabled {{
    color: {c["outline_variant"] if dark else c["outline"]};
    border-color: {c["outline_variant"]};
    background-color: {c["surface_low"]};
}}
QPushButton[class="primary"] {{
    background-color: {c["primary"]};
    color: {c["on_primary"]};
    border: 1px solid {c["primary"]};
}}
QPushButton[class="primary"]:hover {{
    background-color: {c["primary_container"]};
    border-color: {c["primary_container"]};
}}
QPushButton[class="cell"] {{
    padding: {SPACING_XS}px {SPACING_SM}px;
    font-weight: {TYPO_BODY_MD[1]};
    letter-spacing: 0;
}}
QPushButton[class="primary"]:disabled {{
    background-color: {c["surface_low"]};
    border-color: {c["outline_variant"]};
    color: {c["outline"]};
}}

/* --- Section headers -------------------------------------------------- */
QToolButton#sectionHeader {{
    font-family: '{heading}';
    font-size: {TYPO_HEADLINE_SM[0]}px;
    font-weight: {TYPO_HEADLINE_SM[1]};
    color: {c["on_surface"]};
    background: transparent;
    border: none;
    padding: {SPACING_SM}px {SPACING_XS}px;
    text-align: left;
}}
QToolButton#sectionHeader:hover {{
    color: {c["primary"]};
}}
QFrame#sectionRule {{
    color: {c["outline_variant"]};
    background-color: {c["outline_variant"]};
    max-height: 1px;
    border: none;
}}

/* --- Inputs ----------------------------------------------------------- */
QLineEdit, QComboBox {{
    background-color: {c["surface_lowest"]};
    border: 1px solid {c["outline_variant"]};
    border-radius: {RADIUS_SM}px;
    padding: {SPACING_SM}px {SPACING_SM}px;
    color: {c["on_surface"]};
    selection-background-color: {c["primary_fixed"]};
    selection-color: {c["on_surface"]};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {c["primary"]};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background-color: {c["surface_low"]};
    color: {c["outline"]};
}}
QComboBox::drop-down {{
    border: none;
    width: {SPACING_LG}px;
}}
QComboBox QAbstractItemView {{
    background-color: {c["surface_lowest"]};
    border: 1px solid {c["outline_variant"]};
    border-radius: {RADIUS_DEFAULT}px;
    padding: {SPACING_XS}px;
    selection-background-color: {c["primary_fixed"]};
    selection-color: {c["on_surface"]};
    outline: none;
}}

/* --- Tables ----------------------------------------------------------- */
QTableWidget {{
    background-color: {c["surface_lowest"]};
    border: 1px solid {c["outline_variant"]};
    border-radius: {RADIUS_LG}px;
    gridline-color: {c["outline_variant"]};
    selection-background-color: {c["primary_fixed"]};
    selection-color: {c["on_surface"]};
    font-size: {TYPO_BODY_SM[0]}px;
    outline: none;
}}
/* No horizontal padding here: Qt insets a cell's widget by it, so buttons and
   dropdowns would float inside their cell instead of filling it. Tables whose
   cells are all text opt back in below. */
QTableWidget::item {{
    color: {c["on_surface"]};
}}
QTableWidget[textcells="true"]::item {{
    padding: 0 {SPACING_SM}px;
}}
QTableWidget::item:selected {{
    background-color: {c["primary_fixed"]};
    color: {c["on_surface"]};
}}
QHeaderView {{
    background-color: {c["surface_low"]};
    border: none;
}}
QHeaderView::section {{
    background-color: {c["surface_low"]};
    color: {c["on_surface_variant"]};
    font-size: {TYPO_LABEL_MD[0]}px;
    font-weight: {TYPO_LABEL_MD[1]};
    padding: 0 {SPACING_SM}px;
    height: {HEADER_HEIGHT}px;
    border: none;
    border-right: 1px solid {c["outline_variant"]};
    border-bottom: 1px solid {c["outline_variant"]};
}}

/* The header paints over the table's rounded top edge, so the sections that
   sit in the corners have to carry the radius themselves. Which widget is in
   the top-left depends on whether the table shows a vertical header: the
   corner button when it does, the first column header when it does not. */
QTableCornerButton::section {{
    background-color: {c["surface_low"]};
    border: none;
    border-right: 1px solid {c["outline_variant"]};
    border-bottom: 1px solid {c["outline_variant"]};
    border-top-left-radius: {RADIUS_LG}px;
}}
QTableWidget[cornerless="true"] QHeaderView::section:horizontal:first {{
    border-top-left-radius: {RADIUS_LG}px;
}}
QHeaderView::section:horizontal:last {{
    border-top-right-radius: {RADIUS_LG}px;
    border-right: none;
}}
QHeaderView::section:horizontal:only-one {{
    border-top-left-radius: {RADIUS_LG}px;
    border-top-right-radius: {RADIUS_LG}px;
}}
QHeaderView::section:vertical {{
    color: {c["outline"]};
    border-right: 1px solid {c["outline_variant"]};
}}

/* --- Scroll area ------------------------------------------------------ */
/* Its viewport fills itself with the palette's base colour, which has nothing
   to do with this theme; the panels have to show the window behind them. */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

/* --- Splitters and scrollbars ----------------------------------------- */
QSplitter::handle {{
    background-color: transparent;
}}
QSplitter::handle:horizontal {{
    width: {SPACING_SM}px;
}}
QSplitter::handle:vertical {{
    height: {SPACING_SM}px;
}}
QSplitter::handle:hover {{
    background-color: {c["outline_variant"]};
}}
QScrollBar:vertical {{
    width: {SPACING_SM}px;
    background: transparent;
}}
QScrollBar:horizontal {{
    height: {SPACING_SM}px;
    background: transparent;
}}
QScrollBar::handle {{
    background: {c["outline_variant"]};
    border-radius: {RADIUS_DEFAULT}px;
    min-height: {SPACING_LG}px;
    min-width: {SPACING_LG}px;
}}
QScrollBar::handle:hover {{
    background: {c["outline"]};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* --- Progress --------------------------------------------------------- */
QProgressBar {{
    background-color: {c["surface_low"]};
    border: 1px solid {c["outline_variant"]};
    border-radius: {RADIUS_SM}px;
    height: {SPACING_SM}px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {c["primary"]};
    border-radius: {RADIUS_SM}px;
}}
"""


SYSTEM_THEME = "System"


def os_dark(app):
    """Whether the desktop is asking for a dark colour scheme.

    Qt reports Unknown on platforms that do not say, where light is the
    safer assumption.
    """
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply(app, name=SYSTEM_THEME):
    """Apply a theme by name. SYSTEM_THEME follows the desktop's light/dark."""
    app.setStyleSheet(stylesheet(palette_for(app, name)))


def palette_for(app, name):
    if name == SYSTEM_THEME or name not in available_themes():
        return DARK if os_dark(app) else LIGHT
    return available_themes()[name]

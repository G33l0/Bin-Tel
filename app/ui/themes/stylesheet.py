"""Builds the application stylesheet from a :class:`ThemeTokens` palette.

One template, five palettes. Sizing is driven by a scale factor so the
Appearance settings can enlarge the whole interface without a restart, and by a
compact flag that tightens padding for smaller displays.
"""

from __future__ import annotations

from app.ui.themes.palette import ThemeTokens


def build_stylesheet(theme: ThemeTokens, *, scale: float = 1.0, compact: bool = False) -> str:
    """Render the full QSS for *theme*."""
    scale = max(0.75, min(2.0, scale))

    def px(value: float) -> str:
        return f"{max(1, round(value * scale))}px"

    def pt(value: float) -> str:
        return f"{max(7, round(value * scale))}pt"

    density = 0.78 if compact else 1.0
    t = theme

    return f"""
/* ============================ Bin-Tel — {t.display_name} ============================ */

* {{
    outline: none;
}}

/* Plain container widgets stay transparent so a card's surface shows through
   them; only the actual surfaces below paint a background. */
QWidget {{
    color: {t.text_primary};
    font-family: "Inter", "Segoe UI", "SF Pro Text", "Ubuntu", "Noto Sans", sans-serif;
    font-size: {pt(10)};
}}

QWidget:disabled {{
    color: {t.disabled_fg};
}}

QMainWindow, QDialog, QStackedWidget, QTabWidget, QSplitter {{
    background-color: {t.window_bg};
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

QToolTip {{
    background-color: {t.tooltip_bg};
    color: {t.tooltip_fg};
    border: 1px solid {t.border_strong};
    border-radius: {px(6)};
    padding: {px(6 * density)} {px(9 * density)};
    font-size: {pt(9)};
}}

/* ---------------------------------- Typography --------------------------- */

QLabel[role="pageTitle"] {{
    font-size: {pt(19)};
    font-weight: 700;
    color: {t.text_primary};
}}

QLabel[role="pageSubtitle"] {{
    font-size: {pt(10)};
    color: {t.text_secondary};
}}

QLabel[role="sectionTitle"] {{
    font-size: {pt(12)};
    font-weight: 600;
    color: {t.text_primary};
}}

QLabel[role="fieldLabel"] {{
    font-size: {pt(8)};
    font-weight: 600;
    color: {t.text_muted};
    letter-spacing: 1px;
}}

QLabel[role="fieldValue"] {{
    font-size: {pt(11)};
    color: {t.text_primary};
}}

QLabel[role="metricValue"] {{
    font-size: {pt(22)};
    font-weight: 700;
    color: {t.text_primary};
}}

QLabel[role="metricLabel"] {{
    font-size: {pt(9)};
    color: {t.text_secondary};
}}

QLabel[role="muted"] {{
    color: {t.text_muted};
}}

QLabel[role="mono"] {{
    font-family: "JetBrains Mono", "SF Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
    font-size: {pt(12)};
    letter-spacing: 1px;
}}

QLabel[state="success"] {{ color: {t.success}; }}
QLabel[state="warning"] {{ color: {t.warning}; }}
QLabel[state="danger"]  {{ color: {t.danger}; }}
QLabel[state="info"]    {{ color: {t.info}; }}

/* ------------------------------------ Header ----------------------------- */

QFrame#AppHeader {{
    background-color: {t.header_bg};
    border-bottom: 1px solid {t.header_border};
}}

QFrame#AppHeader QLabel {{
    background: transparent;
}}

/* ----------------------------------- Sidebar ----------------------------- */

QFrame#Sidebar {{
    background-color: {t.sidebar_bg};
    border-right: 1px solid {t.sidebar_border};
}}

QFrame#Sidebar QLabel {{
    background: transparent;
}}

QLabel#SidebarSectionLabel {{
    color: {t.text_muted};
    font-size: {pt(8)};
    font-weight: 700;
    letter-spacing: 1.4px;
    padding: {px(12 * density)} {px(14)} {px(4)} {px(14)};
}}

QPushButton#NavButton {{
    background-color: transparent;
    color: {t.nav_fg};
    border: none;
    border-left: {px(3)} solid transparent;
    border-radius: 0px;
    padding: {px(9 * density)} {px(12)};
    text-align: left;
    font-size: {pt(10)};
    font-weight: 500;
}}

QPushButton#NavButton:hover {{
    background-color: {t.nav_hover_bg};
    color: {t.text_primary};
}}

QPushButton#NavButton:checked {{
    background-color: {t.nav_active_bg};
    color: {t.nav_active_fg};
    border-left: {px(3)} solid {t.nav_active_marker};
    font-weight: 600;
}}

QPushButton#NavButton:focus {{
    border-left: {px(3)} solid {t.focus_ring};
}}

/* ------------------------------------ Cards ------------------------------ */

QFrame#Card, QFrame#MetricCard, QFrame#ResultCard {{
    background-color: {t.card_bg};
    border: 1px solid {t.card_border};
    border-radius: {px(10)};
}}

QFrame#Card QLabel, QFrame#MetricCard QLabel, QFrame#ResultCard QLabel {{
    background: transparent;
}}

QFrame#MetricCard:hover {{
    border: 1px solid {t.card_hover_border};
}}

QFrame#Divider {{
    background-color: {t.divider};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

QFrame#Chip {{
    background-color: {t.chip_bg};
    border: 1px solid {t.border};
    border-radius: {px(11)};
}}

QLabel#ChipText {{
    color: {t.chip_fg};
    font-size: {pt(9)};
    font-weight: 600;
    background: transparent;
}}

/* ----------------------------------- Buttons ----------------------------- */

QPushButton {{
    background-color: {t.button_bg};
    color: {t.button_fg};
    border: 1px solid {t.button_border};
    border-radius: {px(7)};
    padding: {px(7 * density)} {px(15 * density)};
    font-size: {pt(10)};
    font-weight: 500;
    min-height: {px(20)};
}}

QPushButton:hover {{
    background-color: {t.button_hover_bg};
    border-color: {t.border_strong};
}}

QPushButton:pressed {{
    background-color: {t.button_pressed_bg};
}}

QPushButton:focus {{
    border: 1px solid {t.focus_ring};
}}

QPushButton:disabled {{
    background-color: {t.disabled_bg};
    color: {t.disabled_fg};
    border-color: {t.disabled_border};
}}

QPushButton[variant="primary"] {{
    background-color: {t.primary};
    color: {t.primary_fg};
    border: 1px solid {t.primary};
    font-weight: 600;
}}

QPushButton[variant="primary"]:hover {{
    background-color: {t.primary_hover};
    border-color: {t.primary_hover};
}}

QPushButton[variant="primary"]:pressed {{
    background-color: {t.primary_pressed};
    border-color: {t.primary_pressed};
}}

QPushButton[variant="primary"]:disabled {{
    background-color: {t.disabled_bg};
    color: {t.disabled_fg};
    border-color: {t.disabled_border};
}}

QPushButton[variant="danger"] {{
    background-color: transparent;
    color: {t.danger};
    border: 1px solid {t.danger};
}}

QPushButton[variant="danger"]:hover {{
    background-color: {t.danger};
    color: {t.text_inverse};
}}

QPushButton[variant="ghost"] {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {t.text_secondary};
    padding: {px(5 * density)} {px(9 * density)};
}}

QPushButton[variant="ghost"]:hover {{
    background-color: {t.button_hover_bg};
    color: {t.text_primary};
}}

QPushButton[variant="ghost"]:checked {{
    background-color: {t.nav_active_bg};
    color: {t.nav_active_fg};
}}

QPushButton[variant="link"] {{
    background: transparent;
    border: none;
    color: {t.primary};
    text-align: left;
    padding: {px(2)} 0px;
    font-weight: 600;
}}

QPushButton[variant="link"]:hover {{
    color: {t.primary_hover};
    text-decoration: underline;
}}

/* ----------------------------- Inputs and combos ------------------------- */

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {t.input_bg};
    color: {t.input_fg};
    border: 1px solid {t.input_border};
    border-radius: {px(7)};
    padding: {px(7 * density)} {px(10)};
    selection-background-color: {t.selection_bg};
    selection-color: {t.selection_fg};
}}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {t.input_focus_border};
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {t.disabled_bg};
    color: {t.disabled_fg};
    border-color: {t.disabled_border};
}}

QLineEdit[role="search"] {{
    font-size: {pt(13)};
    padding: {px(12 * density)} {px(14)} {px(12 * density)} {px(38)};
    border-radius: {px(10)};
}}

QLineEdit[role="searchCompact"] {{
    padding-left: {px(34)};
    border-radius: {px(8)};
}}

QLineEdit[state="error"] {{
    border: 1px solid {t.danger};
}}

QComboBox {{
    background-color: {t.input_bg};
    color: {t.input_fg};
    border: 1px solid {t.input_border};
    border-radius: {px(7)};
    padding: {px(6 * density)} {px(30)} {px(6 * density)} {px(10)};
    min-height: {px(20)};
}}

QComboBox:hover {{
    border-color: {t.border_strong};
}}

QComboBox:focus, QComboBox:on {{
    border: 1px solid {t.input_focus_border};
}}

QComboBox:disabled {{
    background-color: {t.disabled_bg};
    color: {t.disabled_fg};
    border-color: {t.disabled_border};
}}

QComboBox::drop-down {{
    border: none;
    width: {px(26)};
}}

QComboBox QAbstractItemView {{
    background-color: {t.dialog_bg};
    color: {t.text_primary};
    border: 1px solid {t.dialog_border};
    border-radius: {px(7)};
    padding: {px(4)};
    selection-background-color: {t.selection_bg};
    selection-color: {t.selection_fg};
    outline: none;
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {t.button_bg};
    border: none;
    width: {px(16)};
}}

/* ------------------------------ Toggles and radios ----------------------- */

QCheckBox, QRadioButton {{
    spacing: {px(8)};
    color: {t.text_primary};
    padding: {px(3)} 0px;
    background: transparent;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: {px(16)};
    height: {px(16)};
    border: 1px solid {t.input_border};
    background-color: {t.input_bg};
}}

QCheckBox::indicator {{
    border-radius: {px(4)};
}}

QRadioButton::indicator {{
    border-radius: {px(8)};
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t.primary};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {t.primary};
    border-color: {t.primary};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background-color: {t.disabled_bg};
    border-color: {t.disabled_border};
}}

QSlider::groove:horizontal {{
    height: {px(4)};
    background: {t.progress_bg};
    border-radius: {px(2)};
}}

QSlider::handle:horizontal {{
    background: {t.primary};
    width: {px(14)};
    height: {px(14)};
    margin: {px(-6)} 0;
    border-radius: {px(7)};
}}

QSlider::sub-page:horizontal {{
    background: {t.primary};
    border-radius: {px(2)};
}}

/* ------------------------------------ Tables ----------------------------- */

QTableView, QTreeView, QListView {{
    background-color: {t.table_bg};
    alternate-background-color: {t.table_row_alt_bg};
    color: {t.text_primary};
    border: 1px solid {t.border};
    border-radius: {px(8)};
    gridline-color: {t.table_grid};
    selection-background-color: {t.table_selected_bg};
    selection-color: {t.table_selected_fg};
    outline: none;
}}

QTableView::item, QTreeView::item, QListView::item {{
    padding: {px(6 * density)} {px(8)};
    border: none;
}}

QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{
    background-color: {t.table_hover_bg};
}}

QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{
    background-color: {t.table_selected_bg};
    color: {t.table_selected_fg};
}}

QHeaderView {{
    background-color: {t.table_header_bg};
}}

QHeaderView::section {{
    background-color: {t.table_header_bg};
    color: {t.table_header_fg};
    border: none;
    border-right: 1px solid {t.table_grid};
    border-bottom: 1px solid {t.table_grid};
    padding: {px(8 * density)} {px(9)};
    font-size: {pt(9)};
    font-weight: 600;
}}

QHeaderView::section:hover {{
    background-color: {t.table_hover_bg};
    color: {t.text_primary};
}}

QHeaderView::section:last {{
    border-right: none;
}}

QTableCornerButton::section {{
    background-color: {t.table_header_bg};
    border: none;
}}

/* ---------------------------------- Scrollbars --------------------------- */

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: {t.scrollbar_bg};
    width: {px(11)};
    margin: 0px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {t.scrollbar_handle};
    border-radius: {px(5)};
    min-height: {px(28)};
    margin: {px(2)};
}}

QScrollBar::handle:vertical:hover {{
    background: {t.scrollbar_handle_hover};
}}

QScrollBar:horizontal {{
    background: {t.scrollbar_bg};
    height: {px(11)};
    margin: 0px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: {t.scrollbar_handle};
    border-radius: {px(5)};
    min-width: {px(28)};
    margin: {px(2)};
}}

QScrollBar::handle:horizontal:hover {{
    background: {t.scrollbar_handle_hover};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ---------------------------------- Progress ----------------------------- */

QProgressBar {{
    background-color: {t.progress_bg};
    border: none;
    border-radius: {px(5)};
    height: {px(9)};
    text-align: center;
    color: {t.text_secondary};
    font-size: {pt(9)};
}}

QProgressBar::chunk {{
    background-color: {t.progress_chunk};
    border-radius: {px(5)};
}}

QProgressBar[variant="tall"] {{
    height: {px(20)};
}}

/* ------------------------------------- Tabs ------------------------------ */

QTabWidget::pane {{
    border: 1px solid {t.border};
    border-radius: {px(8)};
    background-color: {t.card_bg};
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    color: {t.text_secondary};
    border: none;
    border-bottom: {px(2)} solid transparent;
    padding: {px(8 * density)} {px(15)};
    margin-right: {px(2)};
    font-weight: 500;
}}

QTabBar::tab:hover {{
    color: {t.text_primary};
}}

QTabBar::tab:selected {{
    color: {t.text_primary};
    border-bottom: {px(2)} solid {t.primary};
    font-weight: 600;
}}

/* ------------------------------- Menus and bars -------------------------- */

QMenu {{
    background-color: {t.dialog_bg};
    color: {t.text_primary};
    border: 1px solid {t.dialog_border};
    border-radius: {px(8)};
    padding: {px(5)};
}}

QMenu::item {{
    padding: {px(6 * density)} {px(22)} {px(6 * density)} {px(12)};
    border-radius: {px(5)};
}}

QMenu::item:selected {{
    background-color: {t.selection_bg};
    color: {t.selection_fg};
}}

QMenu::separator {{
    height: 1px;
    background: {t.divider};
    margin: {px(4)} {px(8)};
}}

QMenuBar {{
    background-color: {t.header_bg};
    color: {t.text_secondary};
    border-bottom: 1px solid {t.header_border};
}}

QMenuBar::item {{
    padding: {px(5)} {px(10)};
    background: transparent;
    border-radius: {px(5)};
}}

QMenuBar::item:selected {{
    background-color: {t.nav_hover_bg};
    color: {t.text_primary};
}}

QStatusBar {{
    background-color: {t.header_bg};
    color: {t.text_secondary};
    border-top: 1px solid {t.header_border};
}}

QStatusBar::item {{
    border: none;
}}

/* -------------------------------- Stacked pages -------------------------- */

QStackedWidget {{
    background-color: {t.window_bg};
}}

QSplitter::handle {{
    background-color: {t.divider};
}}

QSplitter::handle:hover {{
    background-color: {t.primary};
}}

/* ----------------------------------- Dialogs ----------------------------- */

QDialog QFrame#DialogBody {{
    background-color: {t.dialog_bg};
    border: 1px solid {t.dialog_border};
    border-radius: {px(12)};
}}

QMessageBox {{
    background-color: {t.dialog_bg};
}}

/* ------------------------------- State surfaces -------------------------- */

QFrame#StateBanner {{
    border-radius: {px(8)};
    border: 1px solid {t.border};
    background-color: {t.window_bg_alt};
}}

QFrame#StateBanner[state="success"] {{ border-color: {t.success}; }}
QFrame#StateBanner[state="warning"] {{ border-color: {t.warning}; }}
QFrame#StateBanner[state="danger"]  {{ border-color: {t.danger}; }}
QFrame#StateBanner[state="info"]    {{ border-color: {t.info}; }}

QFrame#Toast {{
    background-color: {t.tooltip_bg};
    border: 1px solid {t.border_strong};
    border-radius: {px(9)};
}}

QFrame#Toast QLabel {{
    color: {t.tooltip_fg};
    background: transparent;
}}

/* --------------------------- First-run experience ------------------------ */

QWidget#FirstRunSurface {{
    background-color: {t.window_bg};
}}

QFrame#FirstRunPanel {{
    background-color: {t.card_bg};
    border: 1px solid {t.card_border};
    border-radius: {px(14)};
}}

QFrame#FirstRunPanel QLabel {{
    background: transparent;
}}
"""

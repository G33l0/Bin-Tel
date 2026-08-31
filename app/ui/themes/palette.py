"""Theme definitions.

Each theme is a complete design: window, sidebar, cards, buttons, inputs,
tables, selection, hover, disabled, dialogs, progress and status colours are
all specified. Changing a theme changes the whole surface, not one background.

The five built-in themes are the source of truth; ``assets/themes/*.json`` may
override or add themes, which is what lets a deployment ship its own palette
without touching the code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from app.core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Every colour a Bin-Tel surface can use."""

    name: str
    display_name: str
    description: str
    is_dark: bool

    # Surfaces
    window_bg: str
    window_bg_alt: str
    sidebar_bg: str
    sidebar_border: str
    header_bg: str
    header_border: str
    card_bg: str
    card_border: str
    card_hover_border: str
    dialog_bg: str
    dialog_border: str

    # Text
    text_primary: str
    text_secondary: str
    text_muted: str
    text_inverse: str

    # Lines
    border: str
    border_strong: str
    divider: str

    # Inputs
    input_bg: str
    input_fg: str
    input_border: str
    input_focus_border: str
    input_placeholder: str

    # Buttons
    button_bg: str
    button_fg: str
    button_border: str
    button_hover_bg: str
    button_pressed_bg: str

    # Accent
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_fg: str

    # Sidebar navigation
    nav_fg: str
    nav_hover_bg: str
    nav_active_bg: str
    nav_active_fg: str
    nav_active_marker: str

    # Tables
    table_bg: str
    table_header_bg: str
    table_header_fg: str
    table_row_alt_bg: str
    table_grid: str
    table_hover_bg: str
    table_selected_bg: str
    table_selected_fg: str

    # States
    selection_bg: str
    selection_fg: str
    disabled_bg: str
    disabled_fg: str
    disabled_border: str
    focus_ring: str

    # Feedback
    success: str
    warning: str
    danger: str
    info: str

    # Components
    progress_bg: str
    progress_chunk: str
    scrollbar_bg: str
    scrollbar_handle: str
    scrollbar_handle_hover: str
    tooltip_bg: str
    tooltip_fg: str
    chip_bg: str
    chip_fg: str
    shadow: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2)

    @classmethod
    def from_mapping(cls, data: dict[str, Any], base: ThemeTokens | None = None) -> ThemeTokens:
        """Build a theme from a mapping, inheriting anything it omits."""
        merged: dict[str, Any] = base.as_dict() if base else {}
        known = {field.name for field in fields(cls)}
        merged.update({key: value for key, value in data.items() if key in known})
        missing = known - merged.keys()
        if missing:
            raise ValueError(f"Theme is missing values: {', '.join(sorted(missing))}")
        return cls(**merged)


MIDNIGHT = ThemeTokens(
    name="midnight",
    display_name="Midnight",
    description="Deep navy with a signal-teal accent. The Bin-Tel default.",
    is_dark=True,
    window_bg="#0D1420", window_bg_alt="#111A2A",
    sidebar_bg="#0A111C", sidebar_border="#1B2740",
    header_bg="#0F1826", header_border="#1B2740",
    card_bg="#141E30", card_border="#223046", card_hover_border="#2E4160",
    dialog_bg="#111C2C", dialog_border="#2A3A54",
    text_primary="#E8EEF7", text_secondary="#9FB0C8", text_muted="#6C7E97",
    text_inverse="#0B1220",
    border="#223046", border_strong="#2E4160", divider="#1B2740",
    input_bg="#0D1726", input_fg="#E8EEF7", input_border="#2A3A54",
    input_focus_border="#22C8B4", input_placeholder="#5D6E86",
    button_bg="#1A2639", button_fg="#E8EEF7", button_border="#2A3A54",
    button_hover_bg="#223148", button_pressed_bg="#16202F",
    primary="#22C8B4", primary_hover="#35D8C4", primary_pressed="#17A897",
    primary_fg="#04121A",
    nav_fg="#9FB0C8", nav_hover_bg="#131E30", nav_active_bg="#16283A",
    nav_active_fg="#E8F7F4", nav_active_marker="#22C8B4",
    table_bg="#111A2A", table_header_bg="#16223A", table_header_fg="#9FB0C8",
    table_row_alt_bg="#131D2E", table_grid="#1E2C42", table_hover_bg="#182640",
    table_selected_bg="#1C3A49", table_selected_fg="#E8F7F4",
    selection_bg="#1C3A49", selection_fg="#E8F7F4",
    disabled_bg="#131C2B", disabled_fg="#55647B", disabled_border="#1E2A3C",
    focus_ring="#22C8B4",
    success="#3FBF87", warning="#E0A548", danger="#E06C75", info="#5B8DEF",
    progress_bg="#1A2639", progress_chunk="#22C8B4",
    scrollbar_bg="#0D1420", scrollbar_handle="#2A3A54", scrollbar_handle_hover="#384C6C",
    tooltip_bg="#1D2A3F", tooltip_fg="#E8EEF7",
    chip_bg="#1B2C40", chip_fg="#A7C4D8", shadow="rgba(0, 0, 0, 0.45)",
)

PROFESSIONAL_LIGHT = ThemeTokens(
    name="professional_light",
    display_name="Professional Light",
    description="Bright, high-contrast workspace for well-lit rooms and print work.",
    is_dark=False,
    window_bg="#F4F6FA", window_bg_alt="#EDF1F7",
    sidebar_bg="#FFFFFF", sidebar_border="#E2E8F0",
    header_bg="#FFFFFF", header_border="#E2E8F0",
    card_bg="#FFFFFF", card_border="#E4E9F2", card_hover_border="#CBD5E1",
    dialog_bg="#FFFFFF", dialog_border="#D8E0EC",
    text_primary="#16202E", text_secondary="#55647B", text_muted="#8494A8",
    text_inverse="#FFFFFF",
    border="#E1E7F0", border_strong="#CBD5E1", divider="#EAEFF6",
    input_bg="#FFFFFF", input_fg="#16202E", input_border="#D3DCE8",
    input_focus_border="#2B62D9", input_placeholder="#9AA8BC",
    button_bg="#FFFFFF", button_fg="#243349", button_border="#D3DCE8",
    button_hover_bg="#F1F5FB", button_pressed_bg="#E5EBF4",
    primary="#2B62D9", primary_hover="#3B72E8", primary_pressed="#2251BC",
    primary_fg="#FFFFFF",
    nav_fg="#55647B", nav_hover_bg="#F1F5FB", nav_active_bg="#E9F0FE",
    nav_active_fg="#1B44A0", nav_active_marker="#2B62D9",
    table_bg="#FFFFFF", table_header_bg="#F5F8FC", table_header_fg="#55647B",
    table_row_alt_bg="#FAFBFE", table_grid="#E8EDF5", table_hover_bg="#F2F6FD",
    table_selected_bg="#E3ECFD", table_selected_fg="#16202E",
    selection_bg="#D8E5FC", selection_fg="#16202E",
    disabled_bg="#F1F4F9", disabled_fg="#A5B1C2", disabled_border="#E4E9F2",
    focus_ring="#2B62D9",
    success="#17915B", warning="#B87708", danger="#C7392F", info="#2B62D9",
    progress_bg="#E7ECF5", progress_chunk="#2B62D9",
    scrollbar_bg="#F4F6FA", scrollbar_handle="#C7D2E0", scrollbar_handle_hover="#AEBCCE",
    tooltip_bg="#16202E", tooltip_fg="#F4F6FA",
    chip_bg="#EDF2FB", chip_fg="#3A5680", shadow="rgba(21, 33, 54, 0.10)",
)

SLATE = ThemeTokens(
    name="slate",
    display_name="Slate",
    description="Neutral graphite-blue with a warm amber accent for long sessions.",
    is_dark=True,
    window_bg="#1B1F26", window_bg_alt="#21262F",
    sidebar_bg="#15181E", sidebar_border="#2B313B",
    header_bg="#1E232B", header_border="#2B313B",
    card_bg="#232933", card_border="#313945", card_hover_border="#3D4655",
    dialog_bg="#232933", dialog_border="#3D4655",
    text_primary="#E5E8ED", text_secondary="#A2AAB7", text_muted="#737C8A",
    text_inverse="#15181E",
    border="#2E3540", border_strong="#3D4655", divider="#272D37",
    input_bg="#191D24", input_fg="#E5E8ED", input_border="#363E4A",
    input_focus_border="#E0A64A", input_placeholder="#6A7382",
    button_bg="#262C36", button_fg="#E5E8ED", button_border="#363E4A",
    button_hover_bg="#2E3540", button_pressed_bg="#1F242C",
    primary="#E0A64A", primary_hover="#EEB65C", primary_pressed="#C58F38",
    primary_fg="#1B1207",
    nav_fg="#A2AAB7", nav_hover_bg="#20252D", nav_active_bg="#2A3038",
    nav_active_fg="#F3E9D8", nav_active_marker="#E0A64A",
    table_bg="#21262F", table_header_bg="#262C36", table_header_fg="#A2AAB7",
    table_row_alt_bg="#1E232B", table_grid="#2E3540", table_hover_bg="#272E38",
    table_selected_bg="#3A3325", table_selected_fg="#F3E9D8",
    selection_bg="#3A3325", selection_fg="#F3E9D8",
    disabled_bg="#20252D", disabled_fg="#5C6573", disabled_border="#2A303A",
    focus_ring="#E0A64A",
    success="#4FAE7B", warning="#E0A64A", danger="#DB6B60", info="#6E9BD8",
    progress_bg="#2A313B", progress_chunk="#E0A64A",
    scrollbar_bg="#1B1F26", scrollbar_handle="#39414D", scrollbar_handle_hover="#4A5464",
    tooltip_bg="#2B323D", tooltip_fg="#E5E8ED",
    chip_bg="#2C333E", chip_fg="#B4BECD", shadow="rgba(0, 0, 0, 0.40)",
)

OCEAN = ThemeTokens(
    name="ocean",
    display_name="Ocean",
    description="Deep marine blues with a bright cyan accent and cool contrast.",
    is_dark=True,
    window_bg="#08222B", window_bg_alt="#0B2B36",
    sidebar_bg="#061A22", sidebar_border="#103A47",
    header_bg="#0A2530", header_border="#103A47",
    card_bg="#0D3040", card_border="#144556", card_hover_border="#1A5B6E",
    dialog_bg="#0D3040", dialog_border="#1A5B6E",
    text_primary="#E2F1F4", text_secondary="#92B6BF", text_muted="#6B909B",
    text_inverse="#04171D",
    border="#124352", border_strong="#1A5B6E", divider="#0F3A48",
    input_bg="#072732", input_fg="#E2F1F4", input_border="#16505F",
    input_focus_border="#35C6E0", input_placeholder="#5E8791",
    button_bg="#0F3A4A", button_fg="#E2F1F4", button_border="#16505F",
    button_hover_bg="#145062", button_pressed_bg="#0B3040",
    primary="#35C6E0", primary_hover="#4FD5EC", primary_pressed="#26A8C0",
    primary_fg="#04171D",
    nav_fg="#92B6BF", nav_hover_bg="#0A2C39", nav_active_bg="#0F3C4C",
    nav_active_fg="#DFF6FA", nav_active_marker="#35C6E0",
    table_bg="#0B2B36", table_header_bg="#0E3644", table_header_fg="#92B6BF",
    table_row_alt_bg="#0A2934", table_grid="#14404F", table_hover_bg="#103B4A",
    table_selected_bg="#11505E", table_selected_fg="#DFF6FA",
    selection_bg="#11505E", selection_fg="#DFF6FA",
    disabled_bg="#0B2A34", disabled_fg="#4E7480", disabled_border="#103846",
    focus_ring="#35C6E0",
    success="#46BF97", warning="#DFAA55", danger="#E37070", info="#5AA8E8",
    progress_bg="#103846", progress_chunk="#35C6E0",
    scrollbar_bg="#08222B", scrollbar_handle="#16505F", scrollbar_handle_hover="#1E6B7E",
    tooltip_bg="#114352", tooltip_fg="#E2F1F4",
    chip_bg="#10404F", chip_fg="#8FC3D0", shadow="rgba(0, 12, 18, 0.50)",
)

GRAPHITE = ThemeTokens(
    name="graphite",
    display_name="Graphite",
    description="Achromatic charcoal with a steel-blue accent — minimal and quiet.",
    is_dark=True,
    window_bg="#202020", window_bg_alt="#262626",
    sidebar_bg="#1A1A1A", sidebar_border="#333333",
    header_bg="#232323", header_border="#333333",
    card_bg="#292929", card_border="#383838", card_hover_border="#484848",
    dialog_bg="#292929", dialog_border="#484848",
    text_primary="#EDEDED", text_secondary="#A8A8A8", text_muted="#7C7C7C",
    text_inverse="#1A1A1A",
    border="#363636", border_strong="#484848", divider="#2E2E2E",
    input_bg="#1E1E1E", input_fg="#EDEDED", input_border="#3D3D3D",
    input_focus_border="#7FA8D9", input_placeholder="#757575",
    button_bg="#2E2E2E", button_fg="#EDEDED", button_border="#3D3D3D",
    button_hover_bg="#383838", button_pressed_bg="#262626",
    primary="#7FA8D9", primary_hover="#93B9E6", primary_pressed="#6B92C1",
    primary_fg="#0E1620",
    nav_fg="#A8A8A8", nav_hover_bg="#252525", nav_active_bg="#2F2F2F",
    nav_active_fg="#EDF3FA", nav_active_marker="#7FA8D9",
    table_bg="#262626", table_header_bg="#2C2C2C", table_header_fg="#A8A8A8",
    table_row_alt_bg="#232323", table_grid="#363636", table_hover_bg="#2E2E2E",
    table_selected_bg="#33414F", table_selected_fg="#EDF3FA",
    selection_bg="#33414F", selection_fg="#EDF3FA",
    disabled_bg="#262626", disabled_fg="#626262", disabled_border="#333333",
    focus_ring="#7FA8D9",
    success="#6FBF8E", warning="#D9A85C", danger="#D97A72", info="#7FA8D9",
    progress_bg="#333333", progress_chunk="#7FA8D9",
    scrollbar_bg="#202020", scrollbar_handle="#3D3D3D", scrollbar_handle_hover="#4F4F4F",
    tooltip_bg="#333333", tooltip_fg="#EDEDED",
    chip_bg="#313131", chip_fg="#B5B5B5", shadow="rgba(0, 0, 0, 0.45)",
)

#: Ordered as they appear in the theme picker.
BUILTIN_THEMES: tuple[ThemeTokens, ...] = (
    MIDNIGHT,
    PROFESSIONAL_LIGHT,
    SLATE,
    OCEAN,
    GRAPHITE,
)

DEFAULT_THEME = MIDNIGHT.name


def builtin_theme_map() -> dict[str, ThemeTokens]:
    return {theme.name: theme for theme in BUILTIN_THEMES}


def load_theme_overrides(themes_dir: Path) -> dict[str, ThemeTokens]:
    """Load ``assets/themes/*.json``, inheriting from a built-in when named.

    A malformed file is skipped with a warning rather than failing startup.
    """
    themes: dict[str, ThemeTokens] = {}
    if not themes_dir.exists():
        return themes
    builtins = builtin_theme_map()
    for path in sorted(themes_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unreadable theme file %s: %s", path.name, exc)
            continue
        if not isinstance(data, dict):
            continue
        base_name = data.get("extends") or data.get("name")
        base = builtins.get(str(base_name)) or builtins.get(DEFAULT_THEME)
        try:
            theme = ThemeTokens.from_mapping(data, base=base)
        except (TypeError, ValueError) as exc:
            logger.warning("Skipping invalid theme %s: %s", path.name, exc)
            continue
        themes[theme.name] = theme
    return themes


def export_builtin_themes(themes_dir: Path) -> list[Path]:
    """Write the built-in themes out as JSON, for customisation."""
    themes_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for theme in BUILTIN_THEMES:
        path = themes_dir / f"{theme.name}.json"
        path.write_text(theme.to_json() + "\n", encoding="utf-8")
        written.append(path)
    return written

"""Interface smoke tests: every page builds, in every theme, on both plans.

These construct real widgets on the offscreen Qt platform. They are not
pixel tests -- they assert that the shell wires up, that paid surfaces gate on
entitlements rather than plan names, and that no page holds database logic it
should be asking a service for.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.gui

THEMES = ("midnight", "professional_light", "slate", "ocean", "graphite")


def pump(milliseconds: int = 120) -> None:
    """Run the event loop for a real interval, so queued work can land."""
    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def wait_until(predicate, *, timeout_ms: int = 6000) -> bool:
    """Pump until *predicate* holds, or the deadline passes."""
    from PyQt6.QtCore import QDeadlineTimer

    deadline = QDeadlineTimer(timeout_ms)
    while not deadline.hasExpired():
        if predicate():
            return True
        pump(50)
    return predicate()


@pytest.fixture
def themes(config, paths, qapp):
    from app.ui.themes.theme_manager import ThemeManager

    manager = ThemeManager(config, paths.themes_dir)
    manager.apply("midnight", persist=False)
    return manager


@pytest.fixture
def window(context, themes, qapp):
    """A main window that never reaches the network during a test."""
    from app.core.config import UpdateCheckMode
    from app.ui.windows.main_window import MainWindow

    settings = context.config.settings
    settings.database.automatic_updates = False
    settings.database.check_mode = UpdateCheckMode.MANUAL

    win = MainWindow(context, themes)
    win.resize(1280, 800)
    yield win
    win.close()
    win.deleteLater()
    # Let any background lookup finish before the context closes the database.
    from PyQt6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(5000)
    pump(50)


def test_every_navigation_entry_has_a_page(window):
    from app.ui.widgets.sidebar import NAV_ITEMS

    assert {item.key for item in NAV_ITEMS} <= set(window.pages)


def test_every_page_can_be_shown(window, qapp):
    for key in list(window.pages):
        window.navigate(key)
        pump(30)
        assert window.pages[key].isVisible() or window.stack.currentWidget() is window.pages[key]


@pytest.mark.parametrize("theme", THEMES)
def test_every_theme_applies_cleanly(window, themes, theme, qapp):
    themes.apply(theme, persist=False)
    pump(30)
    assert themes.current.name == theme
    assert qapp.styleSheet()


def test_a_bin_lookup_resolves_through_the_page(window, context, qapp):
    from sqlalchemy import text

    with context.manager.session() as session:
        digits = str(session.execute(text("SELECT bin FROM bins LIMIT 1")).scalar())

    page = window.bin_page
    window.navigate("bin_lookup")
    page.perform_search(digits)
    assert wait_until(lambda: page.stack.currentWidget() is page.result_holder)
    assert digits in page.result_card.bin_label.text().replace(" ", "")


def test_the_bin_page_offers_quick_and_advanced_tabs(window):
    page = window.bin_page
    assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == [
        "Quick lookup",
        "Advanced search",
    ]


def test_every_page_is_reachable_and_none_is_gated(window, qapp):
    """A personal tool hides nothing: every navigation entry opens its page."""
    from app.ui.widgets.sidebar import NAV_ITEMS

    for item in NAV_ITEMS:
        button = window.sidebar._buttons[item.key]
        assert button.isEnabled(), f"{item.key} must stay usable"
        assert not button.isHidden(), f"{item.key} must stay visible"
        window.navigate(item.key)
        pump(20)
        assert window.stack.currentWidget() is window.pages[item.key]


def test_the_advanced_search_panel_is_always_available(window):
    window.navigate("bin_lookup")
    page = window.bin_page
    page.refresh()
    assert page.advanced_panel.isEnabled()


def test_an_ampersand_in_a_label_is_not_read_as_an_accelerator(qapp):
    from app.ui.widgets.sidebar import NavButton, NavItem

    button = NavButton(NavItem("demo", "Plan & Licence", "backup"))
    assert "&&" in button.text()


def test_the_command_palette_finds_a_page(window, qapp):
    window.open_palette()
    window.palette.field.setText("analytics")
    assert wait_until(lambda: window.palette.list_widget.count() > 0)
    window.palette.hide()


def test_the_settings_page_exposes_every_tab(window):
    tabs = window.settings_page.tabs
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    assert labels == [
        "General",
        "Database",
        "Updates",
        "Appearance",
        "Search",
        "Watchlists",
        "Reports",
        "Privacy",
        "Advanced",
    ]


def test_no_page_module_imports_the_orm_directly():
    """Pages talk to services; database logic never leaks into a widget."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "ui"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in ("from sqlalchemy", "import sqlalchemy", "from app.models.entities import"):
            if marker in text:
                offenders.append(f"{path.relative_to(root)}: {marker}")
    assert offenders == []


def test_the_filter_bar_reflows_rather_than_widening_the_page(qapp):
    """Filters have grown from five to seven; the page must not scroll sideways."""
    from app.ui.widgets.data_table import FilterBar

    bar = FilterBar()
    try:
        assert bar.minimumSizeHint().width() < 400
        for width, expected in ((1400, 8), (700, 4), (400, 2)):
            bar.resize(width, 40)
            bar._relayout()
            assert bar._columns == expected, (width, bar._columns)
    finally:
        bar.deleteLater()


def test_the_result_card_states_how_a_match_was_reached(window, context, qapp):
    from sqlalchemy import text

    with context.manager.session() as session:
        digits = str(session.execute(text("SELECT bin FROM bins LIMIT 1")).scalar())

    page = window.bin_page
    window.navigate("bin_lookup")
    page.perform_search(digits)
    assert wait_until(lambda: page.stack.currentWidget() is page.result_holder)

    card = page.result_card
    assert not card.match_label.isHidden()
    assert "Match:" in card.match_label.text()
    assert "Confidence:" in card.match_label.text()


# ---------------------------------------------------------------------------
# The binlist.net second opinion, wired into the page
# ---------------------------------------------------------------------------

BINLIST_RESPONSE = {
    "scheme": "visa",
    "type": "debit",
    "brand": "Visa/Dankort",
    "country": {"alpha2": "DK", "name": "Denmark", "currency": "DKK"},
    "bank": {"name": "Jyske Bank", "url": "www.jyskebank.dk", "city": "Hjorring"},
}


def _scripted_binlist(status=200, payload=None):
    """A client that answers from a script. The suite never hits the network."""
    import httpx

    def handler(request):
        return httpx.Response(status, json=payload if payload is not None else {})

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def _fake_provider(status=200, payload=None):
    from app.providers.binlist import BinlistProvider, RequestBudget

    return BinlistProvider(
        budget=RequestBudget(None), client_factory=_scripted_binlist(status, payload)
    )


def test_the_binlist_button_is_hidden_until_the_setting_is_on(window):
    window.navigate("bin_lookup")
    page = window.bin_page
    page.perform_search("410000")
    pump(20)
    page._refresh_second_opinion()
    assert page.second_opinion_row.isHidden(), "off by default means no button"


def test_a_binlist_reading_never_enters_the_database(window, qapp):
    context = window.context
    context.config.settings.external.binlist_enabled = True
    window.navigate("bin_lookup")
    page = window.bin_page
    page.perform_search("410000")
    pump(40)

    before = context.stats.info().stats.bins if context.database.is_open else 0
    context.binlist = _fake_provider(200, BINLIST_RESPONSE)
    page._check_binlist()
    pump(60)

    assert not page.external_panel.isHidden()
    assert "Jyske Bank" in page.external_panel.fields_label.text()
    after = context.stats.info().stats.bins if context.database.is_open else 0
    assert after == before, "an external reading must never enter the database"


def test_a_new_search_drops_the_previous_binlist_reading(window, qapp):
    context = window.context
    context.config.settings.external.binlist_enabled = True
    context.binlist = _fake_provider(200, BINLIST_RESPONSE)
    window.navigate("bin_lookup")
    page = window.bin_page
    page.perform_search("410000")
    pump(40)
    page._check_binlist()
    pump(60)
    assert not page.external_panel.isHidden()

    page.perform_search("520001")
    pump(40)
    assert page.external_panel.isHidden(), (
        "a reading belongs to the BIN it was fetched for"
    )


def test_binlist_being_unavailable_never_breaks_the_page(window, qapp):
    """Bin-Tel's own answer must not depend on somebody else's service."""
    context = window.context
    context.config.settings.external.binlist_enabled = True
    context.binlist = _fake_provider(503, {})
    window.navigate("bin_lookup")
    page = window.bin_page
    page.perform_search("410000")
    pump(40)
    local_answer = page.result_card.issuer_label.text()

    page._check_binlist()
    pump(60)

    assert page.result_card.issuer_label.text() == local_answer
    assert not page.external_panel.banner.isHidden()

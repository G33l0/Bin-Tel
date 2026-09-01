"""Saved searches, favourites, history, templates and notifications.

These all live in the durable user-data store, which must survive a database
replacement — that is the point of keeping them out of the intelligence file.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.models.schemas import AdvancedQuery
from app.models.user_entities import FavoriteKind, SearchKind
from app.services.workspace_service import WorkspaceService


@pytest.fixture
def workspace(user_store):
    return WorkspaceService(user_store)


# -- saved searches -----------------------------------------------------------


def test_a_search_can_be_saved_and_listed(workspace):
    saved = workspace.save_search(
        "US credit cards",
        kind=SearchKind.ADVANCED,
        criteria=AdvancedQuery(country_code="US", card_type="credit"),
    )
    assert saved.name == "US credit cards"
    assert [item.id for item in workspace.saved_searches()] == [saved.id]


def test_saving_the_same_name_updates_rather_than_duplicates(workspace):
    workspace.save_search("Mine", criteria=AdvancedQuery(country_code="US"))
    workspace.save_search("Mine", criteria=AdvancedQuery(country_code="GB"))
    assert workspace.saved_search_count() == 1


def test_a_saved_search_needs_a_name(workspace):
    with pytest.raises(ValidationError):
        workspace.save_search("   ")


def test_saved_searches_are_not_capped(workspace):
    for index in range(25):
        workspace.save_search(f"Search {index}")
    assert workspace.saved_search_count() == 25


def test_pinned_searches_sort_first(workspace):
    workspace.save_search("Zebra")
    workspace.save_search("Alpha")
    pinned = workspace.save_search("Middle")
    workspace.set_pinned(pinned.id, True)
    assert workspace.saved_searches()[0].name == "Middle"


def test_deleting_a_saved_search(workspace):
    saved = workspace.save_search("Temporary")
    workspace.delete_saved_search(saved.id)
    assert workspace.saved_searches() == []


def test_saved_criteria_round_trip(workspace):
    criteria = AdvancedQuery(country_code="US", network_code="visa", prepaid=True)
    saved = workspace.save_search("Round trip", criteria=criteria)
    restored = next(
        item for item in workspace.saved_searches() if item.id == saved.id
    )
    assert restored.criteria is not None
    assert restored.criteria.country_code == "US"
    assert restored.criteria.prepaid is True


# -- history ------------------------------------------------------------------


def test_history_records_terms_newest_first(workspace):
    workspace.record_search("414720", SearchKind.BIN, result_count=1)
    workspace.record_search("511122", SearchKind.BIN, result_count=1)
    assert workspace.recent_terms(SearchKind.BIN)[0] == "511122"


def test_history_can_be_turned_off(workspace):
    workspace.record_search("414720", SearchKind.BIN, enabled=False)
    assert workspace.history() == []


def test_history_is_trimmed_to_the_configured_size(workspace):
    for index in range(30):
        workspace.record_search(f"41472{index}", SearchKind.BIN, keep=10)
    assert len(workspace.history(SearchKind.BIN, limit=100)) <= 10


def test_history_can_be_cleared(workspace):
    workspace.record_search("414720", SearchKind.BIN)
    assert workspace.clear_history() > 0
    assert workspace.history() == []


# -- favourites ---------------------------------------------------------------


def test_a_favourite_can_be_added_and_removed(workspace):
    workspace.add_favorite(FavoriteKind.BIN, "414720", label="A BIN")
    assert workspace.is_favorite(FavoriteKind.BIN, "414720")

    assert workspace.remove_favorite(FavoriteKind.BIN, "414720")
    assert not workspace.is_favorite(FavoriteKind.BIN, "414720")


def test_toggling_a_favourite_flips_it(workspace):
    assert workspace.toggle_favorite(FavoriteKind.BIN, "414720") is True
    assert workspace.toggle_favorite(FavoriteKind.BIN, "414720") is False


def test_favourites_are_listed_by_kind(workspace):
    workspace.add_favorite(FavoriteKind.BIN, "414720")
    workspace.add_favorite(FavoriteKind.INSTITUTION, "inst-1")
    assert len(workspace.favorites(FavoriteKind.BIN)) == 1
    assert len(workspace.favorites()) == 2


# -- templates and reports ----------------------------------------------------


def test_a_report_template_can_be_saved_and_reused(workspace):
    template = workspace.save_template(
        "Monthly US report",
        report_type="search_results",
        output_format="pdf",
        criteria=AdvancedQuery(country_code="US").model_dump_json(),
    )
    assert workspace.template_count() == 1

    workspace.record_template_use(template.id)
    workspace.delete_template(template.id)
    assert workspace.templates() == []


def test_generated_reports_are_remembered(workspace, tmp_path):
    path = tmp_path / "report.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    workspace.record_report(
        title="A report", report_type="search_results", output_format="csv", path=path
    )
    recent = workspace.recent_reports()
    assert recent and recent[0].title == "A report"


# -- notifications ------------------------------------------------------------


def test_notifications_can_be_raised_and_read(workspace):
    workspace.notify("Database updated", "Version 2026.02.1 installed")
    assert workspace.unread_notifications() == 1

    assert workspace.mark_notifications_read() == 1
    assert workspace.unread_notifications() == 0


def test_notifications_can_be_cleared(workspace):
    workspace.notify("One", "")
    workspace.notify("Two", "")
    assert workspace.clear_notifications() == 2
    assert workspace.notifications() == []


# -- durability ---------------------------------------------------------------


def test_user_data_survives_a_database_replacement(tmp_path, user_store):
    """The whole point of the second store: an update must not erase this."""
    workspace = WorkspaceService(user_store)
    workspace.add_favorite(FavoriteKind.BIN, "414720", label="Kept")
    workspace.save_search("Kept search")
    path = user_store.path
    user_store.close()

    from app.database.user_store import UserDataStore

    reopened = UserDataStore(path)
    reopened.open()
    try:
        restored = WorkspaceService(reopened)
        assert restored.is_favorite(FavoriteKind.BIN, "414720")
        assert [item.name for item in restored.saved_searches()] == ["Kept search"]
    finally:
        reopened.close()

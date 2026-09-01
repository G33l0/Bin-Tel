"""Rebuilding the database from the BIN list, and getting back if it was wrong.

All data here is synthetic.
"""

from __future__ import annotations

import pytest

from app.core.errors import DatabaseCorruptError, ImportError_
from app.database.engine import DatabaseManager
from app.database.integrity import verify_database
from app.repositories.bin_repository import BinRepository
from app.services.lookup_service import LookupService
from app.services.rebuild_service import RebuildService, ShrinkRefused

HEADER = "bin,bank,country,network,card_type"


@pytest.fixture
def workspace(tmp_path):
    """A database path, a list path and a service wired to both."""
    database = tmp_path / "bintel.sqlite"
    listing = tmp_path / "bin-list.csv"
    manager = DatabaseManager(database)
    service = RebuildService(manager, database, staging_dir=tmp_path / "staging")
    yield manager, service, listing, database
    manager.close()


def write(listing, *rows: str) -> None:
    listing.write_text(f"{HEADER}\n" + "".join(f"{row}\n" for row in rows), encoding="utf-8")


def many(count: int) -> list[str]:
    return [f"41{index:04d},Institution {index},US,visa,credit" for index in range(count)]


def lookup_for(manager) -> LookupService:
    return LookupService(BinRepository(manager))


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def test_a_rebuild_produces_a_database_you_can_look_up_in(workspace):
    manager, service, listing, _ = workspace
    write(
        listing,
        "410000,Cascade Federal Bank,US,visa,credit",
        "530001,Meridian Trust Bank,GB,mastercard,credit",
    )
    outcome = service.rebuild(listing)

    assert outcome.accepted == 2
    assert outcome.institutions == 2
    result = lookup_for(manager).lookup("410000")
    assert result.found
    assert result.records[0].institutions[0].display_name == "Cascade Federal Bank"


def test_the_first_rebuild_needs_no_existing_database(workspace):
    _, service, listing, database = workspace
    assert not database.exists()
    write(listing, "410000,Cascade Federal Bank,US,visa,credit")
    service.rebuild(listing)
    assert database.exists()


def test_an_eight_digit_bin_resolves_ahead_of_the_six_it_sits_under(workspace):
    """Specificity decides, not the order the rows were written."""
    manager, service, listing, _ = workspace
    write(
        listing,
        "410000,Cascade Federal Bank,US,visa,credit",
        "41000012,Northshore Credit Union,US,visa,debit",
    )
    service.rebuild(listing)
    lookup = lookup_for(manager)

    six = lookup.lookup("410000")
    eight = lookup.lookup("41000012")
    assert six.records[0].institutions[0].display_name == "Cascade Federal Bank"
    assert eight.records[0].institutions[0].display_name == "Northshore Credit Union"


def test_a_bin_the_list_does_not_name_is_unknown_not_guessed(workspace):
    """A neighbouring BIN is never borrowed to fill a gap."""
    manager, service, listing, _ = workspace
    write(listing, "414720,Cascade Federal Bank,US,visa,credit")
    service.rebuild(listing)
    assert not lookup_for(manager).lookup("414721").found


def test_the_rebuilt_database_passes_verification(workspace):
    _, service, listing, database = workspace
    write(listing, *many(30))
    service.rebuild(listing)
    assert verify_database(database, quick=False).ok


def test_the_version_is_stamped_and_reported(workspace):
    _, service, listing, _ = workspace
    write(listing, "410000,Cascade Federal Bank,US,visa,credit")
    outcome = service.rebuild(listing, version="2026.01.1")
    assert outcome.version == "2026.01.1"
    assert outcome.previous_version is None


def test_a_second_rebuild_reports_the_version_it_replaced(workspace):
    _, service, listing, _ = workspace
    write(listing, *many(10))
    service.rebuild(listing, version="2026.01.1")
    write(listing, *many(12))
    outcome = service.rebuild(listing, version="2026.01.2")
    assert outcome.previous_version == "2026.01.1"
    assert outcome.accepted == 12


def test_rows_the_list_no_longer_carries_are_gone_after_a_rebuild(workspace):
    """A rebuild replaces the database — it does not merge into it."""
    manager, service, listing, _ = workspace
    write(listing, *many(10), "420000,Removed Bank,GB,visa,credit")
    service.rebuild(listing)
    assert lookup_for(manager).lookup("420000").found

    write(listing, *many(10))
    service.rebuild(listing)
    assert not lookup_for(manager).lookup("420000").found


def test_skipped_rows_are_reported_rather_than_hidden(workspace):
    _, service, listing, _ = workspace
    write(listing, "410000,Cascade Bank,US,visa,credit", "nonsense,Nobody,US,,")
    outcome = service.rebuild(listing)
    assert outcome.rejected == 1
    assert outcome.problems


# ---------------------------------------------------------------------------
# Failing safely
# ---------------------------------------------------------------------------


def test_a_missing_list_leaves_the_database_untouched(workspace):
    manager, service, listing, _ = workspace
    write(listing, *many(10))
    service.rebuild(listing)

    listing.unlink()
    with pytest.raises(ImportError_):
        service.rebuild(listing)
    assert lookup_for(manager).lookup("410000").found


def test_an_unknown_column_leaves_the_database_untouched(workspace):
    manager, service, listing, _ = workspace
    write(listing, *many(10))
    service.rebuild(listing)

    listing.write_text("bin,bank,surprise\n410000,X,y\n", encoding="utf-8")
    with pytest.raises(ImportError_):
        service.rebuild(listing)
    assert lookup_for(manager).lookup("410009").found


def test_a_list_that_would_lose_most_of_the_database_is_refused(workspace):
    """A truncated paste and a deliberate cull look identical from here."""
    manager, service, listing, _ = workspace
    write(listing, *many(20))
    service.rebuild(listing)

    write(listing, *many(3))
    with pytest.raises(ShrinkRefused):
        service.rebuild(listing)
    assert lookup_for(manager).lookup("410019").found


def test_a_deliberate_shrink_goes_through_when_it_is_asked_for(workspace):
    manager, service, listing, _ = workspace
    write(listing, *many(20))
    service.rebuild(listing)

    write(listing, *many(3))
    outcome = service.rebuild(listing, allow_shrink=True)
    assert outcome.accepted == 3
    assert not lookup_for(manager).lookup("410019").found


def test_the_database_stays_open_and_usable_after_a_refused_rebuild(workspace):
    manager, service, listing, _ = workspace
    write(listing, *many(10))
    service.rebuild(listing)

    listing.write_text("", encoding="utf-8")
    with pytest.raises(ImportError_):
        service.rebuild(listing)
    assert manager.is_open
    assert lookup_for(manager).lookup("410000").found


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def test_there_is_nothing_to_roll_back_to_after_the_first_build(workspace):
    _, service, listing, _ = workspace
    write(listing, *many(5))
    outcome = service.rebuild(listing)
    assert not outcome.can_roll_back
    assert not service.can_roll_back


def test_a_rebuild_can_be_undone(workspace):
    manager, service, listing, _ = workspace
    write(listing, *many(10))
    service.rebuild(listing, version="2026.01.1")

    write(listing, *many(10), "420000,New Bank,GB,visa,debit")
    service.rebuild(listing, version="2026.01.2")
    assert lookup_for(manager).lookup("420000").found

    service.rollback()
    assert not lookup_for(manager).lookup("420000").found
    assert lookup_for(manager).lookup("410000").found


def test_rolling_back_twice_rolls_forward_again(workspace):
    """Neither copy is discarded, so the operation is its own undo."""
    manager, service, listing, _ = workspace
    write(listing, *many(10))
    service.rebuild(listing)
    write(listing, *many(10), "420000,New Bank,GB,visa,debit")
    service.rebuild(listing)

    service.rollback()
    assert not lookup_for(manager).lookup("420000").found
    service.rollback()
    assert lookup_for(manager).lookup("420000").found


def test_rolling_back_with_nothing_to_roll_back_to_is_an_error(workspace):
    _, service, listing, _ = workspace
    write(listing, *many(5))
    service.rebuild(listing)
    with pytest.raises(Exception) as excinfo:
        service.rollback()
    assert "roll back" in str(excinfo.value).lower()


def test_a_corrupt_previous_database_is_not_restored(workspace):
    manager, service, listing, _ = workspace
    write(listing, *many(10))
    service.rebuild(listing)
    write(listing, *many(11))
    service.rebuild(listing)

    service.previous_path.write_bytes(b"this is not a database")
    with pytest.raises(DatabaseCorruptError):
        service.rollback()
    assert manager.is_open
    assert lookup_for(manager).lookup("410010").found

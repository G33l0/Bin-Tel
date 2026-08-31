"""Settings: defaults, self-healing, atomic writes and derived paths."""

from __future__ import annotations

import json

import pytest

from app.core.config import ConfigManager, Settings, UpdateFrequency


def test_defaults_load_when_no_file_exists(config):
    assert config.is_loaded
    assert config.settings.appearance.theme
    assert config.settings.database.manifest_url


def test_settings_round_trip_through_disk(config):
    config.settings.appearance.theme = "ocean"
    config.settings.search.results_per_page = 75
    config.save()

    reloaded = ConfigManager(config.paths)
    reloaded.load()
    assert reloaded.settings.appearance.theme == "ocean"
    assert reloaded.settings.search.results_per_page == 75


def test_one_bad_value_resets_only_itself(config):
    config.settings.appearance.theme = "graphite"
    config.settings.search.results_per_page = 75
    config.save()

    payload = json.loads(config.paths.settings_file.read_text(encoding="utf-8"))
    payload["search"]["results_per_page"] = "not a number"
    config.paths.settings_file.write_text(json.dumps(payload), encoding="utf-8")

    healed = ConfigManager(config.paths)
    healed.load()
    assert healed.settings.appearance.theme == "graphite", "unrelated values survive"
    assert healed.settings.search.results_per_page == Settings().search.results_per_page


def test_an_unreadable_file_falls_back_to_defaults(config):
    config.paths.settings_file.write_text("{ this is not json", encoding="utf-8")
    manager = ConfigManager(config.paths)
    manager.load()
    assert manager.settings.appearance.theme == Settings().appearance.theme


def test_an_unknown_key_is_ignored_rather_than_fatal(config):
    config.save()
    payload = json.loads(config.paths.settings_file.read_text(encoding="utf-8"))
    payload["a_setting_from_the_future"] = {"nested": True}
    config.paths.settings_file.write_text(json.dumps(payload), encoding="utf-8")

    manager = ConfigManager(config.paths)
    manager.load()
    assert manager.is_loaded


def test_saving_is_atomic(config):
    config.save()
    assert config.paths.settings_file.exists()
    # No temporary files are left behind.
    leftovers = [
        item
        for item in config.paths.settings_file.parent.iterdir()
        if item.name.startswith(config.paths.settings_file.name) and item != config.paths.settings_file
    ]
    assert leftovers == []


def test_derived_paths_follow_the_configured_directories(config, tmp_path):
    database_dir = tmp_path / "elsewhere"
    config.settings.database.database_directory = str(database_dir)
    assert config.database_path().parent == database_dir

    backups = tmp_path / "snapshots"
    config.settings.database.backup_directory = str(backups)
    assert config.backups_path() == backups


def test_default_paths_are_used_when_nothing_is_configured(config, paths):
    config.settings.database.database_directory = ""
    assert config.database_path() == paths.database_file


@pytest.mark.parametrize(
    ("frequency", "days"),
    [
        (UpdateFrequency.DAILY, 1),
        (UpdateFrequency.WEEKLY, 7),
        (UpdateFrequency.MONTHLY, 30),
    ],
)
def test_update_frequencies_map_to_intervals(frequency, days):
    assert frequency.days == days


def test_a_manifest_url_override_is_not_discarded_by_the_context(config, paths):
    from app.core.context import AppContext

    config.settings.database.manifest_url = "https://example.invalid/manifest.json"
    context = AppContext(config=config, paths=paths)
    try:
        assert (
            context.config.settings.database.manifest_url
            == "https://example.invalid/manifest.json"
        )
    finally:
        context.shutdown()

"""The update pipeline: manifests, versions, checksums, install and rollback."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import BinTelError, ChecksumMismatchError
from app.providers.manifest import DatabaseManifest, compare_versions

# -- manifest parsing ---------------------------------------------------------


def _manifest_payload(package, version="2026.02.1") -> dict:
    from app.utils.hashing import file_checksum

    return {
        "version": version,
        "schema_version": 1,
        "database_size": package.stat().st_size,
        "record_count": 240,
        "institution_count": 40,
        "sha256": file_checksum(package),
        "download_url": package.name,
        "compression": "none",
        "publisher": "Bin-Tel Project",
    }


def test_a_manifest_parses_and_folds_the_sha256_alias(sample_package):
    package, _ = sample_package
    manifest = DatabaseManifest.model_validate(_manifest_payload(package))

    assert manifest.version == "2026.02.1"
    assert manifest.checksum.startswith("sha256:")
    assert manifest.record_count == 240


def test_the_published_manifest_round_trips(sample_package):
    _, manifest_path = sample_package
    manifest = DatabaseManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    assert manifest.version
    assert manifest.checksum
    assert manifest.download_url


@pytest.mark.parametrize("version", ["", "   "])
def test_a_manifest_without_a_version_is_rejected(sample_package, version):
    package, _ = sample_package
    payload = _manifest_payload(package, version=version)
    with pytest.raises(PydanticValidationError):
        DatabaseManifest.model_validate(payload)


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "ftp://example.invalid/db.sqlite", "data:text/plain,x"]
)
def test_a_download_url_outside_the_allowed_schemes_is_rejected(sample_package, url):
    package, _ = sample_package
    payload = _manifest_payload(package)
    payload["download_url"] = url
    with pytest.raises(PydanticValidationError):
        DatabaseManifest.model_validate(payload)


def test_a_relative_download_url_is_allowed(sample_package):
    package, _ = sample_package
    payload = _manifest_payload(package)
    payload["download_url"] = "packages/bintel-2026.02.1.sqlite"
    assert DatabaseManifest.model_validate(payload).download_url.endswith(".sqlite")


# -- version comparison -------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("2026.01.1", "2026.01.2", -1),
        ("2026.01.2", "2026.01.1", 1),
        ("2026.01.1", "2026.01.1", 0),
        ("2026.2.1", "2026.10.1", -1),
        ("2026.01.10", "2026.01.9", 1),
        ("1.0.0", "1.0", 0),  # a missing component reads as zero
    ],
)
def test_versions_compare_component_wise(left, right, expected):
    assert compare_versions(left, right) == expected


def test_is_newer_than_treats_a_missing_install_as_older(sample_package):
    package, _ = sample_package
    manifest = DatabaseManifest.model_validate(_manifest_payload(package))
    assert manifest.is_newer_than(None)
    assert manifest.is_newer_than("2026.01.1")
    assert not manifest.is_newer_than("2026.02.1")
    assert not manifest.is_newer_than("2027.01.1")


# -- install ------------------------------------------------------------------


@pytest.fixture
def update_context(tmp_path, sample_package, paths, config):
    """A context whose provider serves the generated package from disk."""
    from app.core.context import AppContext

    package, manifest_path = sample_package
    served = tmp_path / "served"
    served.mkdir()
    shutil.copy2(package, served / package.name)
    shutil.copy2(manifest_path, served / manifest_path.name)

    config.settings.database.manifest_url = (served / manifest_path.name).as_uri()
    context = AppContext(config=config, paths=paths)
    yield context, served / manifest_path.name
    context.shutdown()


def test_a_full_install_lands_a_verified_database(update_context):
    context, _ = update_context
    manifest, _provider = context.providers.fetch_manifest()

    outcome = context.updates.install(manifest)

    assert outcome.success
    assert outcome.version == manifest.version
    assert outcome.report is not None and outcome.report.ok
    assert context.database_path.exists()

    context.open_database()
    assert context.database_version() == manifest.version


def test_installing_records_the_run_in_the_durable_journal(update_context):
    context, _ = update_context
    manifest, _ = context.providers.fetch_manifest()
    context.updates.install(manifest)

    entries = context.updates.journal.entries()
    assert entries
    assert entries[0].to_version == manifest.version
    assert entries[0].status == "success"


def test_a_checksum_mismatch_is_refused_and_nothing_is_installed(update_context):
    context, manifest_path = update_context
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sha256"] = "0" * 64
    payload.pop("checksum", None)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest, _ = context.providers.fetch_manifest()
    with pytest.raises(ChecksumMismatchError):
        context.updates.install(manifest)

    assert not context.database_installed


def test_a_missing_download_fails_without_touching_the_database(update_context):
    context, manifest_path = update_context
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["download_url"] = "bintel-does-not-exist.sqlite"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest, _ = context.providers.fetch_manifest()
    with pytest.raises(BinTelError):
        context.updates.install(manifest)

    assert not context.database_installed


def test_a_corrupt_package_is_rejected_and_the_old_database_survives(
    update_context, tmp_path
):
    context, manifest_path = update_context

    # Install the good package first.
    manifest, _ = context.providers.fetch_manifest()
    context.updates.install(manifest)
    context.open_database()
    good_version = context.database_version()
    assert good_version

    # Now publish a package whose bytes are not a database, with a matching
    # checksum so the failure has to be caught by verification, not hashing.
    from app.utils.hashing import file_checksum

    served = manifest_path.parent
    broken = served / "bintel-broken.sqlite"
    broken.write_bytes(b"absolutely not a database" * 400)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["version"] = "2099.01.1"
    payload["download_url"] = broken.name
    payload["sha256"] = file_checksum(broken)
    payload.pop("checksum", None)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    bad_manifest, _ = context.providers.fetch_manifest()
    with pytest.raises(BinTelError):
        context.updates.install(bad_manifest)

    context.open_database()
    assert context.database_version() == good_version


def test_check_reports_an_available_update(update_context):
    context, _ = update_context
    check = context.updates.check(current_version=None)
    assert check.update_available
    assert check.manifest is not None


def test_check_reports_up_to_date_when_the_versions_match(update_context):
    context, _ = update_context
    manifest, _ = context.providers.fetch_manifest()
    check = context.updates.check(current_version=manifest.version)
    assert not check.update_available


# ---------------------------------------------------------------------------
# file:// URLs, on every platform
# ---------------------------------------------------------------------------


def test_a_windows_file_url_becomes_a_usable_windows_path():
    """`file:///C:/x` parses to `/C:/x` — a slash in front of the drive letter.

    Treated as a path that looks for \\C:\\x on the current drive, which is
    nowhere, and it made every URL-configured update source unreachable on
    Windows. The stdlib converter is exercised directly so the behaviour is
    pinned from any platform rather than only where the bug appears.
    """
    import nturl2path
    from urllib.parse import urlparse

    parsed = urlparse("file:///C:/data/served/database-manifest.json")
    assert nturl2path.url2pathname(parsed.path) == (
        r"C:\data\served\database-manifest.json"
    )


def test_a_posix_file_url_is_unchanged(tmp_path):
    from app.providers.local_provider import path_from_url

    target = tmp_path / "served" / "database-manifest.json"
    assert path_from_url(f"file://{target}") == target
    assert path_from_url(str(target)) == target


def test_a_file_url_with_an_escaped_space_is_decoded(tmp_path):
    from app.providers.local_provider import path_from_url

    assert path_from_url("file:///tmp/with%20space/m.json") == Path(
        "/tmp/with space/m.json"
    )


def test_a_host_in_a_file_url_is_kept_as_a_share():
    """`file://server/share/m.json` is a UNC path, not a path missing a root.

    A mirrored package on a shared drive is a case this provider exists for, so
    the host is reassembled rather than quietly dropped.
    """
    from app.providers.local_provider import path_from_url

    assert str(path_from_url("file://server/share/m.json")).replace("\\", "/") == (
        "//server/share/m.json"
    )


def test_localhost_names_this_machine_rather_than_a_share():
    from app.providers.local_provider import path_from_url

    assert str(path_from_url("file://localhost/tmp/m.json")).replace("\\", "/") == (
        "/tmp/m.json"
    )

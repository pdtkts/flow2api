import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from src.services.google_drive_backup import (
    GoogleDriveBackupError,
    _build_archive,
    _validate_and_extract_archive,
)


def _make_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE admin_config (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE tokens (id INTEGER PRIMARY KEY)")
        connection.commit()


def test_archive_preserves_profile_data_and_excludes_runtime_metrics(tmp_path):
    database = tmp_path / "flow.db"
    _make_database(database)
    profiles = tmp_path / "browser_profiles"
    profile = profiles / "token-1"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_bytes(b"private-cookie-data")
    (profile / "Default" / "Cache").write_bytes(b"normal-cache")
    (profile / "BrowserMetrics").mkdir()
    (profile / "BrowserMetrics" / "BrowserMetrics-test.pma").write_bytes(b"metrics")
    (profile / "SingletonLock").write_bytes(b"lock")
    (profile / "download.part").write_bytes(b"partial")

    work = tmp_path / "work"
    work.mkdir()
    archive = work / "backup.tar.gz"
    manifest = _build_archive(
        database,
        profiles,
        work,
        archive,
        backup_id="backup-1",
        backup_type="manual",
        app_version="test",
    )

    names = {entry["path"] for entry in manifest["files"]}
    assert "database/flow.db" in names
    assert "browser_profiles/token-1/Default/Cookies" in names
    assert "browser_profiles/token-1/Default/Cache" in names
    assert all("BrowserMetrics" not in name for name in names)
    assert all("Singleton" not in name for name in names)
    assert all(not name.endswith(".part") for name in names)

    extracted = tmp_path / "extracted"
    restored_manifest = _validate_and_extract_archive(archive, extracted)
    assert restored_manifest["backup_id"] == "backup-1"
    assert (extracted / "browser_profiles/token-1/Default/Cookies").read_bytes() == b"private-cookie-data"


def test_restore_rejects_archive_traversal(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    payload = b"escape"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("../outside")
        member.size = len(payload)
        handle.addfile(member, io.BytesIO(payload))
    with pytest.raises(GoogleDriveBackupError, match="unsafe path"):
        _validate_and_extract_archive(archive, tmp_path / "extract")


def test_restore_rejects_checksum_mismatch(tmp_path):
    database = tmp_path / "flow.db"
    _make_database(database)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    archive = work / "backup.tar.gz"
    _build_archive(
        database,
        profiles,
        work,
        archive,
        backup_id="backup-2",
        backup_type="automatic",
        app_version="test",
    )

    unpacked = tmp_path / "unpacked"
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(unpacked)
    manifest_path = unpacked / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    altered = tmp_path / "altered.tar.gz"
    with tarfile.open(altered, "w:gz") as handle:
        handle.add(manifest_path, arcname="manifest.json")
        handle.add(unpacked / "database/flow.db", arcname="database/flow.db")

    with pytest.raises(GoogleDriveBackupError, match="checksum"):
        _validate_and_extract_archive(altered, tmp_path / "restore")


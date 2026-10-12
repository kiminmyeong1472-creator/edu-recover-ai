"""Runtime backup coverage, safe snapshots, and unchanged owner configuration."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.training.backup_adapters import BackupStorageError
from app.training.backup_inventory import (
    RUNTIME_DATABASES, checked_source_path,
    discover_runtime_assets, merge_runtime_assets,
)
from app.training.backup_service import RegisteredAsset, _snapshot, run_configured_backup_once


class BackupInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)

    def write(self, relative, content=b"example"):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_inventory_includes_runtime_data_but_does_not_scan_secrets_or_backups(self):
        expected = set(RUNTIME_DATABASES)
        for relative in expected:
            self.write(relative)
        name = "original.docx"
        metadata = hashlib.sha256(name.encode()).hexdigest() + ".json"
        files = {"private_documents/documents/" + name,
                 "private_documents/documents/screenshot.jpg",
                 "private_documents/metadata/" + metadata,
                 "private_documents/metadata/" + hashlib.sha256(b"screenshot.jpg").hexdigest() + ".json",
                 "classroom_media/" + "a" * 32 + ".mp4"}
        for relative in files:
            self.write(relative)
        for relative in ("oauth_private/drive_oauth.edurbk", "unapproved/accounts.sqlite3",
                         ".env", "backups/nested.docx", "backup_state/latest.json",
                         "private_documents/documents/.import.abc.tmp",
                         "classroom_media/" + "b" * 32 + ".tmp"):
            self.write(relative, b"MUST_NOT_BE_READ")
        inventory = discover_runtime_assets(self.source)
        self.assertEqual({item["source"] for item in inventory.assets}, expected | files)
        self.assertFalse(inventory.issues)
        self.assertTrue(all(item["contains_personal_data"] for item in inventory.assets))
        self.assertTrue(all(item["sqlite_snapshot"] for item in inventory.assets
                            if item["source"] in expected))
        self.assertEqual(inventory.summary()["account_database"],
                         "included_in_encrypted_backup_scope")
        self.assertTrue(inventory.summary()["account_database_discovered"])
        self.assertFalse(inventory.summary()["full_service_restore_verified"])

    def test_traversal_symlink_parent_and_symlink_file_are_rejected(self):
        self.write("plain/file.docx")
        (self.source / "linked").symlink_to(self.source / "plain", target_is_directory=True)
        (self.source / "alias.docx").symlink_to(self.source / "plain/file.docx")
        for relative in ("../outside", "linked/file.docx", "alias.docx", "/absolute", "a\\b"):
            with self.subTest(relative=relative), self.assertRaises(BackupStorageError):
                checked_source_path(self.source, relative)
        (self.source / "private_documents").symlink_to(self.source / "plain", target_is_directory=True)
        result = discover_runtime_assets(self.source)
        self.assertFalse(result.summary()["inventory_complete_for_scope"])
        self.assertFalse(any(item["source"].startswith("private_documents") for item in result.assets))

    def test_file_byte_and_scan_bounds_are_explicit_coverage_failures(self):
        for index in range(3):
            self.write(f"classroom_media/{index:032x}.png", b"1234")
        cases = (
            ({"max_files": 1}, "INVENTORY_FILE_LIMIT_REACHED"),
            ({"max_entries": 1}, "INVENTORY_SCAN_LIMIT_REACHED"),
            ({"max_file_bytes": 3}, "INVENTORY_FILE_TOO_LARGE"),
            ({"max_total_bytes": 5}, "INVENTORY_TOTAL_BYTES_LIMIT_REACHED"),
        )
        for kwargs, code in cases:
            with self.subTest(code=code):
                result = discover_runtime_assets(self.source, **kwargs)
                self.assertIn(code, {issue["code"] for issue in result.issues})
                self.assertFalse(result.summary()["inventory_complete_for_scope"])

    def test_existing_owner_entries_win_without_mutation_or_duplicate_source(self):
        self.write("liferecover/life.sqlite3")
        registered = [{"asset_id": "owner-life", "source": "./liferecover/life.sqlite3",
                       "kind": "OTHER", "contains_credentials": True}]
        before = json.dumps(registered, sort_keys=True)
        result = merge_runtime_assets(registered, discover_runtime_assets(self.source))
        self.assertEqual(result, registered)
        self.assertEqual(json.dumps(registered, sort_keys=True), before)

    def test_unrecognized_files_are_not_silently_counted_as_covered(self):
        self.write("classroom_media/renamed-file.mp4")
        self.write("private_documents/metadata/credentials.json")
        result = discover_runtime_assets(self.source)
        self.assertEqual(result.assets, [])
        self.assertFalse(result.summary()["inventory_complete_for_scope"])
        self.assertNotIn("renamed-file", json.dumps(result.summary()))

    def test_missing_original_or_metadata_is_reported_as_recovery_gap(self):
        self.write("private_documents/documents/original.docx")
        self.write("private_documents/metadata/" + "c" * 64 + ".json")
        result = discover_runtime_assets(self.source)
        self.assertEqual({issue["code"] for issue in result.issues}, {
            "PRIVATE_DOCUMENT_METADATA_MISSING", "PRIVATE_DOCUMENT_ORIGINAL_MISSING"})
        self.assertFalse(result.summary()["inventory_complete_for_scope"])

    def test_sqlite_snapshot_includes_committed_wal_and_excludes_uncommitted_rows(self):
        database = self.source / "source.sqlite3"
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE records (value INTEGER)")
        connection.execute("INSERT INTO records VALUES (42)")
        connection.commit()
        connection.execute("INSERT INTO records VALUES (99)")
        self.assertTrue(Path(str(database) + "-wal").exists())
        destination = self.root / "snapshot.sqlite3"
        # SQLite filenames remain safe even if an old owner manifest omitted the flag.
        asset = RegisteredAsset(asset_id="database", source="source.sqlite3", kind="OTHER")
        _snapshot(asset, self.source, destination)
        with sqlite3.connect(destination) as restored:
            self.assertEqual(restored.execute("SELECT value FROM records").fetchall(), [(42,)])
            self.assertEqual(restored.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(connection.execute("SELECT value FROM records").fetchall(), [(42,), (99,)])
        self.assertEqual(destination.stat().st_mode & 0o077, 0)

    def test_explicit_credential_paths_are_rejected_before_opening(self):
        for source in ("oauth_private/drive_oauth.edurbk", "unapproved/accounts.sqlite3",
                       "liferecover/accounts.sqlite3-wal", "liferecover/accounts.sqlite3-shm",
                       ".env", "service_account.json", "private.key", "token.json"):
            asset = RegisteredAsset(asset_id="sensitive", source=source, kind="UPLOAD")
            with self.subTest(source=source), self.assertRaises(BackupStorageError) as failure:
                _snapshot(asset, self.source, self.root / "must-not-exist")
            self.assertEqual(failure.exception.safe_code, "CREDENTIAL_SOURCE_REQUIRES_METADATA_ONLY")
            self.assertFalse((self.root / "must-not-exist").exists())

    def test_flat_file_snapshot_preserves_exact_bytes(self):
        original = self.write("private_documents/documents/original.docx", b"exact\x00bytes")
        destination = self.root / "snapshot"
        digest = _snapshot(RegisteredAsset(asset_id="doc", source=str(original.relative_to(self.source)),
                                           kind="UPLOAD"), self.source, destination)
        self.assertEqual(destination.read_bytes(), original.read_bytes())
        self.assertEqual(digest, hashlib.sha256(original.read_bytes()).hexdigest())

    def test_sqlite_sidecar_symlink_is_never_followed(self):
        self.write("db.sqlite3")
        elsewhere = self.root / "outside-wal"
        elsewhere.write_bytes(b"private")
        (self.source / "db.sqlite3-wal").symlink_to(elsewhere)
        with self.assertRaises(BackupStorageError):
            _snapshot(RegisteredAsset(asset_id="db", source="db.sqlite3", kind="OTHER"),
                      self.source, self.root / "snapshot")

    def test_parent_swap_after_path_validation_cannot_copy_outside_file(self):
        self.write("materials/lesson.docx", b"approved-source")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "lesson.docx").write_bytes(b"MUST_NOT_BACK_UP_OUTSIDE_ROOT")
        def swapped_after_validation(root, relative):
            result = checked_source_path(root, relative)
            (self.source / "materials").rename(self.source / "original-materials")
            (self.source / "materials").symlink_to(outside, target_is_directory=True)
            return result
        destination = self.root / "must-not-exist"
        with patch("app.training.backup_service.checked_source_path", side_effect=swapped_after_validation):
            with self.assertRaises(BackupStorageError):
                _snapshot(RegisteredAsset(asset_id="doc", source="materials/lesson.docx", kind="UPLOAD"),
                          self.source, destination)
        self.assertFalse(destination.exists())

    def test_swap_after_secure_open_rejects_receipt_and_never_reads_new_parent(self):
        from app.training.backup_service import _assert_source_still_bound
        self.write("materials/lesson.docx", b"approved-source")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "lesson.docx").write_bytes(b"MUST_NOT_BACK_UP_OUTSIDE_ROOT")
        called = False
        def swap_after_binding_check(opened):
            nonlocal called
            _assert_source_still_bound(opened)
            if not called:
                called = True
                (self.source / "materials").rename(self.source / "original-materials")
                (self.source / "materials").symlink_to(outside, target_is_directory=True)
        destination = self.root / "unpublished-snapshot"
        with patch("app.training.backup_service._assert_source_still_bound", side_effect=swap_after_binding_check):
            with self.assertRaises(BackupStorageError):
                _snapshot(RegisteredAsset(asset_id="doc", source="materials/lesson.docx", kind="UPLOAD"),
                          self.source, destination)
        self.assertEqual(destination.read_bytes(), b"approved-source")

    def configure(self):
        state = self.root / "state"
        server = self.root / "server"
        state.mkdir(mode=0o700)
        server.mkdir(mode=0o700)
        self.write("owner.txt")
        manifest = self.root / "manifest.json"
        manifest.write_text('[{"asset_id":"owner","source":"owner.txt","kind":"OTHER"}]')
        environment = {
            "EDURECOVER_BACKUP_MANIFEST": str(manifest),
            "EDURECOVER_BACKUP_SOURCE_ROOT": str(self.source),
            "EDURECOVER_BACKUP_SERVER_ROOT": str(server),
            "EDURECOVER_BACKUP_STATE_DIR": str(state),
            "EDURECOVER_BACKUP_ENABLED": "true",
        }
        return manifest, environment

    def test_configured_cycles_refresh_new_files_without_changing_manifest(self):
        manifest, environment = self.configure()
        original_manifest = manifest.read_bytes()
        self.write("private_documents/documents/original.docx")
        self.write("private_documents/metadata/" + hashlib.sha256(b"original.docx").hexdigest() + ".json")
        seen = []
        def fake_backup(**kwargs):
            seen.append([item.source for item in kwargs["manifest"]])
            return {"status": "verified", "verified_assets": len(seen[-1]),
                    "failed_assets": 0, "skipped_assets": 0}
        with patch.dict(os.environ, environment, clear=True), \
             patch("app.training.backup_service.get_backup_service_status",
                   return_value={"configuration_ready": True, "blockers": []}), \
             patch("app.training.backup_service._run_assets", side_effect=fake_backup):
            first = run_configured_backup_once()
            new_source = "classroom_media/" + "f" * 32 + ".mp3"
            self.write(new_source)
            second = run_configured_backup_once()
        self.assertNotIn(new_source, seen[0])
        self.assertIn(new_source, seen[1])
        self.assertEqual(manifest.read_bytes(), original_manifest)
        self.assertEqual(first["status"], "verified")
        self.assertTrue(second["receipt_persisted"])
        self.assertFalse(second["coverage"]["full_service_restore_verified"])

    def test_incomplete_inventory_cannot_produce_successful_overall_receipt(self):
        _, environment = self.configure()
        self.write("classroom_media/unknown.mp4")
        with patch.dict(os.environ, environment, clear=True), \
             patch("app.training.backup_service.get_backup_service_status",
                   return_value={"configuration_ready": True, "blockers": []}), \
             patch("app.training.backup_service._run_assets",
                   return_value={"status": "verified", "verified_assets": 1}):
            result = run_configured_backup_once()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_code"], "RUNTIME_INVENTORY_INCOMPLETE")
        self.assertFalse(result["coverage"]["inventory_complete_for_scope"])

    def test_disabled_configuration_never_discovers_or_runs_backup(self):
        _, environment = self.configure()
        environment["EDURECOVER_BACKUP_ENABLED"] = "false"
        with patch.dict(os.environ, environment, clear=True), \
             patch("app.training.backup_service.discover_runtime_assets") as discover, \
             patch("app.training.backup_service._run_assets") as backup:
            result = run_configured_backup_once()
            self.assertEqual(os.environ["EDURECOVER_BACKUP_ENABLED"], "false")
        self.assertEqual(result["status"], "blocked")
        discover.assert_not_called()
        backup.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Full migration coverage and fail-closed private staging restore."""
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import closing, contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import aws_migration_snapshot as migration


class AWSMigrationSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        self.archive = self.root / "state.edurmig"
        self.staging = self.root / "staging"
        self.manifest_output = self.root / "staging.edurmig-manifest.json"
        self.environment = patch.dict(os.environ, {
            migration.PASSPHRASE_ENV: "owner exact password 한글 with spaces",
            "LIFERECOVER_DATA_KEY": "NOT_AN_EXPORTABLE_ENV_SECRET",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write(self, name, data=b"private source\0bytes"):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def database(self, name, value="private original", *, wal=False):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(target)
        if wal:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE records(value TEXT)")
        if value is not None:
            db.execute("INSERT INTO records VALUES (?)", (value,))
        db.commit()
        return db

    def accounts(self, value=None):
        self.database(migration.ACCOUNTS_PATH, value).close()

    def export(self, **kwargs):
        return migration.export_snapshot(self.source, self.archive, writes_quiesced=True, **kwargs)

    def malicious_archive(self, entries, *, manifest_overrides=None):
        """Authenticated malformed archives exercise validation after AES auth."""
        items = []
        for name, payload, kind in entries:
            if name.startswith("state/") and kind == tarfile.REGTYPE:
                items.append({"path": name[6:], "size": len(payload),
                              "sha256": hashlib.sha256(payload).hexdigest(), "sqlite": False})
        manifest = {
            "format": migration.FORMAT, "created_at": "2026-10-12T00:00:00Z",
            "logical_id": migration.LOGICAL_ID,
            "operator_confirmed_writes_quiesced": True, "environment_secrets_included": False,
            "accounts_state": "operator_confirmed_not_initialized", "directories": [], "files": items,
        }
        manifest.update(manifest_overrides or {})
        plain = self.root / "test-input.tar"
        with tarfile.open(plain, "w") as tar:
            content = json.dumps(manifest).encode()
            first = tarfile.TarInfo("manifest.json")
            first.size = len(content)
            tar.addfile(first, io.BytesIO(content))
            for name, payload, kind in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "../../outside"
                member.size = len(payload) if kind == tarfile.REGTYPE else 0
                tar.addfile(member, io.BytesIO(payload) if kind == tarfile.REGTYPE else None)
        migration._encrypt(plain, self.archive)
        plain.unlink()

    def assert_failed_restore_clean(self):
        with self.assertRaises((migration.MigrationError, sqlite3.Error, ValueError, tarfile.TarError)):
            migration.restore_snapshot(self.archive, self.staging)
        self.assertFalse(self.staging.exists())
        self.assertFalse(self.manifest_output.exists())
        self.assertEqual(list(self.root.glob("edurmig-restore-*")), [])

    def test_complete_state_includes_accounts_vault_oauth_and_preserves_identifiers(self):
        self.accounts("DREAMKEEPER-001-COMMANDER-DREAMI")
        databases = ["liferecover/life.sqlite3", "liferecover/medical.sqlite3", "liferecover/legal.sqlite3",
                     "classroom_tutor.sqlite3", "ai_tutor_training.sqlite3", "secret_vault.sqlite3"]
        for name in databases:
            self.database(name).close()
        originals = {
            "private_documents/documents/수학 원본.docx": b"private teaching original",
            "private_documents/metadata/file.json": b'{"logical_id":"DREAMKEEPER-001"}',
            "classroom_media/lesson.mp4": b"private media",
            "backups/history.edurbk": b"existing encrypted backup",
            "backup_state/latest.json": b'{"done":true}',
            "backup.json": b'{"logical_id":"DREAMKEEPER-001"}',
            "oauth_private/drive_oauth.edurbk": b"existing encrypted OAuth credential",
        }
        for name, data in originals.items():
            self.write(name, data)
        (self.source / "unused_empty").mkdir()
        before = {name: (self.source / name).read_bytes() for name in originals}
        exported = self.export()
        restored = migration.restore_snapshot(self.archive, self.staging)
        self.assertEqual(exported["sqlite_database_count"], 7)
        self.assertEqual(restored["accounts_state"], "included")
        self.assertEqual(restored["logical_id"], "DREAMKEEPER-001")
        self.assertFalse(restored["full_service_restore_verified"])
        self.assertFalse(restored["environment_secrets_included"])
        self.assertFalse(restored["cross_database_atomicity_automatically_verified"])
        self.assertTrue(restored["application_and_key_restore_verification_required"])
        self.assertEqual(self.staging.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.manifest_output.stat().st_mode & 0o777, 0o600)
        verification = json.loads(self.manifest_output.read_text())
        self.assertEqual(verification["logical_id"], "DREAMKEEPER-001")
        self.assertEqual(len(verification["files"]), exported["file_count"])
        self.assertFalse(verification["restore_receipt"]["full_service_restore_verified"])
        self.assertFalse((self.staging / ".edurmig-manifest.json").exists())
        for name, data in originals.items():
            self.assertEqual((self.staging / name).read_bytes(), data)
            self.assertEqual((self.source / name).read_bytes(), before[name])
            self.assertEqual((self.staging / name).stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.staging / "unused_empty").is_dir())
        with closing(sqlite3.connect(self.staging / migration.ACCOUNTS_PATH)) as db:
            self.assertEqual(db.execute("SELECT value FROM records").fetchone()[0], "DREAMKEEPER-001-COMMANDER-DREAMI")
        encrypted = self.archive.read_bytes()
        self.assertNotIn(b"private teaching original", encrypted)
        self.assertNotIn(b"NOT_AN_EXPORTABLE_ENV_SECRET", encrypted)
        self.assertNotIn(b"DREAMKEEPER-001", encrypted)

    def test_sqlite_backup_preserves_committed_wal_without_uncommitted_rows(self):
        self.accounts()
        db = self.database("liferecover/life.sqlite3", "committed", wal=True)
        self.addCleanup(db.close)
        db.execute("INSERT INTO records VALUES ('not committed')")
        self.assertTrue((self.source / "liferecover/life.sqlite3-wal").exists())
        self.export()
        migration.restore_snapshot(self.archive, self.staging)
        with closing(sqlite3.connect(self.staging / "liferecover/life.sqlite3")) as restored:
            self.assertEqual(restored.execute("SELECT value FROM records").fetchall(), [("committed",)])
            self.assertEqual(restored.execute("PRAGMA quick_check").fetchall(), [("ok",)])
        self.assertFalse((self.staging / "liferecover/life.sqlite3-wal").exists())
        self.assertEqual(db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)

    def test_write_quiescence_and_missing_accounts_are_explicit_requirements(self):
        self.write("classroom_media/example.txt")
        with self.assertRaisesRegex(migration.MigrationError, "every service writer"):
            migration.export_snapshot(self.source, self.archive)
        with self.assertRaisesRegex(migration.MigrationError, "Account database is missing"):
            self.export()
        self.assertFalse(self.archive.exists())
        result = self.export(accounts_not_initialized=True)
        self.assertEqual(result["accounts_state"], "operator_confirmed_not_initialized")
        restored = migration.restore_snapshot(self.archive, self.staging)
        self.assertFalse(restored["full_service_restore_verified"])
        self.assertFalse((self.staging / migration.ACCOUNTS_PATH).exists())

    def test_empty_account_database_is_valid_without_inventing_user_accounts(self):
        self.write(migration.ACCOUNTS_PATH, b"")
        self.export()
        restored = migration.restore_snapshot(self.archive, self.staging)
        self.assertEqual(restored["accounts_state"], "included")
        with closing(sqlite3.connect(self.staging / migration.ACCOUNTS_PATH)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_wrong_passphrase_and_ciphertext_tampering_leave_no_staging_or_plaintext(self):
        self.accounts()
        self.export()
        with patch.dict(os.environ, {migration.PASSPHRASE_ENV: "wrong exact password"}):
            self.assert_failed_restore_clean()
        data = bytearray(self.archive.read_bytes())
        data[migration.HEADER.size + 2] ^= 1
        self.archive.write_bytes(data)
        self.assert_failed_restore_clean()

    def test_password_is_exact_and_scrypt_parameters_are_fixed(self):
        key = migration._derive_key(b"a" * 16)
        with patch.dict(os.environ, {migration.PASSPHRASE_ENV: "owner exact password 한글 with spaces "}):
            self.assertNotEqual(key, migration._derive_key(b"a" * 16))
        with patch.dict(os.environ, {migration.PASSPHRASE_ENV: ""}):
            with self.assertRaises(migration.MigrationError):
                migration._derive_key(b"a" * 16)

    def test_traversal_absolute_windows_and_noncanonical_paths_are_rejected(self):
        for name in ("state/../escape", "state//absolute", "state/C:\\escape", "state/a/../escape", "state/./escape"):
            with self.subTest(name=name):
                self.malicious_archive([(name, b"outside bytes", tarfile.REGTYPE)])
                self.assert_failed_restore_clean()
                self.assertFalse((self.root / "escape").exists())
                self.archive.unlink()

    def test_authenticated_archive_links_and_special_members_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                self.malicious_archive([("state/link", b"", kind)])
                self.assert_failed_restore_clean()
                self.archive.unlink()

    def test_hash_mismatch_duplicate_and_missing_members_are_rejected(self):
        valid = {"path": "example.txt", "size": 5, "sha256": hashlib.sha256(b"hello").hexdigest(), "sqlite": False}
        self.malicious_archive([("state/example.txt", b"hello", tarfile.REGTYPE)],
                               manifest_overrides={"files": [{**valid, "sha256": "0" * 64}]})
        self.assert_failed_restore_clean()
        self.archive.unlink()
        self.malicious_archive([("state/example.txt", b"hello", tarfile.REGTYPE),
                               ("state/example.txt", b"hello", tarfile.REGTYPE)],
                               manifest_overrides={"files": [valid]})
        self.assert_failed_restore_clean()
        self.archive.unlink()
        missing_account = {"path": migration.ACCOUNTS_PATH, "size": 5, "sha256": "0" * 64, "sqlite": True}
        self.malicious_archive([], manifest_overrides={"files": [missing_account], "accounts_state": "included"})
        self.assert_failed_restore_clean()

    def test_source_symlinks_hardlinks_and_fifo_are_rejected(self):
        self.accounts()
        original = self.write("private_documents/original.txt")
        link = self.source / "link"
        link.symlink_to(original)
        with self.assertRaises(migration.MigrationError):
            self.export()
        link.unlink()
        link.symlink_to(original.parent, target_is_directory=True)
        with self.assertRaises(migration.MigrationError):
            self.export()
        link.unlink()
        os.link(original, link)
        with self.assertRaises(migration.MigrationError):
            self.export()
        link.unlink()
        os.mkfifo(link)
        with self.assertRaises(migration.MigrationError):
            self.export()
        self.assertFalse(self.archive.exists())

    def test_environment_key_files_require_separate_secret_transfer(self):
        self.accounts()
        self.write(".env", b"AWS_SECRET_ACCESS_KEY=private")
        with self.assertRaisesRegex(migration.MigrationError, "separate secret transfer"):
            self.export()
        self.assertFalse(self.archive.exists())

    def test_changed_state_rejects_export_and_leaves_no_archive(self):
        self.accounts()
        record = self.write("private_documents/original.txt", b"before")
        original_snapshot = migration._snapshot_sqlite
        def change_after_database(*args):
            original_snapshot(*args)
            record.write_bytes(b"changed despite stopped-writers declaration")
        with patch.object(migration, "_snapshot_sqlite", side_effect=change_after_database):
            with self.assertRaisesRegex(migration.MigrationError, "State changed"):
                self.export()
        self.assertFalse(self.archive.exists())

    def test_live_restore_and_archive_overwrite_are_refused(self):
        self.accounts()
        self.export()
        archive_before = self.archive.read_bytes()
        with self.assertRaises(migration.MigrationError):
            self.export()
        self.assertEqual(self.archive.read_bytes(), archive_before)
        self.staging.mkdir()
        marker = self.staging / "live.txt"
        marker.write_bytes(b"live state must remain")
        with self.assertRaises(migration.MigrationError):
            migration.restore_snapshot(self.archive, self.staging)
        self.assertEqual(marker.read_bytes(), b"live state must remain")

    def test_restore_refuses_an_archive_for_a_different_logical_id(self):
        self.accounts()
        self.export()
        with self.assertRaisesRegex(migration.MigrationError, "logical ID does not match"):
            migration.restore_snapshot(self.archive, self.staging, logical_id="DIFFERENT-PROJECT")
        self.assertFalse(self.staging.exists())
        self.assertFalse(self.manifest_output.exists())

    def test_existing_sidecar_file_or_symlink_refuses_restore_before_staging_creation(self):
        self.accounts()
        self.export()
        self.manifest_output.write_bytes(b"existing private verification record")
        with self.assertRaises(migration.MigrationError):
            migration.restore_snapshot(self.archive, self.staging)
        self.assertEqual(self.manifest_output.read_bytes(), b"existing private verification record")
        self.assertFalse(self.staging.exists())
        self.manifest_output.unlink()
        self.manifest_output.symlink_to(self.root / "missing-outside-file")
        with self.assertRaises(migration.MigrationError):
            migration.restore_snapshot(self.archive, self.staging)
        self.assertTrue(self.manifest_output.is_symlink())
        self.assertFalse(self.staging.exists())

    def test_sidecar_publication_failure_removes_new_staging_and_leaves_no_report(self):
        self.accounts()
        self.export()
        with patch.object(migration.os, "link", side_effect=OSError("storage failure")):
            with self.assertRaises(OSError):
                migration.restore_snapshot(self.archive, self.staging)
        self.assertFalse(self.staging.exists())
        self.assertFalse(self.manifest_output.exists())
        self.assertEqual(list(self.root.glob("edurmig-restore-*")), [])

    def test_failure_after_sidecar_publication_cleans_both_outputs(self):
        self.accounts()
        self.export()
        original_temporary = tempfile.TemporaryDirectory
        @contextmanager
        def failing_cleanup(*args, **kwargs):
            with original_temporary(*args, **kwargs) as path:
                yield path
            raise OSError("private staging cleanup failed")
        with patch.object(migration.tempfile, "TemporaryDirectory", side_effect=failing_cleanup):
            with self.assertRaises(OSError):
                migration.restore_snapshot(self.archive, self.staging)
        self.assertFalse(self.staging.exists())
        self.assertFalse(self.manifest_output.exists())
        self.assertEqual(list(self.root.glob("edurmig-restore-*")), [])

    def test_restored_state_can_be_exported_again_without_manifest_inside_it(self):
        self.accounts()
        self.write("private_documents/original.txt", b"exact original bytes")
        self.export()
        migration.restore_snapshot(self.archive, self.staging)
        second = self.root / "second.edurmig"
        result = migration.export_snapshot(self.staging, second, writes_quiesced=True)
        self.assertEqual(result["file_count"], 2)

    def test_archive_inside_source_and_destination_symlink_are_refused(self):
        self.accounts()
        with self.assertRaises(migration.MigrationError):
            migration.export_snapshot(self.source, self.source / "state.edurmig", writes_quiesced=True)
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(migration.MigrationError):
            migration.export_snapshot(alias, self.archive, writes_quiesced=True)

    def test_cli_receipt_never_claims_live_restore_or_prints_password(self):
        self.accounts()
        output = io.StringIO()
        with redirect_stdout(output):
            code = migration.main(["export", "--state-root", str(self.source),
                                   "--archive", str(self.archive), "--writes-quiesced"])
        self.assertEqual(code, 0)
        receipt = json.loads(output.getvalue())
        self.assertTrue(receipt["ok"])
        self.assertFalse(receipt["full_service_restore_verified"])
        self.assertNotIn(os.environ[migration.PASSPHRASE_ENV], output.getvalue())
        self.assertNotIn(os.environ["LIFERECOVER_DATA_KEY"], output.getvalue())


if __name__ == "__main__":
    unittest.main()

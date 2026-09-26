import unittest
from datetime import datetime, timezone

from app.training.trade_secret_backup_runner import (
    StorageWriteReceipt,
    execute_backup_cycle,
)
from app.training.trade_secret_engine import (
    AssetKind,
    BackupTarget,
    SecretAsset,
    classify_secret_asset,
)


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def write(
        self,
        *,
        asset,
        decision,
        plan,
        target,
        source_ref,
    ):
        self.calls.append(
            {
                "asset_id": asset.asset_id,
                "target": target,
                "source_ref": source_ref,
            }
        )
        return StorageWriteReceipt(
            asset_id=asset.asset_id,
            target=target,
            content_hash=asset.content_hash,
            storage_ref=f"fake://{target.value}/{asset.asset_id}",
            raw_content_written=source_ref is not None,
        )


def asset(**overrides):
    data = dict(
        asset_id="asset-1",
        kind=AssetKind.UPLOAD,
        content_hash="b" * 64,
        provenance_complete=True,
        integrity_verified=True,
        owner_marked_trade_secret=True,
    )
    data.update(overrides)
    return SecretAsset(**data)


class BackupRunnerTests(unittest.TestCase):
    def test_trade_secret_writes_server_and_drive(self):
        item = asset()
        decision = classify_secret_asset(item)
        server = FakeAdapter()
        drive = FakeAdapter()
        adapters = {
            BackupTarget.SERVER_PRIVATE: server,
            BackupTarget.DRIVE_TRADE_SECRET: drive,
        }

        receipt = execute_backup_cycle(
            asset=item,
            decision=decision,
            adapters=adapters,
            source_ref="private://asset-1",
            last_completed_at=None,
            now=datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(receipt.due)
        self.assertTrue(receipt.complete)
        self.assertEqual(len(receipt.writes), 2)
        self.assertEqual(server.calls[0]["source_ref"], "private://asset-1")
        self.assertEqual(drive.calls[0]["source_ref"], "private://asset-1")

    def test_credential_asset_passes_metadata_only_to_adapters(self):
        item = asset(
            owner_marked_trade_secret=False,
            contains_credentials=True,
        )
        decision = classify_secret_asset(item)
        server = FakeAdapter()
        drive = FakeAdapter()
        adapters = {
            BackupTarget.SERVER_QUARANTINE: server,
            BackupTarget.DRIVE_QUARANTINE: drive,
        }

        receipt = execute_backup_cycle(
            asset=item,
            decision=decision,
            adapters=adapters,
            source_ref="private://credential-bearing-content",
            last_completed_at=None,
            now=datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(receipt.complete)
        self.assertIsNone(server.calls[0]["source_ref"])
        self.assertIsNone(drive.calls[0]["source_ref"])
        self.assertFalse(receipt.writes[0].raw_content_written)
        self.assertFalse(receipt.writes[1].raw_content_written)

    def test_missing_required_adapter_fails_closed(self):
        item = asset()
        decision = classify_secret_asset(item)

        with self.assertRaises(ValueError):
            execute_backup_cycle(
                asset=item,
                decision=decision,
                adapters={},
                source_ref="private://asset-1",
                last_completed_at=None,
                now=datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()

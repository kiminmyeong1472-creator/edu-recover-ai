import unittest
from datetime import datetime, timedelta, timezone

from app.training.trade_secret_engine import (
    AssetKind,
    BackupPolicy,
    BackupTarget,
    SecretAsset,
    SecretClass,
    assert_external_delivery_allowed,
    build_backup_plan,
    classify_secret_asset,
)


def asset(**overrides):
    data = dict(
        asset_id="asset-1",
        kind=AssetKind.UPLOAD,
        content_hash="a" * 64,
        provenance_complete=True,
        integrity_verified=True,
    )
    data.update(overrides)
    return SecretAsset(**data)


class TradeSecretClassificationTests(unittest.TestCase):
    def test_owner_marked_secret_routes_to_trade_secret_storage(self):
        decision = classify_secret_asset(
            asset(owner_marked_trade_secret=True)
        )
        self.assertEqual(decision.classification, SecretClass.TRADE_SECRET)
        self.assertIn(BackupTarget.SERVER_PRIVATE, decision.backup_targets)
        self.assertIn(
            BackupTarget.DRIVE_TRADE_SECRET,
            decision.backup_targets,
        )
        self.assertFalse(decision.external_provider_delivery_allowed)

    def test_unfiled_patent_is_trade_secret_by_default(self):
        decision = classify_secret_asset(
            asset(
                kind=AssetKind.PATENT_DRAFT,
                unfiled_patent_material=True,
            )
        )
        self.assertEqual(decision.classification, SecretClass.TRADE_SECRET)

    def test_chat_secret_context_uses_txt_backup(self):
        decision = classify_secret_asset(
            asset(
                kind=AssetKind.CHAT_TRANSCRIPT,
                chat_contains_secret_context=True,
            )
        )
        self.assertEqual(decision.classification, SecretClass.TRADE_SECRET)
        self.assertEqual(decision.backup_extension, "txt")

    def test_credentials_are_quarantined_without_raw_backup(self):
        decision = classify_secret_asset(
            asset(contains_credentials=True)
        )
        self.assertEqual(decision.classification, SecretClass.QUARANTINED)
        self.assertFalse(decision.raw_content_backup_allowed)
        self.assertFalse(decision.external_provider_delivery_allowed)

    def test_missing_provenance_is_quarantined(self):
        decision = classify_secret_asset(
            asset(provenance_complete=False)
        )
        self.assertEqual(decision.classification, SecretClass.QUARANTINED)

    def test_personal_raw_data_is_restricted(self):
        decision = classify_secret_asset(
            asset(
                contains_personal_data=True,
                deidentified=False,
            )
        )
        self.assertEqual(
            decision.classification,
            SecretClass.PERSONAL_RESTRICTED,
        )

    def test_no_public_mark_defaults_to_internal_confidential(self):
        decision = classify_secret_asset(asset())
        self.assertEqual(
            decision.classification,
            SecretClass.INTERNAL_CONFIDENTIAL,
        )

    def test_public_release_requires_explicit_flag(self):
        decision = classify_secret_asset(
            asset(explicit_public_release=True)
        )
        self.assertEqual(
            decision.classification,
            SecretClass.PUBLIC_RELEASED,
        )
        self.assertTrue(decision.external_provider_delivery_allowed)

    def test_external_delivery_fails_closed_for_trade_secret(self):
        decision = classify_secret_asset(
            asset(owner_marked_trade_secret=True)
        )
        with self.assertRaises(ValueError):
            assert_external_delivery_allowed(decision)


class BackupPlannerTests(unittest.TestCase):
    def test_first_backup_is_due_immediately(self):
        now = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
        item = asset(owner_marked_trade_secret=True)
        decision = classify_secret_asset(item)

        plan = build_backup_plan(
            asset=item,
            decision=decision,
            last_completed_at=None,
            now=now,
        )

        self.assertTrue(plan.due)
        self.assertEqual(plan.next_due_at, now)

    def test_backup_becomes_due_after_thirty_minutes(self):
        start = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
        item = asset(owner_marked_trade_secret=True)
        decision = classify_secret_asset(item)

        before = build_backup_plan(
            asset=item,
            decision=decision,
            last_completed_at=start,
            now=start + timedelta(minutes=29),
            policy=BackupPolicy(interval_minutes=30),
        )
        at_due = build_backup_plan(
            asset=item,
            decision=decision,
            last_completed_at=start,
            now=start + timedelta(minutes=30),
            policy=BackupPolicy(interval_minutes=30),
        )

        self.assertFalse(before.due)
        self.assertTrue(at_due.due)

    def test_trade_secret_plan_contains_server_and_drive_targets(self):
        now = datetime(2026, 9, 27, 0, 30, tzinfo=timezone.utc)
        item = asset(owner_marked_trade_secret=True)
        decision = classify_secret_asset(item)

        plan = build_backup_plan(
            asset=item,
            decision=decision,
            last_completed_at=now - timedelta(minutes=30),
            now=now,
        )

        self.assertIn(BackupTarget.SERVER_PRIVATE, plan.targets)
        self.assertIn(BackupTarget.DRIVE_TRADE_SECRET, plan.targets)


if __name__ == "__main__":
    unittest.main()


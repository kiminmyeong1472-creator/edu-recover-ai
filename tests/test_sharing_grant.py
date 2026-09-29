import unittest

from app.platform_core.sharing import SharingGrant, revoke


class SharingGrantTests(unittest.TestCase):
    def test_share_requires_explicit_resources(self):
        with self.assertRaises(ValueError):
            SharingGrant.issue(
                subject_id="p1",
                source_domain="medical",
                target_party="clinic",
                purpose="visit",
                resource_refs=[],
                expires_at="2026-10-01T00:00:00+00:00",
            )

    def test_revocation_preserves_scope(self):
        grant = SharingGrant.issue(
            subject_id="p1",
            source_domain="legal",
            target_party="lawyer",
            purpose="review",
            resource_refs=["case_1"],
            expires_at="2026-10-01T00:00:00+00:00",
        )
        revoked = revoke(grant)
        self.assertTrue(revoked.revoked)
        self.assertEqual(revoked.resource_refs, grant.resource_refs)


if __name__ == "__main__":
    unittest.main()

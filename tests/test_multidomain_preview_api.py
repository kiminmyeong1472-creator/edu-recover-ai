import unittest

from fastapi.testclient import TestClient

from app.multidomain_preview import preview_app


class MultidomainPreviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(preview_app)

    def headers(self, subject: str, domain: str) -> dict[str, str]:
        return {"X-Subject-Id": subject, "X-Domain-Scope": domain}

    def test_preview_is_explicitly_not_production_wired(self):
        response = self.client.get("/preview/health")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["production_wired"])
        self.assertIn("consent", response.json()["core_controls"])

    def test_core_consent_and_share_are_audited(self):
        self.client.delete("/preview/core/_test/reset")

        consent = self.client.post(
            "/preview/core/consents",
            headers=self.headers("p1", "medical"),
            json={
                "subject_id": "p1",
                "domain": "medical",
                "purpose": "medication review",
                "policy_version": "v1",
            },
        )
        self.assertEqual(consent.status_code, 200, consent.text)

        share = self.client.post(
            "/preview/core/shares",
            headers=self.headers("p1", "medical"),
            json={
                "subject_id": "p1",
                "source_domain": "medical",
                "target_party": "clinic",
                "purpose": "visit",
                "resource_refs": ["med_1"],
                "expires_at": "2026-10-01T00:00:00+09:00",
            },
        )
        self.assertEqual(share.status_code, 200, share.text)

        audits = self.client.get(
            "/preview/core/audits/p1",
            headers={"X-Subject-Id": "p1"},
        )
        self.assertEqual(audits.status_code, 200, audits.text)
        actions = [event["action"] for event in audits.json()["events"]]
        self.assertIn("consent_issued", actions)
        self.assertIn("sharing_grant_issued", actions)

    def test_medical_rejects_wrong_domain_header(self):
        response = self.client.post(
            "/preview/medical/medications",
            headers=self.headers("p1", "education"),
            json={"subject_id": "p1", "product_name": "sample"},
        )
        self.assertEqual(response.status_code, 403)

    def test_medical_passport_flags_unknown_treatment_purpose(self):
        self.client.delete("/preview/medical/_test/reset")
        created = self.client.post(
            "/preview/medical/medications",
            headers=self.headers("p1", "medical"),
            json={
                "subject_id": "p1",
                "product_name": "sample",
                "approved_indications": ["label indication"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        medication_id = created.json()["medication_id"]

        passport = self.client.get(
            "/preview/medical/passport/p1",
            headers=self.headers("p1", "medical"),
        )
        self.assertEqual(passport.status_code, 200, passport.text)
        self.assertIn(medication_id, passport.json()["confirmation_needed"])

    def test_medical_treatment_purpose_can_be_confirmed_with_source(self):
        self.client.delete("/preview/medical/_test/reset")
        created = self.client.post(
            "/preview/medical/medications",
            headers=self.headers("p1", "medical"),
            json={"subject_id": "p1", "product_name": "sample"},
        )
        medication_id = created.json()["medication_id"]

        confirmed = self.client.post(
            "/preview/medical/treatment-purpose/confirm",
            headers=self.headers("p1", "medical"),
            json={
                "subject_id": "p1",
                "medication_id": medication_id,
                "purpose": "confirmed treatment purpose",
                "source": "CLINICAL_DOCUMENT",
                "source_ref": "record_1",
            },
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["verification_state"], "DOCUMENT")

        passport = self.client.get(
            "/preview/medical/passport/p1",
            headers=self.headers("p1", "medical"),
        )
        self.assertNotIn(medication_id, passport.json()["confirmation_needed"])

    def test_legal_deadline_starts_unverified_then_can_be_verified(self):
        self.client.delete("/preview/legal/_test/reset")
        response = self.client.post(
            "/preview/legal/deadlines",
            headers=self.headers("p1", "legal"),
            json={
                "subject_id": "p1",
                "case_id": "c1",
                "title": "example",
                "due_at": "2026-10-31T00:00:00+09:00",
                "source_ref": "notice_1",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "UNVERIFIED")
        deadline_id = response.json()["deadline_id"]

        verified = self.client.post(
            "/preview/legal/deadlines/verify",
            headers=self.headers("p1", "legal"),
            json={
                "subject_id": "p1",
                "deadline_id": deadline_id,
                "verification_note": "official notice checked",
            },
        )
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertEqual(verified.json()["state"], "VERIFIED")

    def test_life_rejects_subject_mismatch(self):
        response = self.client.post(
            "/preview/life/activities",
            headers=self.headers("p2", "life"),
            json={
                "subject_id": "p1",
                "domain": "medical",
                "action": "visit",
                "resource_ref": "r1",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_public_support_lists_only_actionable_official_programs(self):
        self.client.delete("/preview/life/_test/reset")

        open_program = self.client.post(
            "/preview/life/support-programs",
            headers=self.headers("p1", "life"),
            json={
                "program_id": "prog_1",
                "domain": "MEDICAL",
                "title": "official medical support",
                "provider": "public agency",
                "official_source_ref": "official_source_1",
                "state": "OPEN",
            },
        )
        self.assertEqual(open_program.status_code, 200, open_program.text)
        self.assertTrue(open_program.json()["actionable"])

        closed_program = self.client.post(
            "/preview/life/support-programs",
            headers=self.headers("p1", "life"),
            json={
                "program_id": "prog_2",
                "domain": "MEDICAL",
                "title": "closed support",
                "provider": "public agency",
                "official_source_ref": "official_source_2",
                "state": "CLOSED",
            },
        )
        self.assertEqual(closed_program.status_code, 200, closed_program.text)
        self.assertFalse(closed_program.json()["actionable"])

        listed = self.client.get(
            "/preview/life/support-programs/actionable",
            headers=self.headers("p1", "life"),
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        ids = [program["program_id"] for program in listed.json()["programs"]]
        self.assertIn("prog_1", ids)
        self.assertNotIn("prog_2", ids)


if __name__ == "__main__":
    unittest.main()

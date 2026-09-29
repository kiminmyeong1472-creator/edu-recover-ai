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

    def test_legal_deadline_starts_unverified(self):
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


if __name__ == "__main__":
    unittest.main()

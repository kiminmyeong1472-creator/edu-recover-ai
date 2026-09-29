import unittest

from pydantic import ValidationError

from app.domains.life.api_models import ActivityCreateRequest
from app.domains.medical.api_models import MedicationCreateRequest
from app.domains.legal.api_models import LegalDeadlineCreateRequest


class ApiModelTests(unittest.TestCase):
    def test_medical_contract_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            MedicationCreateRequest(
                subject_id="p1",
                product_name="sample",
                unexpected="no",
            )

    def test_life_activity_domain_is_limited(self):
        with self.assertRaises(ValidationError):
            ActivityCreateRequest(
                subject_id="p1",
                domain="education",
                action="x",
                resource_ref="r1",
            )

    def test_legal_deadline_requires_source(self):
        with self.assertRaises(ValidationError):
            LegalDeadlineCreateRequest(
                subject_id="p1",
                case_id="c1",
                title="deadline",
                due_at="2026-12-01T00:00:00+09:00",
                source_ref="",
            )


if __name__ == "__main__":
    unittest.main()

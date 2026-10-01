import unittest

from app.domains.medical.medication_passport import build_medication_passport
from app.domains.medical.models import MedicationRecord
from app.domains.medical.treatment_purpose import (
    TreatmentPurposeEvidence,
    assess_treatment_purpose,
)


class MedicationPassportTests(unittest.TestCase):
    def test_unconfirmed_purpose_is_flagged_for_confirmation(self):
        record = MedicationRecord(
            medication_id="med_1",
            subject_id="person_1",
            product_name="sample",
        )
        passport = build_medication_passport(subject_id="person_1", records=[record])
        self.assertEqual(passport.confirmation_needed, ("med_1",))

    def test_subject_mismatch_is_fail_closed(self):
        record = MedicationRecord(
            medication_id="med_1",
            subject_id="person_2",
            product_name="sample",
        )
        with self.assertRaises(PermissionError):
            build_medication_passport(subject_id="person_1", records=[record])

    def test_official_indication_does_not_become_patient_purpose(self):
        assessment = assess_treatment_purpose(
            [TreatmentPurposeEvidence(source="OFFICIAL_LABEL", value="approved indication")]
        )
        self.assertEqual(assessment.status, "NEEDS_CONFIRMATION")
        self.assertIsNone(assessment.display_purpose)

    def test_clinician_confirmation_has_priority(self):
        assessment = assess_treatment_purpose(
            [
                TreatmentPurposeEvidence(source="USER_REPORT", value="remembered purpose"),
                TreatmentPurposeEvidence(source="CLINICIAN_CONFIRMED", value="confirmed purpose"),
            ]
        )
        self.assertEqual(assessment.status, "CONFIRMED")
        self.assertEqual(assessment.display_purpose, "confirmed purpose")


if __name__ == "__main__":
    unittest.main()

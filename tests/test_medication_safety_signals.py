import unittest

from app.domains.medical.safety_signals import MedicationSafetySignal, user_message


class MedicationSafetySignalTests(unittest.TestCase):
    def test_message_never_orders_medication_stop(self):
        signal = MedicationSafetySignal(
            signal_id="s1",
            subject_id="p1",
            medication_refs=("m1", "m2"),
            kind="DUPLICATE_INGREDIENT",
            severity="REVIEW",
            source_ref="official-rule",
            explanation="동일성분 중복 가능성이 있습니다.",
        )
        message = user_message(signal)
        self.assertIn("임의로 약을 중단하지 말고", message)
        self.assertIn("의사 또는 약사", message)


if __name__ == "__main__":
    unittest.main()

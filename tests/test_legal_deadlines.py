import unittest

from app.domains.legal.deadlines import LegalDeadline, verify_deadline


class LegalDeadlineTests(unittest.TestCase):
    def test_deadline_starts_unverified(self):
        deadline = LegalDeadline.create(
            case_id="c1",
            subject_id="p1",
            title="example",
            due_at="2026-10-31T00:00:00+09:00",
            source_ref="notice_1",
        )
        self.assertEqual(deadline.state, "UNVERIFIED")

    def test_verification_requires_note(self):
        deadline = LegalDeadline.create(
            case_id="c1",
            subject_id="p1",
            title="example",
            due_at="2026-10-31T00:00:00+09:00",
            source_ref="notice_1",
        )
        with self.assertRaises(ValueError):
            verify_deadline(deadline, verification_note="")


if __name__ == "__main__":
    unittest.main()

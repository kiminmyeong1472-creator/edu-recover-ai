import unittest

from app.domains.legal.case_timeline import CaseTimelineEvent, ordered_timeline, revise_event


class CaseTimelineTests(unittest.TestCase):
    def test_revision_preserves_predecessor_link(self):
        first = CaseTimelineEvent.create(
            case_id="case_1",
            subject_id="person_1",
            stage="POLICE",
            kind="NOTICE",
            title="first",
            occurred_at="2026-01-01T00:00:00+00:00",
        )
        successor = revise_event(first, title="corrected")
        self.assertEqual(successor.supersedes_event_id, first.event_id)
        self.assertEqual(first.title, "first")

    def test_mixed_case_scope_is_rejected(self):
        a = CaseTimelineEvent.create(
            case_id="case_1",
            subject_id="person_1",
            stage="POLICE",
            kind="FACT",
            title="a",
        )
        b = CaseTimelineEvent.create(
            case_id="case_2",
            subject_id="person_1",
            stage="COURT",
            kind="FACT",
            title="b",
        )
        with self.assertRaises(PermissionError):
            ordered_timeline([a, b])


if __name__ == "__main__":
    unittest.main()

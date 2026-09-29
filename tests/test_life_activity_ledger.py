import unittest

from app.domains.life.activity_ledger import ActivityEntry, append_state_transition


class ActivityLedgerTests(unittest.TestCase):
    def test_state_change_appends_successor(self):
        first = ActivityEntry.create(
            subject_id="person_1",
            domain="medical",
            action="medication_review_created",
            resource_ref="med_1",
        )
        done = append_state_transition(first, state="DONE", action="medication_review_completed")
        self.assertEqual(done.parent_activity_id, first.activity_id)
        self.assertEqual(first.state, "OPEN")
        self.assertEqual(done.state, "DONE")


if __name__ == "__main__":
    unittest.main()

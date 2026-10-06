import json
import unittest

from app.services.interview_memory import context_key
from app.services.interview_prompts import SUBJECT_PROMPTS, SUBJECT_RUBRICS
from app.services.oral_interview import _validate_result


class OralInterviewTests(unittest.TestCase):
    def test_prompt_requires_reasoning_without_revealing_answer(self):
        prompt = SUBJECT_PROMPTS["서울대_구술면접"]
        rubric = SUBJECT_RUBRICS["서울대_구술면접"]
        self.assertIn("정답을 먼저 제시하지 않는다", prompt)
        self.assertIn("일관성", rubric)
        self.assertIn("꼬리질문", rubric)

    def test_context_key_does_not_expose_student_alias(self):
        key = context_key("cls_test", "학생A", "q1")
        self.assertTrue(key.startswith("oralctx:"))
        self.assertNotIn("학생A", key)
        self.assertNotIn("q1", key)

    def test_valid_evaluator_json_is_normalized(self):
        payload = _validate_result(json.dumps({
            "diagnostic_level": 2,
            "feedback_message": "근거는 있으나 이전 답변과 충돌합니다.",
            "fallback_hint": "같은 기준을 예외 상황에도 적용할 수 있습니까?",
        }, ensure_ascii=False))
        self.assertEqual(payload["diagnostic_level"], 2)
        self.assertTrue(payload["fallback_hint"].endswith("?"))

    def test_invalid_level_is_rejected(self):
        with self.assertRaises(Exception):
            _validate_result(json.dumps({
                "diagnostic_level": 7,
                "feedback_message": "x",
                "fallback_hint": "y",
            }))


if __name__ == "__main__":
    unittest.main()

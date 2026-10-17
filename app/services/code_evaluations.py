"""Versioned small EduRecover tasks; these are not official benchmark scores."""
from __future__ import annotations

CATEGORIES = [
    {"id": "problem", "title": "코드 문제 풀이", "references": ["LiveCodeBench"]},
    {"id": "repair", "title": "저장소 코드 수정", "references": ["SWE-bench Verified", "SWE-bench Pro", "DeepSWE"]},
    {"id": "terminal", "title": "터미널 작업", "references": ["Terminal-Bench"]},
    {"id": "science", "title": "과학 계산 코딩", "references": ["SciCode"]},
]

CASES = [
    {"id": "unique-order-v1", "category": "problem", "title": "중복 제거 함수",
     "prompt": "solution.py에 unique_order(values)를 작성하십시오. 입력 순서를 유지하고 중복 정수를 제거하여 새 목록으로 반환하십시오. 빈 입력과 음수도 처리하고 실제 시험을 실행하십시오.",
     "files": [{"path": "solution.py", "content": "def unique_order(values):\n    raise NotImplementedError\n"}],
     "verification": "from solution import unique_order\nassert unique_order([]) == []\nassert unique_order([3,1,3,-2,1]) == [3,1,-2]\nassert unique_order([0,0,0]) == [0]\nprint('3 checks passed')\n"},
    {"id": "median-repair-v1", "category": "repair", "title": "중앙값 오류 수정",
     "prompt": "stats.py의 median 오류를 고치십시오. 홀수·짝수·빈 목록을 처리하고 원본 목록은 변경하지 않아야 합니다. 빈 목록은 ValueError를 발생시키십시오. 기존 시험도 실행하십시오.",
     "files": [{"path": "stats.py", "content": "def median(values):\n    values.sort()\n    return values[len(values)//2]\n"},
               {"path": "test_existing.py", "content": "import unittest\nfrom stats import median\nclass Existing(unittest.TestCase):\n    def test_odd(self): self.assertEqual(median([3,1,2]),2)\n"}],
     "verification": "from stats import median\nx=[4,1,3,2]\nassert median(x)==2.5\nassert x==[4,1,3,2]\nassert median([9])==9\ntry:\n    median([])\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('empty input must fail')\nprint('4 checks passed')\n"},
    {"id": "csv-total-v1", "category": "terminal", "title": "자료 읽기와 결과 파일 생성",
     "prompt": "data.csv의 amount 열 합계를 계산하는 main.py를 작성하고 실행하십시오. 결과를 result.json에 {\"total\": 합계} 형태로 저장하십시오.",
     "files": [{"path": "data.csv", "content": "name,amount\na,12\nb,-2\nc,5\n"}],
     "verification": "import json\nfrom pathlib import Path\nassert Path('main.py').is_file()\nassert json.loads(Path('result.json').read_text()) == {'total':15}\nprint('2 checks passed')\n"},
    {"id": "population-std-v1", "category": "science", "title": "평균과 모집단 표준편차",
     "prompt": "science.py에 summarize(values)를 작성하십시오. 숫자 목록의 평균과 모집단 표준편차를 (mean, std)로 반환하십시오. 외부 패키지 없이 구현하고 빈 목록에는 ValueError를 발생시키십시오. 직접 시험도 실행하십시오.",
     "files": [],
     "verification": "import math\nfrom science import summarize\nm,s=summarize([2,4,4,4,5,5,7,9])\nassert math.isclose(m,5) and math.isclose(s,2)\nassert summarize([3]) == (3,0)\ntry:\n    summarize([])\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('empty input must fail')\nprint('3 checks passed')\n"},
]

def public_cases() -> dict:
    return {"categories": CATEGORIES, "cases": [
        {key: value for key, value in case.items() if key != "verification"}
        | {"state": "미실행", "suite": "EduRecover 자체 시험", "version": "2026-10-17.1"}
        for case in CASES
    ], "official_benchmark_run": False}

def get_case(case_id: str | None) -> dict | None:
    if case_id is None:
        return None
    for case in CASES:
        if case["id"] == case_id:
            return case
    raise ValueError("UNKNOWN_EVALUATION_CASE")

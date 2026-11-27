"""Runs the 100-question customer-search evaluation (synthetic data, MOCKED Groq)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))

import runner  # noqa: E402

CASES = json.loads((Path(__file__).resolve().parent / "eval" / "search_eval_cases.json")
                   .read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def results():
    import ai_guard
    import main

    return {row["id"]: row for row in runner.run(main, ai_guard)}


def test_suite_shape():
    counts = {}
    for case in CASES:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    assert len(CASES) >= 100 and len({c["id"] for c in CASES}) == len(CASES)
    assert counts == {
        "budget_bhk_area": 20, "locality": 15, "facing_amenities": 10, "telugu": 15,
        "telugu_english_mixed": 10, "comparison_followup": 10, "missing_info_no_result": 10,
        "security_abuse": 10,
    }


@pytest.mark.parametrize("case_id", [case["id"] for case in CASES])
def test_search_case(results, case_id):
    row = results[case_id]
    assert row["pass"], row["failure_reason"]

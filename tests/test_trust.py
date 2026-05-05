from chase.trust import (
    classify_failure,
    estimate_contract_risk,
    render_plan_preview,
    render_verification_card,
)


def test_render_plan_preview_lists_sprints_and_risk():
    contracts = [
        {
            "id": 1,
            "title": "Add login validation",
            "description": "Validate email and password before submit.",
            "contract": {
                "criteria": [
                    "Email field rejects malformed addresses",
                    "Password shorter than 8 characters is rejected",
                ],
                "test_command": "pytest tests/test_login.py -v",
            },
            "files_likely_touched": ["app/login.py", "tests/test_login.py"],
        }
    ]

    preview = render_plan_preview(contracts)

    assert "# Chase Plan Preview" in preview
    assert "Sprint 1: Add login validation" in preview
    assert "Risk: medium" in preview
    assert "pytest tests/test_login.py -v" in preview
    assert "app/login.py" in preview


def test_render_verification_card_includes_evidence_and_feedback():
    eval_data = {
        "sprint_id": 2,
        "score": 0.65,
        "verdict": "FAIL",
        "criteria": [
            {
                "name": "Reject empty email",
                "passes": True,
                "evidence": "pytest passed test_empty_email",
            },
            {
                "name": "Show validation message",
                "passes": False,
                "evidence": "No message rendered in app/login.py",
            },
        ],
        "test_output": "1 failed, 3 passed",
        "feedback": "Add visible validation text for invalid email.",
    }

    card = render_verification_card(2, eval_data)

    assert "# Sprint 2 Verification Card" in card
    assert "Verdict: FAIL" in card
    assert "- [x] Reject empty email" in card
    assert "- [ ] Show validation message" in card
    assert "No message rendered in app/login.py" in card
    assert "Failure reason: implementation_incomplete" in card


def test_classify_failure_maps_common_evaluator_outputs():
    assert classify_failure({"verdict": "PASS", "score": 1.0}) == "none"
    assert classify_failure({"verdict": "ERROR", "feedback": "No JSON in evaluator output"}) == "evaluator_error"
    assert classify_failure({"verdict": "FAIL", "test_output": "command not found: pytest"}) == "environment_error"
    assert classify_failure({"verdict": "FAIL", "feedback": "criterion is ambiguous"}) == "requirements_ambiguous"
    assert classify_failure({"verdict": "FAIL", "feedback": "function is missing"}) == "implementation_incomplete"


def test_estimate_contract_risk_uses_files_criteria_and_test_command():
    low = estimate_contract_risk({
        "contract": {"criteria": ["One behavior"], "test_command": "pytest tests/test_one.py"},
        "files_likely_touched": ["one.py"],
    })
    high = estimate_contract_risk({
        "contract": {"criteria": ["A", "B", "C", "D", "E", "F"], "test_command": ""},
        "files_likely_touched": ["a.py", "b.py", "c.py", "d.py"],
    })

    assert low == "low"
    assert high == "high"

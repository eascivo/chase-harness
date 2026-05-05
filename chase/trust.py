"""Trust-layer helpers for plan previews and verification cards."""

from __future__ import annotations

from typing import Any

FailureReason = str


def estimate_contract_risk(contract: dict[str, Any]) -> str:
    """Estimate sprint risk from criteria, touched files, and test coverage."""
    criteria = _criteria_for(contract)
    files = contract.get("files_likely_touched", [])
    test_command = _test_command_for(contract)

    score = 0
    if len(criteria) >= 5:
        score += 2
    elif len(criteria) >= 3:
        score += 1

    if len(files) >= 4:
        score += 2
    elif len(files) >= 2:
        score += 1

    if not test_command:
        score += 2

    if score >= 4:
        return "high"
    if score >= 1:
        return "medium"
    return "low"


def classify_failure(eval_data: dict[str, Any]) -> FailureReason:
    """Map evaluator output into a stable, user-facing failure reason."""
    verdict = str(eval_data.get("verdict", "")).upper()
    if verdict == "PASS":
        return "none"

    criteria_text = " ".join(
        str(item.get("evidence", "")) for item in eval_data.get("criteria", [])
        if isinstance(item, dict)
    )
    text = " ".join([
        str(eval_data.get("feedback", "")),
        str(eval_data.get("test_output", "")),
        criteria_text,
    ]).lower()

    if verdict == "ERROR" or "no json" in text or "evaluator" in text:
        return "evaluator_error"
    if "command not found" in text or "no such file" in text or "not installed" in text:
        return "environment_error"
    if "ambiguous" in text or "unclear" in text or "cannot verify" in text:
        return "requirements_ambiguous"
    if (
        "missing" in text
        or "not implemented" in text
        or "incomplete" in text
        or "no message" in text
        or "not rendered" in text
    ):
        return "implementation_incomplete"
    if "test" in text and ("failed" in text or "failure" in text):
        return "test_failure"
    return "unknown_failure"


def render_plan_preview(contracts: list[dict[str, Any]]) -> str:
    """Render human-readable sprint plan preview markdown."""
    lines = [
        "# Chase Plan Preview",
        "",
        "Review this plan before allowing Chase to modify the repository.",
        "",
    ]

    for contract in contracts:
        sid = contract.get("id", "?")
        title = contract.get("title", "Untitled sprint")
        risk = estimate_contract_risk(contract)
        description = contract.get("description", "")
        criteria = _criteria_for(contract)
        files = contract.get("files_likely_touched", [])
        test_command = _test_command_for(contract) or "No automated test command specified"

        lines.extend([
            f"## Sprint {sid}: {title}",
            "",
            f"Risk: {risk}",
            "",
        ])
        if description:
            lines.extend([description, ""])

        lines.extend(["Acceptance criteria:", ""])
        for item in criteria:
            lines.append(f"- {item}")
        lines.extend(["", f"Test command: `{test_command}`", ""])

        if files:
            lines.extend(["Likely files:", ""])
            for path in files:
                lines.append(f"- `{path}`")
            lines.append("")

    lines.extend([
        "## Approval",
        "",
        "Run `chase approve` to allow `chase run` to execute Generator steps.",
        "",
    ])
    return "\n".join(lines)


def render_verification_card(sprint_id: int, eval_data: dict[str, Any]) -> str:
    """Render a durable sprint verification card from evaluator JSON."""
    verdict = eval_data.get("verdict", "UNKNOWN")
    score = eval_data.get("score", "?")
    failure_reason = classify_failure(eval_data)

    lines = [
        f"# Sprint {sprint_id} Verification Card",
        "",
        f"Verdict: {verdict}",
        f"Score: {score}",
        f"Failure reason: {failure_reason}",
        "",
        "## Criteria",
        "",
    ]

    for criterion in eval_data.get("criteria", []):
        name = criterion.get("name") or criterion.get("criterion") or "Unnamed criterion"
        passes = bool(criterion.get("passes"))
        evidence = criterion.get("evidence", "")
        marker = "x" if passes else " "
        lines.append(f"- [{marker}] {name}")
        if evidence:
            lines.append(f"  Evidence: {evidence}")

    test_output = str(eval_data.get("test_output", "")).strip()
    if test_output:
        lines.extend(["", "## Test Output", "", "```", test_output, "```"])

    feedback = str(eval_data.get("feedback", "")).strip()
    if feedback:
        lines.extend(["", "## Feedback", "", feedback])

    lines.append("")
    return "\n".join(lines)


def _criteria_for(contract: dict[str, Any]) -> list[str]:
    if "negotiated_criteria" in contract:
        return [
            str(item.get("criterion", item))
            for item in contract.get("negotiated_criteria", [])
        ]
    nested = contract.get("contract", {})
    return [str(item) for item in nested.get("criteria", [])]


def _test_command_for(contract: dict[str, Any]) -> str:
    if contract.get("test_command"):
        return str(contract["test_command"])
    nested = contract.get("contract", {})
    return str(nested.get("test_command", ""))

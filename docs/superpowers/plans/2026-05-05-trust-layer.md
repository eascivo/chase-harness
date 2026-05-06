# Chase Trust Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the missing trust layer that lets users inspect, approve, and understand autonomous Chase runs before and after code changes.

**Architecture:** Add a structured planning preview before mutation, persist sprint-level verification evidence after evaluation, and classify failures into actionable product-level reasons. Keep the core Planner-Negotiator-Generator-Evaluator loop intact; add small data models and CLI surfaces around it.

**Tech Stack:** Python 3.9 standard library, argparse CLI, JSON/Markdown state files under `.chase/`, subprocess-driven AI CLI adapters, pytest for project tests.

---

## Product Scope

This plan intentionally focuses on the single-project Chase experience:

- `chase plan` generates sprint contracts and negotiated criteria without running Generator.
- `chase run` can require explicit approval before modifying code.
- `chase status` surfaces evidence, risk, and failure reasons instead of only pass/fail.
- Evaluator output is converted into durable, human-readable verification cards.

Ray multi-project orchestration stays unchanged except that it can later consume the same trust-layer files.

## File Structure

- Create `chase/trust.py`: typed helpers for plan summaries, verification cards, failure taxonomy, and risk estimates.
- Modify `chase/state.py`: add canonical paths for plan preview and verification card files.
- Modify `chase/cli.py`: add `plan`, `approve`, and trust-aware status output.
- Modify `chase/orchestrator.py`: support approval gating before Generator and write verification cards after Evaluator.
- Modify `chase/agents/negotiator.py`: ensure negotiation can be invoked during `chase plan` for all contracts.
- Create `tests/test_trust.py`: unit tests for trust-layer JSON/Markdown rendering and failure classification.
- Create `tests/test_cli_plan.py`: CLI-level tests for `plan`, `approve`, and status output using temporary workspaces and monkeypatched agents.
- Create `pyproject.toml`: minimal pytest configuration so the project can verify itself.
- Modify `README.md` and `README_CN.md`: document the new trust workflow.

---

### Task 1: Add Trust Data Model and Renderers

**Files:**
- Create: `chase/trust.py`
- Modify: `chase/state.py`
- Test: `tests/test_trust.py`
- Create: `pyproject.toml`

- [ ] **Step 1: Write failing tests for trust rendering**

Create `tests/test_trust.py`:

```python
import json

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
```

- [ ] **Step 2: Add pytest configuration**

Create `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 3: Run tests to confirm failure**

Run:

```bash
python3 -m pytest tests/test_trust.py -v
```

Expected: FAIL because `chase.trust` does not exist.

- [ ] **Step 4: Implement trust helpers**

Create `chase/trust.py`:

```python
"""Trust-layer helpers for plan previews and verification cards."""

from __future__ import annotations

from typing import Any


FailureReason = str


def estimate_contract_risk(contract: dict[str, Any]) -> str:
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
    if score >= 2:
        return "medium"
    return "low"


def classify_failure(eval_data: dict[str, Any]) -> FailureReason:
    verdict = str(eval_data.get("verdict", "")).upper()
    if verdict == "PASS":
        return "none"

    text = " ".join([
        str(eval_data.get("feedback", "")),
        str(eval_data.get("test_output", "")),
    ]).lower()

    if verdict == "ERROR" or "no json" in text or "evaluator" in text:
        return "evaluator_error"
    if "command not found" in text or "no such file" in text or "not installed" in text:
        return "environment_error"
    if "ambiguous" in text or "unclear" in text or "cannot verify" in text:
        return "requirements_ambiguous"
    if "missing" in text or "not implemented" in text or "incomplete" in text:
        return "implementation_incomplete"
    if "test" in text and ("failed" in text or "failure" in text):
        return "test_failure"
    return "unknown_failure"


def render_plan_preview(contracts: list[dict[str, Any]]) -> str:
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
```

- [ ] **Step 5: Add state paths**

Modify `chase/state.py` with:

```python
    @property
    def plan_preview_file(self) -> Path:
        return self.root / "plan-preview.md"

    @property
    def approval_file(self) -> Path:
        return self.root / "approved.json"

    def sprint_verification_card(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-verification.md"
```

Place these near the existing path helpers.

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest tests/test_trust.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml chase/trust.py chase/state.py tests/test_trust.py
git commit -m "feat: add chase trust layer primitives"
```

---

### Task 2: Add `chase plan` and `chase approve`

**Files:**
- Modify: `chase/cli.py`
- Modify: `chase/agents/negotiator.py`
- Test: `tests/test_cli_plan.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli_plan.py`:

```python
import json
from pathlib import Path

from chase.cli import cmd_approve, cmd_plan


class Args:
    def __init__(self, workspace: str):
        self.workspace = workspace


def test_cmd_approve_writes_approval_file(tmp_path):
    ws = tmp_path
    (ws / ".chase").mkdir()
    (ws / "MISSION.md").write_text("# Goal\nShip trust layer\n", encoding="utf-8")

    exit_code = cmd_approve(Args(str(ws)))

    assert exit_code == 0
    data = json.loads((ws / ".chase" / "approved.json").read_text(encoding="utf-8"))
    assert data["approved"] is True


def test_cmd_plan_renders_preview_from_existing_contracts(tmp_path, monkeypatch):
    ws = tmp_path
    sprints = ws / ".chase" / "sprints"
    sprints.mkdir(parents=True)
    (ws / ".chase" / "handoffs").mkdir()
    (ws / ".chase" / "logs").mkdir()
    (ws / "MISSION.md").write_text("# Goal\nAdd validation\n", encoding="utf-8")
    (sprints / "01-contract.md").write_text(json.dumps({
        "id": 1,
        "title": "Add validation",
        "description": "Validate user input.",
        "contract": {
            "criteria": ["Reject empty input"],
            "test_command": "pytest tests/test_validation.py",
        },
        "files_likely_touched": ["app.py"],
    }), encoding="utf-8")

    exit_code = cmd_plan(Args(str(ws)))

    assert exit_code == 0
    preview = (ws / ".chase" / "plan-preview.md").read_text(encoding="utf-8")
    assert "Sprint 1: Add validation" in preview
    assert "Run `chase approve`" in preview
```

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
python3 -m pytest tests/test_cli_plan.py -v
```

Expected: FAIL because `cmd_plan` and `cmd_approve` do not exist.

- [ ] **Step 3: Implement approval command**

Modify `chase/cli.py` imports:

```python
from datetime import datetime, timezone
from chase.trust import render_plan_preview
```

Add command:

```python
def cmd_approve(args) -> int:
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)
    state.init_directories()
    payload = {
        "approved": True,
        "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    state.approval_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print_green("Plan approved. `chase run` may now execute code changes.")
    return 0
```

- [ ] **Step 4: Implement plan command for existing and new contracts**

Add command:

```python
def cmd_plan(args) -> int:
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)
    state.init_directories()

    if not state.mission_file.exists():
        print_red("MISSION.md not found. Create it first.")
        return 1

    config = ChaseConfig.from_env(ws)
    contracts = state.existing_contracts()
    if not contracts:
        orch = Orchestrator(config, state)
        orch.preflight()
        result = orch.planner.run(orch.cost, orch.logger)
        if not result.success:
            print_red("Planner failed.")
            return 1
        contracts = state.existing_contracts()

    contract_data = []
    for path in contracts:
        contract_data.append(json.loads(path.read_text(encoding="utf-8")))

    preview = render_plan_preview(contract_data)
    state.plan_preview_file.write_text(preview, encoding="utf-8")
    print(preview)
    print_green(f"Plan preview written to {state.plan_preview_file}")
    return 0
```

- [ ] **Step 5: Register new commands**

Modify the parser setup in `main()`:

```python
    for name in ("init", "plan", "approve", "run", "resume", "status", "reset"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", default=None)
```

Modify dispatch:

```python
        "plan": cmd_plan,
        "approve": cmd_approve,
```

- [ ] **Step 6: Run CLI tests**

Run:

```bash
python3 -m pytest tests/test_cli_plan.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add chase/cli.py tests/test_cli_plan.py
git commit -m "feat: add plan preview and approval commands"
```

---

### Task 3: Gate Generator Execution on Approval

**Files:**
- Modify: `chase/config.py`
- Modify: `chase/orchestrator.py`
- Modify: `chase/cli.py`
- Test: `tests/test_cli_plan.py`

- [ ] **Step 1: Add failing approval gate test**

Append to `tests/test_cli_plan.py`:

```python
from chase.orchestrator import Orchestrator
from chase.config import ChaseConfig
from chase.state import StateDir


def test_orchestrator_requires_approval_when_configured(tmp_path):
    ws = tmp_path
    state = StateDir.for_workspace(ws)
    state.init_directories()
    (ws / "MISSION.md").write_text("# Goal\nTest gate\n", encoding="utf-8")
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws, require_approval=True)
    orch = Orchestrator(config, state)

    assert orch._approval_granted() is False

    state.approval_file.write_text(json.dumps({"approved": True}), encoding="utf-8")

    assert orch._approval_granted() is True
```

- [ ] **Step 2: Run test to confirm failure**

Run:

```bash
python3 -m pytest tests/test_cli_plan.py::test_orchestrator_requires_approval_when_configured -v
```

Expected: FAIL because `require_approval` and `_approval_granted` do not exist.

- [ ] **Step 3: Add config flag**

Modify `ChaseConfig` in `chase/config.py`:

```python
    require_approval: bool = False
```

Modify `from_env`:

```python
            require_approval=os.environ.get("CHASE_REQUIRE_APPROVAL", "") == "1",
```

- [ ] **Step 4: Implement approval helper**

Add to `Orchestrator`:

```python
    def _approval_granted(self) -> bool:
        if not self.config.require_approval:
            return True
        try:
            data = json.loads(self.state.approval_file.read_text())
            return bool(data.get("approved"))
        except Exception:
            return False
```

- [ ] **Step 5: Gate before sprint mutation**

In `Orchestrator.run()`, after negotiation and before the Generator-Evaluator retry loop, add:

```python
            if not self._approval_granted():
                self.logger.error("Plan approval required. Run `chase plan`, review it, then run `chase approve`.")
                generate_handoff(self.state, self.config, self.cost, sprint_id, "approval_required")
                return 1
```

- [ ] **Step 6: Document env template**

In `cmd_init`, add to `.chase/.env` template:

```text
# Require explicit `chase approve` before Generator modifies code
# CHASE_REQUIRE_APPROVAL=1
```

- [ ] **Step 7: Run tests**

Run:

```bash
python3 -m pytest tests/test_cli_plan.py tests/test_trust.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add chase/config.py chase/orchestrator.py chase/cli.py tests/test_cli_plan.py
git commit -m "feat: gate autonomous runs on plan approval"
```

---

### Task 4: Persist Verification Cards After Evaluation

**Files:**
- Modify: `chase/orchestrator.py`
- Modify: `chase/cli.py`
- Test: `tests/test_trust.py`

- [ ] **Step 1: Add failing test for writing cards**

Append to `tests/test_trust.py`:

```python
from chase.config import ChaseConfig
from chase.orchestrator import Orchestrator
from chase.state import StateDir


def test_orchestrator_writes_verification_card(tmp_path):
    state = StateDir.for_workspace(tmp_path)
    state.init_directories()
    config = ChaseConfig(chase_home=tmp_path, workspace=tmp_path)
    orch = Orchestrator(config, state)
    eval_data = {
        "score": 1.0,
        "verdict": "PASS",
        "criteria": [{"name": "Works", "passes": True, "evidence": "pytest passed"}],
    }

    orch._write_verification_card(1, eval_data)

    card = state.sprint_verification_card(1).read_text(encoding="utf-8")
    assert "Sprint 1 Verification Card" in card
    assert "- [x] Works" in card
```

- [ ] **Step 2: Run test to confirm failure**

Run:

```bash
python3 -m pytest tests/test_trust.py::test_orchestrator_writes_verification_card -v
```

Expected: FAIL because `_write_verification_card` does not exist.

- [ ] **Step 3: Implement card writing**

Modify `chase/orchestrator.py` imports:

```python
from chase.trust import render_verification_card
```

Add method:

```python
    def _write_verification_card(self, sprint_id: int, eval_data: dict) -> None:
        card = render_verification_card(sprint_id, eval_data)
        self.state.sprint_verification_card(sprint_id).write_text(card, encoding="utf-8")
```

After `eval_result.parsed_data` is confirmed, before score parsing, add:

```python
                self._write_verification_card(sprint_id, eval_result.parsed_data)
```

- [ ] **Step 4: Show cards in status output**

In `cmd_status`, when printing each sprint with an eval file, add:

```python
                card_path = state.sprint_verification_card(sid)
                if card_path.exists():
                    print(f"          evidence: {card_path}")
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_trust.py tests/test_cli_plan.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add chase/orchestrator.py chase/cli.py tests/test_trust.py
git commit -m "feat: persist sprint verification cards"
```

---

### Task 5: Improve Status With Failure Reasons and Risk

**Files:**
- Modify: `chase/cli.py`
- Test: `tests/test_cli_plan.py`

- [ ] **Step 1: Add failing status test**

Append to `tests/test_cli_plan.py`:

```python
from chase.cli import cmd_status


def test_status_prints_failure_reason_and_evidence_path(tmp_path, capsys):
    ws = tmp_path
    sprints = ws / ".chase" / "sprints"
    logs = ws / ".chase" / "logs"
    sprints.mkdir(parents=True)
    (ws / ".chase" / "handoffs").mkdir()
    logs.mkdir()
    (logs / "cost-tracking.json").write_text('{"total_cost": 0.0, "sprints": []}\n', encoding="utf-8")
    (ws / "MISSION.md").write_text("# Goal\nStatus test\n", encoding="utf-8")
    (sprints / "01-contract.md").write_text(json.dumps({
        "id": 1,
        "title": "Fix login",
        "contract": {"criteria": ["Reject invalid login"], "test_command": ""},
        "files_likely_touched": ["login.py", "auth.py", "views.py", "tests.py"],
    }), encoding="utf-8")
    (sprints / "01-eval.json").write_text(json.dumps({
        "verdict": "FAIL",
        "score": 0.4,
        "feedback": "function is missing",
    }), encoding="utf-8")
    (sprints / "01-verification.md").write_text("# evidence\n", encoding="utf-8")

    exit_code = cmd_status(Args(str(ws)))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "reason: implementation_incomplete" in out
    assert "risk: high" in out
    assert "01-verification.md" in out
```

- [ ] **Step 2: Run test to confirm failure**

Run:

```bash
python3 -m pytest tests/test_cli_plan.py::test_status_prints_failure_reason_and_evidence_path -v
```

Expected: FAIL because status does not print reason or risk.

- [ ] **Step 3: Import trust helpers**

Modify `chase/cli.py`:

```python
from chase.trust import classify_failure, estimate_contract_risk, render_plan_preview
```

- [ ] **Step 4: Add risk and failure reason output**

Inside `cmd_status`, after loading `contract`, compute:

```python
        risk = estimate_contract_risk(contract)
```

For passed and failed evals, include:

```python
                    reason = classify_failure(eval_data)
                    print(f"          risk: {risk} | reason: {reason}")
```

For pending sprints, include:

```python
                print(f"          risk: {risk}")
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_cli_plan.py tests/test_trust.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add chase/cli.py tests/test_cli_plan.py
git commit -m "feat: show sprint risk and failure reasons"
```

---

### Task 6: Update Documentation and Product Messaging

**Files:**
- Modify: `README.md`
- Modify: `README_CN.md`

- [ ] **Step 1: Add English trust workflow docs**

In `README.md`, add a section after Quick Start:

```markdown
## Trust Workflow

For safer autonomous runs, enable approval gating:

```bash
CHASE_REQUIRE_APPROVAL=1 chase plan
# review .chase/plan-preview.md
chase approve
chase run
```

`chase plan` generates sprint contracts and a human-readable preview without running Generator. After each evaluated sprint, Chase writes `.chase/sprints/NN-verification.md` with the criteria, evidence, test output, score, verdict, and failure reason.
```
```

- [ ] **Step 2: Add Chinese trust workflow docs**

In `README_CN.md`, add:

```markdown
## 信任工作流

如果你希望 Chase 在改代码前必须经过确认，可以启用审批门禁：

```bash
CHASE_REQUIRE_APPROVAL=1 chase plan
# 查看 .chase/plan-preview.md
chase approve
chase run
```

`chase plan` 只生成 sprint 合约和可读预览，不会运行 Generator。每个 sprint 验收后，Chase 会写入 `.chase/sprints/NN-verification.md`，包含验收条件、证据、测试输出、分数、结论和失败原因。
```
```

- [ ] **Step 3: Update command tables**

Add rows in both READMEs:

```markdown
| `chase plan` | Generate sprint plan preview without code changes |
| `chase approve` | Approve the current plan so `chase run` can modify code |
```

Chinese:

```markdown
| `chase plan` | 生成 sprint 计划预览，不改代码 |
| `chase approve` | 审批当前计划，允许 `chase run` 修改代码 |
```

- [ ] **Step 4: Run full tests**

Run:

```bash
python3 -m pytest -v
```

Expected: PASS.

- [ ] **Step 5: Smoke test CLI help**

Run:

```bash
python3 -m chase --help
```

Expected: help lists `plan` and `approve`.

- [ ] **Step 6: Commit**

```bash
git add README.md README_CN.md
git commit -m "docs: document chase trust workflow"
```

---

## Self-Review

Spec coverage:

- Plan review mode is covered by Task 2.
- Explicit approval before code mutation is covered by Task 3.
- Structured verification evidence is covered by Task 4.
- Failure reason taxonomy is covered by Tasks 1 and 5.
- Risk visibility is covered by Tasks 1 and 5.
- Documentation is covered by Task 6.

Placeholder scan:

- The plan contains no TBD/TODO placeholders.
- Every code-changing task includes concrete code snippets and commands.
- Ray is intentionally excluded from MVP scope.

Type consistency:

- `StateDir.plan_preview_file`, `StateDir.approval_file`, and `StateDir.sprint_verification_card()` are introduced before use.
- `render_plan_preview`, `render_verification_card`, `classify_failure`, and `estimate_contract_risk` are imported consistently.
- `ChaseConfig.require_approval` is added before `Orchestrator._approval_granted()` uses it.

## Recommended Execution Order

1. Task 1 creates the trust-layer primitives.
2. Task 2 exposes planning and approval in the CLI.
3. Task 3 makes approval meaningful by gating Generator.
4. Task 4 persists sprint-level evidence.
5. Task 5 makes status useful for decision-making.
6. Task 6 updates product documentation.

This sequence keeps every commit independently testable and avoids changing the autonomous execution loop before the new state files and tests exist.

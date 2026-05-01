# Chase

Multi-agent autonomous development loop for Claude Code. Three-agent Planner-Generator-Evaluator pattern that breaks down goals into sprints, implements them, and verifies results automatically.

## How It Works

```
MISSION.md (your goal)
    ↓
┌──────────────────────────────┐
│  Planner Agent               │
│  Breaks goal into sprints    │
│  → sprints/01-contract.md    │
├──────────────────────────────┤
│  Generator Loop (per sprint) │
│  Implements sprint contract  │
│  → git commit                │
│  → sprints/01-result.md      │
├──────────────────────────────┤
│  Evaluator Agent             │
│  Runs tests, verifies code   │
│  → sprints/01-eval.json      │
│  Score < 0.7? → retry (×3)   │
└──────────────────────────────┘
```

## Quick Start

```bash
# Install
git clone https://github.com/eascivo/chase-harness.git ~/.chase-harness
ln -sf ~/.chase-harness/bin/chase /usr/local/bin/chase

# Use in any project
cd your-project
chase init       # creates MISSION.md template + .chase/
# ... edit MISSION.md with your goal ...
chase run        # start the autonomous loop
chase status     # check progress anytime
```

## Uninstall

```bash
rm /usr/local/bin/chase
```

## Commands

| Command | Description |
|---------|-------------|
| `chase init` | Create MISSION.md template and `.chase/` directory |
| `chase run` | Start Planner-Generator-Evaluator loop (auto-resumes) |
| `chase status` | Show sprint progress, scores, and cost |
| `chase reset` | Clean sprints/handoffs/logs to re-plan |
| `chase resume` | Alias for `run` |

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_COST_LIMIT` | `10000.0` | Budget limit in USD |
| `CHASE_MAX_SPRINTS` | `50` | Maximum number of sprints |
| `CHASE_MAX_RETRIES` | `3` | Max retries per sprint |
| `CHASE_EVAL_THRESHOLD` | `0.7` | Pass score threshold (0-1) |
| `CHASE_STALE_LIMIT` | `3` | Consecutive no-progress limit |
| `CHASE_APP_URL` | `""` | App URL for Playwright UI testing (e.g. `http://localhost:8000`) |
| `CHASE_PLAYWRIGHT` | `""` | Set to `1` to enable Playwright browser testing |

## MISSION.md Format

```markdown
# Goal

Describe what you want to accomplish.

# Context

Project background, tech stack, relevant files.

# Acceptance Criteria

1. Specific, testable condition
2. Another condition
```

The more specific and measurable your acceptance criteria, the better the results.

## Architecture

Each agent runs as a fresh `claude -p` session with its own system prompt:

- **Planner** (`prompts/planner.md`): Decomposes MISSION.md into sprint contracts (what to do, not how)
- **Negotiator** (`prompts/negotiator.md`): Refines contracts into precise, agreed-upon criteria before coding
- **Generator** (`prompts/generator.md`): Implements sprint contract, commits code, writes result report
- **Evaluator** (`prompts/evaluator.md`): Independent QA — runs real tests, checks edge cases, scores 0-1

### Three-Agent Flow

```
Planner → Negotiator → Generator ↔ Evaluator
              ↓              ↑          ↓
     Precise criteria    Implements   Verifies
     before coding       against      against
                         negotiated   negotiated
                         criteria     criteria
```

### Contract Negotiation

Before the Generator writes any code, the Negotiator refines each sprint contract into precise, measurable criteria that both Generator and Evaluator agree on. This reduces retry loops caused by ambiguous requirements.

### UI Testing with Playwright

Set `CHASE_PLAYWRIGHT=1` and `CHASE_APP_URL=http://localhost:8000` to enable browser-based testing. The Evaluator can navigate pages, click buttons, fill forms, and take screenshots.

### Design Scoring

When a sprint involves frontend work, the Evaluator adds a `design_score` (0-1) evaluating visual quality: color consistency, spacing, typography, responsiveness, and polish. The final score combines functional (70%) and design (30%) scores.

## What Makes Chase Different

- **Pure Python, zero pip dependencies** — only stdlib (dataclass, json, subprocess, argparse)
- **Four-agent adversarial pattern** — Planner, Negotiator, Generator, and Evaluator work independently
- **Contract negotiation** — criteria are refined before coding, reducing wasted retries
- **Cost tracking** — real-time budget monitoring per sprint and total
- **Resume from interruption** — picks up from the last completed sprint
- **Multi-project** — one installation serves all your projects
- **Playwright + design scoring** — browser-based UI testing with visual quality evaluation

## Requirements

- [Claude Code CLI](https://claude.ai/code)
- Python 3.9+
- Git

## License

MIT

---

**[English](README.md)** | [中文文档](README_CN.md)

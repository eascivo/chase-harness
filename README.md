# Chase

Multi-agent autonomous development for Claude Code. Four-agent Planner-Negotiator-Generator-Evaluator pattern that decomposes goals into sprint contracts, negotiates precise criteria, implements them, and verifies results automatically.

**[English](README.md)** | [中文文档](README_CN.md)

## How It Works

```
MISSION.md (your goal)
      ↓
┌─────────────────────────────────────────┐
│  Planner                                │
│  Decompose goal into sprint contracts   │
│  → sprints/01-contract.md               │
├─────────────────────────────────────────┤
│  Negotiator (per sprint)                │
│  Refine contract into precise criteria  │
│  → sprints/01-negotiated.md             │
├─────────────────────────────────────────┤
│  Generator                              │
│  Implement against negotiated criteria  │
│  → git commit → sprints/01-result.md    │
├─────────────────────────────────────────┤
│  Evaluator                              │
│  Verify against negotiated criteria     │
│  → sprints/01-eval.json                 │
│  Score < 0.7? → retry (up to 3×)        │
└─────────────────────────────────────────┘
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
| `chase run` | Start the full agent loop (auto-resumes from checkpoint) |
| `chase resume` | Alias for `run` |
| `chase status` | Show sprint progress, scores, and cost |
| `chase reset` | Clean sprints/handoffs/logs to re-plan |

## Configuration

Edit `.chase/.env` (created by `chase init`), or set environment variables:

### LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_MODEL` | `""` | Default model for all agents |
| `CHASE_PLANNER_MODEL` | `""` | Model for Planner (overrides `CHASE_MODEL`) |
| `CHASE_GENERATOR_MODEL` | `""` | Model for Generator (overrides `CHASE_MODEL`) |
| `CHASE_EVALUATOR_MODEL` | `""` | Model for Evaluator (overrides `CHASE_MODEL`) |
| `CHASE_LLM_API_KEY` | `""` | API key for your LLM provider |
| `CHASE_LLM_BASE_URL` | `""` | Custom API endpoint (e.g. `https://open.bigmodel.cn/api/paas/v4`) |

### Sprint & Budget

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_COST_LIMIT` | `10000.0` | Budget limit in USD |
| `CHASE_MAX_SPRINTS` | `50` | Maximum number of sprints |
| `CHASE_MAX_RETRIES` | `3` | Max retries per sprint |
| `CHASE_EVAL_THRESHOLD` | `0.7` | Pass score threshold (0-1) |
| `CHASE_STALE_LIMIT` | `3` | Consecutive no-progress limit |

### UI Testing

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_APP_URL` | `""` | App URL for Playwright UI testing |
| `CHASE_PLAYWRIGHT` | `""` | Set to `1` to enable browser testing |

Priority: environment variables > `.chase/.env` > defaults

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

- **Planner** — Decomposes MISSION.md into sprint contracts (what to do, not how)
- **Negotiator** — Refines contracts into precise, measurable criteria before any code is written
- **Generator** — Implements sprint contract, commits code, writes result report
- **Evaluator** — Independent QA: runs real tests, checks edge cases, scores 0-1

### Four-Agent Flow

```
Planner → Negotiator → Generator ↔ Evaluator
              ↓              ↑          ↓
     Precise criteria   Implements   Verifies
     agreed before       against      against
     coding starts       negotiated   negotiated
                          criteria     criteria
```

### Contract Negotiation

Before the Generator writes any code, the Negotiator refines each sprint contract into precise, measurable criteria. Both Generator and Evaluator work against the same negotiated checklist, eliminating ambiguity and reducing wasted retry loops.

### Playwright UI Testing

Set `CHASE_PLAYWRIGHT=1` and `CHASE_APP_URL=http://localhost:8000` to enable browser-based testing. The Evaluator can navigate pages, click buttons, fill forms, and take screenshots as evidence.

### Design Scoring

When a sprint involves frontend work, the Evaluator adds a `design_score` (0-1) evaluating visual quality: color consistency, spacing rhythm, typography hierarchy, responsiveness, and polish. Final score = functional × 70% + design × 30%.

## Highlights

- **Pure Python, zero pip dependencies** — only stdlib (dataclass, json, subprocess, argparse)
- **Four-agent pattern** — Planner, Negotiator, Generator, Evaluator work independently
- **Contract negotiation** — criteria refined before coding, not after failing
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

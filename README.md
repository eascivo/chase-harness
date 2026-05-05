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

## Trust Workflow

For safer autonomous runs, enable approval gating:

```bash
CHASE_REQUIRE_APPROVAL=1 chase plan
# review .chase/plan-preview.md
chase approve
chase run
```

`chase plan` generates sprint contracts and a human-readable preview without running Generator. After each evaluated sprint, Chase writes `.chase/sprints/NN-verification.md` with the criteria, evidence, test output, score, verdict, and failure reason.

## Uninstall

```bash
rm /usr/local/bin/chase
```

## Commands

| Command | Description |
|---------|-------------|
| `chase init` | Create MISSION.md template and `.chase/` directory |
| `chase plan` | Generate sprint plan preview without code changes |
| `chase approve` | Approve the current plan so `chase run` can modify code |
| `chase run` | Start the full agent loop (auto-resumes from checkpoint) |
| `chase resume` | Alias for `run` |
| `chase status` | Show sprint progress, scores, and cost |
| `chase reset` | Clean sprints/handoffs/logs to re-plan |

## Configuration

Edit `.chase/.env` (created by `chase init`), or set environment variables:

### CLI Adapter

Choose which AI coding CLI to use. Defaults to `claude` if not set.

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_CLI` | `claude` | CLI to use: `claude`, `codex`, or `gemini` |

### LLM Provider

Each agent can use a different provider — e.g., GPT for planning, Claude for code generation. All fields are optional; if not configured, agents use the Claude Code CLI's default settings (no extra setup needed).

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_MODEL` | `""` | Default model for all agents |
| `CHASE_LLM_API_KEY` | `""` | API key (global default) |
| `CHASE_LLM_BASE_URL` | `""` | API endpoint (global default) |
| `CHASE_PLANNER_MODEL` | `""` | Planner model (overrides global) |
| `CHASE_PLANNER_API_KEY` | `""` | Planner API key (overrides global) |
| `CHASE_PLANNER_BASE_URL` | `""` | Planner endpoint (overrides global) |
| `CHASE_GENERATOR_MODEL` | `""` | Generator model (overrides global) |
| `CHASE_GENERATOR_API_KEY` | `""` | Generator API key (overrides global) |
| `CHASE_GENERATOR_BASE_URL` | `""` | Generator endpoint (overrides global) |
| `CHASE_EVALUATOR_MODEL` | `""` | Evaluator model (overrides global) |
| `CHASE_EVALUATOR_API_KEY` | `""` | Evaluator API key (overrides global) |
| `CHASE_EVALUATOR_BASE_URL` | `""` | Evaluator endpoint (overrides global) |

### Sprint & Budget

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_COST_LIMIT` | `10000.0` | Budget limit in USD |
| `CHASE_MAX_SPRINTS` | `50` | Maximum number of sprints |
| `CHASE_MAX_RETRIES` | `3` | Max retries per sprint |
| `CHASE_EVAL_THRESHOLD` | `0.7` | Pass score threshold (0-1) |
| `CHASE_STALE_LIMIT` | `3` | Consecutive no-progress limit |
| `CHASE_REQUIRE_APPROVAL` | `""` | Set to `1` to require `chase approve` before Generator modifies code |

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

Each agent runs as a fresh CLI session (`claude -p`, `codex -q`, or `gemini -p` depending on `CHASE_CLI`) with its own system prompt:

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
- **Multi-CLI support** — works with Claude Code, Codex CLI, and Gemini CLI
- **Four-agent pattern** — Planner, Negotiator, Generator, Evaluator work independently
- **Contract negotiation** — criteria refined before coding, not after failing
- **Cost tracking** — real-time budget monitoring per sprint and total
- **Resume from interruption** — picks up from the last completed sprint
- **Multi-project** — one installation serves all your projects
- **Playwright + design scoring** — browser-based UI testing with visual quality evaluation

## Requirements

- An AI coding CLI: [Claude Code](https://claude.ai/code), [Codex CLI](https://github.com/openai/codex), or [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- Python 3.9+
- Git

## License

MIT

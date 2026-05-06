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
│  Score < 0.7? → retry (up to 10×)       │
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

Approval gating is enabled by default:

```bash
chase plan
# review .chase/plan-preview.md
chase approve
chase run
```

`chase plan` generates sprint contracts and a human-readable preview without running Generator. After each evaluated sprint, Chase writes `.chase/sprints/NN-verification.md` with the criteria, evidence, test output, score, verdict, and failure reason.

For fully automatic runs, explicitly disable approval:

```bash
CHASE_REQUIRE_APPROVAL=0 chase run
```

## Ray Multi-Project Mode

Ray coordinates multiple Chase workspaces with priorities, dependencies, and concurrency. It follows the same trust workflow as single-project Chase:

```bash
chase ray init
chase ray dispatch api /path/to/api --priority 0
chase ray dispatch web /path/to/web --priority 1 --depends-on api
chase ray start
```

Unapproved projects run `chase plan` first and then move to `waiting_approval`. Review each project's `.chase/plan-preview.md`, approve it, and start Ray again:

```bash
chase ray inspect api
chase ray approve api
chase ray start
```

Approved projects run `chase run`. Ray writes the same sprint evidence files as normal Chase, including `.chase/sprints/NN-verification.md`.

Ray also syncs queue state from each project's `.chase/` directory before `ray status` and `ray start`. You can run it explicitly:

```bash
chase ray sync
```

If a project is completed outside Ray, sync updates it to `completed` when all sprint evals pass, or `needs_review` when any sprint eval is `FAIL` or `ERROR`. `needs_review` means Chase ran and found acceptance issues; `failed` is reserved for process or environment failures.

Useful review commands:

```bash
chase ray inspect api              # show plan preview and verification cards
chase ray inspect api --sprint 2   # show one sprint verification card
chase ray log api                  # show project audit timeline
chase ray approve --all-low-risk   # approve only projects whose contracts are all low risk
chase ray pause api                # pause a running project
chase ray resume api               # resume a paused project
chase ray priority api 1           # adjust project priority
chase ray remove api               # remove project from queue
```

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
| `chase ray init` | Initialize Ray multi-project workspace |
| `chase ray start` | Start Ray orchestration loop |
| `chase ray dispatch` | Add project to Ray queue |
| `chase ray approve` | Approve project for execution |
| `chase ray status` | Show all Ray projects status |
| `chase ray inspect` | View project plan and verification cards |
| `chase ray log` | Show project audit timeline |
| `chase ray sync` | Sync queue state from project `.chase/` dirs |
| `chase ray pause` | Pause a running project |
| `chase ray resume` | Resume a paused project |
| `chase ray priority` | Adjust project priority |
| `chase ray remove` | Remove project from queue |
| `chase ray stop` | Gracefully stop Ray daemon |

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
| `CHASE_MAX_RETRIES` | `10` | Max retries per sprint |
| `CHASE_EVAL_THRESHOLD` | `0.7` | Pass score threshold (0-1) |
| `CHASE_STALE_LIMIT` | `3` | Consecutive no-progress limit |
| `CHASE_REQUIRE_APPROVAL` | `"1"` | Approval is required by default. Set to `0`, `false`, `no`, or `off` for fully automatic runs |

### UI Testing

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_APP_URL` | `""` | App URL for Playwright UI testing |
| `CHASE_PLAYWRIGHT` | `""` | Set to `1` to enable browser testing |

### Computer Use (CDP)

Zero-dependency browser automation via Chrome DevTools Protocol. Works with Brave or Chrome.

| Variable | Default | Description |
|----------|---------|-------------|
| `CHASE_COMPUTER_USE` | `""` | Set to `1` to enable CDP browser automation |
| `CHASE_CDP_PORT` | `9222` | Chrome remote debugging port |

When enabled, the Evaluator can launch a browser, navigate pages, click elements, type text, take screenshots, and run JavaScript — all without any pip dependencies.

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

### Computer Use (CDP)

Set `CHASE_COMPUTER_USE=1` to enable zero-dependency browser automation via Chrome DevTools Protocol. Chase will launch Brave or Chrome with remote debugging, connect via WebSocket, and can navigate, click, type, screenshot, and evaluate JavaScript. This is a pure-stdlib alternative to Playwright — no pip install required.

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
- **Computer Use (CDP)** — zero-dependency browser automation via Chrome DevTools Protocol

## Requirements

- An AI coding CLI: [Claude Code](https://claude.ai/code), [Codex CLI](https://github.com/openai/codex), or [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- Python 3.9+
- Git

## License

MIT

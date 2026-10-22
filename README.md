# Chase

Multi-agent autonomous development for Claude Code, Codex CLI, and Gemini CLI. Chase turns a goal into sprint contracts, implements them, and verifies the results.

**[English](README.md)** | [中文文档](README_CN.md)

## How It Works

```text
MISSION.md → Planner → Negotiator → Generator ↔ Evaluator → Final Review
```

The Planner breaks down your goal; the Negotiator refines acceptance criteria; the Generator implements them; the Evaluator checks tests and evidence. Chase includes project context, git checkpoints and rollback, retries, adaptive re-planning, cost tracking, and resume support.

## Quick Start

Requires **Python 3.9+**, **Git**, and an AI coding CLI: [Claude Code](https://claude.ai/code), [Codex CLI](https://github.com/openai/codex), or [Gemini CLI](https://github.com/google-gemini/gemini-cli). Chase has no runtime pip dependencies.

```bash
git clone https://github.com/eascivo/chase-harness.git
cd chase-harness
pip install -e .

cd /path/to/your-project
chase init       # creates MISSION.md and .chase/.env
# Edit MISSION.md with your goal and measurable acceptance criteria.
# Edit .chase/.env if using a CLI other than the default Claude Code.
chase doctor
chase plan
# Review .chase/plan-preview.md before approving.
chase approve
chase run
chase status
```

Alternatively, use `bin/chase` directly without installing. To uninstall the package, run `pip uninstall chase`.

**Approval is required by default.** `chase plan` creates contracts and a preview without running the Generator. Each evaluated sprint produces `.chase/sprints/NN-verification.md` with criteria, evidence, test output, score, verdict, and failure reason.

To explicitly allow fully automatic runs without approval:

```bash
CHASE_REQUIRE_APPROVAL=0 chase run
```

## Writing a Mission

Fill in the `MISSION.md` template created by `chase init`:

- **Goal / Context:** desired outcome, project background, tech stack, and relevant files.
- **Acceptance Criteria (MUST):** specific, testable requirements; these get priority.
- **Nice to Have:** optional, lower-priority enhancements.
- **Performance / UI Requirements:** optional targets for verification.

## Commands

| Command | Purpose |
|---------|---------|
| `chase doctor` | Check setup |
| `chase init` | Create mission template and workspace state |
| `chase plan` / `chase approve` | Preview and approve a plan |
| `chase run` / `chase resume` | Execute the plan or resume from saved state |
| `chase run --watchdog` | Restart after crashes (up to 5 times per 60 seconds) |
| `chase status` | Show sprint progress, scores, and cost |
| `chase reset` | Delete sprint state to start a new plan |

Use `chase --help` or `chase <command> --help` for all commands and options.

## Configuration

Edit `.chase/.env`, or set environment variables (which take precedence). Custom LLM settings are optional; unset values use the selected CLI's defaults. See [configuration definitions](chase/config.py) for the full list and [.env.example](.env.example) for provider examples.

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHASE_CLI` | `claude` | `claude`, `codex`, or `gemini` |
| `CHASE_MODEL` | unset | Default model for all agents |
| `CHASE_LLM_API_KEY` / `CHASE_LLM_BASE_URL` | unset | Custom provider credentials and endpoint |
| `CHASE_COST_LIMIT` | `10000.0` | Budget limit in USD |
| `CHASE_MAX_SPRINTS` / `CHASE_MAX_RETRIES` | `50` / `10` | Sprint and retry limits |
| `CHASE_EVAL_THRESHOLD` / `CHASE_STALE_LIMIT` | `0.7` / `3` | Pass score and no-progress limit |
| `CHASE_REQUIRE_APPROVAL` | `1` | Set to `0`, `false`, `no`, or `off` to disable approval |

Override provider settings per agent with `CHASE_{PLANNER,GENERATOR,EVALUATOR}_{MODEL,API_KEY,BASE_URL}` (for example, `CHASE_PLANNER_MODEL`).

For UI testing, set `CHASE_PLAYWRIGHT=1` and `CHASE_APP_URL`. Alternatively, enable dependency-free Chrome/Brave automation with `CHASE_COMPUTER_USE=1` and optionally `CHASE_CDP_PORT` (default `9222`). Frontend evaluations can include a design score.

## Ray: Multiple Projects

Ray coordinates initialized Chase workspaces with priorities, dependencies, and concurrency:

```bash
chase ray init
chase ray dispatch api /path/to/api --priority 0
chase ray dispatch web /path/to/web --priority 1 --depends-on api
chase ray start
```

Unapproved projects generate a plan and enter `waiting_approval`. Review and approve each project, then restart Ray:

```bash
chase ray inspect api             # plan preview and verification cards
chase ray approve api
chase ray start
chase ray status
```

Approved projects run `chase run` and produce the same sprint evidence as single-project runs. Ray syncs state from each project's `.chase/` before `status` and `start`; use `chase ray sync` to sync manually. Completed runs become `completed` when all sprint evaluations pass, or `needs_review` for acceptance failures; `failed` denotes process or environment failures.

Other controls include `inspect <name> --sprint <n>`, `log <name>`, `pause <name>`, `resume <name>`, `priority <name> <level>`, `remove <name>`, and `stop`. `chase ray approve --all-low-risk` approves only waiting projects whose contracts are all low risk. See `chase ray --help` for details.

## License

[MIT](LICENSE)

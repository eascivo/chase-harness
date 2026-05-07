# Chase

Claude Code 多 Agent 自主开发工具。四 Agent Planner-Negotiator-Generator-Evaluator 模式：自动拆解目标、协商验收标准、逐个实现并验证。

[English](README.md) | **[中文文档](README_CN.md)**

## 工作原理

```
MISSION.md（你的目标）
      ↓
┌─────────────────────────────────────────┐
│  Planner                                │
│  分析项目 + 拆解目标为 sprint 合约       │
│  → sprints/01-contract.md               │
├─────────────────────────────────────────┤
│  Negotiator（逐 sprint）                │
│  精炼合约为具体、可测量的验收清单         │
│  → sprints/01-negotiated.md             │
├─────────────────────────────────────────┤
│  Generator（含检查点/回滚）              │
│  按协商后的清单实现功能                   │
│  → git commit → sprints/01-result.md    │
├─────────────────────────────────────────┤
│  Evaluator                              │
│  按协商后的清单独立验收                   │
│  → sprints/01-eval.json                 │
│  分数 < 0.7？→ 重试（最多 10 次）         │
│  连续 3 次停滞？→ 重新规划               │
├─────────────────────────────────────────┤
│  Final Review                           │
│  项目级最终验收                          │
│  → sprints/final-review.json            │
└─────────────────────────────────────────┘
```

## 快速开始

```bash
# 安装
git clone https://github.com/eascivo/chase-harness.git ~/.chase-harness
ln -sf ~/.chase-harness/bin/chase /usr/local/bin/chase

# 在任意项目中使用
cd your-project
chase init       # 创建 MISSION.md 模板 + .chase/
# ... 编辑 MISSION.md 写入你的目标 ...
chase run        # 启动自主循环
chase status     # 随时查看进度
```

## 信任工作流

审批门禁默认开启：

```bash
chase plan
# 查看 .chase/plan-preview.md
chase approve
chase run
```

`chase plan` 只生成 sprint 合约和可读预览，不会运行 Generator。每个 sprint 验收后，Chase 会写入 `.chase/sprints/NN-verification.md`，包含验收条件、证据、测试输出、分数、结论和失败原因。

如果你明确希望完全自动运行，可以关闭审批：

```bash
CHASE_REQUIRE_APPROVAL=0 chase run
```

## Ray 多项目模式

Ray 用来按优先级、依赖关系和并发数编排多个 Chase 工作区。它复用单项目 Chase 的信任工作流：

```bash
chase ray init
chase ray dispatch api /path/to/api --priority 0
chase ray dispatch web /path/to/web --priority 1 --depends-on api
chase ray start
```

未审批项目会先运行 `chase plan`，然后进入 `waiting_approval`。查看每个项目的 `.chase/plan-preview.md` 后，逐个审批并再次启动 Ray：

```bash
chase ray inspect api
chase ray approve api
chase ray start
```

已审批项目会运行 `chase run`。Ray 会保留普通 Chase 的 sprint 证据文件，包括 `.chase/sprints/NN-verification.md`。

Ray 会在 `ray status` 和 `ray start` 前自动从各项目的 `.chase/` 目录同步队列状态。也可以手动执行：

```bash
chase ray sync
```

如果某个项目在 Ray 外部完成，sync 会在所有 sprint eval 都通过时标记为 `completed`；如果存在 `FAIL` 或 `ERROR`，则标记为 `needs_review`。`needs_review` 表示 Chase 正常运行但验收未全过；`failed` 保留给进程或环境层失败。

常用审阅命令：

```bash
chase ray inspect api              # 查看计划预览和验收卡
chase ray inspect api --sprint 2   # 只查看某个 sprint 的验收卡
chase ray log api                  # 查看项目审计时间线
chase ray approve --all-low-risk   # 只自动审批全部 sprint 均为低风险的项目
chase ray pause api                # 暂停运行中的项目
chase ray resume api               # 恢复暂停的项目
chase ray priority api 1           # 调整项目优先级
chase ray remove api               # 从队列移除项目
```

## 卸载

```bash
rm /usr/local/bin/chase
```

## 命令

| 命令 | 说明 |
|------|------|
| `chase init` | 创建 MISSION.md 模板和 `.chase/` 目录 |
| `chase plan` | 生成 sprint 计划预览，不改代码 |
| `chase approve` | 审批当前计划，允许 `chase run` 修改代码 |
| `chase run` | 启动四 Agent 循环（自动从断点恢复） |
| `chase run --watchdog` | 崩溃自动重启（60s 内最多 5 次） |
| `chase resume` | `run` 的别名 |
| `chase status` | 显示 sprint 进度、评分和成本 |
| `chase reset` | 清理 sprints/handoffs/logs，重新规划 |
| `chase ray init` | 初始化 Ray 多项目工作区 |
| `chase ray start` | 启动 Ray 编排循环 |
| `chase ray dispatch` | 向 Ray 队列添加项目 |
| `chase ray approve` | 审批项目，允许执行 |
| `chase ray status` | 显示所有 Ray 项目状态 |
| `chase ray inspect` | 查看项目计划和验收证据 |
| `chase ray log` | 显示项目审计时间线 |
| `chase ray sync` | 从项目 `.chase/` 目录同步队列状态 |
| `chase ray pause` | 暂停运行中的项目 |
| `chase ray resume` | 恢复暂停的项目 |
| `chase ray priority` | 调整项目优先级 |
| `chase ray remove` | 从队列移除项目 |
| `chase ray stop` | 优雅停机 |

## 配置

编辑 `.chase/.env`（`chase init` 自动创建），或设置环境变量：

### CLI 适配器

选择使用哪个 AI 编码 CLI，默认 `claude`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_CLI` | `claude` | CLI 选择：`claude`、`codex` 或 `gemini` |

### LLM 提供商

每个 Agent 可以用不同的提供商——比如 GPT 规划，Claude 执行。所有配置项均可选，不配置则使用 Claude Code CLI 自身默认设置，无需额外操作。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_MODEL` | `""` | 全局默认模型 |
| `CHASE_LLM_API_KEY` | `""` | API Key（全局默认） |
| `CHASE_LLM_BASE_URL` | `""` | API 地址（全局默认） |
| `CHASE_PLANNER_MODEL` | `""` | Planner 模型（覆盖全局） |
| `CHASE_PLANNER_API_KEY` | `""` | Planner API Key（覆盖全局） |
| `CHASE_PLANNER_BASE_URL` | `""` | Planner 端点（覆盖全局） |
| `CHASE_GENERATOR_MODEL` | `""` | Generator 模型（覆盖全局） |
| `CHASE_GENERATOR_API_KEY` | `""` | Generator API Key（覆盖全局） |
| `CHASE_GENERATOR_BASE_URL` | `""` | Generator 端点（覆盖全局） |
| `CHASE_EVALUATOR_MODEL` | `""` | Evaluator 模型（覆盖全局） |
| `CHASE_EVALUATOR_API_KEY` | `""` | Evaluator API Key（覆盖全局） |
| `CHASE_EVALUATOR_BASE_URL` | `""` | Evaluator 端点（覆盖全局） |

### Sprint 与预算

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_COST_LIMIT` | `10000.0` | 预算上限（USD） |
| `CHASE_MAX_SPRINTS` | `50` | 最大 sprint 数 |
| `CHASE_MAX_RETRIES` | `10` | 每 sprint 最大重试次数 |
| `CHASE_EVAL_THRESHOLD` | `0.7` | 通过分数阈值（0-1） |
| `CHASE_STALE_LIMIT` | `3` | 连续无进展停止阈值 |
| `CHASE_REQUIRE_APPROVAL` | `"1"` | 默认要求审批。设为 `0`、`false`、`no` 或 `off` 后完全自动运行 |

### UI 测试

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_APP_URL` | `""` | 应用 URL（Playwright） |
| `CHASE_PLAYWRIGHT` | `""` | 设为 `1` 启用浏览器测试 |

### Computer Use（CDP）

零依赖浏览器自动化，通过 Chrome DevTools Protocol 实现。支持 Brave 和 Chrome。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_COMPUTER_USE` | `""` | 设为 `1` 启用 CDP 浏览器自动化 |
| `CHASE_CDP_PORT` | `9222` | Chrome 远程调试端口 |

启用后，Evaluator 可以启动浏览器、导航页面、点击元素、输入文本、截图和执行 JavaScript——无需任何 pip 依赖。

优先级：环境变量 > `.chase/.env` > 默认值

## MISSION.md 格式

```markdown
# 目标

描述你想完成什么。

# 上下文

项目背景、技术栈、相关文件。

# 验收标准 (MUST)

- [ ] 具体、可测试的条件
- [ ] 另一个条件

# Nice to Have

- [ ] 可选增强
```

验收标准越具体、越可测量，效果越好。MUST 条件生成高优先级 sprint，Nice to Have 生成低优先级 sprint。

## 架构

每个 Agent 以独立的 CLI 会话运行（`claude -p`、`codex -q` 或 `gemini -p`，取决于 `CHASE_CLI` 设置），拥有各自的系统提示词：

- **Planner** — 将 MISSION.md 拆解为 sprint 合约（只定义"做什么"，不指定"怎么做"）
- **Negotiator** — 在编码前将合约精炼为具体、可测量的验收清单
- **Generator** — 按协商后的清单实现功能，提交代码，输出结果报告
- **Evaluator** — 独立 QA：运行真实测试，检查边界情况，打分 0-1

### 四 Agent 流程

```
MISSION.md → Planner → Negotiator → Generator ↔ Evaluator
               ↓            ↓            ↑           ↓
        Sprint 合约       精确验收     按清单实现   按清单验收
        含风险和依赖       先协商再编码
```

### 项目上下文注入

所有 Agent 自动获得丰富的项目上下文，无需手动配置：

- **CLAUDE.md** — 项目指令、技术栈、编码规范
- **项目结构** — 目录树（自动排除 .git/.venv/node_modules 等）
- **最近提交** — 最近 15 个 commit，了解当前状态
- **项目配置** — pyproject.toml / package.json 依赖信息

这意味着 Planner 在规划前就理解你的代码库，Generator 遵循你的项目规范，Evaluator 知道"正确"应该是什么样子。

### Generator 检查点与回滚

每次 Generator 运行前，Chase 会创建一个 git 检查点。如果 Generator 失败或产出质量不达标，工作树会回滚到干净状态再重试，不会积累半成品代码。

### Watchdog 模式

```bash
chase run --watchdog
```

看门狗监控 orchestrator 进程，崩溃（OOM、网络中断、机器休眠）后 5 秒内自动重启。60 秒内连续崩溃 5 次则停止。

### 自适应重新规划

Sprint 持续失败时，Chase 会自适应而不是放弃：

- **连续 3 次失败** → 自动触发重新规划（移除失败合约，带进度上下文重跑 Planner）
- **停滞检测** → 触发重新规划而非停止
- **拓扑排序** → 按 depends_on 依赖顺序执行 sprint

### 项目级终验

所有 sprint 完成后，Chase 运行项目级终验：

1. 逐条验证 MISSION.md 验收标准
2. 跑完整测试套件和 lint
3. 检查 TODO/FIXME/HACK 注释
4. 输出 `final-review.json`，包含总体结论和任务覆盖率评分

### 结构化 MISSION.md

`chase init` 模板现在包含结构化章节：

```markdown
# Acceptance Criteria (MUST)     → 高优先级 sprint
# Nice to Have                    → 低优先级 sprint
# Performance Requirements        → 性能验证 sprint
# UI Requirements                 → UI 验证 sprint
```

Planner 会将每个章节映射到相应优先级的 sprint。

### 合约协商（Contract Negotiation）

Generator 写代码之前，Negotiator 先把每个 sprint 合约精炼为具体、可测量的验收清单。Generator 和 Evaluator 共用同一份清单，消除需求歧义，减少无效重试。

### Playwright UI 测试

设置 `CHASE_PLAYWRIGHT=1` 和 `CHASE_APP_URL=http://localhost:8000` 即可启用浏览器自动化测试。Evaluator 可以打开页面、点击按钮、填写表单、截图取证。

### Computer Use（CDP）

设置 `CHASE_COMPUTER_USE=1` 启用零依赖浏览器自动化，通过 Chrome DevTools Protocol 实现。Chase 会启动 Brave 或 Chrome 的远程调试模式，通过 WebSocket 连接，可以导航、点击、输入、截图和执行 JavaScript。这是 Playwright 的纯标准库替代方案——无需 pip install。

### 设计评分（Design Scoring）

当 sprint 涉及前端工作时，Evaluator 会额外给出 `design_score`（0-1），评估配色一致性、间距节奏、排版层级、响应式布局等视觉质量。最终得分 = 功能分 × 70% + 设计分 × 30%。

## 核心优势

- **纯 Python，零 pip 依赖** — 仅用 stdlib（dataclass、json、subprocess、argparse）
- **多 CLI 支持** — 支持 Claude Code、Codex CLI、Gemini CLI
- **四 Agent 模式** — Planner、Negotiator、Generator、Evaluator 独立工作
- **项目上下文注入** — 所有 Agent 自动获得 CLAUDE.md、项目结构、最近提交
- **合约协商** — 编码前精炼验收标准，不是失败后才补
- **检查点与回滚** — 每次 Generator 重试保证从干净状态开始
- **自适应重新规划** — Sprint 持续失败时自动重新规划
- **Watchdog 模式** — 崩溃后自动重启 orchestrator
- **项目级终验** — 所有 sprint 完成后对照 MISSION.md 做最终验收
- **成本追踪** — 实时监控每个 sprint 和总预算消耗
- **断点恢复** — 从上次完成的 sprint 继续
- **多项目编排** — Ray 模式支持资源冲突检测和跨项目 artifact 传递
- **Playwright + 设计评分** — 浏览器自动化测试 + 视觉质量评估
- **Computer Use（CDP）** — 零依赖浏览器自动化，通过 Chrome DevTools Protocol 实现

## 依赖

- AI 编码 CLI：[Claude Code](https://claude.ai/code)、[Codex CLI](https://github.com/openai/codex) 或 [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- Python 3.9+
- Git

## 许可证

MIT

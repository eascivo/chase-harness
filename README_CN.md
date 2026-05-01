# Chase

Claude Code 多 Agent 自主开发工具。四 Agent Planner-Negotiator-Generator-Evaluator 模式：自动拆解目标、协商验收标准、逐个实现并验证。

[English](README.md) | **[中文文档](README_CN.md)**

## 工作原理

```
MISSION.md（你的目标）
      ↓
┌─────────────────────────────────────────┐
│  Planner                                │
│  将目标拆解为 sprint 合约列表            │
│  → sprints/01-contract.md               │
├─────────────────────────────────────────┤
│  Negotiator（逐 sprint）                │
│  精炼合约为具体、可测量的验收清单         │
│  → sprints/01-negotiated.md             │
├─────────────────────────────────────────┤
│  Generator                              │
│  按协商后的清单实现功能                   │
│  → git commit → sprints/01-result.md    │
├─────────────────────────────────────────┤
│  Evaluator                              │
│  按协商后的清单独立验收                   │
│  → sprints/01-eval.json                 │
│  分数 < 0.7？→ 重试（最多 3 次）          │
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

## 卸载

```bash
rm /usr/local/bin/chase
```

## 命令

| 命令 | 说明 |
|------|------|
| `chase init` | 创建 MISSION.md 模板和 `.chase/` 目录 |
| `chase run` | 启动四 Agent 循环（自动从断点恢复） |
| `chase resume` | `run` 的别名 |
| `chase status` | 显示 sprint 进度、评分和成本 |
| `chase reset` | 清理 sprints/handoffs/logs，重新规划 |

## 配置

编辑 `.chase/.env`（`chase init` 自动创建），或设置环境变量：

### LLM 提供商

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_MODEL` | `""` | 全局默认模型 |
| `CHASE_PLANNER_MODEL` | `""` | Planner 模型（覆盖 `CHASE_MODEL`） |
| `CHASE_GENERATOR_MODEL` | `""` | Generator 模型（覆盖 `CHASE_MODEL`） |
| `CHASE_EVALUATOR_MODEL` | `""` | Evaluator 模型（覆盖 `CHASE_MODEL`） |
| `CHASE_LLM_API_KEY` | `""` | API Key |
| `CHASE_LLM_BASE_URL` | `""` | 自定义 API 地址（如 `https://open.bigmodel.cn/api/paas/v4`） |

### Sprint 与预算

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_COST_LIMIT` | `10000.0` | 预算上限（USD） |
| `CHASE_MAX_SPRINTS` | `50` | 最大 sprint 数 |
| `CHASE_MAX_RETRIES` | `3` | 每 sprint 最大重试次数 |
| `CHASE_EVAL_THRESHOLD` | `0.7` | 通过分数阈值（0-1） |
| `CHASE_STALE_LIMIT` | `3` | 连续无进展停止阈值 |

### UI 测试

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHASE_APP_URL` | `""` | 应用 URL（Playwright） |
| `CHASE_PLAYWRIGHT` | `""` | 设为 `1` 启用浏览器测试 |

优先级：环境变量 > `.chase/.env` > 默认值

## MISSION.md 格式

```markdown
# 目标

描述你想完成什么。

# 上下文

项目背景、技术栈、相关文件。

# 验收标准

1. 具体、可测试的条件
2. 另一个条件
```

验收标准越具体、越可测量，效果越好。

## 架构

每个 Agent 以独立的 `claude -p` 会话运行，拥有各自的系统提示词：

- **Planner** — 将 MISSION.md 拆解为 sprint 合约（只定义"做什么"，不指定"怎么做"）
- **Negotiator** — 在编码前将合约精炼为具体、可测量的验收清单
- **Generator** — 按协商后的清单实现功能，提交代码，输出结果报告
- **Evaluator** — 独立 QA：运行真实测试，检查边界情况，打分 0-1

### 四 Agent 流程

```
Planner → Negotiator → Generator ↔ Evaluator
              ↓              ↑          ↓
        精确验收清单       按清单实现   按清单验收
        先协商再编码
```

### 合约协商（Contract Negotiation）

Generator 写代码之前，Negotiator 先把每个 sprint 合约精炼为具体、可测量的验收清单。Generator 和 Evaluator 共用同一份清单，消除需求歧义，减少无效重试。

### Playwright UI 测试

设置 `CHASE_PLAYWRIGHT=1` 和 `CHASE_APP_URL=http://localhost:8000` 即可启用浏览器自动化测试。Evaluator 可以打开页面、点击按钮、填写表单、截图取证。

### 设计评分（Design Scoring）

当 sprint 涉及前端工作时，Evaluator 会额外给出 `design_score`（0-1），评估配色一致性、间距节奏、排版层级、响应式布局等视觉质量。最终得分 = 功能分 × 70% + 设计分 × 30%。

## 核心优势

- **纯 Python，零 pip 依赖** — 仅用 stdlib（dataclass、json、subprocess、argparse）
- **四 Agent 模式** — Planner、Negotiator、Generator、Evaluator 独立工作
- **合约协商** — 编码前精炼验收标准，不是失败后才补
- **成本追踪** — 实时监控每个 sprint 和总预算消耗
- **断点恢复** — 从上次完成的 sprint 继续
- **多项目复用** — 一份安装服务所有项目
- **Playwright + 设计评分** — 浏览器自动化测试 + 视觉质量评估

## 依赖

- [Claude Code CLI](https://claude.ai/code)
- Python 3.9+
- Git

## 许可证

MIT

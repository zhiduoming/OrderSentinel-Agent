# OrderSentinel-Agent

面向订单服务的 AI 故障调查与辅助处置系统。通过 MCP（Model Context Protocol，模型上下文协议）查询指标、日志、调用链和配置变更，生成带证据引用的调查报告，并在人工审批后执行固定的本地处置动作、验证恢复结果。

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 核心功能

- **故障调查**：从告警建立调查会话，结合多类观测分析候选原因、反证和缺失信息。
- **证据报告**：为观测分配证据 ID，保存原始返回结果；报告只能引用本次调查实际读取过的证据。
- **报告校验**：检查引用归属和证据覆盖；主要原因判断至少需要两类当前观测支持，历史案例仅作为参考。
- **人工审批**：Agent 提出处置建议，用户在浏览器复核后批准或拒绝；模型工具不提供审批或执行入口。
- **本地恢复验证**：在隔离的订单、库存与 PostgreSQL 实验环境中注入故障，审批后恢复配置并重新压测。
- **调查记录**：使用 SQLite 保存工具观测、报告、提案、审批决定和执行结果，支持历史查看与重复操作核对。

## 工作流程

```text
选择告警 / 创建本地故障实验
    ↓
Agent 通过 MCP 查询指标、日志、调用链、变更和历史记录
    ↓
生成候选原因、支持证据、反证与待补充信息
    ↓
校验证据引用并保存调查报告
    ↓
建立固定动作提案 → 浏览器人工审批
    ↓
执行本地处置 / 合成模拟 → 保存验证结果
```

### 故障场景

| 模式 | 场景 | 处置方式 |
| --- | --- | --- |
| 合成案例 `INC-001` | 订单服务连接池配置回归 | 按预设规则模拟连接池恢复 |
| 合成案例 `INC-002` | 下游库存服务并发下降，伴随无关发布 | 按预设规则模拟库存并发恢复 |
| 合成案例 `INC-003` | 日志或链路等关键观测缺失 | 建立补证任务 |
| 本地实验 `LIVE-*` | 实际降低订单连接池或库存处理并发 | 恢复本地服务配置并重新发送请求验证 |

合成案例无需启动业务实验环境。本地实验运行真实 HTTP 请求与数据库操作，观测来自实验快照，恢复结果来自审批后的独立测量。

## 系统架构

| 模块 | 技术与职责 |
| --- | --- |
| 浏览器界面 | React、TypeScript、Vite；调查入口、会话、证据报告与审批面板 |
| Agent 服务 | Bun、TypeScript、WebSocket RPC；会话、模型调用与工具调度 |
| 故障调查工具 | Python、MCP SDK；11 个工具提供观测查询、报告提交和处置提案 |
| 业务与审批逻辑 | Python、SQLite；证据校验、调查账本、提案状态与执行记录 |
| 本地业务实验 | FastAPI、HTTPX、Psycopg、PostgreSQL 17；订单、库存、故障注入与恢复验证 |

本地业务请求链：

```text
POST /orders
    → 获取数据库连接，创建 pending 订单
    → POST /reserve 调用库存服务
    → 库存排队、预留和扣减
    → 更新订单状态并返回结果
```

## 快速开始

### 环境要求

- Bun
- Python 3.11 或以上版本
- uv
- 可用的模型 API Key
- Docker Compose（仅本地业务实验需要，可使用支持 Docker 的 OrbStack 环境）

以下命令适用于 macOS / Linux，在项目根目录执行。

### 1. 获取项目并安装依赖

```bash
git clone https://github.com/zhiduoming/OrderSentinel-Agent.git
cd OrderSentinel-Agent

bun install --frozen-lockfile --ignore-scripts
uv sync --project services/incident-tools
bun run incident:setup
```

初始化脚本生成独立工作区、MCP 配置和本地登录口令，已有配置不会被覆盖。

### 2. 配置模型

在项目根目录创建 `.env.local`，按 `.env.incident.example` 填写所需密钥。可只配置其中一个：

```dotenv
DEEPSEEK_API_KEY=your-deepseek-api-key
BAILIAN_API_KEY=
INCIDENT_PORT=9100
```

启动脚本会将密钥导入本地加密凭据存储，并在默认连接未配置凭据时选择已配置的连接。模型、接口地址和连接配置位于 `.incident/config/config.json`，请按所用服务调整。

`.env.local` 和 `.incident/` 已加入 Git 忽略规则。

### 3. 构建并启动

```bash
bun run incident:build
bun run incident:start
```

打开 <http://127.0.0.1:9100>，使用 `.incident/server-token` 文件中的口令登录。

在“故障调查”中选择案例和模型开始调查，然后进入“报告与审批”查看证据与处置提案。

### 4. 启动本地业务实验（可选）

先启动 Docker，再在另一个终端运行：

```bash
bun run incident:lab
```

回到浏览器，创建连接池或库存并发故障实验。系统采集正常基线与故障观测，Agent 完成调查后，用户可以审批恢复动作并查看前后指标。

| 服务 | 默认地址 / 端口 |
| --- | --- |
| 浏览器入口 | `127.0.0.1:9100` |
| 订单服务 | `127.0.0.1:19101` |
| 库存服务 | `127.0.0.1:19102` |
| 实验控制器 | `127.0.0.1:19103` |
| PostgreSQL | `127.0.0.1:15432` |

每轮实验发送 120 次请求，并发上限为 24。创建新实验会使旧实验的未执行提案失效；调查期间请保持当前实验不变。按 `Ctrl+C` 停止实验脚本时，Python 服务停止，数据库容器和持久数据保留。

详细操作见 [本地业务实验说明](docs/incident-lab.md)。

## 测试与评估

```bash
# Python 业务、证据校验与处置测试
bun run incident:test

# MCP stdio 握手与工具调用
bun run incident:test:mcp

# 浏览器接口与审批链路集成测试
bun run incident:test:integration

# 浏览器界面类型检查
bun run webui:typecheck

# 对本地已保存的调查报告进行结构评估
bun run incident:evaluate
```

真实业务验收需要先启动实验服务，再运行：

```bash
bun run incident:test:lab
```

该命令会创建新实验并使旧实验提案失效。报告结构评估检查判定结果、关键证据覆盖和缺失项；模型输出仍需结合原始观测复核。

## 目录结构

```text
apps/webui/src/incident/           # 故障调查与报告审批界面
packages/server/src/incident-api.ts # 调查与审批 HTTP 接口
packages/shared/                  # Agent、会话、模型连接与凭据管理
services/incident-tools/
  server.py                       # MCP 工具入口
  admin.py                        # 浏览器管理操作桥
  incident_tools/                 # 证据、报告、提案与处置逻辑
  lab/                            # 订单、库存与实验控制器
  fixtures/                       # 合成故障案例
  tests/                          # 测试与验收脚本
incident/
  skills/incident-investigator/    # 故障调查 Skill
  eval/                           # 评估配置与验收记录
lab/compose.yaml                  # PostgreSQL 实验环境
scripts/incident-*                # 初始化、启动、构建与评估脚本
docs/                             # 设计、实验与实现状态说明
output/playwright/                # 浏览器验收截图
```

## 当前范围

当前面向本地单用户开发与故障实验，支持合成案例和隔离业务服务的实际恢复验证。处置仅限固定动作，不接受模型生成的任意命令。尚未接入生产环境或完整的 Prometheus、Loki、Jaeger 采集平台。

本地实验的错误率和延迟随机器与运行状态变化；合成模拟指标与本地实测指标分别记录。

## 项目文档

- [实现设计](docs/incident-design.md)
- [本地业务实验与调用链](docs/incident-lab.md)
- [实现状态与验收记录](docs/incident-progress.md)
- [模型浏览器验收记录](incident/eval/live-browser-acceptance.json)
- [本地业务验收记录](incident/eval/lab-acceptance.json)

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。第三方软件归属与声明见 [NOTICE](NOTICE)，相关商标说明见 [TRADEMARK.md](TRADEMARK.md)。

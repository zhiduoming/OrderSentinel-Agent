## 本地真实业务实验（新增）

现已加入订单服务、库存服务、PostgreSQL、实际压测、日志/链路采集和审批后真实配置恢复。
先运行 `bun run incident:lab`，再运行 `bun run incident:start`。
浏览器“故障调查”中创建 LIVE 实验；原有 INC 案例继续使用合成数据。
详细操作与边界见 [本地业务实验说明](../docs/incident-lab.md)。
下方旧版合成案例操作仍适用于 INC，不能套用于 LIVE。

# Incident Agent 开发与演示入口

本项目在 Craft Agents 0.11.4 上新增订单服务故障调查、证据报告和人工审批本地模拟。根目录 README 为原项目文档；本文件是改造项目入口。

## 已准备的本机环境

项目目录：`/Users/chen/Code/Incident-Agent`。Python 工具环境已安装；浏览器与 Agent 子进程已构建。Bun 临时路径是 `/tmp/incident-bun/node_modules/.bin`，系统清理临时目录后需要重新准备 Bun。

当前环境启动：

```bash
cd /Users/chen/Code/Incident-Agent
export PATH="/tmp/incident-bun/node_modules/.bin:$PATH"
bun run incident:start
```

打开 http://127.0.0.1:9100；登录密码在 `.incident/server-token`，不在日志打印或提交。当前开发服务可能已经运行，不要重复启动占用同一端口。

其他环境首次启动（需 Bun 和 uv）：

```bash
bun install --frozen-lockfile --ignore-scripts
python3 scripts/incident-bootstrap.py
uv sync --project services/incident-tools
bun run incident:build
bun run incident:start
```

bootstrap 不覆盖已有用户配置。浏览器入口使用本地 Python MCP，不需要启动 Electron。

## 演示顺序

1. 点击“故障调查”，选案例与已配置模型，开始调查。
2. 在会话里观察实际工具查询、候选原因、反证和保存报告。
3. 点击“报告与审批”，选对应调查，展开证据 ID 查看真实返回过的原始观测。
4. 查看模型建立的待审批提案。如果旧调查尚无提案，可复核报告后选择固定动作保存提案。
5. 批准并运行本地模拟，或拒绝提案。批准会保存模拟前后指标；拒绝不运行。重复批准返回相同结果。
6. INC-003 只能建立补证任务，不能批准恢复动作。其缺失观测不会被伪造。

已有真实模型调查记录保存在 `.incident/incident-ledger.sqlite3` 与工作区 sessions 中；首次配置失败的会话记录也保留，没有删除。自动化验收执行过本地模拟批准和拒绝，这不是用户本人生产操作。

## 密钥

`.env.local` 在本机保存，Git ignore。现有文件不要覆盖；按 `.env.incident.example` 补充所需变量即可，也可用 Craft 模型设置界面。启动脚本将 Key 导入 Craft 既有加密存储，只记录连接名。真实验收使用过 DeepSeek 官方 deepseek-flash 和百炼 qwen3.7-plus；qwen3.8-flash 尚未验证。

百炼 Token Plan 使用范围请参考需求合同和官方说明；协议兼容不代表任意应用都符合套餐范围。可使用 DeepSeek 官方按量 API 或普通百炼按量 API。

## 验证命令

```bash
bun run incident:test
bun run incident:test:mcp
bun run incident:test:integration
bun run webui:typecheck
bun run incident:evaluate
```

Python 19项测试；TypeScript 集成6项测试；MCP stdio 验证11工具。以上自动化测试不调用模型。`incident:evaluate` 检查实际保存报告的预期 verdict、关键证据覆盖和缺失项，不自动判断完整因果语义。

真实模型与浏览器验收详见 `docs/incident-progress.md`；截图在 `output/playwright/`。三个案例是合成数据，处置结果是规则模拟，不是生产经历或真实性能测量。

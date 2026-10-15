# 本地业务故障实验使用与调用链

这是一套实际运行、可复现的个人业务实验，不是企业生产系统。
INC-001～003 保留合成案例；LIVE- 加完整随机 ID 是实际本地实验，不能混写。

## 启动

在项目根目录开两个终端：

```bash
cd /Users/chen/Code/Incident-Agent
export PATH=/tmp/incident-bun/node_modules/.bin:$PATH
bun run incident:lab
```

第二个终端：

```bash
cd /Users/chen/Code/Incident-Agent
export PATH=/tmp/incident-bun/node_modules/.bin:$PATH
bun run incident:start
```

前提：OrbStack/Docker 正常运行；Python 环境已在 `services/incident-tools` 执行 `uv sync`，浏览器产物已 `bun run incident:build`。
当前 Bun 位于临时目录；重启或清理临时目录后如不存在，需安装 Bun 或重新准备运行时。
浏览器 http://127.0.0.1:9100；登录口令保存在 `.incident/server-token`，不要写入仓库。

实验启动脚本运行独立的 Compose 项目 `incident-agent-lab`，PostgreSQL 17 容器绑定 127.0.0.1:15432。
订单服务 19101、库存服务 19102、实验控制器 19103 都只绑定回环地址。
三个 Python 服务前台运行，Ctrl+C 停止它们；数据库容器继续运行，数据库、快照和账本保留。
如需只停止数据库，可运行 `docker stop incident-agent-lab-postgres-1`，保留容器和数据卷。不要使用 down -v、prune 或删除数据目录。

## 浏览器演示

1. 打开“故障调查”，点击“创建连接池故障实验”或“创建库存并发故障实验”。
2. 系统先恢复健康配置，发送 120 次请求得到正常基线，再降低对应配置并发送 120 次请求，保存指标/日志/链路/变更快照。
3. 选择新增的 LIVE 案例及模型，点击“开始调查”。Agent 通过 MCP 查询本次冻结观测，生成报告和 pending 提案。
4. 打开“报告与审批”，复核证据。INC 批准会模拟；LIVE 批准会修改隔离业务服务配置并再次发送 120 次请求。
5. 查看真实 before/after 的错误数、错误率和 P95。恢复判据是验证样本错误率不超过 5%，且错误率下降或 P95 下降至少 20%。诊断动作始终不声明恢复。
6. 不要在调查期间创建另一轮实验。新实验会改变运行配置，使旧实验提案失效。已执行的历史结果仍可查看。

数值由当前机器实际测量，会变化；每轮并发上限 24，属于闭环负载，不是固定 RPS 的生产性能评测。

## 业务链

```text
压测请求 POST /orders
  → Psycopg 连接池申请 PostgreSQL 连接
  → 执行 SQL（受控 pg_sleep 120ms），写入 pending 订单
  → HTTP POST inventory /reserve，携带 trace_id
  → 库存信号量排队（超时 180ms）
  → 受控处理延迟 60ms + PostgreSQL 事务：幂等预留记录、库存扣减
  → 订单更新为 confirmed
  → 返回成功；任何阶段异常返回失败并记录真实日志、耗时和阶段
```

连接池故障：30→5，真实连接等待超时；恢复动作重建 30 个连接的池。
库存故障：16→2，真实并发排队超时，订单下游调用失败；恢复动作将处理并发恢复为 16。
这两个动作不会执行生产回滚、Kubernetes 操作或任意命令。
业务为故障实验而简化：没有支付/真实商品/鉴权/完整跨服务补偿。失败订单可能保持 pending，库存预留也可能需要后续对账；不能声称完整电商交易一致性已解决。

## Agent 链

```text
浏览器 → Craft 会话 → 实际模型
  → incident-observability MCP（11 个固定工具）
  → LIVE 证据适配器 → 控制器不可变实验快照
  → 本次调查证据读取账本 → 报告引用校验 → 待审批提案
浏览器用户审批 → Craft 登录+同源门禁 → 固定 Python bridge
  → 先持久保存 approved 和 waiting 执行状态
  → 本地控制器：操作 ID 幂等 + 实验/配置版本 + 服务启动 ID 检查
  → 修改固定配置 → 再压测 → 保存实测结果 → 浏览器显示
```

MCP 不提供创建实验、注入故障、批准或执行工具。模型只能提出固定动作。
SQLite 事务不包住外部 HTTP 操作，不声称分布式事务或无条件 exactly-once。
控制器先持久接受操作再修改配置；相同操作 ID 不再次修改或压测。
响应丢失时 approved 与 unknown 分开显示，按钮“核对同一操作的执行结果”重用相同 ID。
进程在接受后崩溃可能留下 running，不能盲目重发动作或声明恢复；核对服务状态并创建新实验。
服务重启或新实验改变配置后，旧提案受 boot_id/revision 检查拒绝。新实验开始时先持久失效旧版本，即使后续配置或基线步骤失败，旧提案也不能再执行；控制器重启清除当前实验指针并推进版本，保留历史快照。

## 观测方式与边界

- 原始指标由实际请求计数、客户端耗时、服务连接池/排队采样产生；P95 对 120 次请求（含失败）排序计算。
- 服务暴露 Prometheus 格式 `/metrics/`，当前未部署 Prometheus 抓取服务或 Grafana。
- JSON 结构化错误日志与自建 spans 关联 trace_id；当前未部署 OpenTelemetry Collector/Jaeger/Loki。
- MCP 故障证据是实验创建时的冻结快照，含正常基线和故障期 phase；不代表调查当下实时状态。审批后测量独立保存在执行结果。
- 数据库探针证明可连接、查询耗时和连接数/订单库存计数，不等于完整数据库健康诊断；没有虚构 CPU 指标。业务计数累计全部实验，不能当作本次故障损失；工具 scope_notes 明确说明这些边界。
- 控制接口需要独立本地 token，只向固定回环地址发请求；没有模型自选 URL/命令。
- Craft Explore 的工具限制不是操作系统沙箱，当前面向可信本地开发环境，不具备生产多租户隔离。

## 验证

```bash
bun run incident:test
bun run incident:test:integration
bun run incident:test:mcp
bun run incident:test:lab
```

最后一条要求实验服务运行，会创建新实验并使旧提案失效。
`incident/eval/lab-acceptance.json` 是真实服务与正式 stdio MCP 验收，报告由测试脚本提供，不能混称模型推理验证。
模型浏览器验证见 `incident/eval/live-browser-acceptance.json`。连接池（DeepSeek）错误率 85%→0%，最终冷启动复验 78.3%→0%（P95 362.5→302.13ms）；库存（Qwen）60%→0%，每次恢复验证均为 120 次实测请求。首次报告存在观测范围表述过强的问题，复核后要求模型保存修订报告，旧提案拒绝。最终会话确认 scope_notes 随 MCP 观测返回、重新打开面板保留 DeepSeek 模型选择；报告仍经人工复核修订。不能将这些样例当作自主诊断准确率。所有测试临时目录保留，不删除文件或数据库。

最终浏览器截图：`output/playwright/live-final-verified.png`；库存恢复截图：`output/playwright/live-inventory-verified.png`。

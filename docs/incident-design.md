## 2026-10-03 本地业务实验更新

已新增实际订单/库存/PostgreSQL 请求链与 LIVE 证据适配器，审批后会真实修改本地配置并重新压测。
原有 INC 仍使用合成规则模拟。新增能力与执行失败边界见 [incident-lab.md](incident-lab.md)。
26 项 Python 测试、7 项浏览器接口集成测试、11 个 MCP 工具握手及真实业务验收通过。
实际验收记录：`incident/eval/lab-acceptance.json`；这些报告为脚本输入，模型浏览器验收见 `incident/eval/live-browser-acceptance.json`，记录 DeepSeek 连接池调查及 Qwen 库存调查，两项均经浏览器复核修订后实际审批恢复。
下文保留上一版设计快照，“仅合成数据/未接实际服务”描述仅适用于原 INC 模式。

# Incident Agent 实现说明

## 业务链和复用范围

浏览器告警入口 → Craft createSession/sendMessage → WebSocket RPC → 既有 SessionManager/PiAgent → Skill 先决阅读 → stdio MCP → Python 业务查询 → 校验并保存报告 → 固定动作待审批提案 → 浏览器复核 → 同源审批接口 → 合成模拟与结果落库。

复用的能力：Craft 0.11.4 会话、模型执行循环、工具调用、取消、Source、Skill 加载、通用权限、模型凭据存储和浏览器适配器。新增的能力：故障案例、11 个业务工具、证据门禁、调查账本、提案规则、浏览器报告/审批接口、规则模拟器、针对业务的测试和离线报告评测。不把基座能力写成用户原创实现。

## 数据模型与一致性

- investigations：调查 ID、case_id、建立时间。
- evidence_reads：本次调查实际返回过的 evidence_id、kind；复合主键去重。
- calls：带自增 sequence 的业务工具观测和人工决定；按调查和顺序索引。它不是模型思考的完整审计记录。
- reports：不可变报告快照。
- proposals：不可变动作参数、关联报告、支持证据、风险、状态、决定者和模拟结果。

外键启用。查询返回数据和 evidence_reads/calls 在同一事务写入；报告校验、保存、调用记录在同一事务完成。审批使用 BEGIN IMMEDIATE 将 pending→approved/rejected 与模拟结果、人工决定记录原子保存；重复相同决定返回原结果，不重复运行，反向决定被拒绝。本地模拟无外部网络副作用，因此该事务能覆盖完整动作；它不证明真实外部执行器的 exactly-once。

报告引用必须属于本次调查且实际查询返回。likely_cause 的首个假设需要至少两类当前数据支持，history 不计数。该规则证明引用完整性和来源覆盖，不证明因果语义。候选原因与反证仍需离线评测和人工复核。

## 审批入口

Agent 的 MCP 工具可以建立提案，不能 approve/decide/execute。固定动作目录只允许恢复订单连接池至30、恢复库存消费并发至16、建立补证任务，不能传入任意命令。证据不足时只允许补证；恢复提案需要主要假设的两类当前支持证据。

报告复核 API 是 headless server 的可选扩展，仅在 CRAFT_INCIDENT_ROOT 配置时开启。它复用 Craft JWT 登录；POST 要求相同 Origin、application/json 和 X-Incident-UI:1。Python 管理桥只接收固定 JSON 操作，不接收 shell 命令，也不继承模型 Key。客户端审批不能更改已保存动作参数。

这是一套受信任的本地单用户开发环境。调查 ID 不是用户权限，登录口令不是多租户 RBAC；Source 权限与 Skill 也不是 OS 沙箱。Craft 的通用 Read 等工具仍存在，不宣称恶意模型无法读取全部本地文件。MCP 不返回 gold，Skill 明确禁止直接读取 fixture/评测答案；目前没有经过专门提示注入对抗评测。

## 模拟范围

模拟器根据 case_id 与固定动作生成预设规则结果，展示模拟前后错误率/P95。错误动作返回无改善；补证仅建立模拟任务，不补造缺失证据、不声称恢复。每项提案独立模拟，不修改原告警观测快照，也不启动真实订单/库存服务、数据库、集群或压测。指标不能作为生产可用性、性能收益或真实恢复率。

## 模型与密钥

百炼/DeepSeek 通过 Craft 的 pi_compat + piAuthProvider=openai + customEndpoint=openai-completions 接入。密钥从本地 .env.local 或 Craft 凭据页面进入既有加密存储，不进入浏览器页面、源码或业务报告。默认连接未配置凭据时，启动脚本选择有凭据的项目连接；保留已有可用选择。真实验收已使用 DeepSeek 官方 deepseek-flash 完成三个案例，并使用百炼 qwen3.7-plus 完成一次配置回归调查与待审批提案；其他百炼模型尚未调用。

## 主要代码入口

- services/incident-tools/incident_tools/domain.py：查询与报告门禁。
- services/incident-tools/incident_tools/mitigation.py：提案、决定、事务模拟。
- services/incident-tools/server.py：MCP 注册；没有审批工具。
- services/incident-tools/admin.py：固定浏览器管理操作桥。
- packages/server/src/incident-api.ts：登录、同源与输入校验。
- apps/webui/src/incident/IncidentLauncher.tsx：真实会话启动。
- apps/webui/src/incident/IncidentReview.tsx：报告、证据与审批展示。
- scripts/incident-bootstrap.py / incident-start.ts：隔离配置与启动。
- scripts/incident-evaluate.py：读取已保存报告的离线结构评测。

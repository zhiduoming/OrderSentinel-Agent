# 订单服务故障调查与处置辅助 Agent

## 已确认的决策

- 基座：附带 Craft Agents 0.11.4 源码；浏览器入口复用 `apps/webui`。
- 开发副本：`/Users/chen/Code/Incident-Agent`；原始参考源码保持不变。
- 用户负责在成型后沿项目、源码和简历学习；实现与验证由 Codex 推进。
- 主模型候选：百炼 `qwen3.7-plus`；可选 `qwen3.8-flash`；另有官方 DeepSeek `deepseek-flash`。
- 模型通过 Craft 的 Pi 自定义 OpenAI-compatible connection 接入；不另造执行引擎。
- 使用套餐的服务范围须按官方文档确认，不能因协议兼容就认定套餐允许任意自写应用调用。
- API Key 通过本地配置或 Craft 凭据界面保存，不写进源码、报告或简历。

## 用户任务

给定订单服务告警，值班工程师希望确认影响范围、调查候选原因、查看正反证据并获得可执行的恢复建议。Agent 的价值是实际查询多个数据源并提交可追溯的报告；证据不足时明确保留不确定性。

## 第一版业务链

告警 → 建立调查 → 查询指标、日志与请求链路 → 检查部署和配置变化 → 比较历史故障 → 提出假设与反证 → 提交校验后的报告 → 生成处置建议 → 人工批准后运行合成案例模拟并展示验证结果。

调查顺序允许由模型根据工具结果调整；时间相关性不能独立证明因果。历史相似案例只能提供线索。工具结果按不可信数据处理，不能改变权限或调查规则。

## 首批案例

1. 配置回归：新版本连接池上限降低；数据库本身正常；新旧实例有差异。
2. 下游超时：库存服务响应变慢；订单服务有一次无关部署，避免将部署误当根因。
3. 证据不足：指标确认异常，但日志不可用且链路样本缺失；报告必须请求补证。

案例均为明确标注的合成数据，不能声称生产故障经历。正确答案放在评测文件，不由业务查询工具返回。

## 工程产物与边界

| 产物 | 基座复用 | 新增内容 |
| --- | --- | --- |
| Incident MCP | Sources 与 stdio MCP | 案例查询、指标、日志、链路、发布和历史故障工具 |
| 调查工作区与 Skill | Workspace 与 Skill Loader | 业务调查 SOP、证据引用与停止条件 |
| Evidence Report | 会话和工具事件 | 跨来源证据结构、引用存在性与本次调查读取记录校验 |
| 处置辅助 | 通用权限框架 | 固定动作提案、浏览器审批、幂等的合成模拟；真实回滚和生产执行不在当前验收范围 |
| 评测 | 现有测试基础 | 已知原因、误导线索、数据源失败、越界引用等案例与断言 |

代码校验能证明证据存在、属于本次调查、曾经查询，不能自动证明模型结论因果正确。正确性仍需案例评测和人工核查。

## 验收条件

- 工具实际返回案例数据；每条证据带稳定 ID、服务、环境、来源和时间。
- 调查 ID 隔离证据集合；报告不得引用其他案例或未读取证据。
- 模型必须区分确定事实、候选解释和未知项。
- 数据源不可用返回明确失败，不能伪造数据或宣布恢复。
- 受限操作不得仅凭 Prompt 约束；Agent 可提出建议，审批和执行需单独验证。
- 浏览器真实模型调查通过后才记为端到端可运行。
- 测试、MCP 握手、真实模型调用和浏览器演示分别记录，禁止相互替代。

## 源码学习入口

- 模型后端：`packages/shared/src/agent/backend/factory.ts`。
- MCP 配置：`packages/shared/src/sources/server-builder.ts`。
- Skill 加载：`packages/shared/src/skills/storage.ts`。
- 权限检查：`packages/shared/src/agent/core/pre-tool-use.ts`。
- 消息、取消、审批：`packages/server-core/src/handlers/rpc/sessions.ts`。
- 浏览器桥接：`apps/webui/src/App.tsx`。

## 参考来源

- Skill 场景库：`/Users/chen/Code/冲刺Agent/craft-agents-resume-skill/references/multi-industry-agent-scenario-library.md` 第 14 节。
- Skill 工程推导：`/Users/chen/Code/冲刺Agent/craft-agents-resume-skill/references/scenario-architecture.md` 第 2、6、7 节。
- 百炼接入：https://help.aliyun.com/zh/model-studio/more-tools
- DeepSeek 接入：https://api-docs.deepseek.com/guides/codex
- MCP Python v1 SDK：https://github.com/modelcontextprotocol/python-sdk/tree/v1.x

## 实现状态

业务工具的代码和独立测试以 `docs/incident-progress.md` 为准。此需求合同本身不证明已实现、已上线或个人独立完成。

# Incident observability

此 Source 同时提供三个 INC 合成案例和 LIVE 本地业务实验。LIVE 来自实际订单接口 → PostgreSQL → 库存接口 → PostgreSQL 库存扣减的请求，仍非生产系统。

## Scope

使用 list_cases → start_investigation 获取调查 ID，再查询对应指标、日志、链路、部署和历史案例。每条记录带 evidence_id、service、environment、source、observed_at 和 data_mode。

## Guidelines

INC 查询读取 fixture；LIVE 查询读取实验控制器保存的不可变请求观测快照，并将已读取证据保存到调查账本。不同 investigation_id 不共享引用权限。submit_report 保存报告前验证证据确实在本次调查读取过；likely_cause 的首个假设必须有两类来源支持。

INC 时间是案例固定时间；LIVE observed_at 为采集时刻，retrieved_at 是工具实际执行时间。不要混淆二者。search_history 返回历史线索，不自动证明当前原因。接口没有执行回滚或修改配置的能力。

## Report

report.verdict 为 likely_cause 或 insufficient_evidence。必填 summary、impact、recommended_next_step、missing_information、hypotheses。每个 hypothesis 必填 description、confidence、supporting_evidence 和 counter_evidence。具体约束以工具 schema 和返回错误为准。

固定处置动作通过 mitigation_actions 查询；propose_mitigation 只建立待批准的固定动作提案。批准/拒绝只在浏览器报告面板进行，MCP 无审批工具。INC 为规则模拟。LIVE 审批后修改本地业务服务配置并重新压测，结果是实际测量；旧实验提案受版本检查约束。MCP 不能创建实验、注入故障或执行配置。LIVE 证据是冻结的故障期快照，与审批后验证数据分开。此实验的连接池是 Psycopg，trace_id 为自建链路标识，未部署 Jaeger/Loki。

LIVE 的 scope_notes 描述观测边界。日志与链路为有界样本，不是全部错误；数据库业务计数累计全部实验，不能当成本次故障损失。consumer_concurrency 是库存 HTTP 处理信号量并发，不是 MQ 消费者线程。

"""Real stdio MCP handshake and tool calls, without a model/API key."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def decode(result):
    if result.isError:
        raise AssertionError(str(result.content))
    return json.loads(result.content[0].text)


async def main():
    db = Path(tempfile.mkdtemp(prefix="incident-mcp-")) / "ledger.sqlite3"
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")],
                                  env={**os.environ, "INCIDENT_DATABASE": str(db)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            names = {t.name for t in tools.tools}
            assert len(names) == 11, names
            assert not any("execute" in n or "rollback" in n or "approve" in n or "decide" in n for n in names)
            cases = decode(await client.call_tool("list_cases", {}))
            assert len([c for c in cases["cases"] if c["data_mode"]=="synthetic"]) == 3
            run = decode(await client.call_tool("start_investigation", {"case_id": "INC-001"}))["investigation_id"]
            for name in ["query_metrics", "search_logs", "query_traces", "query_deployments", "search_history"]:
                observed = decode(await client.call_tool(name, {"investigation_id": run}))
                assert observed["records"] and observed["data_mode"] == "synthetic"
            report = {"verdict": "likely_cause", "summary": "连接池配置变更导致等待", "impact": "新版本请求错误率升高",
                      "recommended_next_step": "人工核对并恢复连接池配置", "missing_information": [],
                      "hypotheses": [{"description": "连接池上限从30降为5", "confidence": "high",
                                      "supporting_evidence": ["INC-001:metrics:1", "INC-001:logs:3", "INC-001:deployments:1"],
                                      "counter_evidence": []}]}
            saved = decode(await client.call_tool("submit_report", {"investigation_id": run, "report": report}))
            assert saved["status"] == "saved"
            actions = decode(await client.call_tool("mitigation_actions", {}))
            assert len(actions["actions"]) == 3
            proposal = decode(await client.call_tool("propose_mitigation", {
                "investigation_id": run, "report_id": saved["report_id"], "action": "restore_order_pool",
                "rationale": "人工复核池容量后模拟旧配置", "evidence_ids": report["hypotheses"][0]["supporting_evidence"]}))
            assert proposal["status"] == "pending"
            bad = await client.call_tool("submit_report", {"investigation_id": run, "report": {**report, "hypotheses": [
                {**report["hypotheses"][0], "supporting_evidence": ["fabricated"]}]}})
            assert bad.isError
            state = decode(await client.call_tool("get_investigation", {"investigation_id": run}))
            assert len(state["reports"]) == 1
            assert state["proposals"][0]["result"] is None
            print(json.dumps({"status": "passed", "transport": "stdio", "tools": sorted(names), "model_used": False}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

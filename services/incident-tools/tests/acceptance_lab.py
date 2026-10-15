"""Opt-in real-service acceptance. Generates real load and preserves all artifacts.
Reports here are scripted test inputs, not model-generated reports.
"""
import asyncio
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from incident_tools.domain import IncidentService
from incident_tools.lab_client import LabClient

ROOT=Path(__file__).resolve().parents[3]
SERVICE=ROOT/"services/incident-tools"
STATE=Path(tempfile.mkdtemp(prefix="incident-lab-acceptance-"))
service=IncidentService(SERVICE/"fixtures",STATE/"ledger.sqlite3")
lab=LabClient()
local_http=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def decode(result):
    assert not result.isError,str(result.content)
    return json.loads(result.content[0].text)


async def investigate(case_id):
    params=StdioServerParameters(command=sys.executable,args=[str(SERVICE/"server.py")],
        env={**os.environ,"INCIDENT_DATABASE":str(service.database),"PYTHONDONTWRITEBYTECODE":"1"})
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            names={t.name for t in (await client.list_tools()).tools}
            assert len(names)==11 and not any(x in n for n in names for x in ("execute","approve","prepare"))
            inv=decode(await client.call_tool("start_investigation",{"case_id":case_id}))["investigation_id"]
            observed={}
            for kind,tool in (("metrics","query_metrics"),("logs","search_logs"),("traces","query_traces"),("deployments","query_deployments")):
                observed[kind]=decode(await client.call_tool(tool,{"investigation_id":inv}))
                assert observed[kind]["data_mode"]=="local_live" and observed[kind]["records"] and observed[kind]["scope_notes"]
            support=[next(r for r in observed["metrics"]["records"] if r.get("phase")=="fault")["evidence_id"],
                     observed["logs"]["records"][0]["evidence_id"],observed["deployments"]["records"][0]["evidence_id"]]
            report={"verdict":"likely_cause","summary":"SCRIPTED ACCEPTANCE TEST","impact":"measured failures",
                    "recommended_next_step":"review fixed restoration action","missing_information":[],
                    "hypotheses":[{"description":"test diagnosis input","confidence":"high","supporting_evidence":support,"counter_evidence":[]}]}
            rid=decode(await client.call_tool("submit_report",{"investigation_id":inv,"report":report}))["report_id"]
            change=observed["deployments"]["records"][0]["change"]
            action="restore_order_pool" if "maximumPoolSize" in change else "restore_inventory_concurrency"
            async def proposal(action):
                return decode(await client.call_tool("propose_mitigation",{"investigation_id":inv,"report_id":rid,
                    "action":action,"rationale":"scripted real-service acceptance","evidence_ids":support}))
            return {"investigation_id":inv,"proposal":await proposal(action),"wrong_proposal":await proposal("restore_order_pool" if action!="restore_order_pool" else "restore_inventory_concurrency")}


async def main():
    artifacts={"model_used":False,"report_origin":"scripted acceptance inputs","transport":"actual stdio MCP","checks":[]}
    try:
        local_http.open(urllib.request.Request("http://127.0.0.1:19103/experiments",data=b'{"scenario":"pool"}',headers={"Content-Type":"application/json"}))
        raise AssertionError("unauthorized control accepted")
    except urllib.error.HTTPError as error: assert error.code==403
    for scenario in ("pool","inventory"):
        case=lab.prepare(scenario);snapshot=lab.case(case["case_id"])
        baseline,fault=snapshot["metrics"][:2]
        assert snapshot["metrics"][2]["counter_scope"]=="cumulative_all_lab_experiments"
        assert baseline["error_rate"]<=.05 and fault["error_rate"]>.1,(baseline,fault)
        run=await investigate(case["case_id"])
        before=service.get_investigation(run["investigation_id"])
        assert all(p["result"] is None for p in before["proposals"])
        result=service.decide_proposal(run["proposal"]["proposal_id"],"approve")["result"]
        assert result["execution_status"]=="completed" and result["recovered"],result
        assert result["after"]["requests"]==120 and result["after"]["error_rate"]<=.05,result
        replay=service.decide_proposal(run["proposal"]["proposal_id"],"approve")
        assert replay["replayed"] and replay["result"]==result
        artifacts["checks"].append({"scenario":scenario,"case_id":case["case_id"],"baseline":baseline,"result":result,"replay_same_result":True})
    case=lab.prepare("inventory");run=await investigate(case["case_id"])
    wrong=service.decide_proposal(run["wrong_proposal"]["proposal_id"],"approve")["result"]
    assert not wrong["recovered"] and wrong["verification"]=="no_improvement",wrong
    artifacts["wrong_action"]=wrong
    old=lab.prepare("pool");run=await investigate(old["case_id"])
    lab.prepare("inventory")
    stale=service.decide_proposal(run["proposal"]["proposal_id"],"approve")["result"]
    assert stale["verification"]=="stale_proposal" and not stale["recovered"],stale
    artifacts["stale_proposal"]=stale
    with local_http.open("http://127.0.0.1:19101/metrics/") as r:
        assert "lab_requests_total" in r.read().decode()
    artifacts["status"]="passed"
    artifacts["ledger"]=str(service.database)
    output=ROOT/"incident/eval/lab-acceptance.json"
    output.write_text(json.dumps(artifacts,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":"passed","checks":["two actual faults","actual restorations","MCP evidence and reference gates","idempotent replay","wrong action remains unhealthy","stale proposal rejected","unauthorized control rejected","Prometheus metrics emitted"],"artifact":str(output)},ensure_ascii=False))


if __name__=="__main__":asyncio.run(main())

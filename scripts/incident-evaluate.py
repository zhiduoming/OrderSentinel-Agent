"""Evaluate saved model reports offline. Never passed to the investigation model."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("--database",type=Path,default=root/".incident/incident-ledger.sqlite3")
parser.add_argument("--output",type=Path,default=root/"incident/eval/latest-results.json")
args=parser.parse_args()
gold=json.loads((root/"incident/eval/gold.json").read_text())["cases"]
results=[]
with sqlite3.connect(f"file:{args.database}?mode=ro",uri=True) as db:
    for cid,expectation in gold.items():
        row=db.execute("SELECT r.id,r.investigation_id,r.payload FROM reports r JOIN investigations i "
                       "ON i.id=r.investigation_id WHERE i.case_id=? ORDER BY r.created_at DESC LIMIT 1",(cid,)).fetchone()
        if not row:
            results.append({"case_id":cid,"status":"not_run"});continue
        rid,inv,raw=row
        report=json.loads(raw)
        support=set(report["hypotheses"][0]["supporting_evidence"])
        missing=set(expectation["required_support"])-support
        checks={"expected_verdict":report["verdict"]==expectation["expected_verdict"],
                "required_primary_support":not missing,
                "gaps_when_insufficient":report["verdict"]!="insufficient_evidence" or bool(report["missing_information"])}
        results.append({"case_id":cid,"report_id":rid,"investigation_id":inv,
            "status":"structural_checks_passed" if all(checks.values()) else "needs_review",
            "checks":checks,"missing_support":sorted(missing),"summary":report["summary"],
            "expected_cause":expectation["expected_cause"],"manual_counter_check":expectation["important_counter"]})
output={"evaluated_at":datetime.now(timezone.utc).isoformat(),"data_mode":"synthetic",
        "scope":"One latest saved report per case; structural checks plus manual causal review, not a benchmark accuracy estimate",
        "results":results}
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"output":str(args.output),"results":[{"case_id":r["case_id"],"status":r["status"]} for r in results]},ensure_ascii=False))

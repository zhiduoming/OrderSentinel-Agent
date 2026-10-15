"""Domain tools and durable evidence ledger, independent of the MCP SDK."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class IncidentError(ValueError):
    pass


KINDS = {"metrics", "logs", "traces", "deployments", "history"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


from .mitigation import MitigationMixin
from .lab_client import LabClient, LabUnavailable, LIVE_ID


class IncidentService(MitigationMixin):
    def __init__(self, cases: Path, database: Path):
        self.cases = cases
        self.database = database
        self.lab = LabClient()
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS live_cases(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposal_executions(proposal_id TEXT PRIMARY KEY REFERENCES proposals(id), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS investigations (
                  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence_reads (
                  investigation_id TEXT NOT NULL REFERENCES investigations(id),
                  evidence_id TEXT NOT NULL, kind TEXT NOT NULL,
                  PRIMARY KEY (investigation_id, evidence_id));
                CREATE TABLE IF NOT EXISTS calls (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  investigation_id TEXT NOT NULL REFERENCES investigations(id),
                  tool TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS calls_by_investigation
                  ON calls(investigation_id, sequence);
                CREATE TABLE IF NOT EXISTS reports (
                  id TEXT PRIMARY KEY,
                  investigation_id TEXT NOT NULL REFERENCES investigations(id),
                  payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (
                  id TEXT PRIMARY KEY,
                  investigation_id TEXT NOT NULL REFERENCES investigations(id),
                  payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                  result TEXT, actor TEXT, created_at TEXT NOT NULL, decided_at TEXT);
                CREATE INDEX IF NOT EXISTS proposals_by_investigation ON proposals(investigation_id, created_at);
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def case(self, case_id: str) -> dict[str, Any]:
        if LIVE_ID.fullmatch(case_id):
            with closing(self.connect()) as db:
                row = db.execute("SELECT payload FROM live_cases WHERE id=?",(case_id,)).fetchone()
            if row:
                return json.loads(row[0])
            try: case = self.lab.case(case_id)
            except LabUnavailable as error: raise IncidentError(str(error)) from None
            with closing(self.connect()) as db, db:
                db.execute("INSERT OR IGNORE INTO live_cases VALUES(?,?)",(case_id,json.dumps(case,ensure_ascii=False)))
            return case
        # Never resolve user-supplied paths; the case_id must match an inventory key.
        for path in self.cases.glob("*.json"):
            case = json.loads(path.read_text())
            if case["case_id"] == case_id:
                return case
        raise IncidentError("Unknown case_id; use list_cases first")

    def list_cases(self) -> dict[str, Any]:
        cases = [{**{k: case[k] for k in ("case_id", "title", "service", "environment", "alert")},
                  "data_mode":"synthetic"}
                 for case in (json.loads(p.read_text()) for p in sorted(self.cases.glob("*.json")))]
        try:
            live = self.lab.cases()
            return {"cases":cases+live["cases"],"lab_status":"ready","active_case":live["active_case"],
                    "data_mode":"mixed"}
        except LabUnavailable:
            return {"cases":cases,"lab_status":"unavailable","data_mode":"synthetic"}

    def prepare_lab(self, scenario):
        try: result = self.lab.prepare(scenario)
        except LabUnavailable as error: raise IncidentError(str(error)) from None
        self.case(result["case_id"])
        return result

    def start_investigation(self, case_id: str) -> dict[str, Any]:
        case = self.case(case_id)
        investigation_id = uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO investigations VALUES (?, ?, ?)",
                       (investigation_id, case_id, now()))
            self.record(db, investigation_id, "start_investigation", {"case_id": case_id})
        return {"investigation_id": investigation_id, "case_id": case_id,
                "service": case["service"], "environment": case["environment"],
                "alert": case["alert"], "data_mode": case.get("data_mode","synthetic")}

    def resolve(self, db: sqlite3.Connection, investigation_id: str) -> dict[str, Any]:
        row = db.execute("SELECT case_id FROM investigations WHERE id = ?",
                         (investigation_id,)).fetchone()
        if not row:
            raise IncidentError("Unknown investigation_id")
        return self.case(row[0])

    def record(self, db: sqlite3.Connection, investigation_id: str,
               tool: str, payload: dict[str, Any]) -> None:
        db.execute("INSERT INTO calls(investigation_id, tool, payload, created_at) VALUES(?,?,?,?)",
                   (investigation_id, tool, json.dumps(payload, ensure_ascii=False), now()))

    def query(self, investigation_id: str, kind: str,
              contains: str = "", limit: int = 20) -> dict[str, Any]:
        if kind not in KINDS:
            raise IncidentError("Unknown data kind")
        if not 1 <= limit <= 50 or len(contains) > 200:
            raise IncidentError("limit must be 1..50; contains must be <=200 characters")
        with closing(self.connect()) as db, db:
            case = self.resolve(db, investigation_id)
            unavailable = case.get("unavailable", {}).get(kind)
            if unavailable:
                result = {"status": "unavailable", "reason": unavailable, "records": []}
            else:
                matches = [record for record in case.get(kind, [])
                           if contains.casefold() in json.dumps(record, ensure_ascii=False).casefold()]
                records = matches[:limit]
                for record in records:
                    db.execute("INSERT OR IGNORE INTO evidence_reads VALUES (?,?,?)",
                               (investigation_id, record["evidence_id"], kind))
                result = {"status": "ok", "records": records,
                          "truncated": len(matches) > limit, "total_matches": len(matches)}
            result.update({"investigation_id": investigation_id, "kind": kind,
                           "data_mode": case.get("data_mode","synthetic"), "retrieved_at": now()})
            if case.get("data_mode")=="local_live":
                result["scope_notes"] = {
                    "metrics":["phase separates healthy baseline and fault requests; PostgreSQL business counters are cumulative across ALL lab experiments, not losses for this case", "A database probe is not CPU/IO/lock-wait monitoring; reachable does not prove overall health"],
                    "logs":["Error-first bounded excerpts, not all requests or all errors; do not infer complete coverage from this sample"],
                    "traces":["Bounded cross-service sample with custom trace_id; inventory.queue is HTTP semaphore waiting, not message broker lag"],
                    "deployments":["Controlled LOCAL runtime config change; consumer_concurrency means inventory HTTP processing semaphore, not MQ consumer threads"],
                    "history":["No live historical incident corpus is currently connected"]
                }[kind]
            self.record(db, investigation_id, "query_" + kind,
                        {"contains": contains, "limit": limit, "result": result})
            return result

    def submit_report(self, investigation_id: str, report: dict[str, Any]) -> dict[str, Any]:
        """Validate reference integrity, not the causal truth of a model conclusion."""
        if len(json.dumps(report, ensure_ascii=False)) > 40000:
            raise IncidentError("Report exceeds size limit")
        if report.get("verdict") not in {"likely_cause", "insufficient_evidence"}:
            raise IncidentError("verdict must be likely_cause or insufficient_evidence")
        for field in ("summary", "impact", "recommended_next_step"):
            if not isinstance(report.get(field), str) or not report[field].strip():
                raise IncidentError("Missing nonempty report field: " + field)
        hypotheses = report.get("hypotheses")
        if not isinstance(hypotheses, list) or not 1 <= len(hypotheses) <= 8:
            raise IncidentError("Report requires 1..8 hypotheses")
        gaps = report.get("missing_information")
        if not isinstance(gaps, list) or not all(isinstance(x, str) for x in gaps):
            raise IncidentError("missing_information must be a string list")
        if report["verdict"] == "insufficient_evidence" and not gaps:
            raise IncidentError("Insufficient evidence requires missing_information")
        with closing(self.connect()) as db, db:
            case = self.resolve(db, investigation_id)
            seen = dict(db.execute("SELECT evidence_id, kind FROM evidence_reads WHERE investigation_id = ?",
                                   (investigation_id,)).fetchall())
            all_references = set()
            for hypothesis in hypotheses:
                if not isinstance(hypothesis, dict) or not isinstance(hypothesis.get("description"), str) or not hypothesis["description"].strip():
                    raise IncidentError("Each hypothesis needs a nonempty description")
                if hypothesis.get("confidence") not in {"low", "medium", "high"}:
                    raise IncidentError("confidence must be low, medium or high")
                support = hypothesis.get("supporting_evidence")
                counter = hypothesis.get("counter_evidence")
                if not isinstance(support, list) or not isinstance(counter, list):
                    raise IncidentError("Evidence references must be lists")
                if not all(isinstance(x, str) for x in support + counter):
                    raise IncidentError("Evidence references must be strings")
                if set(support) & set(counter):
                    raise IncidentError("Same evidence cannot be support and counter for one hypothesis")
                references = set(support + counter)
                if not references <= seen.keys():
                    raise IncidentError("Evidence was not read in this investigation: " + ", ".join(sorted(references - seen.keys())))
                all_references.update(references)
            if not all_references:
                raise IncidentError("Report must cite observed evidence")
            if report["verdict"] == "likely_cause":
                primary = hypotheses[0]
                support_kinds = {seen[x] for x in primary["supporting_evidence"] if seen[x] != "history"}
                if len(support_kinds) < 2:
                    raise IncidentError("Likely cause needs primary support from at least two data kinds from current observations (history is a hint)")
            report_id = uuid4().hex
            db.execute("INSERT INTO reports VALUES(?,?,?,?)",
                       (report_id, investigation_id, json.dumps(report, ensure_ascii=False), now()))
            self.record(db, investigation_id, "submit_report", {"report_id": report_id})
            return {"status": "saved", "report_id": report_id, "data_mode": case.get("data_mode","synthetic"),
                    "validation": "Reference integrity checked; causal correctness needs evaluation",
                    "report": report}

    def get_investigation(self, investigation_id: str) -> dict[str, Any]:
        with closing(self.connect()) as db:
            case = self.resolve(db, investigation_id)
            calls = [{"sequence": seq, "tool": tool, "payload": json.loads(payload), "created_at": stamp}
                     for seq, tool, payload, stamp in db.execute(
                         "SELECT sequence,tool,payload,created_at FROM calls WHERE investigation_id=? ORDER BY sequence",
                         (investigation_id,))]
            reports = [{"report_id": rid, "report": json.loads(payload)}
                       for rid, payload in db.execute(
                           "SELECT id,payload FROM reports WHERE investigation_id=? ORDER BY created_at",
                           (investigation_id,))]
            proposals = [{"proposal_id": pid, **json.loads(payload), "status": status,
                          "result": json.loads(result) if result else None, "actor": actor}
                         for pid,payload,status,result,actor in db.execute(
                             "SELECT id,payload,status,result,actor FROM proposals WHERE investigation_id=? ORDER BY created_at",
                             (investigation_id,))]
            return {"investigation_id": investigation_id, "case_id": case["case_id"],
                    "calls": calls, "reports": reports, "proposals": proposals, "data_mode": case.get("data_mode","synthetic")}

    def list_investigations(self):
        with closing(self.connect()) as db:
            return {"investigations": [{"investigation_id": inv, "case_id": cid, "created_at": stamp,
                     "reports": reports, "pending_proposals": pending}
                for inv,cid,stamp,reports,pending in db.execute(
                    "SELECT i.id,i.case_id,i.created_at,"
                    "(SELECT count(*) FROM reports WHERE investigation_id=i.id),"
                    "(SELECT count(*) FROM proposals WHERE investigation_id=i.id AND status='pending') "
                    "FROM investigations i ORDER BY i.created_at DESC LIMIT 100")]}


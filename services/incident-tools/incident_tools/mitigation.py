"""Immutable proposals and atomic, idempotent human-approved local simulations."""
import json
from contextlib import closing
from uuid import uuid4

ACTIONS = {
    "restore_order_pool": {"name": "订单连接池恢复至 30", "service": "order-service",
        "change": {"maximumPoolSize": 30}, "risk": "增加数据库连接数，需要确认数据库连接容量。"},
    "restore_inventory_concurrency": {"name": "库存处理并发恢复至 16", "service": "inventory-service",
        "change": {"consumer_concurrency": 16}, "risk": "增加下游处理负载，需要观察积压和数据库负载。"},
    "collect_missing_observations": {"name": "补采日志与链路诊断任务", "service": "order-service",
        "change": {}, "risk": "仅补采本地诊断观测或建立模拟补证任务，不证明业务恢复，也不补造缺失日志。"},
}


def simulate(case_id, action):
    # This is a deterministic fixture simulator, never a production actuator.
    before = {"INC-001": {"error_rate": .18, "p95_ms": 4200},
              "INC-002": {"error_rate": .12, "p95_ms": 5200},
              "INC-003": {"error_rate": .08, "p95_ms": 3900}}[case_id]
    if action == "collect_missing_observations":
        return {"mode": "local_simulation", "verification": "diagnostic_task_created",
                "before": before, "after": before, "recovered": False,
                "message": "模拟补证任务已建立；原有观测缺失仍保留，不能声明恢复。"}
    effective = (case_id, action) in {("INC-001", "restore_order_pool"),
                                     ("INC-002", "restore_inventory_concurrency")}
    after = {"error_rate": .005, "p95_ms": 240} if effective else dict(before)
    return {"mode": "local_simulation", "verification": "improved" if effective else "no_improvement",
            "before": before, "after": after, "recovered": effective,
            "message": "仅合成模拟结果；未调用真实服务或生产基础设施。"}


class MitigationMixin:
    def mitigation_actions(self):
        return {"mode": "case-dependent", "actions": [
            {"action": key, **value} for key, value in ACTIONS.items()],
            "approval": "Synthetic cases simulate; LIVE cases change only isolated local services. Only the authenticated browser decision API may approve; no MCP approval tool."}

    def propose_mitigation(self, investigation_id, report_id, action, rationale, evidence_ids):
        from .domain import IncidentError, now
        if action not in ACTIONS:
            raise IncidentError("Unknown mitigation action")
        if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 4000:
            raise IncidentError("rationale must contain 1..4000 characters")
        if not isinstance(evidence_ids, list) or not evidence_ids or not all(isinstance(x, str) for x in evidence_ids):
            raise IncidentError("A proposal requires report evidence IDs")
        with closing(self.connect()) as db, db:
            case = self.resolve(db, investigation_id)
            row = db.execute("SELECT payload FROM reports WHERE id=? AND investigation_id=?",
                             (report_id, investigation_id)).fetchone()
            if not row:
                raise IncidentError("Report does not belong to this investigation")
            report = json.loads(row[0])
            cited = {ref for h in report["hypotheses"] for ref in h["supporting_evidence"]}
            if not set(evidence_ids) <= cited:
                raise IncidentError("Proposal references must be supporting evidence in the saved report")
            if report["verdict"] == "insufficient_evidence" and action != "collect_missing_observations":
                raise IncidentError("Insufficient evidence cannot authorize recovery proposals")
            if action != "collect_missing_observations":
                primary = set(report["hypotheses"][0]["supporting_evidence"])
                seen = dict(db.execute("SELECT evidence_id,kind FROM evidence_reads WHERE investigation_id=?",
                                       (investigation_id,)).fetchall())
                kinds = {seen[x] for x in evidence_ids if x in primary and seen[x] != "history"}
                if len(kinds) < 2:
                    raise IncidentError("Recovery proposal needs two current data kinds from the primary hypothesis")
            proposal_id = uuid4().hex
            payload = {"action": action, **ACTIONS[action], "rationale": rationale,
                       "evidence_ids": sorted(set(evidence_ids)), "report_id": report_id,
                       "mode": "local_live" if case.get("data_mode")=="local_live" else "local_simulation"}
            if payload["mode"] == "local_live":
                payload["expected_revision"] = case["revision"]
            db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?)",
                (proposal_id, investigation_id, json.dumps(payload, ensure_ascii=False),
                 "pending", None, None, now(), None))
            self.record(db, investigation_id, "propose_mitigation", {"proposal_id": proposal_id, **payload})
            return {"proposal_id": proposal_id, "status": "pending", **payload}

    def decide_proposal(self, proposal_id, decision):
        # Decision commits before network execution. Lab operation ID = immutable proposal ID.
        from .domain import IncidentError, now
        from .lab_client import LabUnavailable
        if decision not in {"approve", "reject"}:
            raise IncidentError("decision must be approve or reject")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT investigation_id,payload,status,result FROM proposals WHERE id=?",
                             (proposal_id,)).fetchone()
            if not row:
                raise IncidentError("Unknown proposal_id")
            inv, raw, status, saved = row
            payload = json.loads(raw)
            desired = "approved" if decision == "approve" else "rejected"
            replayed = status != "pending"
            if replayed and status != desired:
                raise IncidentError("Proposal decision is immutable")
            live = payload["mode"] == "local_live" and decision == "approve"
            if replayed and not live:
                return {"proposal_id":proposal_id,"status":status,"result":json.loads(saved) if saved else None,"replayed":True}
            case = self.resolve(db,inv)
            if live:
                execution = db.execute("SELECT payload FROM proposal_executions WHERE proposal_id=?",(proposal_id,)).fetchone()
                result = json.loads(execution[0]) if execution else {
                    "mode":"local_live","execution_status":"waiting","verification":"awaiting_dispatch",
                    "before":case["before"],"after":None,"recovered":False,
                    "message":"审批已保存，等待本地执行确认。"}
                if result["execution_status"] in {"completed","failed"}:
                    return {"proposal_id":proposal_id,"status":desired,"result":result,"replayed":True}
                db.execute("INSERT OR IGNORE INTO proposal_executions VALUES(?,?)",(proposal_id,json.dumps(result,ensure_ascii=False)))
            else:
                result = simulate(case["case_id"],payload["action"]) if decision=="approve" else None
            db.execute("UPDATE proposals SET status=?,result=?,actor=?,decided_at=COALESCE(decided_at,?) WHERE id=?",
                       (desired,json.dumps(result,ensure_ascii=False) if result else None,"local-browser-user",now(),proposal_id))
            if not replayed:
                self.record(db,inv,"human_decision",{"proposal_id":proposal_id,"decision":decision,"actor":"local-browser-user"})
        if live:
            try:
                result = self.lab.execute(proposal_id,payload,case["case_id"])
            except LabUnavailable as error:
                reason = str(error)
                result.update({"execution_status":"failed" if reason.startswith("Stale proposal") else "unknown",
                               "verification":"stale_proposal" if reason.startswith("Stale proposal") else "execution_unconfirmed",
                               "message":reason})
            with closing(self.connect()) as db, db:
                # Concurrent observers must not replace a confirmed completion with an older running/unknown view.
                current = json.loads(db.execute("SELECT payload FROM proposal_executions WHERE proposal_id=?",(proposal_id,)).fetchone()[0])
                if current["execution_status"] in {"completed","failed"}:
                    result = current
                else:
                    raw = json.dumps(result,ensure_ascii=False)
                    db.execute("UPDATE proposal_executions SET payload=? WHERE proposal_id=?",(raw,proposal_id))
                    db.execute("UPDATE proposals SET result=? WHERE id=?",(raw,proposal_id))
                    self.record(db,inv,"local_execution_observed",{"proposal_id":proposal_id,"result":result})
        return {"proposal_id":proposal_id,"status":desired,"result":result,"replayed":replayed}

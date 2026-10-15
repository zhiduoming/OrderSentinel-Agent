"""Real-mode decision durability and reconciliation, without running business services."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from incident_tools.domain import IncidentService, IncidentError
from incident_tools.lab_client import LabUnavailable


class LiveDecisionTests(unittest.TestCase):
    def setUp(self):
        self.state=Path(tempfile.mkdtemp(prefix="incident-live-unit-"))
        self.service=IncidentService(Path(__file__).resolve().parents[1]/"fixtures",self.state/"ledger.sqlite3")
        self.cid="LIVE-"+"a"*32
        case={"case_id":self.cid,"title":"live","service":"order-service","environment":"local-lab",
              "alert":"actual error","data_mode":"local_live","revision":7,
              "before":{"error_rate":.5,"p95_ms":400},
              "metrics":[{"evidence_id":self.cid+":metrics:1","error_rate":.5}],
              "logs":[{"evidence_id":self.cid+":logs:1","message":"pool timeout"}]}
        self.service.lab=Mock()
        self.service.lab.case.return_value=case
        self.inv=self.service.start_investigation(self.cid)["investigation_id"]
        metrics=self.service.query(self.inv,"metrics")["records"][0]["evidence_id"]
        logs=self.service.query(self.inv,"logs")["records"][0]["evidence_id"]
        self.report=self.service.submit_report(self.inv,{"verdict":"likely_cause","summary":"pool contention",
            "impact":"errors","recommended_next_step":"restore","missing_information":[],
            "hypotheses":[{"description":"pool saturation","confidence":"high","supporting_evidence":[metrics,logs],"counter_evidence":[]}]})
        self.proposal=self.service.propose_mitigation(self.inv,self.report["report_id"],"restore_order_pool","restore",[metrics,logs])
        self.pid=self.proposal["proposal_id"]
        self.completed={"mode":"local_live","execution_status":"completed","before":case["before"],
            "after":{"error_rate":0,"p95_ms":200},"verification":"improved","recovered":True,"message":"actual measurements"}

    def test_live_scope_metadata_is_recorded_with_observations(self):
        metrics=self.service.query(self.inv,"metrics")
        logs=self.service.query(self.inv,"logs")
        self.assertTrue(any("cumulative" in note for note in metrics["scope_notes"]))
        self.assertTrue(any("sample" in note for note in logs["scope_notes"]))
        stored=IncidentService(self.service.cases,self.service.database).get_investigation(self.inv)
        observations=[call["payload"]["result"] for call in stored["calls"] if call["tool"] in {"query_metrics","query_logs"}]
        self.assertTrue(all(observation["scope_notes"] for observation in observations))

    def test_pending_never_executes_and_preserves_live_mode(self):
        self.service.lab.execute.assert_not_called()
        self.assertEqual(self.proposal["expected_revision"],7)
        self.assertEqual(self.proposal["mode"],"local_live")
        self.assertEqual(self.report["data_mode"],"local_live")

    def test_decision_is_committed_before_external_side_effect(self):
        def execute(pid,payload,cid):
            reloaded=IncidentService(self.service.cases,self.service.database).get_investigation(self.inv)
            self.assertEqual(reloaded["proposals"][0]["status"],"approved")
            self.assertEqual(reloaded["proposals"][0]["result"]["execution_status"],"waiting")
            self.assertEqual(pid,self.pid)
            return self.completed
        self.service.lab.execute.side_effect=execute
        self.assertTrue(self.service.decide_proposal(self.pid,"approve")["result"]["recovered"])
        self.service.decide_proposal(self.pid,"approve")
        self.assertEqual(self.service.lab.execute.call_count,1)
        with self.assertRaises(IncidentError):self.service.decide_proposal(self.pid,"reject")

    def test_lost_response_can_reconcile_same_operation_after_restart(self):
        self.service.lab.execute.side_effect=LabUnavailable("response lost")
        first=self.service.decide_proposal(self.pid,"approve")
        self.assertEqual(first["status"],"approved")
        self.assertEqual(first["result"]["execution_status"],"unknown")
        self.assertFalse(first["result"]["recovered"])
        reloaded=IncidentService(self.service.cases,self.service.database)
        reloaded.lab=Mock();reloaded.lab.execute.return_value=self.completed
        self.assertEqual(reloaded.decide_proposal(self.pid,"approve")["result"],self.completed)
        self.assertEqual(reloaded.lab.execute.call_args.args[0],self.pid)

    def test_stale_revision_does_not_claim_success_or_repeat(self):
        self.service.lab.execute.side_effect=LabUnavailable("Stale proposal: investigate again")
        result=self.service.decide_proposal(self.pid,"approve")["result"]
        self.assertEqual(result["execution_status"],"failed")
        self.assertFalse(result["recovered"])
        self.service.decide_proposal(self.pid,"approve")
        self.assertEqual(self.service.lab.execute.call_count,1)

    def test_rejected_live_proposal_never_executes(self):
        self.service.decide_proposal(self.pid,"reject")
        self.service.lab.execute.assert_not_called()


if __name__=="__main__":unittest.main()

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from incident_tools.domain import IncidentService, IncidentError
from test_domain import report, FIXTURES

class MitigationTests(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp(prefix="incident-mitigation-")) / "ledger.sqlite3"
        self.service = IncidentService(FIXTURES, self.db)
        self.inv = self.service.start_investigation("INC-001")["investigation_id"]
        self.service.query(self.inv,"metrics")
        self.service.query(self.inv,"logs")
        self.refs = ["INC-001:metrics:1","INC-001:logs:1"]
        self.rid = self.service.submit_report(self.inv,report(self.refs))["report_id"]

    def propose(self, action="restore_order_pool"):
        return self.service.propose_mitigation(self.inv,self.rid,action,"人工核对配置并观察负载",self.refs)

    def test_proposal_does_not_execute(self):
        p = self.propose()
        self.assertEqual(p["status"],"pending")
        saved = self.service.get_investigation(self.inv)["proposals"][0]
        self.assertIsNone(saved["result"])
        self.assertIsNone(saved["actor"])

    def test_unknown_action_and_unrelated_report_rejected(self):
        with self.assertRaises(IncidentError): self.propose("run_shell")
        other = self.service.start_investigation("INC-001")["investigation_id"]
        with self.assertRaisesRegex(IncidentError,"belong"):
            self.service.propose_mitigation(other,self.rid,"restore_order_pool","reason",self.refs)
        with self.assertRaisesRegex(IncidentError,"supporting"):
            self.service.propose_mitigation(self.inv,self.rid,"restore_order_pool","reason",["INC-001:metrics:2"])

    def test_recovery_proposal_requires_current_cross_source_primary_evidence(self):
        with self.assertRaisesRegex(IncidentError,"two current"):
            self.service.propose_mitigation(self.inv,self.rid,"restore_order_pool","reason",self.refs[:1])

    def test_approval_is_atomic_idempotent_and_persistent(self):
        p = self.propose()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _: self.service.decide_proposal(p["proposal_id"],"approve"),range(4)))
        self.assertEqual(sum(not r["replayed"] for r in results),1)
        self.assertTrue(all(r["result"]["recovered"] for r in results))
        recovered=IncidentService(FIXTURES,self.db).get_investigation(self.inv)
        self.assertEqual(len([c for c in recovered["calls"] if c["tool"]=="human_decision"]),1)
        self.assertEqual(recovered["proposals"][0]["actor"],"local-browser-user")

    def test_rejection_is_terminal_and_never_simulates(self):
        p = self.propose()
        result=self.service.decide_proposal(p["proposal_id"],"reject")
        self.assertIsNone(result["result"])
        with self.assertRaisesRegex(IncidentError,"immutable"):
            self.service.decide_proposal(p["proposal_id"],"approve")

    def test_wrong_action_cannot_claim_recovery(self):
        p = self.propose("restore_inventory_concurrency")
        result=self.service.decide_proposal(p["proposal_id"],"approve")["result"]
        self.assertFalse(result["recovered"])
        self.assertEqual(result["before"],result["after"])
        self.assertEqual(result["verification"],"no_improvement")

    def test_insufficient_evidence_only_allows_diagnostic_proposal(self):
        inv=self.service.start_investigation("INC-003")["investigation_id"]
        self.service.query(inv,"metrics")
        refs=["INC-003:metrics:1"]
        rid=self.service.submit_report(inv,report(refs,verdict="insufficient_evidence"))["report_id"]
        with self.assertRaisesRegex(IncidentError,"Insufficient"):
            self.service.propose_mitigation(inv,rid,"restore_order_pool","guess",refs)
        p=self.service.propose_mitigation(inv,rid,"collect_missing_observations","补证",refs)
        result=self.service.decide_proposal(p["proposal_id"],"approve")["result"]
        self.assertFalse(result["recovered"])
        self.assertEqual(result["verification"],"diagnostic_task_created")
        self.assertEqual(self.service.query(inv,"logs")["status"],"unavailable")

if __name__=="__main__": unittest.main()

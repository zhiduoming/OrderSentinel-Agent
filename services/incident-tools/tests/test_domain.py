import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock
from incident_tools.lab_client import LabUnavailable

from incident_tools.domain import IncidentError, IncidentService

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def report(support, counter=None, verdict="likely_cause"):
    return {"verdict": verdict, "summary": "候选原因需进一步核验", "impact": "提交订单请求异常",
            "recommended_next_step": "核对配置后人工决定处置",
            "missing_information": ["确认变更审批记录"] if verdict == "insufficient_evidence" else [],
            "hypotheses": [{"description": "候选原因", "confidence": "medium",
                            "supporting_evidence": support, "counter_evidence": counter or []}]}


class DomainTests(unittest.TestCase):
    def setUp(self):
        # Preserve test artifacts; do not delete user files or directories.
        self.path = Path(tempfile.mkdtemp(prefix="incident-test-")) / "ledger.sqlite3"
        self.service = IncidentService(FIXTURES, self.path)
        self.service.lab = Mock()
        self.service.lab.cases.side_effect = LabUnavailable("isolated fixture test")
        self.run = self.service.start_investigation("INC-001")["investigation_id"]

    def test_inventory_does_not_expose_gold_answer(self):
        listing = self.service.list_cases()
        self.assertEqual(len(listing["cases"]), 3)
        self.assertNotIn("root_cause", json.dumps(listing))
        self.assertEqual(listing["data_mode"], "synthetic")

    def test_unknown_case_and_path_traversal_rejected(self):
        for value in ["../fixtures/INC-001", "../../.env.local", "INC-404"]:
            with self.assertRaises(IncidentError):
                self.service.start_investigation(value)

    def test_report_rejects_unread_and_fabricated_evidence(self):
        for ref in ["INC-001:metrics:1", "made-up-evidence"]:
            with self.assertRaisesRegex(IncidentError, "not read"):
                self.service.submit_report(self.run, report([ref]))
        self.assertEqual(self.service.get_investigation(self.run)["reports"], [])

    def test_cross_investigation_evidence_rejected(self):
        self.service.query(self.run, "metrics")
        second = self.service.start_investigation("INC-001")["investigation_id"]
        with self.assertRaisesRegex(IncidentError, "not read"):
            self.service.submit_report(second, report(["INC-001:metrics:1"]))

    def test_likely_cause_needs_two_support_sources(self):
        self.service.query(self.run, "metrics")
        with self.assertRaisesRegex(IncidentError, "two data kinds"):
            self.service.submit_report(self.run, report(["INC-001:metrics:1", "INC-001:metrics:2"]))

    def test_history_cannot_substitute_for_current_causal_evidence(self):
        self.service.query(self.run, "metrics")
        self.service.query(self.run, "history")
        with self.assertRaisesRegex(IncidentError, "current observations"):
            self.service.submit_report(self.run, report(["INC-001:metrics:1", "INC-001:history:1"]))

    def test_saved_report_and_observations_survive_restart(self):
        self.service.query(self.run, "metrics")
        self.service.query(self.run, "logs")
        saved = self.service.submit_report(self.run, report(["INC-001:metrics:1", "INC-001:logs:1"]))
        restarted = IncidentService(FIXTURES, self.path)
        recovered = restarted.get_investigation(self.run)
        self.assertEqual(recovered["reports"][0]["report_id"], saved["report_id"])
        self.assertEqual([c["tool"] for c in recovered["calls"]],
                         ["start_investigation", "query_metrics", "query_logs", "submit_report"])

    def test_unavailable_source_and_honest_partial_report(self):
        run = self.service.start_investigation("INC-003")["investigation_id"]
        result = self.service.query(run, "logs")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["records"], [])
        self.service.query(run, "metrics")
        self.service.submit_report(run, report(["INC-003:metrics:1"], verdict="insufficient_evidence"))
        bad = report(["INC-003:metrics:1"], verdict="insufficient_evidence")
        bad["missing_information"] = []
        with self.assertRaises(IncidentError):
            self.service.submit_report(run, bad)

    def test_conflicting_reference_rejected(self):
        self.service.query(self.run, "metrics")
        with self.assertRaisesRegex(IncidentError, "support and counter"):
            self.service.submit_report(self.run, report(["INC-001:metrics:1"], ["INC-001:metrics:1"]))

    def test_filtered_results_do_not_mark_other_records_read(self):
        self.service.query(self.run, "logs", contains="201")
        with self.assertRaisesRegex(IncidentError, "not read"):
            self.service.submit_report(self.run, report(["INC-001:logs:1"]))

    def test_query_limits_enforced(self):
        for limit in [0, -1, 51]:
            with self.assertRaises(IncidentError):
                self.service.query(self.run, "logs", limit=limit)

    def test_concurrent_queries_and_evidence_deduplication(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.service.query(self.run, "metrics"), range(8)))
        state = self.service.get_investigation(self.run)
        sequences = [c["sequence"] for c in state["calls"]]
        self.assertEqual(len(sequences), 9)
        self.assertEqual(sequences, sorted(set(sequences)))
        with closing(self.service.connect()) as db:
            count = db.execute("SELECT count(*) FROM evidence_reads WHERE investigation_id=?", (self.run,)).fetchone()[0]
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()

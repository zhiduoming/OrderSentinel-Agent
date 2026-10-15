"""Fixed browser operations via stdin JSON; this module is not an MCP server."""
import json
import os
import sys
from pathlib import Path
from incident_tools.domain import IncidentService, IncidentError
root = Path(__file__).resolve().parent
service = IncidentService(root / "fixtures", Path(os.environ["INCIDENT_DATABASE"]))
try:
    request = json.loads(sys.stdin.read(45001))
    operation = request["operation"]
    if operation == "cases":
        result = service.list_cases()
    elif operation == "prepare_lab":
        result = service.prepare_lab(request["scenario"])
    elif operation == "list":
        result = service.list_investigations()
    elif operation == "get":
        result = service.get_investigation(request["investigation_id"])
    elif operation == "actions":
        result = service.mitigation_actions()
    elif operation == "propose":
        result = service.propose_mitigation(request["investigation_id"], request["report_id"],
            request["action"], request["rationale"], request["evidence_ids"])
    elif operation == "decide":
        result = service.decide_proposal(request["proposal_id"], request["decision"])
    else:
        raise IncidentError("Unknown browser operation")
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
except (IncidentError, ValueError, KeyError, TypeError) as error:
    print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
    sys.exit(2)

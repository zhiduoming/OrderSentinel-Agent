"""Fixed loopback adapter. Models cannot supply URLs, credentials, or commands."""
import json
import re
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = "http://127.0.0.1:19103"
LIVE_ID = re.compile(r"LIVE-[a-f0-9]{32}\Z")


class LabUnavailable(ValueError):
    pass


class LabClient:
    def request(self, path, body=None):
        headers = {"Content-Type":"application/json"}
        if body is not None:
            try: headers["X-Lab-Token"] = (ROOT/".incident/lab/control-token").read_text().strip()
            except OSError: raise LabUnavailable("本地实验未启动，请运行 incident:lab")
        request = urllib.request.Request(BASE+path,headers=headers,
                  data=json.dumps(body).encode() if body is not None else None)
        # Explicitly bypass ambient proxies for the fixed loopback address.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request,timeout=30) as response:
                return json.loads(response.read(2_000_000))
        except urllib.error.HTTPError as error:
            try: reason = json.loads(error.read()).get("detail","Local lab rejected operation")
            except ValueError: reason = "Local lab rejected operation"
            raise LabUnavailable(str(reason)) from None
        except (OSError, ValueError):
            raise LabUnavailable("本地实验不可用或响应超时；检查 incident:lab。若已审批，重试只核对同一操作，不创建新操作。") from None

    def cases(self):
        return self.request("/cases")

    def case(self, case_id):
        if not LIVE_ID.fullmatch(case_id): raise LabUnavailable("Invalid local case ID")
        return self.request("/cases/"+case_id)

    def prepare(self, scenario):
        if scenario not in ("pool","inventory"): raise LabUnavailable("Unknown local scenario")
        return self.request("/experiments", {"scenario":scenario})

    def execute(self, proposal_id, payload, case_id):
        return self.request("/executions", {"operation_id":proposal_id,"case_id":case_id,
                            "expected_revision":payload["expected_revision"],"action":payload["action"]})

import os
import time
import secrets
from collections import deque
from datetime import datetime, timezone
from fastapi import Header, HTTPException
from prometheus_client import Counter, Histogram


def stamp():
    return datetime.now(timezone.utc).isoformat()


def authorize(x_lab_token: str = Header(default="")):
    expected = os.environ.get("INCIDENT_LAB_TOKEN", "")
    if not expected or not secrets.compare_digest(x_lab_token, expected):
        raise HTTPException(403, "Local lab control token required")


class Telemetry:
    def __init__(self, service):
        self.service = service
        self.boot_id = secrets.token_hex(8)
        self.logs = deque(maxlen=400)
        self.traces = deque(maxlen=400)
        self.requests = Counter("lab_requests_total", "Actual business requests", ["service", "status"])
        self.latency = Histogram("lab_request_seconds", "Actual request duration", ["service"],
                                 buckets=(.05,.1,.2,.3,.5,1,2))

    def observe(self, trace_id, began, status, spans, error=None):
        duration = (time.perf_counter() - began) * 1000
        self.requests.labels(self.service, str(status)).inc()
        self.latency.labels(self.service).observe(duration/1000)
        self.traces.append({"trace_id": trace_id, "service": self.service, "status": status,
                            "duration_ms": round(duration,2), "spans": spans, "observed_at": stamp()})
        self.logs.append({"trace_id": trace_id, "service": self.service, "status": status,
                          "message": error or "request completed", "observed_at": stamp()})

    def snapshot(self):
        return {"service": self.service, "boot_id": self.boot_id,
                "logs": list(self.logs), "traces": list(self.traces)}


def span(name, began, status="ok"):
    return {"name": name, "duration_ms": round((time.perf_counter()-began)*1000,2), "status": status}

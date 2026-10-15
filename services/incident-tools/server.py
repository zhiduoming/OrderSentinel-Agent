"""Run with `uv run server.py`; stdout is reserved for MCP messages."""
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from incident_tools.domain import IncidentService

ROOT = Path(__file__).resolve().parent
service = IncidentService(ROOT / "fixtures", Path(os.environ.get(
    "INCIDENT_DATABASE", str(ROOT / ".data" / "investigations.sqlite3"))))
mcp = FastMCP("incident-observability")


@mcp.tool()
def list_cases() -> dict[str, Any]:
    """List synthetic and real local-lab incident alerts. Titles do not reveal the correct cause."""
    return service.list_cases()


@mcp.tool()
def start_investigation(case_id: str) -> dict[str, Any]:
    """Create an isolated investigation ledger and return its alert."""
    return service.start_investigation(case_id)


@mcp.tool()
def query_metrics(investigation_id: str) -> dict[str, Any]:
    """Query service/database/downstream metrics; compare version and time scope."""
    return service.query(investigation_id, "metrics")


@mcp.tool()
def search_logs(investigation_id: str, contains: str = "", limit: int = 20) -> dict[str, Any]:
    """Search incident logs; unavailable is a real missing data condition."""
    return service.query(investigation_id, "logs", contains, limit)


@mcp.tool()
def query_traces(investigation_id: str) -> dict[str, Any]:
    """Inspect request spans and downstream failure timing."""
    return service.query(investigation_id, "traces")


@mcp.tool()
def query_deployments(investigation_id: str) -> dict[str, Any]:
    """Query release and configuration changes. Correlation alone is not causation."""
    return service.query(investigation_id, "deployments")


@mcp.tool()
def search_history(investigation_id: str, contains: str = "") -> dict[str, Any]:
    """Retrieve historical incident hints, which may not share this incident's cause."""
    return service.query(investigation_id, "history", contains)


@mcp.tool()
def submit_report(investigation_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Save a report with verdict, summary, impact, recommended_next_step,
    missing_information list, and hypotheses list. Each hypothesis contains
    description, confidence(low/medium/high), supporting_evidence ID list,
    counter_evidence ID list. References must have been queried in this investigation.
    likely_cause requires the primary hypothesis supported by >=2 current data kinds (history does not count).
    This checks evidence integrity, not causal truth. No infrastructure is changed.
    """
    return service.submit_report(investigation_id, report)


@mcp.tool()
def get_investigation(investigation_id: str) -> dict[str, Any]:
    """Read ordered tool observations and saved reports for a known investigation."""
    return service.get_investigation(investigation_id)


@mcp.tool()
def mitigation_actions() -> dict[str, Any]:
    """List fixed, case-dependent simulation or local-lab actions and risks. This reveals no case outcome."""
    return service.mitigation_actions()


@mcp.tool()
def propose_mitigation(investigation_id: str, report_id: str, action: str,
                       rationale: str, evidence_ids: list[str]) -> dict[str, Any]:
    """Create an immutable proposal (synthetic simulation or isolated local-lab execution) from a saved evidence report.
    Does not approve or execute. A human must decide in the browser report panel.
    Insufficient evidence only allows collect_missing_observations.
    """
    return service.propose_mitigation(investigation_id, report_id, action, rationale, evidence_ids)


if __name__ == "__main__":
    mcp.run(transport="stdio")

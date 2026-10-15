"""Create an isolated Craft workspace without overwriting existing user configuration."""
import json
import secrets
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".incident"
WORKSPACE = STATE / "workspaces" / "incident-demo"
SOURCE = WORKSPACE / "sources" / "incident-observability"


def write_once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def bootstrap():
    stamp = int(time.time() * 1000)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    write_once(WORKSPACE / "config.json", {
        "id": "incident-demo", "name": "订单服务故障调查", "slug": "incident-demo",
        "createdAt": stamp, "updatedAt": stamp,
        "defaults": {"permissionMode": "safe", "enabledSourceSlugs": ["incident-observability"],
                     "defaultLlmConnection": "bailian-token-plan", "model": "qwen3.7-plus"},
        "localMcpServers": {"enabled": True}})
    write_once(STATE / "config" / "config.json", {
        "workspaces": [{"id": "incident-demo", "name": "订单服务故障调查", "slug": "incident-demo",
                        "rootPath": str(WORKSPACE), "createdAt": stamp}],
        "activeWorkspaceId": "incident-demo", "activeSessionId": None, "setupDeferred": True,
        "browserToolEnabled": False, "defaultLlmConnection": "bailian-token-plan",
        "llmConnections": [
            {"slug": "bailian-token-plan", "name": "百炼 Token Plan", "providerType": "pi_compat", "piAuthProvider": "openai",
             "authType": "api_key", "baseUrl": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
             "customEndpoint": {"api": "openai-completions", "supportsImages": False},
             "models": ["qwen3.7-plus", "qwen3.8-flash"], "defaultModel": "qwen3.7-plus",
             "modelSelectionMode": "userDefined3Tier"},
            {"slug": "deepseek-official", "name": "DeepSeek 官方", "providerType": "pi_compat", "piAuthProvider": "openai",
             "authType": "api_key", "baseUrl": "https://api.deepseek.com/v1",
             "customEndpoint": {"api": "openai-completions", "supportsImages": False},
             "models": ["deepseek-flash"], "defaultModel": "deepseek-flash",
             "modelSelectionMode": "userDefined3Tier"}]})
    # Only these eleven business tools are additionally admitted in Explore mode.
    # Some persist audit/report artifacts; none execute infrastructure changes.
    names = ["list_cases", "start_investigation", "query_metrics", "search_logs", "query_traces",
             "query_deployments", "search_history", "submit_report", "get_investigation", "mitigation_actions", "propose_mitigation"]
    write_once(SOURCE / "config.json", {
        "id": "incident-observability", "name": "故障观测数据", "slug": "incident-observability",
        "enabled": True, "provider": "incident-demo", "type": "mcp", "icon": "🔎",
        "tagline": "实际本地故障实验、合成案例与证据报告校验", "isAuthenticated": True,
        "mcp": {"transport": "stdio", "command": str(ROOT / "services/incident-tools/.venv/bin/python"),
                "args": [str(ROOT / "services/incident-tools/server.py")],
                "env": {"INCIDENT_DATABASE": str(STATE / "incident-ledger.sqlite3"), "PYTHONDONTWRITEBYTECODE": "1"}}})
    write_once(SOURCE / "permissions.json", {"allowedMcpPatterns": [
        "(" + "|".join(names) + ")$"]})
    for source, target in [
        (ROOT / "incident/source-guide.md", SOURCE / "guide.md"),
        (ROOT / "incident/skills/incident-investigator/SKILL.md", WORKSPACE / "skills/incident-investigator/SKILL.md")]:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
    token = STATE / "server-token"
    if not token.exists():
        token.write_text(secrets.token_hex(32))
        token.chmod(0o600)
    # The token doubles as a local browser login password; do not print it to logs.
    print("Incident workspace ready. Login password: .incident/server-token (local file).")


if __name__ == "__main__":
    bootstrap()

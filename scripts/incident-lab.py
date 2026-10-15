"""Start isolated PostgreSQL and three local Python services. Never removes state."""
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".incident/lab"
PYTHON = ROOT / "services/incident-tools/.venv/bin/python"
STATE.mkdir(parents=True, exist_ok=True)
for name in ("control-token", "pg-password"):
    path = STATE / name
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
env = dict(os.environ, INCIDENT_LAB_STATE=str(STATE), PYTHONDONTWRITEBYTECODE="1",
           INCIDENT_LAB_TOKEN=(STATE/"control-token").read_text().strip(),
           INCIDENT_LAB_PG_PASSWORD=(STATE/"pg-password").read_text().strip())
env["INCIDENT_LAB_DSN"] = "postgresql://incident:"+env["INCIDENT_LAB_PG_PASSWORD"]+"@127.0.0.1:15432/incident_lab"
service_env = {key:env[key] for key in ("PATH","LANG","LC_ALL","TMPDIR","PYTHONDONTWRITEBYTECODE",
               "INCIDENT_LAB_STATE","INCIDENT_LAB_TOKEN","INCIDENT_LAB_DSN") if key in env}
children, logs = [], []


def stop(signum=None, frame=None):
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for child in children:
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired: child.kill(); child.wait()
    for log in logs: log.close()


def ready(port):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"http://127.0.0.1:{port}/health",timeout=1) as response:
            return response.status==200
    except Exception: return False


try:
    if any(ready(port) for port in (19101,19102,19103)):
        raise RuntimeError("Lab ports are already occupied; inspect the existing instance first")
    subprocess.run(["docker","compose","--progress","plain","-f",str(ROOT/"lab/compose.yaml"),"up","-d","--wait"],env=env,check=True)
    for module, port in (("inventory",19102),("orders",19101),("controller",19103)):
        log = (STATE/f"{module}.log").open("a")
        logs.append(log)
        child = subprocess.Popen([str(PYTHON),"-m","uvicorn",f"lab.{module}:app","--host","127.0.0.1","--port",str(port),"--no-access-log"],
                                 cwd=ROOT/"services/incident-tools",env=service_env,stdout=log,stderr=log)
        children.append(child)
        for _ in range(80):
            if child.poll() is not None: raise RuntimeError(f"{module} exited; see .incident/lab/{module}.log")
            if ready(port): break
            time.sleep(.25)
        else: raise RuntimeError(f"{module} did not become ready")
    print("Local lab ready: orders 19101 / inventory 19102 / controller 19103",flush=True)
    print("Browser: create a local experiment in 故障调查. Ctrl+C stops Python services; PostgreSQL and saved evidence are retained.",flush=True)
    signal.signal(signal.SIGTERM,lambda a,b:sys.exit(0))
    while all(child.poll() is None for child in children): time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    stop()

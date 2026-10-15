"""Durable local experiment snapshots and idempotent, revision-checked actuations."""
import asyncio
import json
import math
import os
import sqlite3
import time
from contextlib import closing, asynccontextmanager
from pathlib import Path
from uuid import uuid4
import httpx
import psycopg
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import Literal
from .common import authorize, stamp

STATE = Path(os.environ["INCIDENT_LAB_STATE"])
DB = STATE / "lab.sqlite3"
lock = asyncio.Lock()
client = None


def connect():
    return sqlite3.connect(DB, timeout=10)


@asynccontextmanager
async def lifespan(app):
    global client
    STATE.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as db, db:
        db.executescript("""
          CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT);
          CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),active_case TEXT,revision INTEGER NOT NULL);
          INSERT OR IGNORE INTO state VALUES(1,NULL,0);
          UPDATE state SET active_case=NULL,revision=revision+1 WHERE id=1;
          CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,case_id TEXT,action TEXT,revision INTEGER,status TEXT,payload TEXT);
        """)
    client = httpx.AsyncClient(trust_env=False, timeout=20, limits=httpx.Limits(max_connections=64,max_keepalive_connections=48))
    yield
    await client.aclose()


app = FastAPI(title="Incident Lab Controller", lifespan=lifespan)


async def call(port, path, body=None):
    response = await client.request("POST" if body is not None else "GET", f"http://127.0.0.1:{port}{path}",
                                    json=body, headers={"X-Lab-Token": os.environ["INCIDENT_LAB_TOKEN"]})
    response.raise_for_status()
    return response.json()


async def health_pair():
    a,b = await asyncio.gather(call(19101,"/health"), call(19102,"/health"))
    return {"order": a, "inventory": b}


async def load():
    semaphore = asyncio.Semaphore(24)
    readings, trace_ids, pools, queues = [], set(), [], []
    running = True
    async def sample():
        while running:
            a,b = await asyncio.gather(call(19101,"/observations"),call(19102,"/observations"))
            pools.append(a["pool"]);queues.append(b["waiting"])
            await asyncio.sleep(.03)
    async def request():
        async with semaphore:
            trace_id = uuid4().hex
            trace_ids.add(trace_id)
            began = time.perf_counter()
            try:
                response = await client.post("http://127.0.0.1:19101/orders",headers={"X-Trace-ID":trace_id},timeout=2)
                code = response.status_code
            except httpx.HTTPError:
                code = 599
            readings.append({"latency_ms":(time.perf_counter()-began)*1000,"status":code})
    sampler = asyncio.create_task(sample())
    try:
        await asyncio.gather(*(request() for _ in range(120)))
    finally:
        running = False
        await sampler
    a,b = await asyncio.gather(call(19101,"/observations"),call(19102,"/observations"))
    durations = sorted(r["latency_ms"] for r in readings)
    errors = sum(r["status"]!=200 for r in readings)
    metrics = {"requests":len(readings),"errors":errors,"error_rate":errors/len(readings),
               "p95_ms":round(durations[math.ceil(len(durations)*.95)-1],2),
               "load_concurrency":24,"maximumPoolSize":a["maximumPoolSize"],
               "consumer_concurrency":b["consumer_concurrency"],
               "pool_waiting_peak":max((p.get("requests_waiting",0) for p in pools),default=0),
               "inventory_waiting_peak":max(queues,default=0),"observed_at":stamp()}
    return {"metrics":metrics,"logs":[r for x in (a,b) for r in x["logs"] if r["trace_id"] in trace_ids],
            "traces":[r for x in (a,b) for r in x["traces"] if r["trace_id"] in trace_ids]}


async def database_probe():
    began = time.perf_counter()
    async with await psycopg.AsyncConnection.connect(os.environ["INCIDENT_LAB_DSN"],autocommit=True) as conn:
        row = await (await conn.execute("SELECT count(*) FROM lab_orders")).fetchone()
        confirmed = await (await conn.execute("SELECT count(*) FROM lab_orders WHERE status='confirmed'" )).fetchone()
        reserved = await (await conn.execute("SELECT count(*) FROM lab_reservations" )).fetchone()
        stock = await (await conn.execute("SELECT remaining FROM lab_stock WHERE sku='demo-sku'" )).fetchone()
        activity = await (await conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()" )).fetchone()
        maximum = await (await conn.execute("SHOW max_connections")).fetchone()
    return {"service":"postgresql","counter_scope":"cumulative_all_lab_experiments","probe_ms":round((time.perf_counter()-began)*1000,2),
            "orders_persisted":row[0],"orders_confirmed":confirmed[0],"reservations":reserved[0],"stock_remaining":stock[0],"connections":activity[0],"max_connections":int(maximum[0]),
            "status":"reachable","observed_at":stamp()}


def get_case(case_id):
    with closing(connect()) as db:
        row = db.execute("SELECT payload FROM experiments WHERE id=?",(case_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Unknown local experiment")
    return json.loads(row[0])


@app.get("/health")
async def health():
    try:
        services = await health_pair()
        return {"status":"ok","services":services}
    except httpx.HTTPError:
        raise HTTPException(503,"Business services unavailable")


@app.get("/cases")
async def cases():
    with closing(connect()) as db:
        rows = db.execute("SELECT payload FROM experiments ORDER BY created_at DESC LIMIT 20").fetchall()
        active,revision = db.execute("SELECT active_case,revision FROM state WHERE id=1").fetchone()
    fields = ("case_id","title","service","environment","alert","data_mode","revision")
    return {"data_mode":"local_live","active_case":active,"revision":revision,
            "cases":[{k:c[k] for k in fields} for c in (json.loads(r[0]) for r in rows)]}


@app.get("/cases/{case_id}")
async def snapshot(case_id: str):
    return get_case(case_id)


class Experiment(BaseModel):
    scenario: Literal["pool", "inventory"]


@app.post("/experiments", dependencies=[Depends(authorize)])
async def prepare(body: Experiment):
    if lock.locked():
        raise HTTPException(409,"Another experiment or approved execution is running")
    async with lock:
        # Invalidate the old evidence/config binding BEFORE the first external write.
        # Partial setup failures must not leave a previous proposal executable.
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM state WHERE id=1").fetchone()[0]+1
            db.execute("UPDATE state SET active_case=NULL,revision=? WHERE id=1",(revision,))
        await call(19101,"/control/config",{"maximumPoolSize":30})
        await call(19102,"/control/config",{"concurrency":16})
        baseline = await load()
        if baseline["metrics"]["error_rate"] > .05:
            raise HTTPException(503,"Healthy baseline failed; no fault experiment created")
        changed_at = stamp()
        if body.scenario == "pool":
            await call(19101,"/control/config",{"maximumPoolSize":5})
            change = {"service":"order-service","change":{"maximumPoolSize":{"before":30,"after":5}},
                      "message":"local runtime configuration change"}
        else:
            await call(19102,"/control/config",{"concurrency":2})
            change = {"service":"inventory-service","change":{"consumer_concurrency":{"before":16,"after":2}},
                      "message":"local runtime configuration change"}
        fault = await load()
        dbmetric = await database_probe()
        order_traces = sorted((r for r in fault["traces"] if r["service"]=="order-service"),key=lambda r:(r["status"]==200,-r["duration_ms"]))[:8]
        chosen_ids = {r["trace_id"] for r in order_traces}
        inventory_traces = sorted((r for r in fault["traces"] if r["service"]=="inventory-service"),key=lambda r:(r["trace_id"] not in chosen_ids,r["status"]==200))[:8]
        boots = await health_pair()
        case_id = "LIVE-" + uuid4().hex
        with closing(connect()) as db, db:
            db.execute("UPDATE state SET active_case=? WHERE id=1 AND revision=?",(case_id,revision))
            payload = {"case_id":case_id,"title":"提交订单错误率升高" if body.scenario=="pool" else "订单下游调用异常",
                       "service":"order-service","environment":"local-lab","data_mode":"local_live","revision":revision,
                       "boot_ids":{k:v["boot_id"] for k,v in boots.items()},
                       "alert":f"实测 {fault['metrics']['requests']} 次请求，错误率 {fault['metrics']['error_rate']:.1%}，P95 {fault['metrics']['p95_ms']}ms",
                       "metrics":[{"service":"order-service","phase":"healthy_baseline",**baseline["metrics"]},
                                  {"service":"order-service","phase":"fault",**fault["metrics"]},dbmetric],
                       "logs": sorted(fault["logs"],key=lambda r:r["status"]==200)[:18],
                       "traces":order_traces+inventory_traces,
                       "deployments":[{**change,"observed_at":changed_at,"revision":revision}],
                       "history":[],"before":fault["metrics"]}
            for kind in ("metrics","logs","traces","deployments","history"):
                for i,record in enumerate(payload[kind]):
                    record.update({"evidence_id":f"{case_id}:{kind}:{i+1}","environment":"local-lab",
                                   "data_mode":"local_live","source":"request-driven-lab"})
            db.execute("INSERT INTO experiments VALUES(?,?,?)",(case_id,json.dumps(payload,ensure_ascii=False),stamp()))
        return {k:payload[k] for k in ("case_id","title","alert","revision","data_mode")}


class Execution(BaseModel):
    operation_id: str
    case_id: str
    expected_revision: int
    action: Literal["restore_order_pool","restore_inventory_concurrency","collect_missing_observations"]


@app.get("/executions/{operation_id}", dependencies=[Depends(authorize)])
async def execution_status(operation_id: str):
    with closing(connect()) as db:
        row = db.execute("SELECT status,payload FROM executions WHERE id=?",(operation_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Execution has not been accepted")
    return {"execution_status":row[0],**json.loads(row[1])}


@app.post("/executions", dependencies=[Depends(authorize)])
async def execute(body: Execution):
    if len(body.operation_id)!=32 or any(c not in "0123456789abcdef" for c in body.operation_id):
        raise HTTPException(400,"Invalid operation ID")
    with closing(connect()) as db:
        existing = db.execute("SELECT case_id,action,revision,status,payload FROM executions WHERE id=?",(body.operation_id,)).fetchone()
    if existing:
        if existing[:3] != (body.case_id,body.action,body.expected_revision):
            raise HTTPException(409,"Operation ID cannot be reused with different parameters")
        return {"execution_status":existing[3],**json.loads(existing[4]),"replayed":True}
    if lock.locked():
        raise HTTPException(409,"Another experiment or approved execution is running")
    async with lock:
        case = get_case(body.case_id)
        boots = await health_pair()
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            active, revision = db.execute("SELECT active_case,revision FROM state WHERE id=1").fetchone()
            if (active,revision)!=(body.case_id,body.expected_revision) or case["boot_ids"]!={k:v["boot_id"] for k,v in boots.items()}:
                raise HTTPException(409,"Stale proposal: experiment/configuration or service lifetime changed; investigate again")
            result = {"mode":"local_live","operation_id":body.operation_id,"before":case["before"],
                      "after":None,"recovered":False,"verification":"running","message":"Approved local execution accepted"}
            db.execute("INSERT INTO executions VALUES(?,?,?,?,?,?)",(body.operation_id,body.case_id,body.action,revision,"running",json.dumps(result)))
            # Reserve revision before touching remote services. A crash cannot silently reuse it.
            db.execute("UPDATE state SET revision=revision+1 WHERE id=1")
        applied = False
        try:
            if body.action=="restore_order_pool":
                await call(19101,"/control/config",{"maximumPoolSize":30})
                applied = True
            elif body.action=="restore_inventory_concurrency":
                await call(19102,"/control/config",{"concurrency":16})
                applied = True
            after = (await load())["metrics"]
            diagnostics = body.action=="collect_missing_observations"
            recovered = not diagnostics and after["error_rate"] <= .05 and (after["error_rate"] < case["before"]["error_rate"] or after["p95_ms"] < case["before"]["p95_ms"]*.8)
            result.update({"after":after,"recovered":recovered,"configuration_applied":applied,
                           "verification":"diagnostics_collected" if diagnostics else "improved" if recovered else "no_improvement",
                           "message":"实际本地请求验证；120 次请求、并发 24。结果来自测量，不代表生产 SLO。"})
            status = "completed"
        except Exception as error:
            # Configuration may have changed even when verification failed. Never claim rollback/success.
            status = "failed"
            result.update({"verification":"verification_failed","configuration_applied":applied,
                           "message":"执行或验证失败；配置可能已改变，需要核对当前服务状态。", "error_type":type(error).__name__})
        with closing(connect()) as db, db:
            db.execute("UPDATE executions SET status=?,payload=? WHERE id=?",(status,json.dumps(result,ensure_ascii=False),body.operation_id))
        return {"execution_status":status,**result}

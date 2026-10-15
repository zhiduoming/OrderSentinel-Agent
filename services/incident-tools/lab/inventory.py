import os
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool
import asyncio
import time
from uuid import uuid4
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from prometheus_client import make_asgi_app
from .common import Telemetry, authorize, span

pool = None

@asynccontextmanager
async def lifespan(app):
    global pool
    pool = AsyncConnectionPool(os.environ["INCIDENT_LAB_DSN"], min_size=2, max_size=16, open=False)
    await pool.open()
    await pool.wait(timeout=15)
    async with pool.connection() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS lab_stock(sku TEXT PRIMARY KEY, remaining INTEGER NOT NULL CHECK(remaining>=0))")
        await conn.execute("INSERT INTO lab_stock VALUES('demo-sku',1000000) ON CONFLICT DO NOTHING")
        await conn.execute("CREATE TABLE IF NOT EXISTS lab_reservations(trace_id TEXT PRIMARY KEY,sku TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT now())")
    yield
    await pool.close()

app = FastAPI(title="Incident Lab Inventory", lifespan=lifespan)
telemetry = Telemetry("inventory-service")
concurrency = 16
slots = asyncio.Semaphore(concurrency)
active = 0
waiting = 0
peak_waiting = 0
app.mount("/metrics", make_asgi_app())


class Config(BaseModel):
    concurrency: int = Field(ge=2, le=16)


@app.get("/health")
async def health():
    return {"status": "ok", "boot_id": telemetry.boot_id, "consumer_concurrency": concurrency}


@app.get("/observations", dependencies=[Depends(authorize)])
async def observations():
    return {**telemetry.snapshot(), "consumer_concurrency": concurrency,
            "active": active, "waiting": waiting, "peak_waiting": peak_waiting}


@app.post("/control/config", dependencies=[Depends(authorize)])
async def configure(config: Config):
    global concurrency, slots, peak_waiting
    if active or waiting:
        raise HTTPException(409, "Cannot reconfigure while requests are in flight")
    concurrency = config.concurrency
    slots = asyncio.Semaphore(concurrency)
    peak_waiting = 0
    return await health()


@app.post("/reserve")
async def reserve(x_trace_id: str = Header(default="")):
    global active, waiting, peak_waiting
    trace_id = x_trace_id or uuid4().hex
    began = time.perf_counter()
    spans = []
    waiting += 1
    peak_waiting = max(peak_waiting, waiting)
    acquired = False
    try:
        queue_began = time.perf_counter()
        await asyncio.wait_for(slots.acquire(), timeout=.18)
        acquired = True
        waiting -= 1
        spans.append(span("inventory.queue", queue_began))
        active += 1
        processing = time.perf_counter()
        # Bounded real work delay, deliberately controlled for repeatable experiments.
        await asyncio.sleep(.06)
        async with pool.connection() as conn:
            inserted = await (await conn.execute("INSERT INTO lab_reservations(trace_id,sku) VALUES(%s,'demo-sku') ON CONFLICT DO NOTHING RETURNING trace_id",(trace_id,))).fetchone()
            if inserted:
                reserved = await (await conn.execute("UPDATE lab_stock SET remaining=remaining-1 WHERE sku='demo-sku' AND remaining>0 RETURNING remaining")).fetchone()
                if not reserved:
                    telemetry.observe(trace_id,began,409,spans,"stock exhausted")
                    raise HTTPException(409,"stock exhausted")
        spans.append(span("inventory.reserve", processing))
        telemetry.observe(trace_id, began, 200, spans)
        return {"reserved": True, "trace_id": trace_id}
    except TimeoutError:
        waiting -= 1
        spans.append(span("inventory.queue", queue_began, "timeout"))
        telemetry.observe(trace_id, began, 503, spans, "inventory concurrency queue timeout")
        raise HTTPException(503, "inventory queue timeout")
    finally:
        if acquired:
            active -= 1
            slots.release()

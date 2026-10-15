import os
import time
from uuid import uuid4
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from prometheus_client import make_asgi_app
from .common import Telemetry, authorize, span

telemetry = Telemetry("order-service")
maximum = 30
pool = None
client = None


async def open_pool(size):
    candidate = AsyncConnectionPool(os.environ["INCIDENT_LAB_DSN"], min_size=size, max_size=size,
                                   open=False, timeout=.10, kwargs={"autocommit": True})
    await candidate.open()
    await candidate.wait(timeout=15)
    return candidate


@asynccontextmanager
async def lifespan(app):
    global pool, client
    pool = await open_pool(maximum)
    async with pool.connection() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS lab_orders (id TEXT PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT now(),status TEXT NOT NULL DEFAULT 'pending')")
    async with pool.connection() as conn:
        await conn.execute("ALTER TABLE lab_orders ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'")
    client = httpx.AsyncClient(trust_env=False, base_url="http://127.0.0.1:19102", timeout=.6,
                              limits=httpx.Limits(max_connections=40, max_keepalive_connections=40))
    yield
    await client.aclose()
    await pool.close()


app = FastAPI(title="Incident Lab Orders", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


class Config(BaseModel):
    maximumPoolSize: int = Field(ge=5, le=30)


@app.get("/health")
async def health():
    return {"status": "ok", "boot_id": telemetry.boot_id, "maximumPoolSize": maximum}


@app.get("/observations", dependencies=[Depends(authorize)])
async def observations():
    return {**telemetry.snapshot(), "maximumPoolSize": maximum, "pool": pool.get_stats()}


@app.post("/control/config", dependencies=[Depends(authorize)])
async def configure(config: Config):
    global maximum, pool
    if pool.get_stats().get("requests_waiting", 0) or pool.get_stats().get("pool_available",0) < maximum:
        raise HTTPException(409, "Cannot reconfigure while requests are in flight")
    replacement = await open_pool(config.maximumPoolSize)
    old = pool
    pool = replacement
    maximum = config.maximumPoolSize
    await old.close()
    return await health()


@app.post("/orders")
async def submit(x_trace_id: str = Header(default="")):
    trace_id = x_trace_id or uuid4().hex
    order_id = uuid4().hex
    began = time.perf_counter()
    spans = []
    status = 200
    error = None
    try:
        acquire_began = time.perf_counter()
        try:
            async with pool.connection(timeout=.10) as conn:
                spans.append(span("db.acquire", acquire_began))
                query_began = time.perf_counter()
                # Actual SQL work holds a real PostgreSQL connection for 120ms.
                await conn.execute("SELECT pg_sleep(0.12)")
                await conn.execute("INSERT INTO lab_orders(id) VALUES (%s)", (order_id,))
                spans.append(span("db.write_order", query_began))
        except PoolTimeout:
            spans.append(span("db.acquire", acquire_began, "timeout"))
            status, error = 503, "PostgreSQL connection pool acquire timeout"
            raise HTTPException(status, error)
        downstream_began = time.perf_counter()
        response = await client.post("/reserve", headers={"X-Trace-ID": trace_id})
        spans.append(span("inventory.http", downstream_began, "ok" if response.status_code==200 else "error"))
        if response.status_code != 200:
            status, error = 503, "inventory-service reservation failed"
            raise HTTPException(status, error)
        confirm_began = time.perf_counter()
        async with pool.connection() as conn:
            await conn.execute("UPDATE lab_orders SET status='confirmed' WHERE id=%s",(order_id,))
        spans.append(span("db.confirm_order",confirm_began))
        return {"order_created": True, "order_id":order_id, "trace_id": trace_id}
    except PoolTimeout:
        status, error = 503, "PostgreSQL connection pool confirmation timeout"
        spans.append(span("db.confirm_order",confirm_began,"timeout"))
        raise HTTPException(status,error)
    except httpx.HTTPError:
        status, error = 503, "inventory-service transport failure"
        raise HTTPException(status, error)
    except HTTPException:
        raise
    except Exception:
        status, error = 500, "database operation failed"
        raise HTTPException(status, error)
    finally:
        telemetry.observe(trace_id, began, status, spans, error)

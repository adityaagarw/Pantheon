"""Pantheon backend — FastAPI entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.agents.runtime import runtime
from app.api import (
    admin,
    attachments,
    comms,
    extend,
    meetings,
    memory,
    observe,
    orgs,
    secrets,
    stage,
    voice,
    whiteboards,
    work,
    world,
    ws,
)
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Provider
from app.services.comms import CommsError
from app.services.orgs import OrgError
from app.services.tasks import TaskError
from app.tools.base import ToolError
from app.tools.mcp import manager as mcp_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pantheon")

if sys.platform == "win32":  # MCP stdio servers need subprocess support
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def run_migrations() -> None:
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    here = Path(__file__).resolve().parent.parent
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["embedded"] = True
    command.upgrade(cfg, "head")


async def ensure_mock_provider() -> None:
    async with SessionLocal() as session:
        exists = (await session.execute(select(Provider).limit(1))).first()
        if exists is None:
            session.add(Provider(name="Mock (offline)", type="mock", models=[{"id": "mock"}],
                                 default_model="mock", is_default=True))
            await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_migrate:
        await asyncio.to_thread(run_migrations)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    for root in settings.workspace_root_paths:
        root.mkdir(parents=True, exist_ok=True)
    await ensure_mock_provider()
    from app import plugins
    from app.services import customtools, oversight
    from app.services import secrets as secrets_svc
    from app.services.supervisor import ensure_supervisor

    plugins.load_all()
    await customtools.load_all()
    await secrets_svc.warm()
    secrets_svc.install_log_filter()
    await ensure_supervisor()
    await runtime.start()
    watcher = asyncio.create_task(oversight.run_forever(), name="argus-watches")
    from app.services import memory as memory_svc

    backfill = asyncio.create_task(memory_svc.backfill(), name="memory-backfill")
    log.info("Pantheon runtime started")
    try:
        yield
    finally:
        watcher.cancel()
        backfill.cancel()
        await asyncio.gather(watcher, backfill, return_exceptions=True)
        await runtime.stop()
        from app.browser.manager import manager as browsers

        await browsers.stop()
        await mcp_manager.stop_all()


app = FastAPI(title="Pantheon", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


@app.exception_handler(OrgError)
@app.exception_handler(TaskError)
@app.exception_handler(CommsError)
@app.exception_handler(ToolError)
async def domain_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for r in (orgs.router, comms.router, meetings.router, work.router, observe.router, admin.router,
          voice.router, world.router, extend.router, stage.router, memory.router, whiteboards.router,
          attachments.router, secrets.router,
          ws.router):
    app.include_router(r)


@app.get("/api/v1/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "runtime": runtime.started}

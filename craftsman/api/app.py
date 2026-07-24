from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from craftsman.api.auth import require_scope
from craftsman.api.routers import (
    analytics,
    campaigns,
    inbox,
    keys,
    leads,
    mailboxes,
    meetings,
    ops,
    tasks,
    unsubscribe,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from craftsman.core.db import run_migrations
    from craftsman.core.logging import configure_logging

    configure_logging()
    # Apply schema migrations on startup so `docker compose up` stays one command.
    run_migrations()
    yield


# docs/openapi are disabled here and re-exposed below behind the `read` scope, so the
# API surface is not enumerable without a key.
app = FastAPI(
    title="Craftsman",
    description="Open-source AI SDR with a Thompson-sampling learning loop.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(leads.router)
app.include_router(campaigns.router)
app.include_router(inbox.router)
app.include_router(mailboxes.router)
app.include_router(analytics.router)
app.include_router(meetings.router)
app.include_router(keys.router)
app.include_router(ops.router)
app.include_router(tasks.router)
app.include_router(unsubscribe.router)


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_scope("read"))])
def openapi_spec():
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_scope("read"))])
def swagger_docs():
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Craftsman API")


@app.get("/metrics", include_in_schema=False, dependencies=[Depends(require_scope("read"))])
def metrics():
    from craftsman.core.metrics import metrics_payload

    payload, content_type = metrics_payload()
    return Response(content=payload, media_type=content_type)


@app.get("/health")
def health():
    return {"ok": True}

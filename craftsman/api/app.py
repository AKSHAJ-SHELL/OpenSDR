from contextlib import asynccontextmanager

from fastapi import FastAPI

from craftsman.api.routers import analytics, campaigns, inbox, leads, mailboxes, unsubscribe


@asynccontextmanager
async def lifespan(app: FastAPI):
    from craftsman.core.db import init_db

    init_db()
    yield


app = FastAPI(
    title="Craftsman",
    description="Open-source AI SDR with a Thompson-sampling learning loop.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(leads.router)
app.include_router(campaigns.router)
app.include_router(inbox.router)
app.include_router(mailboxes.router)
app.include_router(analytics.router)
app.include_router(unsubscribe.router)


@app.get("/health")
def health():
    return {"ok": True}

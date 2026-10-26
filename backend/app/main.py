"""Create the FastAPI application, initialize local tables, and register routes."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.database.extra_statutes import init_extra_statutes
from app.database.library import init_library
from app.routes import cases, chat, health, judges, library, research, statutes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    """Initialize local databases; AI clients and research indexes load on first use."""
    for initialize in (init_library, init_extra_statutes):
        try:
            initialize()
        except Exception:
            logger.exception("Database initialization failed: %s", initialize.__name__)
    yield


app = FastAPI(title="Courtroom AI API", version="2.0.0", lifespan=lifespan)
# Allow browser requests from the configured frontend origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for module in (health, judges, cases, statutes, chat, library, research):
    app.include_router(module.router)


@app.get("/")
def root():
    """Expose the application name and useful entry points."""
    return {
        "name": "Courtroom AI API",
        "docs": "/docs",
        "health": "/health",
        "library": "/api/v1/library/cases",
    }

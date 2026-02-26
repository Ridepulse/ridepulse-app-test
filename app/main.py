from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.routers import rides, ride_info, shows, restaurants, calendar, history
from app.scheduler import start_scheduler, stop_scheduler
from app.database import connect_db, close_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

SUPPORTED_PARKS = ["efteling", "disneylandparis", "europapark", "phantasialand"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting RidePulse API...")
    await connect_db()
    start_scheduler()
    yield
    logger.info("Shutting down...")
    stop_scheduler()
    await close_db()


app = FastAPI(
    title="RidePulse — Themepark Unified API",
    description=(
        "Unified real-time API for multiple theme parks. "
        "Live wait times, show schedules, restaurant status and opening calendars."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Register all routers for every supported park
for park_id in SUPPORTED_PARKS:
    app.include_router(rides.router,       prefix=f"/{park_id}", tags=[park_id])
    app.include_router(ride_info.router,   prefix=f"/{park_id}", tags=[park_id])
    app.include_router(shows.router,       prefix=f"/{park_id}", tags=[park_id])
    app.include_router(restaurants.router, prefix=f"/{park_id}", tags=[park_id])
    app.include_router(calendar.router,    prefix=f"/{park_id}", tags=[park_id])
    app.include_router(history.router,     prefix=f"/{park_id}", tags=[park_id])


@app.get("/", tags=["root"])
async def root():
    return {
        "api": "RidePulse Themepark API",
        "version": "1.0.0",
        "supported_parks": SUPPORTED_PARKS,
        "docs": "/docs",
    }


@app.get("/health", tags=["root"])
async def health():
    return {"status": "ok"}

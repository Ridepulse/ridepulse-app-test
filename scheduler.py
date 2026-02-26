"""
Background scheduler — runs two recurring jobs:
  1. fetch_all_live    — every 5 minutes: fetch live data from all park APIs
                         → saves historical wait times to MongoDB
                         → overwrites /data/{park_id}/live.json
  2. fetch_all_calendars — every 24 hours: fetch opening calendars
                         → overwrites /data/{park_id}/calendar.json
                         (calendar data is NOT stored in MongoDB)
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.connectors import CONNECTORS
from app.database import get_db

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))


def start_scheduler():
    scheduler.add_job(
        fetch_all_live,
        trigger=IntervalTrigger(minutes=5),
        id="fetch_live",
        name="Fetch live park data",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # run immediately on startup
    )
    scheduler.add_job(
        fetch_all_calendars,
        trigger=IntervalTrigger(hours=24),
        id="fetch_calendars",
        name="Fetch park calendars",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # run immediately on startup
    )
    scheduler.start()
    logger.info("Scheduler started (live: every 5 min, calendar: every 24h).")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")


# ──────────────────────────────────────────────
# Live data job (every 5 minutes)
# ──────────────────────────────────────────────

async def fetch_all_live():
    logger.info("Running fetch_all_live job...")
    for park_id, connector in CONNECTORS.items():
        try:
            await _fetch_park_live(park_id, connector)
        except Exception as e:
            logger.error(f"[{park_id}] Unhandled error in fetch_all_live: {e}", exc_info=True)


async def _fetch_park_live(park_id: str, connector):
    now = datetime.now(timezone.utc)
    db = get_db()

    # Fetch all data types from the park connector
    # Note: Efteling connector fetches WIS once per call; each method is separate
    # For efficiency, connectors can internally cache the raw response.
    attractions = await connector.fetch_wait_times()
    shows       = await connector.fetch_shows()
    restaurants = await connector.fetch_restaurants()
    shops       = await connector.fetch_shops()

    # ── Save historical wait times to MongoDB ──
    # Only attraction wait times are stored — static info (location, photos, etc.)
    # is intentionally excluded to save storage.
    if attractions and db is not None:
        docs = []
        for attr in attractions:
            doc = {
                "park_id": park_id,
                "ride_id": attr.id,
                "ride_name": attr.name,
                "status": attr.status.value,
                "wait_time": attr.wait_time,
                "timestamp": now,
            }
            # store single rider wait if available
            if attr.single_rider and attr.single_rider.available:
                doc["single_rider_wait"] = attr.single_rider.wait_time
                doc["single_rider_status"] = attr.single_rider.status
            # store virtual queue state if available
            if attr.virtual_queue and attr.virtual_queue.available:
                doc["virtual_queue_state"] = attr.virtual_queue.state.value if attr.virtual_queue.state else None

            docs.append(doc)

        if docs:
            try:
                await db.wait_times.insert_many(docs, ordered=False)
                logger.info(f"[{park_id}] Inserted {len(docs)} wait time records into MongoDB.")
            except Exception as e:
                logger.error(f"[{park_id}] MongoDB insert failed: {e}")

    # ── Save live snapshot JSON ──
    # This JSON is read directly by the API endpoints on every request.
    # It contains everything needed for the live response.
    live_data = {
        "park_id": park_id,
        "park_name": connector.park_name,
        "last_updated": now.isoformat(),
        "park_status": _derive_park_status(attractions),
        "attractions": [a.model_dump(mode="json") for a in attractions],
        "shows": [s.model_dump(mode="json") for s in shows],
        "restaurants": [r.model_dump(mode="json") for r in restaurants],
        "shops": [sh.model_dump(mode="json") for sh in shops],
    }
    _save_json(park_id, "live.json", live_data)
    logger.info(f"[{park_id}] Live snapshot saved ({len(attractions)} rides, {len(shows)} shows, "
                f"{len(restaurants)} restaurants, {len(shops)} shops).")


def _derive_park_status(attractions) -> str:
    if not attractions:
        return "unknown"
    open_count = sum(1 for a in attractions if a.status.value in ("open", "open_soon"))
    return "open" if open_count > 0 else "closed"


# ──────────────────────────────────────────────
# Calendar job (every 24 hours)
# ──────────────────────────────────────────────

async def fetch_all_calendars():
    logger.info("Running fetch_all_calendars job...")
    for park_id, connector in CONNECTORS.items():
        try:
            calendar = await connector.fetch_calendar()
            _save_json(park_id, "calendar.json", calendar.model_dump(mode="json"))
            logger.info(f"[{park_id}] Calendar saved ({len(calendar.days)} days).")
        except Exception as e:
            logger.error(f"[{park_id}] Error fetching calendar: {e}", exc_info=True)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _save_json(park_id: str, filename: str, data: dict):
    park_dir = DATA_DIR / park_id
    park_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = park_dir / f"{filename}.tmp"
    final_path = park_dir / filename
    # write to temp file first, then atomic rename to avoid partial reads
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str, ensure_ascii=False, indent=2)
    tmp_path.rename(final_path)

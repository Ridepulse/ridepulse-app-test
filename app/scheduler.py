"""
Background Scheduler
====================
Runs two recurring jobs:

  fetch_all_live      — every 5 minutes
      • calls fetch_wait_times / fetch_shows / fetch_restaurants / fetch_shops
        on every park connector in app/parks/
      • stores historical wait times in MongoDB
      • writes /data/{park_id}/live.json  (read by API endpoints)

  fetch_all_calendars — every 24 hours
      • calls fetch_calendar on every park connector
      • overwrites /data/{park_id}/calendar.json
      • calendar data is NOT stored in MongoDB
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.parks import PARKS
from app.database import get_db

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))


def start_scheduler():
    scheduler.add_job(
        fetch_all_live,
        trigger=IntervalTrigger(minutes=5),
        id="fetch_live",
        name="Fetch live park data (every 5 min)",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # run immediately on startup
    )
    scheduler.add_job(
        fetch_all_calendars,
        trigger=IntervalTrigger(hours=24),
        id="fetch_calendars",
        name="Fetch park calendars (every 24h)",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # run immediately on startup
    )
    scheduler.start()
    logger.info("Scheduler started — live: every 5 min | calendar: every 24h")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")


# ──────────────────────────────────────────────
# Job: live data (every 5 minutes)
# ──────────────────────────────────────────────

async def fetch_all_live():
    logger.info("─── fetch_all_live started ───")
    for park_id, connector in PARKS.items():
        try:
            await _fetch_park_live(park_id, connector)
        except Exception as e:
            logger.error(f"[{park_id}] Unhandled error in fetch_all_live: {e}", exc_info=True)
    logger.info("─── fetch_all_live done ───")


async def _fetch_park_live(park_id: str, connector):
    now = datetime.now(timezone.utc)
    db  = get_db()

    # Fetch from park connector
    # Efteling: all four methods share one cached WIS HTTP call
    attractions = await connector.fetch_wait_times()
    shows       = await connector.fetch_shows()
    restaurants = await connector.fetch_restaurants()
    shops       = await connector.fetch_shops()

    # ── MongoDB: store historical wait times ──
    # Static info (location, photos, capacity) is intentionally excluded
    # to keep the collection lean and the ride-info JSON editable separately.
    if attractions and db is not None:
        docs = []
        for attr in attractions:
            doc = {
                "park_id":   park_id,
                "ride_id":   attr.id,
                "ride_name": attr.name,
                "status":    attr.status.value,
                "wait_time": attr.wait_time,
                "timestamp": now,
            }
            if attr.single_rider and attr.single_rider.available:
                doc["single_rider_wait"]   = attr.single_rider.wait_time
                doc["single_rider_status"] = attr.single_rider.status
            if attr.virtual_queue and attr.virtual_queue.available:
                doc["virtual_queue_state"] = (
                    attr.virtual_queue.state.value if attr.virtual_queue.state else None
                )
            docs.append(doc)

        try:
            await db.wait_times.insert_many(docs, ordered=False)
            logger.info(f"[{park_id}] Inserted {len(docs)} wait time records.")
        except Exception as e:
            logger.error(f"[{park_id}] MongoDB insert failed: {e}")

    # ── Write live.json snapshot ──
    # Atomic write: tmp file → rename, so API never reads a partial file
    live_data = {
        "park_id":     park_id,
        "park_name":   connector.park_name,
        "last_updated": now.isoformat(),
        "park_status": _derive_park_status(attractions),
        "attractions": [a.model_dump(mode="json") for a in attractions],
        "shows":       [s.model_dump(mode="json") for s in shows],
        "restaurants": [r.model_dump(mode="json") for r in restaurants],
        "shops":       [sh.model_dump(mode="json") for sh in shops],
    }
    _write_json(park_id, "live.json", live_data)
    logger.info(
        f"[{park_id}] live.json written — "
        f"{len(attractions)} attractions, {len(shows)} shows, "
        f"{len(restaurants)} restaurants, {len(shops)} shops."
    )


def _derive_park_status(attractions) -> str:
    if not attractions:
        return "unknown"
    return "open" if any(
        a.status.value in ("open", "open_soon") for a in attractions
    ) else "closed"


# ──────────────────────────────────────────────
# Job: calendars (every 24 hours)
# ──────────────────────────────────────────────

async def fetch_all_calendars():
    logger.info("─── fetch_all_calendars started ───")
    for park_id, connector in PARKS.items():
        try:
            calendar = await connector.fetch_calendar()
            _write_json(park_id, "calendar.json", calendar.model_dump(mode="json"))
            logger.info(f"[{park_id}] calendar.json written ({len(calendar.days)} days).")
        except Exception as e:
            logger.error(f"[{park_id}] Calendar error: {e}", exc_info=True)
    logger.info("─── fetch_all_calendars done ───")


# ──────────────────────────────────────────────
# Helper: atomic JSON write
# ──────────────────────────────────────────────

def _write_json(park_id: str, filename: str, data: dict):
    """Write JSON atomically using a temp file + rename."""
    park_dir = DATA_DIR / park_id
    park_dir.mkdir(parents=True, exist_ok=True)
    tmp   = park_dir / f"{filename}.tmp"
    final = park_dir / filename
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str, ensure_ascii=False, indent=2)
    tmp.rename(final)

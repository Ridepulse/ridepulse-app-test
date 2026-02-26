"""
Data Loader
===========
Helpers for loading JSON files from disk.

  /data/{park_id}/live.json      — written every 5 min by scheduler
  /data/{park_id}/calendar.json  — written every 24h by scheduler
  /ride-info/{park_id}.json      — edited manually (static ride info)
"""

import json
import os
from pathlib import Path
from fastapi import HTTPException, Request

DATA_DIR      = Path(os.getenv("DATA_DIR",      "/data"))
RIDE_INFO_DIR = Path(os.getenv("RIDE_INFO_DIR", "/ride-info"))

SUPPORTED_PARKS = {"efteling", "disneylandparis", "europapark", "phantasialand"}


def get_park_id(request: Request) -> str:
    """Extract and validate the park_id from the request path."""
    park_id = request.url.path.strip("/").split("/")[0]
    if park_id not in SUPPORTED_PARKS:
        raise HTTPException(status_code=404, detail=f"Park '{park_id}' not found.")
    return park_id


def load_live_json(park_id: str) -> dict:
    path = DATA_DIR / park_id / "live.json"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Live data for '{park_id}' not yet available — scheduler may still be starting up.",
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_calendar_json(park_id: str) -> dict:
    path = DATA_DIR / park_id / "calendar.json"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Calendar for '{park_id}' not yet available.",
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ride_info(park_id: str) -> dict:
    """
    Load static ride info from /ride-info/{park_id}.json.
    This file is mounted as a volume and edited manually —
    it is never overwritten by the scheduler.
    """
    path = RIDE_INFO_DIR / f"{park_id}.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Ride info for '{park_id}' not found. "
                   f"Expected file: ride-info/{park_id}.json",
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

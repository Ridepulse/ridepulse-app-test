"""
Efteling Park Connector
=======================
Live data source: https://api.efteling.com/app/wis/
Calendar source:  https://www.efteling.com/service/cached/getpoiinfo/en/{year}/{month}

All field names and state strings are based on the official Efteling app source.

WIS AttractionInfo entry fields:
  Id            — unique identifier
  Name          — display name
  Type          — "Attraction" | "Attracties" | "Shows en Entertainment"
                  | "Horeca" | "Souvenirwinkel"
  State         — ride state string (see _map_state)
  WaitingTime   — integer minutes (attractions)
  ShowTimes     — upcoming show times (shows)
  PastShowTimes — past show times (shows)
  OpeningTimes  — [{HourFrom, HourTo}] (restaurants, shops)
  VirtualQueue  — {State, WaitingTime} (attractions, optional)
"""

import httpx
import logging
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.parks.base import BaseParkConnector
from app.models.schemas import (
    AttractionLive, AttractionStatus,
    SingleRiderInfo, VirtualQueueInfo, VirtualQueueState,
    Show, ShowTime,
    Restaurant, Shop, VenueStatus,
    ParkCalendar, ParkDay, ParkHours,
    Location,
)

logger = logging.getLogger(__name__)

PARK_TIMEZONE = ZoneInfo("Europe/Amsterdam")
VQ_WINDOW_MINUTES = 15  # Efteling return time window duration

WIS_URL      = "https://api.efteling.com/app/wis/"
CALENDAR_URL = "https://www.efteling.com/service/cached/getpoiinfo/en/{year}/{month}"

# Headers matching the official Efteling Android app
WIS_HEADERS = {
    "User-Agent":       "okhttp/4.12.0",
    "Accept-Encoding":  "gzip",
    "x-app-version":    "5.0.0",
    "x-app-name":       "Efteling",
    "x-app-id":         "nl.efteling.android",
    "x-app-platform":   "Android",
    "x-app-language":   "en",
    "x-app-timezone":   "Europe/Amsterdam",
}

CALENDAR_HEADERS = {
    "User-Agent":        "Mozilla/5.0 (compatible; RidePulse/1.0)",
    "X-Requested-With":  "XMLHttpRequest",
    "Referer":           "https://www.efteling.com/en/park/opening-hours?app=true",
    "Cookie":            "website#lang=en",
}


# ──────────────────────────────────────────────
# State mapping
# ──────────────────────────────────────────────

def _map_state(raw: str) -> AttractionStatus:
    """
    Map Efteling WIS State strings → unified AttractionStatus.

    Known states (from official app source):
      open                  → open
      nognietopen           → open_soon    (not yet open, opening later today)
      storing               → breakdown    (unplanned interruption)
      tijdelijkbuitenbedrijf → breakdown   (temporary out of service)
      inonderhoud           → maintenance  (planned refurbishment)
      buitenbedrijf         → closed       (closed for the day)
      gesloten              → closed
      wachtrijgesloten      → closed       (queue closed)
      ""                    → closed
    """
    state = (raw or "").lower().strip()
    mapping = {
        "open":                    AttractionStatus.open,
        "nognietopen":             AttractionStatus.open_soon,
        "storing":                 AttractionStatus.breakdown,
        "tijdelijkbuitenbedrijf":  AttractionStatus.breakdown,
        "inonderhoud":             AttractionStatus.maintenance,
        "buitenbedrijf":           AttractionStatus.closed,
        "gesloten":                AttractionStatus.closed,
        "wachtrijgesloten":        AttractionStatus.closed,
        "":                        AttractionStatus.closed,
    }
    if state not in mapping:
        logger.warning(f"[Efteling] Unknown State: '{raw}' — defaulting to closed")
        return AttractionStatus.closed
    return mapping[state]


# ──────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────

class EftelingConnector(BaseParkConnector):
    park_id   = "efteling"
    park_name = "Efteling"

    # Internal WIS cache — avoids 4 identical HTTP calls per scheduler tick
    _wis_cache: Optional[dict] = None
    _wis_cache_time: Optional[datetime] = None
    WIS_CACHE_SECONDS = 60

    async def _fetch_wis(self) -> dict:
        """
        Fetch the raw WIS response. Cached for WIS_CACHE_SECONDS so that
        fetch_wait_times, fetch_shows, fetch_restaurants and fetch_shops
        all share the same response within one scheduler run.
        """
        now = datetime.now(timezone.utc)
        if (
            self._wis_cache is not None
            and self._wis_cache_time is not None
            and (now - self._wis_cache_time).total_seconds() < self.WIS_CACHE_SECONDS
        ):
            return self._wis_cache

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(WIS_URL, params={"language": "en"}, headers=WIS_HEADERS)
            resp.raise_for_status()
            data = resp.json()

        self._wis_cache = data
        self._wis_cache_time = now
        logger.info("[Efteling] WIS fetched and cached.")
        return data

    # ──────────────────────────────────────────
    # Attractions
    # ──────────────────────────────────────────

    async def fetch_wait_times(self) -> List[AttractionLive]:
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_wait_times failed: {e}")
            return []

        all_entries = {e["Id"]: e for e in data.get("AttractionInfo", [])}

        # Identify single-rider sub-entries by Id suffix
        # Efteling single-rider Ids typically end with "sr" or contain "singlerider"
        single_rider_map: dict[str, str] = {}  # sr_id → parent_id
        for entry_id in all_entries:
            lower = entry_id.lower()
            if "singlerider" in lower or lower.endswith("sr"):
                parent_id = entry_id.lower().replace("singlerider", "").replace("sr", "")
                # find closest matching parent key
                match = next((k for k in all_entries if k.lower() == parent_id), None)
                if match:
                    single_rider_map[entry_id] = match

        # droomvluchtstandby → droomvlucht (official app hack)
        special_merge = {"droomvluchtstandby": "droomvlucht"}

        attractions: List[AttractionLive] = []
        skip_ids = set(single_rider_map.keys())

        for entry_id, entry in all_entries.items():
            if entry.get("Type") not in ("Attraction", "Attracties"):
                continue
            if entry_id in skip_ids:
                continue

            resolved_id = special_merge.get(entry_id, entry_id)
            status = _map_state(entry.get("State", ""))

            wait_time: Optional[int] = None
            if status == AttractionStatus.open:
                try:
                    wait_time = int(entry.get("WaitingTime") or 0)
                except (ValueError, TypeError):
                    wait_time = None

            # ── Single Rider ──
            single_rider: Optional[SingleRiderInfo] = None
            sr_id = next((k for k, v in single_rider_map.items() if v == entry_id), None)
            if sr_id and sr_id in all_entries:
                sr = all_entries[sr_id]
                sr_status = _map_state(sr.get("State", ""))
                sr_wait: Optional[int] = None
                if sr_status == AttractionStatus.open:
                    try:
                        sr_wait = int(sr.get("WaitingTime") or 0)
                    except (ValueError, TypeError):
                        sr_wait = None
                single_rider = SingleRiderInfo(
                    available=True,
                    status="open" if sr_status == AttractionStatus.open else "closed",
                    wait_time=sr_wait,
                )

            # ── Virtual Queue ──
            virtual_queue: Optional[VirtualQueueInfo] = None
            vq_raw = entry.get("VirtualQueue")
            if vq_raw:
                vq_state_raw = (vq_raw.get("State") or "").lower()
                vq_wait = vq_raw.get("WaitingTime")

                if vq_state_raw == "walkin":
                    # No VQ needed right now — walk straight in
                    virtual_queue = VirtualQueueInfo(
                        available=True,
                        state=VirtualQueueState.temporarily_full,
                    )
                elif vq_state_raw == "enabled":
                    # Calculate return window: now + WaitingTime, window = +15 min
                    now_park = datetime.now(PARK_TIMEZONE).replace(second=0, microsecond=0)
                    try:
                        wait_min = int(vq_wait or 0)
                    except (ValueError, TypeError):
                        wait_min = 0
                    return_start = now_park + timedelta(minutes=wait_min)
                    return_end   = return_start + timedelta(minutes=VQ_WINDOW_MINUTES)
                    virtual_queue = VirtualQueueInfo(
                        available=True,
                        state=VirtualQueueState.available,
                        return_start=return_start,
                        return_end=return_end,
                    )
                elif vq_state_raw == "full":
                    virtual_queue = VirtualQueueInfo(
                        available=True,
                        state=VirtualQueueState.full,
                    )
                else:
                    logger.warning(f"[Efteling] Unknown VQ state '{vq_state_raw}' for {entry_id}")
                    virtual_queue = VirtualQueueInfo(
                        available=True,
                        state=VirtualQueueState.closed,
                    )

            attractions.append(AttractionLive(
                id=resolved_id,
                name=entry.get("Name", "Unknown"),
                status=status,
                wait_time=wait_time,
                single_rider=single_rider,
                virtual_queue=virtual_queue,
            ))

        logger.info(f"[Efteling] {len(attractions)} attractions fetched.")
        return attractions

    # ──────────────────────────────────────────
    # Shows
    # ──────────────────────────────────────────

    async def fetch_shows(self) -> List[Show]:
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_shows failed: {e}")
            return []

        shows: List[Show] = []
        now_park = datetime.now(PARK_TIMEZONE)

        for entry in data.get("AttractionInfo", []):
            if entry.get("Type") != "Shows en Entertainment":
                continue

            raw_times = (entry.get("ShowTimes") or []) + (entry.get("PastShowTimes") or [])
            show_times: List[ShowTime] = []
            for t in raw_times:
                start = _parse_dt(t.get("StartDateTime"))
                end   = _parse_dt(t.get("EndDateTime"))
                if start and end:
                    show_times.append(ShowTime(
                        start_date_time=start,
                        end_date_time=end,
                        edition=t.get("Edition") or "Showtime",
                    ))

            show_times.sort(key=lambda x: x.start_date_time)
            has_upcoming = any(st.start_date_time >= now_park for st in show_times)

            shows.append(Show(
                id=entry.get("Id", "").lower(),
                name=entry.get("Name", "Unknown"),
                status="open" if has_upcoming else "closed",
                show_times=show_times,
            ))

        logger.info(f"[Efteling] {len(shows)} shows fetched.")
        return shows

    # ──────────────────────────────────────────
    # Restaurants
    # ──────────────────────────────────────────

    async def fetch_restaurants(self) -> List[Restaurant]:
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_restaurants failed: {e}")
            return []

        restaurants: List[Restaurant] = []
        for entry in data.get("AttractionInfo", []):
            if entry.get("Type") != "Horeca":
                continue

            times = entry.get("OpeningTimes") or []
            if times:
                opening_time = _parse_dt(times[0].get("HourFrom"))
                closing_time = _parse_dt(times[0].get("HourTo"))
                status = VenueStatus.open
            else:
                opening_time = None
                closing_time = None
                status = VenueStatus.closed

            restaurants.append(Restaurant(
                id=entry.get("Id", "").lower(),
                name=entry.get("Name", "Unknown"),
                status=status,
                opening_time=opening_time,
                closing_time=closing_time,
            ))

        logger.info(f"[Efteling] {len(restaurants)} restaurants fetched.")
        return restaurants

    # ──────────────────────────────────────────
    # Shops
    # ──────────────────────────────────────────

    async def fetch_shops(self) -> List[Shop]:
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_shops failed: {e}")
            return []

        shops: List[Shop] = []
        for entry in data.get("AttractionInfo", []):
            if entry.get("Type") != "Souvenirwinkel":
                continue

            times = entry.get("OpeningTimes") or []
            if times:
                opening_time = _parse_dt(times[0].get("HourFrom"))
                closing_time = _parse_dt(times[0].get("HourTo"))
                status = VenueStatus.open
            else:
                opening_time = None
                closing_time = None
                status = VenueStatus.closed

            shops.append(Shop(
                id=entry.get("Id", "").lower(),
                name=entry.get("Name", "Unknown"),
                status=status,
                opening_time=opening_time,
                closing_time=closing_time,
            ))

        logger.info(f"[Efteling] {len(shops)} shops fetched.")
        return shops

    # ──────────────────────────────────────────
    # Calendar
    # ──────────────────────────────────────────

    async def fetch_calendar(self) -> ParkCalendar:
        """
        Fetch opening calendar for the next 3 months.
        Endpoint returns: { OpeningHours: [{ Date, OpeningHours: [{Open, Close}] }] }
        Multiple OpeningHours entries per day = regular + evening hours.
        """
        days: List[ParkDay] = []
        now = datetime.now(PARK_TIMEZONE)

        for offset in range(3):
            target = (now.replace(day=1) + timedelta(days=32 * offset)).replace(day=1)
            url = CALENDAR_URL.format(year=target.year, month=target.month)
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(url, headers=CALENDAR_HEADERS)
                    if resp.status_code == 400:
                        # Efteling returns 400 for past months
                        continue
                    resp.raise_for_status()
                    cal = resp.json()
            except Exception as e:
                logger.error(f"[Efteling] Calendar fetch failed ({target.year}/{target.month}): {e}")
                continue

            for day_entry in cal.get("OpeningHours", []):
                raw_hours = sorted(
                    day_entry.get("OpeningHours", []),
                    key=lambda h: h.get("Open", "00:00"),
                )
                park_hours = [
                    ParkHours(
                        opening_time=h.get("Open", ""),
                        closing_time=h.get("Close", ""),
                        type="operating" if idx == 0 else "informational",
                        description=None if idx == 0 else "Evening Hours",
                    )
                    for idx, h in enumerate(raw_hours)
                ]
                days.append(ParkDay(
                    date=day_entry.get("Date", ""),
                    is_open=len(park_hours) > 0,
                    hours=park_hours,
                ))

        logger.info(f"[Efteling] Calendar fetched: {len(days)} days.")
        return ParkCalendar(
            park_id=self.park_id,
            park_name=self.park_name,
            last_updated=datetime.now(timezone.utc),
            days=days,
        )


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _parse_dt(value) -> Optional[datetime]:
    """Parse Efteling ISO 8601 datetime strings with timezone offset."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=PARK_TIMEZONE)
        return dt
    except Exception:
        logger.debug(f"[Efteling] Could not parse datetime: {value!r}")
        return None

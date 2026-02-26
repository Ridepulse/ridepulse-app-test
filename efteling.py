"""
Efteling Connector
==================
Fetches live data from the official Efteling WIS API.
API base: https://api.efteling.com/app/wis/

All field names and status strings are derived from the official Efteling app source.

WIS response structure (AttractionInfo array):
  entry.Id           — unique ride/show/restaurant ID
  entry.Name         — display name
  entry.Type         — "Attraction" | "Attracties" | "Shows en Entertainment" | "Horeca" | "Souvenirwinkel"
  entry.State        — ride state string (see _map_efteling_state)
  entry.WaitingTime  — integer minutes (attractions only)
  entry.ShowTimes    — list of upcoming show times (shows only)
  entry.PastShowTimes — list of past show times (shows only)
  entry.OpeningTimes — list of {HourFrom, HourTo} (restaurants/shops)
  entry.VirtualQueue — object with .State and .WaitingTime (optional, attractions only)
"""

import httpx
import logging
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.connectors.base import BaseParkConnector
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
VIRTUAL_QUEUE_WINDOW_MINUTES = 15   # Efteling return time window

WIS_URL = "https://api.efteling.com/app/wis/"
CALENDAR_URL = "https://www.efteling.com/service/cached/getpoiinfo/en/{year}/{month}"

# Headers matching the official Efteling Android app (from source analysis)
WIS_HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "Accept-Encoding": "gzip",
    "x-app-version": "5.0.0",       # update to latest app version if requests fail
    "x-app-name": "Efteling",
    "x-app-id": "nl.efteling.android",
    "x-app-platform": "Android",
    "x-app-language": "en",
    "x-app-timezone": "Europe/Amsterdam",
}

CALENDAR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ThemeparkAPI/1.0)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.efteling.com/en/park/opening-hours?app=true",
    "Cookie": "website#lang=en",
}


# ──────────────────────────────────────────────
# State → unified status mapping
# ──────────────────────────────────────────────

def _map_state(raw: str) -> AttractionStatus:
    """
    Map Efteling WIS State strings to unified AttractionStatus.

    Known states from source:
      storing              → breakdown   (unplanned interruption)
      tijdelijkbuitenbedrijf → breakdown (temporary out of service)
      buitenbedrijf        → closed      (closed for the day)
      inonderhoud          → maintenance (planned refurbishment)
      gesloten             → closed
      wachtrijgesloten     → closed      (queue closed, ride may reopen)
      nognietopen          → open_soon   (not yet open, opening later today)
      open                 → open
      (empty string)       → closed
    """
    state = (raw or "").lower().strip()
    mapping = {
        "open": AttractionStatus.open,
        "nognietopen": AttractionStatus.open_soon,
        "storing": AttractionStatus.breakdown,
        "tijdelijkbuitenbedrijf": AttractionStatus.breakdown,
        "inonderhoud": AttractionStatus.maintenance,
        "buitenbedrijf": AttractionStatus.closed,
        "gesloten": AttractionStatus.closed,
        "wachtrijgesloten": AttractionStatus.closed,
        "": AttractionStatus.closed,
    }
    status = mapping.get(state)
    if status is None:
        logger.warning(f"[Efteling] Unknown State value: '{raw}' — defaulting to closed")
        return AttractionStatus.closed
    return status


def _map_venue_state(raw: str) -> VenueStatus:
    return VenueStatus.open if (raw or "").lower() == "open" else VenueStatus.closed


# ──────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────

class EftelingConnector(BaseParkConnector):
    park_id = "efteling"
    park_name = "Efteling"

    _wis_cache: dict = None
    _wis_cache_time: datetime = None
    WIS_CACHE_SECONDS = 60  # reuse WIS response within same scheduler run

    async def _fetch_wis(self) -> dict:
        """
        Fetch raw WIS response. Shared by attractions, shows, restaurants, shops.
        Internally cached for WIS_CACHE_SECONDS to avoid 4 identical HTTP calls
        per scheduler tick (one per fetch_* method).
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
        logger.info("[Efteling] WIS data fetched and cached.")
        return data

    # ──────────────────────────────────────────
    # Attractions (wait times)
    # ──────────────────────────────────────────

    async def fetch_wait_times(self) -> List[AttractionLive]:
        """
        Parse attractions and their wait times from the WIS response.

        Single-rider queues appear as separate entries in AttractionInfo with a
        different Id. The mapping from ride → single-rider-id lives in the POI
        feed (alternateid field). Since we don't load the POI feed here, we do a
        reverse lookup: any AttractionInfo entry whose Id is NOT in the POI set
        but IS referenced by another entry's singleRiderId gets attached to its
        parent ride.

        Strategy used here (without POI feed):
          - Build a dict of all entries by Id.
          - For each entry of Type "Attraction"/"Attracties", look for a partner
            entry that shares the same name but has "singlerider" in the Id,
            or has a much lower wait time and a name containing "Single".
          - This is a best-effort approach; for a perfect mapping load the POI feed.
        """
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_wait_times failed: {e}")
            return []

        all_entries = {e["Id"]: e for e in data.get("AttractionInfo", [])}

        # Build a lookup: singleRider entry Id → parent ride Id
        # Efteling single rider IDs typically end with "sr" or contain "singlerider"
        single_rider_map: dict[str, str] = {}
        for entry_id, entry in all_entries.items():
            lower_id = entry_id.lower()
            if "singlerider" in lower_id or lower_id.endswith("sr"):
                # try to find the parent by stripping the suffix
                parent_id = entry_id.replace("singlerider", "").replace("SR", "").replace("sr", "")
                if parent_id in all_entries:
                    single_rider_map[entry_id] = parent_id

        # Also handle droomvlucht standby hack (from source)
        # droomvluchtstandby → droomvlucht
        special_merge = {"droomvluchtstandby": "droomvlucht"}

        attractions: List[AttractionLive] = []
        processed_sr_ids = set(single_rider_map.keys())

        for entry_id, entry in all_entries.items():
            entry_type = entry.get("Type", "")
            if entry_type not in ("Attraction", "Attracties"):
                continue
            # skip single-rider sub-entries (they'll be attached to their parent)
            if entry_id in processed_sr_ids:
                continue
            # resolve droomvlucht standby → main entry
            resolved_id = special_merge.get(entry_id, entry_id)

            status = _map_state(entry.get("State", ""))
            raw_wait = entry.get("WaitingTime")
            wait_time: Optional[int] = None
            if status == AttractionStatus.open and raw_wait is not None:
                try:
                    wait_time = int(raw_wait)
                except (ValueError, TypeError):
                    wait_time = None

            # ── Single Rider ──
            single_rider: Optional[SingleRiderInfo] = None
            sr_entry_id = next((k for k, v in single_rider_map.items() if v == entry_id), None)
            if sr_entry_id and sr_entry_id in all_entries:
                sr_entry = all_entries[sr_entry_id]
                sr_status = _map_state(sr_entry.get("State", ""))
                sr_wait_raw = sr_entry.get("WaitingTime")
                sr_wait: Optional[int] = None
                if sr_status == AttractionStatus.open and sr_wait_raw is not None:
                    try:
                        sr_wait = int(sr_wait_raw)
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
                vq_waiting = vq_raw.get("WaitingTime")  # minutes until return window opens

                if vq_state_raw == "walkin":
                    # No VQ needed right now — walk in directly
                    virtual_queue = VirtualQueueInfo(
                        available=True,
                        state=VirtualQueueState.temporarily_full,
                        return_start=None,
                        return_end=None,
                    )
                elif vq_state_raw == "enabled":
                    # VQ active — calculate return window
                    now_park = datetime.now(PARK_TIMEZONE)
                    # blank seconds/microseconds for clean times
                    now_park = now_park.replace(second=0, microsecond=0)
                    try:
                        wait_min = int(vq_waiting) if vq_waiting is not None else 0
                    except (ValueError, TypeError):
                        wait_min = 0
                    return_start = now_park + timedelta(minutes=wait_min)
                    return_end = return_start + timedelta(minutes=VIRTUAL_QUEUE_WINDOW_MINUTES)
                    virtual_queue = VirtualQueueInfo(
                        available=True,
                        state=VirtualQueueState.available,
                        return_start=return_start,
                        return_end=return_end,
                    )
                elif vq_state_raw == "full":
                    # VQ full for the rest of the day
                    virtual_queue = VirtualQueueInfo(
                        available=True,
                        state=VirtualQueueState.full,
                        return_start=None,
                        return_end=None,
                    )
                else:
                    logger.warning(f"[Efteling] Unknown VirtualQueue state: '{vq_state_raw}' for {entry_id}")
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

        logger.info(f"[Efteling] Fetched {len(attractions)} attractions.")
        return attractions

    # ──────────────────────────────────────────
    # Shows
    # ──────────────────────────────────────────

    async def fetch_shows(self) -> List[Show]:
        """
        Parse shows from the WIS response.
        Type: "Shows en Entertainment"
        Each entry has ShowTimes (upcoming) and PastShowTimes (past) arrays.
        Each showtime has StartDateTime, EndDateTime, Edition.
        """
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_shows failed: {e}")
            return []

        shows: List[Show] = []
        for entry in data.get("AttractionInfo", []):
            if entry.get("Type") != "Shows en Entertainment":
                continue

            # Combine upcoming + past show times
            raw_times = (entry.get("ShowTimes") or []) + (entry.get("PastShowTimes") or [])
            show_times: List[ShowTime] = []
            for t in raw_times:
                start = _parse_efteling_dt(t.get("StartDateTime"))
                end = _parse_efteling_dt(t.get("EndDateTime"))
                if start and end:
                    show_times.append(ShowTime(
                        start_date_time=start,
                        end_date_time=end,
                        edition=t.get("Edition") or "Showtime",
                    ))

            # Sort by start time
            show_times.sort(key=lambda x: x.start_date_time)

            # Status: closed if no upcoming show times
            upcoming = [st for st in show_times if st.start_date_time >= datetime.now(PARK_TIMEZONE)]
            status = "open" if upcoming else "closed"

            shows.append(Show(
                id=entry.get("Id", "").lower(),
                name=entry.get("Name", "Unknown"),
                status=status,
                show_times=show_times,
            ))

        logger.info(f"[Efteling] Fetched {len(shows)} shows.")
        return shows

    # ──────────────────────────────────────────
    # Restaurants
    # ──────────────────────────────────────────

    async def fetch_restaurants(self) -> List[Restaurant]:
        """
        Parse restaurants from the WIS response.
        Type: "Horeca"
        Opening times are in entry.OpeningTimes as [{HourFrom, HourTo}].
        """
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_restaurants failed: {e}")
            return []

        restaurants: List[Restaurant] = []
        for entry in data.get("AttractionInfo", []):
            if entry.get("Type") != "Horeca":
                continue

            opening_times = entry.get("OpeningTimes") or []
            if opening_times:
                opening_time = _parse_efteling_dt(opening_times[0].get("HourFrom"))
                closing_time = _parse_efteling_dt(opening_times[0].get("HourTo"))
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

        logger.info(f"[Efteling] Fetched {len(restaurants)} restaurants.")
        return restaurants

    # ──────────────────────────────────────────
    # Shops
    # ──────────────────────────────────────────

    async def fetch_shops(self) -> List[Shop]:
        """
        Parse souvenir shops from the WIS response.
        Type: "Souvenirwinkel"
        """
        try:
            data = await self._fetch_wis()
        except Exception as e:
            logger.error(f"[Efteling] fetch_shops failed: {e}")
            return []

        shops: List[Shop] = []
        for entry in data.get("AttractionInfo", []):
            if entry.get("Type") != "Souvenirwinkel":
                continue

            opening_times = entry.get("OpeningTimes") or []
            if opening_times:
                opening_time = _parse_efteling_dt(opening_times[0].get("HourFrom"))
                closing_time = _parse_efteling_dt(opening_times[0].get("HourTo"))
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

        logger.info(f"[Efteling] Fetched {len(shops)} shops.")
        return shops

    # ──────────────────────────────────────────
    # Calendar
    # ──────────────────────────────────────────

    async def fetch_calendar(self) -> ParkCalendar:
        """
        Fetch Efteling opening calendar for the next 3 months.
        Endpoint: https://www.efteling.com/service/cached/getpoiinfo/en/{year}/{month}
        Response: { OpeningHours: [{ Date, OpeningHours: [{Open, Close}] }] }

        Multiple OpeningHours entries per day = regular hours + evening hours.
        """
        from datetime import date

        days: List[ParkDay] = []
        now = datetime.now(PARK_TIMEZONE)

        for month_offset in range(3):
            # calculate target month
            target = (now.replace(day=1) + timedelta(days=32 * month_offset)).replace(day=1)
            month_str = str(target.month)
            year_str = str(target.year)

            url = CALENDAR_URL.format(year=year_str, month=month_str)
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(url, headers=CALENDAR_HEADERS)
                    if resp.status_code == 400:
                        # Efteling returns 400 for past months
                        logger.info(f"[Efteling] Calendar 400 for {year_str}/{month_str} (past month), skipping.")
                        continue
                    resp.raise_for_status()
                    cal_data = resp.json()
            except Exception as e:
                logger.error(f"[Efteling] Calendar fetch failed for {year_str}/{month_str}: {e}")
                continue

            opening_hours = cal_data.get("OpeningHours", [])
            for day_entry in opening_hours:
                date_str = day_entry.get("Date", "")
                raw_hours = sorted(
                    day_entry.get("OpeningHours", []),
                    key=lambda h: h.get("Open", "00:00")
                )

                park_hours: List[ParkHours] = []
                for idx, h in enumerate(raw_hours):
                    park_hours.append(ParkHours(
                        opening_time=h.get("Open", ""),
                        closing_time=h.get("Close", ""),
                        type="operating" if idx == 0 else "informational",
                        description=None if idx == 0 else "Evening Hours",
                    ))

                days.append(ParkDay(
                    date=date_str,
                    is_open=len(park_hours) > 0,
                    hours=park_hours,
                ))

        logger.info(f"[Efteling] Fetched calendar with {len(days)} days.")
        return ParkCalendar(
            park_id=self.park_id,
            park_name=self.park_name,
            last_updated=datetime.now(timezone.utc),
            days=days,
        )


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _parse_efteling_dt(value) -> Optional[datetime]:
    """
    Parse Efteling datetime strings.
    Efteling uses ISO 8601 with timezone offset: "2026-02-20T14:30:00+01:00"
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        # ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=PARK_TIMEZONE)
        return dt
    except Exception:
        logger.debug(f"[Efteling] Could not parse datetime: {value!r}")
        return None

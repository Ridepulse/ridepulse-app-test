from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ──────────────────────────────────────────────
# Shared
# ──────────────────────────────────────────────

class Location(BaseModel):
    lat: float
    lng: float


class Photo(BaseModel):
    url: str
    alt: str
    is_primary: bool = False


# ──────────────────────────────────────────────
# Attractions — Live
# ──────────────────────────────────────────────

class AttractionStatus(str, Enum):
    open = "open"
    open_soon = "open_soon"           # opening within the hour / queue open, ride not yet running
    maintenance = "maintenance"       # planned maintenance / refurbishment
    breakdown = "breakdown"           # unplanned technical interruption (storing)
    closed = "closed"                 # closed for the day or queue closed


class SingleRiderInfo(BaseModel):
    available: bool
    status: Optional[str] = None        # "open" | "closed"
    wait_time: Optional[int] = None     # minutes, null if closed or unavailable


class VirtualQueueState(str, Enum):
    available = "available"               # VQ open, return time slots available
    temporarily_full = "temporarily_full" # walkin — no VQ needed right now
    full = "full"                         # VQ full for the day
    closed = "closed"                     # VQ not active


class VirtualQueueInfo(BaseModel):
    available: bool
    state: Optional[VirtualQueueState] = None
    return_start: Optional[datetime] = None  # start of the return time window
    return_end: Optional[datetime] = None    # end of return time window (typically +15 min)


class AttractionLive(BaseModel):
    id: str
    name: str
    status: AttractionStatus
    wait_time: Optional[int] = None     # minutes; null if not operating
    single_rider: Optional[SingleRiderInfo] = None
    virtual_queue: Optional[VirtualQueueInfo] = None


# ──────────────────────────────────────────────
# Attractions — Static info
# ──────────────────────────────────────────────

class AttractionType(str, Enum):
    rollercoaster = "rollercoaster"
    dark_ride = "dark_ride"
    flat_ride = "flat_ride"
    water_ride = "water_ride"
    kids_ride = "kids_ride"
    walkthrough = "walkthrough"
    transport = "transport"
    other = "other"


class ThrillLevel(str, Enum):
    family = "family"
    mild = "mild"
    moderate = "moderate"
    thrilling = "thrilling"
    extreme = "extreme"


class AttractionDetails(BaseModel):
    height_requirement_cm: Optional[int] = None
    capacity_per_hour: Optional[int] = None
    duration_seconds: Optional[int] = None
    thrill_level: Optional[ThrillLevel] = None


class AttractionInfo(BaseModel):
    id: str
    name: str
    land: Optional[str] = None
    type: AttractionType
    location: Optional[Location] = None
    photos: List[Photo] = []
    details: AttractionDetails = AttractionDetails()


# ──────────────────────────────────────────────
# Shows
# ──────────────────────────────────────────────

class ShowTime(BaseModel):
    start_date_time: datetime
    end_date_time: datetime
    edition: Optional[str] = None       # e.g. "Parkshow", "Avondshow"


class Show(BaseModel):
    id: str
    name: str
    status: str = "open"               # "open" | "closed"
    land: Optional[str] = None
    location: Optional[Location] = None
    photos: List[Photo] = []
    duration_minutes: Optional[int] = None
    show_times: List[ShowTime] = []


# ──────────────────────────────────────────────
# Restaurants
# ──────────────────────────────────────────────

class VenueStatus(str, Enum):
    open = "open"
    closed = "closed"


class Restaurant(BaseModel):
    id: str
    name: str
    land: Optional[str] = None
    status: VenueStatus
    location: Optional[Location] = None
    photos: List[Photo] = []
    opening_time: Optional[datetime] = None
    closing_time: Optional[datetime] = None


# ──────────────────────────────────────────────
# Shops
# ──────────────────────────────────────────────

class Shop(BaseModel):
    id: str
    name: str
    land: Optional[str] = None
    status: VenueStatus
    location: Optional[Location] = None
    photos: List[Photo] = []
    opening_time: Optional[datetime] = None
    closing_time: Optional[datetime] = None


# ──────────────────────────────────────────────
# Calendar
# ──────────────────────────────────────────────

class ParkHours(BaseModel):
    opening_time: str               # "HH:MM"
    closing_time: str               # "HH:MM"
    type: str = "operating"        # "operating" | "informational"
    description: Optional[str] = None  # e.g. "Evening Hours"


class ParkDay(BaseModel):
    date: str                       # "YYYY-MM-DD"
    is_open: bool
    hours: List[ParkHours] = []    # multiple entries for e.g. evening hours
    special_event: Optional[str] = None


class ParkCalendar(BaseModel):
    park_id: str
    park_name: str
    last_updated: datetime
    days: List[ParkDay] = []


# ──────────────────────────────────────────────
# Full park live response
# ──────────────────────────────────────────────

class ParkLiveResponse(BaseModel):
    park_id: str
    park_name: str
    last_updated: datetime
    park_status: str
    attractions: List[AttractionLive] = []
    shows: List[Show] = []
    restaurants: List[Restaurant] = []
    shops: List[Shop] = []

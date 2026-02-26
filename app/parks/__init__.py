"""
Park registry — maps park_id to connector instance.

To add a new park:
  1. Create app/parks/yourpark.py extending BaseParkConnector
  2. Import it here and add it to PARKS
"""

from app.parks.efteling import EftelingConnector
from app.parks.base import BaseParkConnector
from app.models.schemas import ParkCalendar
from datetime import datetime, timezone
from typing import List


class _PlaceholderConnector(BaseParkConnector):
    """
    Stub connector for parks that haven't been implemented yet.
    Returns empty lists so the API stays functional.
    """
    def __init__(self, park_id: str, park_name: str):
        self.park_id = park_id
        self.park_name = park_name

    async def fetch_wait_times(self) -> List:
        return []

    async def fetch_shows(self) -> List:
        return []

    async def fetch_restaurants(self) -> List:
        return []

    async def fetch_shops(self) -> List:
        return []

    async def fetch_calendar(self) -> ParkCalendar:
        return ParkCalendar(
            park_id=self.park_id,
            park_name=self.park_name,
            last_updated=datetime.now(timezone.utc),
            days=[],
        )


# ── Registry ──────────────────────────────────
PARKS: dict[str, BaseParkConnector] = {
    "efteling":        EftelingConnector(),
    "disneylandparis": _PlaceholderConnector("disneylandparis", "Disneyland Paris"),
    "europapark":      _PlaceholderConnector("europapark", "Europa-Park"),
    "phantasialand":   _PlaceholderConnector("phantasialand", "Phantasialand"),
}


def get_park(park_id: str) -> BaseParkConnector | None:
    return PARKS.get(park_id)

from abc import ABC, abstractmethod
from typing import List
from app.models.schemas import AttractionLive, Show, Restaurant, Shop, ParkCalendar


class BaseParkConnector(ABC):
    """
    Abstract base class for all theme park connectors.
    Add a new park by creating a file in app/parks/ and extending this class.
    """
    park_id: str
    park_name: str

    @abstractmethod
    async def fetch_wait_times(self) -> List[AttractionLive]: ...

    @abstractmethod
    async def fetch_shows(self) -> List[Show]: ...

    @abstractmethod
    async def fetch_restaurants(self) -> List[Restaurant]: ...

    @abstractmethod
    async def fetch_shops(self) -> List[Shop]: ...

    @abstractmethod
    async def fetch_calendar(self) -> ParkCalendar: ...

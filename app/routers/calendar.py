from fastapi import APIRouter, Request
from app.services.data_loader import get_park_id, load_calendar_json

router = APIRouter()


@router.get("/calendar", summary="Park opening calendar")
async def get_calendar(request: Request):
    """
    Opening calendar for the next 3 months.
    Refreshed every 24 hours — calendar.json is overwritten each time.
    Not stored in MongoDB.
    """
    return load_calendar_json(get_park_id(request))

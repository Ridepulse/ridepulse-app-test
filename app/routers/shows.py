from fastapi import APIRouter, Request
from app.services.data_loader import get_park_id, load_live_json

router = APIRouter()


@router.get("/shows", summary="Today's show schedule")
async def get_shows(request: Request):
    """Show schedule with all showtimes and editions. Refreshed every 5 minutes."""
    park_id = get_park_id(request)
    data = load_live_json(park_id)
    return {
        "park_id":      data["park_id"],
        "park_name":    data["park_name"],
        "last_updated": data["last_updated"],
        "shows":        data.get("shows", []),
    }

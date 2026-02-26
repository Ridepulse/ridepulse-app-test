from fastapi import APIRouter, Request
from app.services.data_loader import get_park_id, load_live_json

router = APIRouter()


@router.get("/rides", summary="Live wait times & attraction statuses")
async def get_rides(request: Request):
    """Live wait times for all attractions. Refreshed every 5 minutes."""
    park_id = get_park_id(request)
    data = load_live_json(park_id)
    return {
        "park_id":     data["park_id"],
        "park_name":   data["park_name"],
        "last_updated": data["last_updated"],
        "park_status": data["park_status"],
        "attractions": data.get("attractions", []),
    }

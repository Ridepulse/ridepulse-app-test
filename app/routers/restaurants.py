from fastapi import APIRouter, Request
from app.services.data_loader import get_park_id, load_live_json

router = APIRouter()


@router.get("/restaurants", summary="Restaurant & shop open/close status")
async def get_restaurants(request: Request):
    """Restaurant and shop statuses with opening hours. Refreshed every 5 minutes."""
    park_id = get_park_id(request)
    data = load_live_json(park_id)
    return {
        "park_id":      data["park_id"],
        "park_name":    data["park_name"],
        "last_updated": data["last_updated"],
        "restaurants":  data.get("restaurants", []),
        "shops":        data.get("shops", []),
    }

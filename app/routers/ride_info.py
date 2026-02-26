from fastapi import APIRouter, Request, HTTPException
from app.services.data_loader import get_park_id, load_ride_info

router = APIRouter()


@router.get("/ride-info", summary="Static attraction info (type, capacity, photos, location)")
async def get_ride_info(request: Request):
    """
    Static info for all attractions in this park.
    Served from ride-info/{park_id}.json — edit that file to add photos, capacity etc.
    Not stored in MongoDB (to save storage).
    """
    return load_ride_info(get_park_id(request))


@router.get("/ride-info/{ride_id}", summary="Static info for a single attraction")
async def get_single_ride_info(request: Request, ride_id: str):
    data = load_ride_info(get_park_id(request))
    ride = next((a for a in data.get("attractions", []) if a["id"] == ride_id), None)
    if not ride:
        raise HTTPException(status_code=404, detail=f"Ride '{ride_id}' not found.")
    return ride

from fastapi import APIRouter, Request, Query, HTTPException
from app.services.data_loader import get_park_id
from app.database import get_db
from datetime import datetime, timezone, timedelta

router = APIRouter()


@router.get("/rides/history/{ride_id}", summary="Historical wait times for one ride")
async def get_ride_history(
    request: Request,
    ride_id: str,
    hours: int = Query(default=24, ge=1, le=720, description="Hours back to fetch (max 720 = 30 days)"),
    date: str  = Query(default=None, description="Fetch specific day: YYYY-MM-DD (overrides hours)"),
):
    """
    Historical wait time data for a single ride — use this for building graphs.
    Records are stored every 5 minutes and kept for 90 days.
    """
    park_id = get_park_id(request)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available.")

    if date:
        try:
            day_start = datetime.fromisoformat(date).replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )
            time_filter = {"$gte": day_start, "$lt": day_start + timedelta(days=1)}
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD.")
    else:
        time_filter = {"$gte": datetime.now(timezone.utc) - timedelta(hours=hours)}

    cursor = db.wait_times.find(
        {"park_id": park_id, "ride_id": ride_id, "timestamp": time_filter},
        {"_id": 0, "park_id": 0, "ride_id": 0},
        sort=[("timestamp", 1)],
    )
    records = await cursor.to_list(length=10_000)

    return {
        "park_id":    park_id,
        "ride_id":    ride_id,
        "ride_name":  records[0].get("ride_name") if records else None,
        "data_points": len(records),
        "history": [
            {
                "timestamp":          r["timestamp"],
                "status":             r["status"],
                "wait_time":          r.get("wait_time"),
                "single_rider_wait":  r.get("single_rider_wait"),
                "virtual_queue_state": r.get("virtual_queue_state"),
            }
            for r in records
        ],
    }


@router.get("/rides/history", summary="Historical wait times for all rides")
async def get_all_rides_history(
    request: Request,
    hours: int = Query(default=8, ge=1, le=48),
):
    """
    Aggregated historical data for all rides — useful for overview heatmaps.
    """
    park_id = get_park_id(request)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available.")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipeline = [
        {"$match": {"park_id": park_id, "timestamp": {"$gte": since}}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id":       "$ride_id",
            "ride_name": {"$first": "$ride_name"},
            "history":   {"$push": {
                "timestamp": "$timestamp",
                "status":    "$status",
                "wait_time": "$wait_time",
            }},
        }},
        {"$project": {"_id": 0, "ride_id": "$_id", "ride_name": 1, "history": 1}},
        {"$sort": {"ride_id": 1}},
    ]
    results = await db.wait_times.aggregate(pipeline).to_list(length=500)
    return {
        "park_id": park_id,
        "hours":   hours,
        "rides":   results,
    }

# Themepark Unified API

A unified FastAPI backend that aggregates live data from multiple theme park APIs into a single, consistent JSON format. Designed to power real-time dashboards, wait time graphs, and park planning tools.

---

## Endpoints

| Method | Endpoint | Description | Refresh |
|--------|----------|-------------|---------|
| GET | `/{parkId}/rides` | Live wait times & attraction statuses | Every 5 min |
| GET | `/{parkId}/ride-info` | Static info: type, capacity, height req, photos | Manual |
| GET | `/{parkId}/ride-info/{rideId}` | Static info for a single ride | Manual |
| GET | `/{parkId}/shows` | Today's show schedule | Every 5 min |
| GET | `/{parkId}/restaurants` | Restaurant open/close status | Every 5 min |
| GET | `/{parkId}/calendar` | Park opening calendar | Every 24h |
| GET | `/{parkId}/rides/history/{rideId}` | Historical wait times (MongoDB) | Stored every 5 min |
| GET | `/{parkId}/rides/history` | Historical wait times for all rides | Stored every 5 min |

**Supported park IDs:** `efteling`, `disneylandparis`, `europapark`, `phantasialand`

**Query params for history endpoints:**
- `?hours=24` — last N hours of data (default: 24, max: 720)
- `?date=2026-02-20` — data for a specific day

---

## Quick Start

```bash
# 1. Clone and navigate
git clone <your-repo>
cd themepark-api

# 2. Copy env file
cp .env.example .env

# 3. Build and start
docker-compose up --build -d

# 4. View docs
open http://localhost:8000/docs
```

---

## Project Structure

```
themepark-api/
├── app/
│   ├── main.py                    # FastAPI app, lifespan, router registration
│   ├── database.py                # MongoDB connection (Motor async)
│   ├── connectors/
│   │   ├── base.py                # Abstract base connector
│   │   ├── __init__.py            # Connector registry (park_id → connector)
│   │   ├── efteling.py            # ✅ Fully implemented
│   │   ├── disneylandparis.py     # 🔧 Placeholder
│   │   ├── europapark.py          # 🔧 Placeholder
│   │   └── phantasialand.py       # 🔧 Placeholder
│   ├── models/
│   │   └── schemas.py             # Pydantic models (unified schema)
│   ├── routers/
│   │   ├── rides.py               # GET /{parkId}/rides
│   │   ├── ride_info.py           # GET /{parkId}/ride-info
│   │   ├── shows.py               # GET /{parkId}/shows
│   │   ├── restaurants.py         # GET /{parkId}/restaurants
│   │   ├── calendar.py            # GET /{parkId}/calendar
│   │   └── history.py             # GET /{parkId}/rides/history
│   └── services/
│       ├── scheduler.py           # APScheduler (5min + 24h jobs)
│       └── data_loader.py         # JSON file loader helpers
├── data/
│   ├── efteling/
│   │   ├── ride-info.json         # ✅ Static ride data (edit manually)
│   │   ├── live.json              # Auto-generated every 5 min
│   │   └── calendar.json          # Auto-generated every 24h
│   ├── disneylandparis/           # Same structure, populate when ready
│   ├── europapark/
│   └── phantasialand/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Adding a New Park

1. Create a new connector in `app/connectors/yourpark.py` extending `BaseParkConnector`
2. Implement all abstract methods (`fetch_wait_times`, `fetch_shows`, `fetch_restaurants`, `fetch_shops`, `fetch_calendar`)
3. Register it in `app/connectors/__init__.py`
4. Add `"yourpark"` to `SUPPORTED_PARKS` in `app/main.py`
5. Create `data/yourpark/ride-info.json` with static attraction data

---

## Adding Static Ride Info (Photos, Capacity, etc.)

Edit `/data/{parkId}/ride-info.json`. This file is served directly from disk — it is **not** written by the scheduler and is **not** stored in MongoDB (to save storage).

```json
{
  "park_id": "efteling",
  "attractions": [
    {
      "id": "baron1898",
      "name": "Baron 1898",
      "land": "Marerijk",
      "type": "rollercoaster",
      "location": { "lat": 51.6494, "lng": 5.0464 },
      "photos": [
        { "url": "https://cdn.degusseme.com/efteling/baron1898/01.jpg", "alt": "Baron 1898", "is_primary": true }
      ],
      "details": {
        "height_requirement_cm": 130,
        "capacity_per_hour": 1200,
        "duration_seconds": 90,
        "thrill_level": "extreme"
      }
    }
  ]
}
```

**Attraction types:** `rollercoaster`, `dark_ride`, `flat_ride`, `water_ride`, `kids_ride`, `walkthrough`, `transport`, `other`
**Thrill levels:** `family`, `mild`, `moderate`, `thrilling`, `extreme`

---

## MongoDB Schema

Only **live/historical wait time data** is written to MongoDB. Static info (location, capacity, photos) is never stored there.

**Collection: `wait_times`**
```json
{
  "park_id": "efteling",
  "ride_id": "baron1898",
  "ride_name": "Baron 1898",
  "status": "open",
  "wait_time": 35,
  "single_rider_wait": null,
  "virtual_queue_status": null,
  "timestamp": "2026-02-20T14:30:00Z"
}
```

Records older than **90 days** are automatically deleted via a MongoDB TTL index.

---

## Nginx Reverse Proxy (for api.degusseme.com)

```nginx
server {
    listen 443 ssl;
    server_name api.degusseme.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Implementing a Placeholder Connector

```python
class EuropaParkConnector(BaseParkConnector):
    park_id = "europapark"
    park_name = "Europa-Park"

    async def fetch_wait_times(self) -> List[AttractionLive]:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://your-europapark-endpoint")
            data = resp.json()
        # Map data["items"] → List[AttractionLive]
        ...
```

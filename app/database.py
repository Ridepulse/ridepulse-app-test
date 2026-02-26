from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
import logging
import os

logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017")
DB_NAME   = os.getenv("MONGO_DB", "themepark")

client = None
db     = None


async def connect_db():
    global client, db
    logger.info(f"Connecting to MongoDB at {MONGO_URI}...")
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    await _ensure_indexes()
    logger.info("MongoDB connected.")


async def close_db():
    global client
    if client:
        client.close()
        logger.info("MongoDB connection closed.")


async def _ensure_indexes():
    # Fast queries by park + ride + time (used by history endpoints)
    await db.wait_times.create_index(
        [("park_id", ASCENDING), ("ride_id", ASCENDING), ("timestamp", DESCENDING)],
        name="park_ride_time",
    )
    # TTL index: auto-delete records older than 90 days
    await db.wait_times.create_index(
        [("timestamp", ASCENDING)],
        expireAfterSeconds=60 * 60 * 24 * 90,
        name="ttl_90days",
    )
    logger.info("MongoDB indexes ensured.")


def get_db():
    return db
